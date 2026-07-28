# ruff: noqa: RUF002, RUF003
"""Scheduler job: сводка «Проблемные товары» в Telegram (09:30 MSK).

Для каждого проекта с включённой настройкой `ads_problem_digest` шлём в
указанные чаты короткий текст + xlsx-файл (лист на бренд). По понедельникам
дополнительно — недельная сводка (прошлая неделя против позапрошлой).
Ошибки одного проекта/чата не роняют рассылку остальным.
"""

import asyncio
import json
import logging

import pytz
from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import Project
from backend.models.refs import ProjectSetting
from backend.services.funnel.problem_digest import (
    ASAP_KEY,
    ASAP_MAX_ATTEMPTS,
    DIGEST_SETTINGS_KEY,
    attach_sheet_link,
    build_daily_digest,
    build_weekly_digest,
    pop_asap_request,
    record_digest_status,
    request_asap_send,
    sanitize_settings,
)
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler.problem_digest")

MSK = pytz.timezone("Europe/Moscow")

# Ретрай отправки в один чат: транзиент сети/5xx Telegram (прокси-путь воркера
# иногда моргает — из-за этого 28.07.2026 утренняя сводка не ушла вовсе).
_SEND_RETRIES = 3
_SEND_BACKOFF_SEC = (5.0, 15.0)


async def _enabled_projects() -> list[tuple[int, dict]]:
    """Проекты с включённой сводкой: [(project_id, cfg)]."""
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(select(ProjectSetting).where(ProjectSetting.key == DIGEST_SETTINGS_KEY))
        ).scalars().all()
    out: list[tuple[int, dict]] = []
    for ps in rows:
        try:
            cfg = sanitize_settings(json.loads(ps.value or "{}"))
        except (TypeError, ValueError):
            continue
        if cfg["enabled"] and cfg["chat_ids"]:
            out.append((ps.project_id, cfg))
    return out


async def send_problem_digests() -> None:
    """Разослать ежедневную (и по понедельникам недельную) сводку проблемных товаров."""
    targets = await _enabled_projects()
    if not targets:
        logger.info("Problem digest: no enabled projects, skipping")
        return

    from backend.integrations.telegram_bot import bot

    if not bot:
        logger.warning("Problem digest: bot not initialized, skipping")
        return

    now_msk = utcnow().replace(tzinfo=pytz.UTC).astimezone(MSK)
    is_monday = now_msk.weekday() == 0

    sent = errors = 0
    for project_id, cfg in targets:
        try:
            async with AsyncSessionLocal() as db:
                project = await db.get(Project, project_id)
                if not project:
                    continue
                payloads = [await build_daily_digest(db, project, cfg, now_msk)]
                if is_monday:
                    payloads.append(await build_weekly_digest(db, project, cfg, now_msk))
                for payload in payloads:
                    await attach_sheet_link(db, project_id, cfg, payload)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Problem digest: build failed (project=%s)", project_id)
            errors += 1
            # Самоизлечение: маркер ASAP — минутный тик добьёт рассылку, когда
            # причина (обычно транзиент БД/сети) пройдёт; переживает и рестарт воркера.
            await _record_and_reschedule(project_id, kind="daily", error=f"build: {e}")
            continue

        failed_kinds: dict[str, str] = {}
        for chat_id in cfg["chat_ids"]:
            for payload in payloads:
                try:
                    sent += await _send_with_retry(bot, chat_id, payload)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.exception("Problem digest: send failed (project=%s, chat=%s)", project_id, chat_id)
                    errors += 1
                    failed_kinds.setdefault(str(payload.get("kind") or "daily"), f"chat {chat_id}: {e}")

        try:
            async with AsyncSessionLocal() as db:
                for payload in payloads:
                    kind = str(payload.get("kind") or "daily")
                    err = failed_kinds.get(kind)
                    await record_digest_status(db, project_id, kind=kind, ok=err is None, error=err)
                # Не все чаты получили → повтор минутным тиком (дневная приоритетнее недельной)
                retry_kind = "daily" if "daily" in failed_kinds else ("weekly" if failed_kinds else None)
                if retry_kind:
                    await request_asap_send(db, project_id, retry_kind)
                await db.commit()
        except Exception:  # noqa: BLE001 — телеметрия не должна ронять рассылку остальных
            logger.exception("Problem digest: status write failed (project=%s)", project_id)

    logger.info("Problem digest complete: sent=%d, errors=%d", sent, errors)


async def _record_and_reschedule(project_id: int, *, kind: str, error: str) -> None:
    """ERROR-статус + ASAP-маркер (best-effort, в своей сессии)."""
    try:
        async with AsyncSessionLocal() as db:
            await record_digest_status(db, project_id, kind=kind, ok=False, error=error)
            await request_asap_send(db, project_id, kind)
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Problem digest: reschedule failed (project=%s)", project_id)


