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
    DIGEST_SETTINGS_KEY,
    attach_sheet_link,
    build_daily_digest,
    build_weekly_digest,
    pop_asap_request,
    sanitize_settings,
)
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler.problem_digest")

MSK = pytz.timezone("Europe/Moscow")


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
        except Exception:
            logger.exception("Problem digest: build failed (project=%s)", project_id)
            errors += 1
            continue

        for chat_id in cfg["chat_ids"]:
            for payload in payloads:
                try:
                    sent += await _send_to_chat(bot, chat_id, payload)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Problem digest: send failed (project=%s, chat=%s)", project_id, chat_id)
                    errors += 1

    logger.info("Problem digest complete: sent=%d, errors=%d", sent, errors)


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
        try:
            async with AsyncSessionLocal() as db:
                kind = await pop_asap_request(db, project_id)
                if not kind:
                    continue
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
            logger.info("Problem digest asap: sent (project=%s, kind=%s)", project_id, kind)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Problem digest asap: failed (project=%s)", project_id)


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
