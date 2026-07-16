"""Каждые 2 часа: расхождения по WB-поставкам → в Telegram-чат проекта.

Сборки с назначенной машиной / в пути, чья WB-поставка расходится по дате сдачи,
паллетам или неоформленному пропуску (см. link_anomalies._supply_discrepancies).
Пока проблема не исправлена — напоминание повторяется на каждом прогоне (интервал
джоба = 2ч и есть «повтор каждые 2 часа, пока проблема есть»). Если расхождений
нет — сообщение не шлётся.

Доставка — send_analytics_message (httpx, работает в воркере без aiogram-синглтона),
адресаты — чаты проекта с ff_notify_enabled. Best-effort: сбой одного проекта не
роняет остальные; исключения (кроме отмены) глотаются и логируются.
"""

import asyncio
import logging

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models.auth import Project
from backend.scheduler.helpers import get_sync_project_ids
from backend.services import telegram_service
from backend.services.assembly.link_anomalies import get_supply_discrepancies
from backend.services.fulfillment_notify import build_supply_discrepancy_message

logger = logging.getLogger("dds.scheduler")


async def check_all_projects_supply_discrepancies() -> None:
    """Обойти проекты, собрать расхождения поставок и разослать в TG-чаты."""
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
                rows = await get_supply_discrepancies(db, project_id)
                if not rows:
                    continue
                project = (
                    await db.execute(select(Project).where(Project.id == project_id))
                ).scalar_one_or_none()
                text = build_supply_discrepancy_message(project.slug if project else None, rows)
                if not text:
                    continue
                results = await asyncio.gather(
                    *[telegram_service.send_analytics_message(c.chat_id, text) for c in chats],
                    return_exceptions=True,
                )
                sent += sum(1 for ok in results if ok is True)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # best-effort: один проект не должен ронять джоб
            logger.error(
                "Supply discrepancy notify: project %d failed — %s", project_id, e, exc_info=True
            )
    if sent:
        logger.info("Supply discrepancy notify: sent %d message(s)", sent)
