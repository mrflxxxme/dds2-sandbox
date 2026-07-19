# ruff: noqa: RUF001, RUF002, RUF003
"""Раз в час: сторож свежести черновиков сборки и остатков WB → Telegram-чат проекта.

Черновик «протух», если по нему дольше STALE_THRESHOLD_SECONDS не было ни одного
события синка/правки (assembly_draft_events c event_type из SYNC_EVENT_TYPES —
любая такая запись означает «черновиком занимались»); событий нет вовсе — берём
draft.updated_at. Отдельно проверяется возраст остатков WB (max updated_at
wb_warehouse_stocks): по старым остаткам едет весь расчёт потребности. Оба чека —
только для проектов, где есть активные (не is_deleted) черновики; проект без
остатков WB вовсе (синк не настроен) по остаткам не алертится.

Анти-спам: не чаще 1 алерта на черновик (и 1 — на остатки) в ALERT_COOLDOWN_SECONDS
(Redis SET NX EX). Redis недоступен — шлём (best-effort, спам ограничен интервалом
джоба). Доставка — send_analytics_message (httpx, работает в воркере без
aiogram-синглтона), адресаты — чаты проекта с supply_notify_enabled (те же, что у
supply_discrepancy). Сбой одного проекта не роняет остальные; исключения (кроме
отмены) глотаются и логируются.
"""

import asyncio
import html
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import get_redis
from backend.database import AsyncSessionLocal
from backend.models.assembly import AssemblyDraft, AssemblyDraftEvent
from backend.models.auth import Project
from backend.models.integrations import WbWarehouseStock
from backend.scheduler.helpers import get_sync_project_ids
from backend.services import telegram_service
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler")

_APP_BASE_URL = "https://app.vyatkin-wb.ru"

# Порог протухания: 6 часов без синка/правки черновика (или без синка остатков WB).
STALE_THRESHOLD_SECONDS = 6 * 3600
# Анти-спам: не чаще одного алерта на черновик / на остатки в 12 часов.
ALERT_COOLDOWN_SECONDS = 12 * 3600
# События, означающие «черновиком занимались» (синк или правка раскладки).
SYNC_EVENT_TYPES = ("AUTO_SYNC", "ACCEPTANCE_SYNC", "MATRIX_EDIT", "MATRIX_WRITE", "PREBOOK_TOPUP")
# Предохранитель от простыни: больше черновиков на проект не разбираем.
_DRAFTS_CAP = 200


