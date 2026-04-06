"""
Service: wb_cancel_sync — download WB orders and aggregate cancel stats per (nm_id, date).

Fetches /api/v1/supplier/orders, counts total and cancelled orders,
and upserts into wb_order_cancel_daily for accurate buyout_pct in BDR rates.
"""

import logging
from collections import defaultdict
from datetime import date

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.wb_order_cancel import WbOrderCancelDaily
from backend.services.integrations_service import _get_wb_key
from backend.utils.time import utcnow

logger = logging.getLogger("dds.wb_cancel_sync")


async def sync_cancel_stats(
    db: AsyncSession,
    project_id: int,
    date_from: date,
) -> dict:
    """Fetch WB orders and upsert cancel stats per (nm_id, date).

    Args:
        db: async session
        project_id: project to sync
        date_from: fetch orders starting from this date (flag=0 → all since date)

    Returns:
        {status, days_synced, total_orders, total_cancelled}
    """
    from backend.integrations.wb_api import WBApiClient

    key_row, api_key = await _get_wb_key(db, project_id)

    client = WBApiClient(api_key, project_id=project_id)
    orders = await client.get_orders(date_from=date_from, flag=0)

    if not orders:
        logger.info("Cancel sync: project=%d — no orders from %s", project_id, date_from)
        return {"status": "empty", "days_synced": 0, "total_orders": 0, "total_cancelled": 0}

    # Aggregate by (nm_id, order_date) → {total, cancelled}
    stats: dict[tuple[int, date], dict[str, int]] = defaultdict(lambda: {"total": 0, "cancelled": 0})

    for order in orders:
        nm_id = order.get("nmId")
        date_str = order.get("date", "")
        is_cancel = order.get("isCancel", False)

        if not nm_id or not date_str:
            continue

        # WB returns ISO datetime like "2026-03-15T10:30:00"
        try:
            order_date = date.fromisoformat(date_str[:10])
        except (ValueError, TypeError):
            logger.debug("Cancel sync: skipping order with bad date %r", date_str)
            continue

        key = (nm_id, order_date)
        stats[key]["total"] += 1
        if is_cancel:
            stats[key]["cancelled"] += 1

    # Upsert in batches
    now = utcnow()
    rows_to_upsert = [
        {
            "project_id": project_id,
            "nm_id": nm_id,
            "dt": dt,
            "total_orders": s["total"],
            "cancelled_orders": s["cancelled"],
            "synced_at": now,
        }
        for (nm_id, dt), s in stats.items()
    ]

    if rows_to_upsert:
        batch_size = 500
        for i in range(0, len(rows_to_upsert), batch_size):
            batch = rows_to_upsert[i : i + batch_size]
            stmt = pg_insert(WbOrderCancelDaily).values(batch)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_wb_order_cancel_daily",
                set_={
                    "total_orders": stmt.excluded.total_orders,
                    "cancelled_orders": stmt.excluded.cancelled_orders,
                    "synced_at": stmt.excluded.synced_at,
                },
            )
            await db.execute(stmt)
        await db.commit()

    total_orders = sum(s["total"] for s in stats.values())
    total_cancelled = sum(s["cancelled"] for s in stats.values())

    logger.info(
        "Cancel sync: project=%d — %d days, %d orders, %d cancelled",
        project_id,
        len(stats),
        total_orders,
        total_cancelled,
    )

    return {
        "status": "ok",
        "days_synced": len(stats),
        "total_orders": total_orders,
        "total_cancelled": total_cancelled,
    }
