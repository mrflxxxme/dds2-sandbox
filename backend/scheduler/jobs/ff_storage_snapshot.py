# ruff: noqa: RUF001, RUF002, RUF003
"""
Scheduler job: ежедневный снапшот хранения ФФ (ff_storage_daily) по всем
проектам × активным FULFILLMENT-складам. Штуки/короба/паллеты фиксируются на
момент среза; повторный запуск в тот же день перезаписывает (UPSERT).
Worker-контейнер; раз в день 23:40 MSK.

Важно: перебираем СКЛАДЫ (не интеграции) — ФФ без API-зеркала («апл/хамза»)
считаются по WarehouseStock и не должны выпадать из биллинга.
"""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models.warehouse import Warehouse, WarehouseType
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler")

_MSK_OFFSET = timedelta(hours=3)
_WAREHOUSES_LIMIT = 2000


async def snapshot_all_projects_ff_storage():
    """Снапшот хранения за сегодня (MSK) для всех активных ФФ-складов (idempotent)."""
    from backend.services.ff_billing.storage import compute_storage_snapshot

    snapshot_date = (utcnow() + _MSK_OFFSET).date()

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(Warehouse.project_id, Warehouse.id)
            .where(
                Warehouse.warehouse_type == WarehouseType.FULFILLMENT.value,
                Warehouse.is_active == True,  # noqa: E712
                Warehouse.is_deleted == False,  # noqa: E712
            )
            .order_by(Warehouse.project_id.asc(), Warehouse.id.asc())
            .limit(_WAREHOUSES_LIMIT)
        )
        pairs = [(pid, wid) for pid, wid in rows.all()]

    if not pairs:
        logger.info("FF storage snapshot: no active FULFILLMENT warehouses, skipping")
        return

    ok = 0
    errors = 0
    for project_id, warehouse_id in pairs:
        try:
            async with AsyncSessionLocal() as db:
                await compute_storage_snapshot(db, project_id, warehouse_id, snapshot_date)
                await db.commit()
            ok += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "FF storage snapshot: project %d wh %d failed — %s",
                project_id,
                warehouse_id,
                str(e),
                exc_info=True,
            )
            errors += 1

    if ok:
        # Списки хранения кэшированы (@cached ff_billing:storage) — сбросить после среза.
        from backend.cache import invalidate_cache

        await invalidate_cache("ff_billing:storage")

    logger.info(
        "FF storage snapshot: done @ %s — %d ok, %d errors", snapshot_date, ok, errors
    )