async def problem_digest_asap_tick() -> None:
    """Раз в минуту: разослать сводки, запрошенные кнопкой «прислать сейчас».

    send-now в API-контейнере не может достучаться до Telegram (РКН, прокси-путь
    живёт в worker) — он ставит маркер ads_problem_digest_asap, а этот тик
    исполняет. Почти всегда no-op (один SELECT по ключу).
    """
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(select(ProjectSetting).where(ProjectSetting.key == ASAP_KEY, ProjectSetting.value != ""))
        ).scalars().all()
        requests = [ps.project_id for ps in rows]
    if not requests:
        return

    from backend.integrations.telegram_bot import bot

    if not bot:
        logger.warning("Problem digest asap: bot not initialized, skipping")
        return

    now_msk = utcnow().replace(tzinfo=pytz.UTC).astimezone(MSK)
    for project_id in requests:
        marker: dict | None = None
        try:
            async with AsyncSessionLocal() as db:
                marker = await pop_asap_request(db, project_id)
                if not marker:
                    continue
                kind = marker["kind"]
                from backend.services.funnel.problem_digest import get_digest_settings

                cfg = await get_digest_settings(db, project_id)
                if not cfg["chat_ids"]:
                    continue
                project = await db.get(Project, project_id)
                if not project:
                    continue
                build = build_daily_digest if kind == "daily" else build_weekly_digest
                payload = await build(db, project, cfg, now_msk)
                await attach_sheet_link(db, project_id, cfg, payload)
            for chat_id in cfg["chat_ids"]:
                await _send_to_chat(bot, chat_id, payload)
            async with AsyncSessionLocal() as db:
                await record_digest_status(db, project_id, kind=kind, ok=True)
                await db.commit()
            logger.info("Problem digest asap: sent (project=%s, kind=%s)", project_id, kind)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Problem digest asap: failed (project=%s)", project_id)
            if marker is None:
                continue
            # Самоизлечение: перевзвести маркер до потолка попыток — следующий тик
            # повторит через минуту. Потолок — чтобы перманентная ошибка не жила вечно.
            attempts = marker["attempts"] + 1
            try:
                async with AsyncSessionLocal() as db:
                    if attempts < ASAP_MAX_ATTEMPTS:
                        await request_asap_send(db, project_id, marker["kind"], attempts=attempts)
                    else:
                        await record_digest_status(
                            db, project_id, kind=marker["kind"], ok=False,
                            error=f"не отправлено за {ASAP_MAX_ATTEMPTS} попыток: {e}",
                        )
                    await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("Problem digest asap: re-arm failed (project=%s)", project_id)


async def _send_to_chat(bot, chat_id: int, payload: dict) -> int:
    """Текст + документ в один чат. Возвращает 1 (для счётчика отправок)."""
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.types import BufferedInputFile

    try:
        await bot.send_message(chat_id=chat_id, text=payload["text"], parse_mode="HTML")
    except TelegramBadRequest:
        # Невалидная HTML-разметка (экзотика в названиях) — доставляем без форматирования
        logger.warning("Problem digest: HTML parse failed (chat=%s), retrying as plain text", chat_id)
        await bot.send_message(chat_id=chat_id, text=payload["text"])
    await bot.send_document(
        chat_id=chat_id,
        document=BufferedInputFile(payload["xlsx"], filename=payload["filename"]),
    )
    return 1


async def _send_with_retry(bot, chat_id: int, payload: dict) -> int:
    """_send_to_chat с ретраем ТОЛЬКО транзиентов: сеть/5xx Telegram/flood-wait.

    Перманентные ошибки (бот не в чате, невалидный chat_id — TelegramAPIError
    вне списка выше) не ретраим: повтор их не лечит, а рассылку задерживает.
    """
    from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError

    last_exc: Exception | None = None
    for attempt in range(_SEND_RETRIES):
        try:
            return await _send_to_chat(bot, chat_id, payload)
        except asyncio.CancelledError:
            raise
        except TelegramRetryAfter as e:
            last_exc = e
            delay = float(getattr(e, "retry_after", 0) or 0) or _SEND_BACKOFF_SEC[-1]
        except (TelegramNetworkError, TelegramServerError) as e:
            last_exc = e
            delay = _SEND_BACKOFF_SEC[min(attempt, len(_SEND_BACKOFF_SEC) - 1)]
        if attempt < _SEND_RETRIES - 1:
            logger.warning(
                "Problem digest: transient send error (chat=%s, attempt=%d/%d): %s — retry in %.0fs",
                chat_id, attempt + 1, _SEND_RETRIES, last_exc, delay,
            )
            await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]  # last_exc всегда установлен, если дошли сюда
