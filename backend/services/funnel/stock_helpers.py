"""Shared stock loading helpers for funnel services.

Extracted from anomalies.py to be reused by capital.py and other modules.
"""

import logging
from datetime import date

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Warehouse, WarehouseStock
from backend.models.cost import Nomenclature
from backend.models.integrations import WbStockSnapshot

logger = logging.getLogger("dds.funnel.stock_helpers")


async def load_rf_stocks(db: AsyncSession, project_id: int) -> dict[int, int]:
    """Load non-WB warehouse stocks grouped by nm_id (article_wb).

    Returns {nm_id: total_quantity} for all RF/external warehouses.
    """
    q = (
        select(
            Nomenclature.article_wb,
            func.sum(WarehouseStock.quantity).label("qty"),
        )
        .join(Nomenclature, Nomenclature.id == WarehouseStock.nomenclature_id)
        .join(Warehouse, Warehouse.id == WarehouseStock.warehouse_id)
        .where(
            Warehouse.project_id == project_id,
            Warehouse.is_deleted == False,  # noqa: E712
            Warehouse.warehouse_type != "wb",
            WarehouseStock.project_id == project_id,
            Nomenclature.article_wb.isnot(None),
        )
        .group_by(Nomenclature.article_wb)
    )
    result = await db.execute(q)
    return {row.article_wb: int(row.qty or 0) for row in result}


async def load_wb_stock_history(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
) -> dict[date, dict[int, int]]:
    """Load WB stock snapshots grouped by date.

    Returns {date: {nm_id: total_quantity}}.
    """
    q = (
        select(
            cast(WbStockSnapshot.synced_at, Date).label("snap_date"),
            WbStockSnapshot.nm_id,
            func.sum(WbStockSnapshot.quantity).label("qty"),
        )
        .where(
            WbStockSnapshot.project_id == project_id,
            cast(WbStockSnapshot.synced_at, Date) >= date_from,
            cast(WbStockSnapshot.synced_at, Date) <= date_to,
        )
        .group_by(
            cast(WbStockSnapshot.synced_at, Date),
            WbStockSnapshot.nm_id,
        )
    )
    result = await db.execute(q)

    history: dict[date, dict[int, int]] = {}
    for row in result:
        d = row.snap_date
        if d not in history:
            history[d] = {}
        history[d][row.nm_id] = int(row.qty or 0)

    return history
