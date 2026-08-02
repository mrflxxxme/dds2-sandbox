# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Scheduler job: отправка approved-ответов на отзывы/вопросы WB (wb_feedback_replies).

Частый прогон (каждые 2 минуты): забирает approved-очередь каждого проекта и
шлёт в WB с троттлингом 1 rps (лимит методов отзывов). Успех → sent + зеркало
is_answered; ошибка → error + текст (429 — остановка прогона проекта).
Каждый проект — в своей сессии; ошибки одного проекта не валят остальные.
"""

import asyncio
import logging

from backend.database import AsyncSessionLocal
from backend.scheduler.helpers import get_sync_project_ids

logger = logging.getLogger("dds.scheduler")


async def send_all_projects_pending_replies():
    """Отправить approved-ответы по всем проектам с WB-ключом."""
    project_ids = await get_sync_project_ids()
    if not project_ids:
        return

    from backend.services.reply_service import send_pending_replies

    total_sent = 0
    total_errors = 0

    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(
                    send_pending_replies(db, project_id),
                    timeout=300,  # 50 ответов × 1.1 сек ≈ минута; 300 сек — с запасом
                )
            total_sent += int(result.get("sent", 0))
            total_errors += int(result.get("errors", 0))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Нет ключа / сбой WB — лог и следующий проект (ValueError без ключа — штатно)
            if "нет WB-ключа" not in str(e) and "WB API ключ не найден" not in str(e):
                logger.error("replies sender: project %d failed — %s", project_id, e, exc_info=True)

    if total_sent or total_errors:
        logger.info("replies sender: done — sent=%d, errors=%d", total_sent, total_errors)
