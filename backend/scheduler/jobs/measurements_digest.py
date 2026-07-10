"""
Scheduler job: ежедневная сводка замеров WB в Telegram (09:00 MSK).

Для каждого проекта, где хотя бы один чат включил тумблер «Замеры»
(measurements_notify_enabled), шлём сводку замеров склада за ВЧЕРА + СЕГОДНЯ
(по MSK) в эти чаты. Перед сборкой — best-effort свежий синк короткого окна,
чтобы «сегодня» было актуальным (основной синк идёт в 01:00, дневные замеры
приходят позже). Строго best-effort: любая ошибка логируется, не роняет джоб.
"""

import asyncio
import logging
from datetime import datetime, time, timedelta

import pytz
from sqlalchemy import distinct, select

from backend.database import AsyncSessionLocal
from backend.models.telegram import TelegramChatBinding
from backend.services import measurements_service, telegram_service

logger = logging.getLogger("dds.scheduler.measurements_digest")

MSK = pytz.timezone("Europe/Moscow")
FRESH_SYNC_DAYS = 2  # окно best-effort досинка перед сводкой (вчера+сегодня)


async def _projects_with_measurement_chats() -> list[int]:
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(distinct(TelegramChatBinding.project_id)).where(
                    TelegramChatBinding.measurements_notify_enabled == True,
                )
            )
        ).all()
    return [r[0] for r in rows]


async def _fresh_sync(project_id: int, date_from, date_to) -> None:
    """Best-effort досинк замеров за короткое окно. Нет ключа/ошибка → тихо пропускаем."""
    from backend.services import wb_measurements_sync

    try:
        async with AsyncSessionLocal() as db:
            await asyncio.wait_for(
                wb_measurements_sync.sync_all_measurements(db, project_id, date_from, date_to),
                timeout=180,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.info("measurements digest: fresh sync skipped (project=%s): %s", project_id, exc)


async def send_measurement_digests():
    """Разослать ежедневную сводку замеров во все чаты с включённым тумблером «Замеры»."""
    logger.info("Measurements digest: starting")
    project_ids = await _projects_with_measurement_chats()
    if not project_ids:
        logger.info("Measurements digest: no chats opted in, skipping")
        return

    now_msk = datetime.now(MSK)
    today = now_msk.date()
    yesterday = today - timedelta(days=1)
    df = MSK.localize(datetime.combine(yesterday, time.min))
    dt = MSK.localize(datetime.combine(today, time.max))

    sent = 0
    errors = 0
    for project_id in project_ids:
        try:
            await _fresh_sync(project_id, yesterday, today)

            async with AsyncSessionLocal() as db:
                chats = await telegram_service.list_measurements_notify_chats(db, project_id)
                if not chats:
                    continue
                data = await measurements_service.warehouse_digest_data(db, project_id, df, dt)

            text = measurements_service.build_measurement_digest_text(yesterday, today, data)
            if not text:
                logger.info("Measurements digest: project %d — no measurements, skip", project_id)
                continue

            results = await asyncio.gather(
                *[telegram_service.send_analytics_message(c.chat_id, text) for c in chats],
                return_exceptions=True,
            )
            sent += sum(1 for r in results if r is True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Measurements digest: project %d failed", project_id)
            errors += 1

    logger.info("Measurements digest: done — sent=%d, errors=%d", sent, errors)