async def get_stale_drafts(
    db: AsyncSession, project_id: int, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Активные черновики проекта, не синковавшиеся дольше порога.

    Возраст = max(created_at) событий из SYNC_EVENT_TYPES этого черновика;
    событий нет — draft.updated_at. Возвращает [{draft_id, name, age_hours}].
    """
    now = now or utcnow()
    last_event = (
        select(
            AssemblyDraftEvent.draft_id,
            sqlfunc.max(AssemblyDraftEvent.created_at).label("last_event_at"),
        )
        .where(
            AssemblyDraftEvent.project_id == project_id,
            AssemblyDraftEvent.event_type.in_(SYNC_EVENT_TYPES),
        )
        .group_by(AssemblyDraftEvent.draft_id)
        .subquery()
    )
    rows = await db.execute(
        select(
            AssemblyDraft.id,
            AssemblyDraft.name,
            AssemblyDraft.updated_at,
            last_event.c.last_event_at,
        )
        .outerjoin(last_event, last_event.c.draft_id == AssemblyDraft.id)
        .where(
            AssemblyDraft.project_id == project_id,
            AssemblyDraft.is_deleted == False,  # noqa: E712
        )
        .limit(_DRAFTS_CAP)
    )

    stale: list[dict[str, Any]] = []
    for draft_id, name, updated_at, last_event_at in rows:
        last = last_event_at or updated_at
        if last is None:  # у updated_at nullable-тип в миксине — перестраховка
            continue
        age = (now - last).total_seconds()
        if age >= STALE_THRESHOLD_SECONDS:
            stale.append({"draft_id": draft_id, "name": name, "age_hours": int(age // 3600)})
    return stale


async def get_wb_stocks_age_seconds(
    db: AsyncSession, project_id: int, *, now: datetime | None = None
) -> float | None:
    """Возраст max(updated_at) остатков WB проекта в секундах. None — остатков нет
    вовсе (проект без WB-синка — по остаткам не алертим)."""
    now = now or utcnow()
    result = await db.execute(
        select(sqlfunc.max(WbWarehouseStock.updated_at)).where(
            WbWarehouseStock.project_id == project_id,
        )
    )
    last_sync = result.scalar()
    if last_sync is None:
        return None
    return (now - last_sync).total_seconds()


def build_draft_stale_message(name: str, age_hours: int) -> str:
    """HTML-строка алерта по протухшему черновику."""
    return (
        f"📋 Черновик «<b>{html.escape(name or '—')}</b>» не синковался {age_hours} ч — "
        "зайди на Сборку, синк подтянет свежую потребность"
    )


def build_wb_stocks_stale_message(age_hours: int) -> str:
    """HTML-строка алерта по протухшим остаткам WB."""
    return (
        f"⚠ Остатки WB не обновлялись {age_hours} ч — "
        "расчёт потребности едет по старым данным"
    )


async def _antispam_acquire(key: str) -> bool:
    """True — алерт можно слать (ключ захвачен на ALERT_COOLDOWN_SECONDS).

    SET NX EX: False — по этому ключу недавно слали. Redis недоступен/сбоит —
    True (лучше редкий повтор, чем молчание; частота капнута интервалом джоба).
    """
    r = await get_redis()
    if r is None:
        return True
    try:
        return bool(await r.set(key, "1", ex=ALERT_COOLDOWN_SECONDS, nx=True))
    except Exception as e:
        logger.warning("Draft staleness watch: antispam redis error (%s) — sending anyway", e)
        return True


async def check_all_projects_draft_staleness() -> None:
    """Обойти проекты, найти протухшие черновики/остатки и разослать в TG-чаты."""
    project_ids = await get_sync_project_ids()
    if not project_ids:
        return

    sent = 0
    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                chats = await telegram_service.list_supply_notify_chats(db, project_id)
                if not chats:
                    continue

                # Оба чека — только для проектов с активными черновиками.
                has_drafts = (
                    await db.execute(
                        select(AssemblyDraft.id)
                        .where(
                            AssemblyDraft.project_id == project_id,
                            AssemblyDraft.is_deleted == False,  # noqa: E712
                        )
                        .limit(1)
                    )
                ).scalar() is not None
                if not has_drafts:
                    continue

                parts: list[str] = []
                for d in await get_stale_drafts(db, project_id):
                    key = f"draft_stale_alert:{project_id}:{d['draft_id']}"
                    if await _antispam_acquire(key):
                        parts.append(build_draft_stale_message(d["name"], d["age_hours"]))

                stocks_age = await get_wb_stocks_age_seconds(db, project_id)
                if (
                    stocks_age is not None
                    and stocks_age >= STALE_THRESHOLD_SECONDS
                    and await _antispam_acquire(f"wb_stocks_stale_alert:{project_id}")
                ):
                    parts.append(build_wb_stocks_stale_message(int(stocks_age // 3600)))

                if not parts:
                    continue

                project = (
                    await db.execute(select(Project).where(Project.id == project_id))
                ).scalar_one_or_none()
                if project and project.slug:
                    url = f"{_APP_BASE_URL}/p/{html.escape(project.slug)}/warehouse/assembly"
                    parts.append(f'\n🔗 <a href="{url}">Открыть «Сборку»</a>')

                text = "\n".join(parts)
                results = await asyncio.gather(
                    *[telegram_service.send_analytics_message(c.chat_id, text) for c in chats],
                    return_exceptions=True,
                )
                sent += sum(1 for ok in results if ok is True)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # best-effort: один проект не должен ронять джоб
            logger.error(
                "Draft staleness watch: project %d failed — %s", project_id, e, exc_info=True
            )
    if sent:
        logger.info("Draft staleness watch: sent %d message(s)", sent)
