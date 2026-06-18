# ruff: noqa: RUF001, RUF002, RUF003
"""
Scheduler job: ежедневный снимок распределения остатков «где товар» по проектам
с активной ФФ-интеграцией. Копит динамику склада ФФ вперёд (FulfillmentStock
истории не имеет). Worker-контейнер; запускается раз в день (MSK).
"""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import IntegrationKey
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler")

_MSK_OFFSET = timedelta(hours=3)


async def snapshot_all_projects_stock_distribution():
    """Снимок распределения за сегодня (MSK) для всех ФФ-проектов (idempotent)."""
    from backend.services.assembly.stock_distribution import snapshot_stock_distribution
    from backend.services.fulfillment_service import SYNCABLE_FF_SERVICES

    snapshot_date = (utcnow() + _MSK_OFFSET).date()

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(IntegrationKey.project_id)
            .where(
                IntegrationKey.service.in_(SYNCABLE_FF_SERVICES),
                IntegrationKey.warehouse_id.isnot(None),
                IntegrationKey.is_deleted == False,  # noqa: E712
                IntegrationKey.project_id.isnot(None),
            )
            .distinct()
        )
        project_ids = [pid for (pid,) in rows.all() if pid]

    if not project_ids:
        logger.info("Stock distribution snapshot: no FF projects, skipping")
        return

    ok = 0
    errors = 0
    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                written = await snapshot_stock_distribution(db, project_id, snapshot_date)
                await db.commit()
            logger.info(
                "Stock distribution snapshot: project %d — %d cells @ %s",
                project_id,
                written,
                snapshot_date,
            )
            ok += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "Stock distribution snapshot: project %d failed — %s",
                project_id,
                str(e),
                exc_info=True,
            )
            errors += 1

    logger.info("Stock distribution snapshot: done — %d ok, %d errors", ok, errors)
