# ruff: noqa: RUF001, RUF002, RUF003
"""
Scheduler job: ежедневный снимок расхождения остатков «наш склад vs ФФ-зеркало»
по проектам с активной ФФ-интеграцией. Копит динамику вперёд (см. модуль
`backend.services.assembly.stock_mismatch_history`). Worker-контейнер, раз в
день (MSK), ПОСЛЕ суточного синка `FulfillmentStock`/`WarehouseStock`.
"""

import asyncio
import logging

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import IntegrationKey

logger = logging.getLogger("dds.scheduler")


async def snapshot_all_projects_stock_mismatch() -> None:
    """Срез расхождения за сегодня (MSK) для всех ФФ-проектов (idempotent)."""
    from backend.services.assembly.stock_mismatch_history import (
        msk_today,
        purge_old,
        snapshot_project,
    )
    from backend.services.fulfillment_service import SYNCABLE_FF_SERVICES

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(IntegrationKey.project_id)
            .where(
                IntegrationKey.service.in_(SYNCABLE_FF_SERVICES),
                IntegrationKey.warehouse_id.isnot(None),
                IntegrationKey.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
                IntegrationKey.project_id.isnot(None),
            )
            .distinct()
        )
        project_ids = [pid for (pid,) in rows.all() if pid]

    if not project_ids:
        logger.info("Stock mismatch snapshot: no FF projects, skipping")
        return

    snapshot_date = msk_today()
    ok = 0
    errors = 0
    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                written = await snapshot_project(db, project_id, day=snapshot_date)
            logger.info(
                "Stock mismatch snapshot: project %d — %d cells @ %s", project_id, written, snapshot_date
            )
            ok += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "Stock mismatch snapshot: project %d failed — %s", project_id, str(e), exc_info=True
            )
            errors += 1

    try:
        async with AsyncSessionLocal() as db:
            purged = await purge_old(db)
        if purged:
            logger.info("Stock mismatch snapshot: purged %d stale row(s)", purged)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Stock mismatch snapshot: retention purge failed", exc_info=True)

    logger.info("Stock mismatch snapshot: done — %d ok, %d errors", ok, errors)
