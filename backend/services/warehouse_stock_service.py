"""
Warehouse Stock Service — sync, view, and compute_need helper.

Handles WbWarehouseStock sync from WB API and stock viewing endpoints.
Restocking need calculation lives in warehouse_need_service.py.
"""

import logging
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.models import WbStockSnapshot, WbWarehouseStock
from backend.utils.time import utcnow

logger = logging.getLogger("dds.stock_analytics")


def __getattr__(name: str) -> Any:
    """Backward-compatible lazy re-export of get_warehouse_need for callers that import from here."""
    if name == "get_warehouse_need":
        from backend.services.warehouse_need_service import get_warehouse_need

        return get_warehouse_need
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def compute_need(stock_qty: int, avg_daily: float, need_days: int) -> int:
    """How many items need to be shipped to cover need_days of demand."""
    needed = avg_daily * need_days
    deficit = needed - stock_qty
    return max(0, int(deficit + 0.5))


async def sync_warehouse_stocks(
    db: AsyncSession,
    project_id: int,
    items: list[dict],
) -> int:
    """Upsert warehouse stock data from WB API response. Returns count of upserted rows."""
    if not items:
        return 0

    await db.execute(delete(WbWarehouseStock).where(WbWarehouseStock.project_id == project_id))

    # WB API returns rows per barcode/size — multiple rows can share the same
    # (nm_id, warehouse_name) key. Aggregate them before INSERT to avoid
    # CardinalityViolationError on ON CONFLICT (one row affected twice).
    aggregated: dict[tuple[int, str], dict[str, Any]] = {}
    for item in items:
        nm_id = item.get("nmId")
        wh_name = item.get("warehouseName", "")
        if not nm_id or not wh_name:
            continue
        key = (nm_id, wh_name)
        existing = aggregated.get(key)
        if existing is None:
            aggregated[key] = {
                "project_id": project_id,
                "nm_id": nm_id,
                "vendor_code": item.get("supplierArticle", "") or "",
                "subject": item.get("subject", "") or "",
                "brand": item.get("brand", "") or "",
                "warehouse_name": wh_name,
                "quantity": int(item.get("quantity", 0) or 0),
                "quantity_full": int(item.get("quantityFull", 0) or 0),
                "in_way_to_client": int(item.get("inWayToClient", 0) or 0),
                "in_way_from_client": int(item.get("inWayFromClient", 0) or 0),
            }
        else:
            existing["quantity"] += int(item.get("quantity", 0) or 0)
            existing["quantity_full"] += int(item.get("quantityFull", 0) or 0)
            existing["in_way_to_client"] += int(item.get("inWayToClient", 0) or 0)
            existing["in_way_from_client"] += int(item.get("inWayFromClient", 0) or 0)
            if not existing["vendor_code"]:
                existing["vendor_code"] = item.get("supplierArticle", "") or ""
            if not existing["subject"]:
                existing["subject"] = item.get("subject", "") or ""
            if not existing["brand"]:
                existing["brand"] = item.get("brand", "") or ""

    count = 0
    batch: list[dict[str, Any]] = []
    for row in aggregated.values():
        batch.append(row)

        if len(batch) >= 500:
            stmt = pg_insert(WbWarehouseStock).values(batch)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_wh_stock_nm_wh",
                set_={
                    "vendor_code": stmt.excluded.vendor_code,
                    "subject": stmt.excluded.subject,
                    "brand": stmt.excluded.brand,
                    "quantity": stmt.excluded.quantity,
                    "quantity_full": stmt.excluded.quantity_full,
                    "in_way_to_client": stmt.excluded.in_way_to_client,
                    "in_way_from_client": stmt.excluded.in_way_from_client,
                },
            )
            await db.execute(stmt)
            count += len(batch)
            batch = []

    if batch:
        stmt = pg_insert(WbWarehouseStock).values(batch)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_wh_stock_nm_wh",
            set_={
                "vendor_code": stmt.excluded.vendor_code,
                "subject": stmt.excluded.subject,
                "brand": stmt.excluded.brand,
                "quantity": stmt.excluded.quantity,
                "quantity_full": stmt.excluded.quantity_full,
                "in_way_to_client": stmt.excluded.in_way_to_client,
                "in_way_from_client": stmt.excluded.in_way_from_client,
            },
        )
        await db.execute(stmt)
        count += len(batch)

    # Save snapshot for history
    now = utcnow()
    snap_batch = []
    for item in items:
        nm_id = item.get("nmId")
        wh_name = item.get("warehouseName", "")
        if not nm_id or not wh_name:
            continue
        snap_batch.append(
            {
                "project_id": project_id,
                "synced_at": now,
                "warehouse_name": wh_name,
                "nm_id": nm_id,
                "vendor_code": item.get("supplierArticle", ""),
                "barcode": item.get("barcode", ""),
                "quantity": item.get("quantity", 0),
                "in_way_to_client": item.get("inWayToClient", 0),
                "in_way_from_client": item.get("inWayFromClient", 0),
            }
        )
        if len(snap_batch) >= 500:
            await db.execute(pg_insert(WbStockSnapshot).values(snap_batch))
            snap_batch = []
    if snap_batch:
        await db.execute(pg_insert(WbStockSnapshot).values(snap_batch))

    await db.commit()

    # Invalidate stock caches after sync
    await invalidate_cache(f"reports:stock_warehouses:project_id={project_id}")
    await invalidate_cache(f"reports:stock_warehouses_articles:project_id={project_id}")
    await invalidate_cache(f"reports:stock_history:project_id={project_id}")

    logger.info(f"Synced {count} warehouse stock rows for project {project_id} (snapshot saved, cache invalidated)")
    return count


@cached(prefix="reports:stock_warehouses", ttl=3600)
async def get_warehouse_stocks(
    db: AsyncSession,
    project_id: int,
) -> dict:
    """Get warehouse stocks grouped by warehouse with yesterday comparison."""
    # Current stocks grouped by warehouse
    result = await db.execute(
        select(
            WbWarehouseStock.warehouse_name,
            func.sum(WbWarehouseStock.quantity).label("total_qty"),
            func.sum(WbWarehouseStock.in_way_to_client).label("total_in_way_to"),
            func.sum(WbWarehouseStock.in_way_from_client).label("total_in_way_from"),
            func.count(func.distinct(WbWarehouseStock.nm_id)).label("articles_count"),
        )
        .where(
            WbWarehouseStock.project_id == project_id,
        )
        .group_by(WbWarehouseStock.warehouse_name)
        .order_by(func.sum(WbWarehouseStock.quantity).desc())
    )

    warehouses = []
    total_qty = 0
    total_in_way_to = 0
    total_in_way_from = 0
    for r in result:
        qty = int(r.total_qty or 0)
        iwt = int(r.total_in_way_to or 0)
        iwf = int(r.total_in_way_from or 0)
        total_qty += qty
        total_in_way_to += iwt
        total_in_way_from += iwf
        warehouses.append(
            {
                "name": r.warehouse_name,
                "total_qty": qty,
                "in_way_to_client": iwt,
                "in_way_from_client": iwf,
                "articles_count": int(r.articles_count or 0),
            }
        )

    # Yesterday snapshot for comparison
    yesterday_map = await _get_yesterday_warehouse_totals(db, project_id)
    for wh in warehouses:
        prev = yesterday_map.get(wh["name"], 0)
        wh["yesterday_qty"] = prev
        wh["change"] = wh["total_qty"] - prev if prev > 0 else 0

    yesterday_total = sum(yesterday_map.values())

    # Last sync time
    last_updated = await db.execute(
        select(func.max(WbWarehouseStock.updated_at)).where(
            WbWarehouseStock.project_id == project_id,
        )
    )
    last_sync = last_updated.scalar()

    return {
        "warehouses": warehouses,
        "total_warehouses": len(warehouses),
        "total_qty": total_qty,
        "total_in_way_to_client": total_in_way_to,
        "total_in_way_from_client": total_in_way_from,
        "yesterday_total_qty": yesterday_total,
        "change_total": total_qty - yesterday_total if yesterday_total > 0 else 0,
        "last_synced_at": last_sync.isoformat() if last_sync else None,
    }


async def _get_yesterday_warehouse_totals(
    db: AsyncSession,
    project_id: int,
) -> dict[str, int]:
    """Get warehouse totals from yesterday's closest snapshot."""
    from datetime import timedelta

    now = utcnow()
    yesterday_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_end = yesterday_start + timedelta(days=1)

    # Find the latest snapshot synced_at within yesterday
    latest_yesterday = await db.execute(
        select(func.max(WbStockSnapshot.synced_at)).where(
            WbStockSnapshot.project_id == project_id,
            WbStockSnapshot.synced_at >= yesterday_start,
            WbStockSnapshot.synced_at < yesterday_end,
        )
    )
    snap_time = latest_yesterday.scalar()
    if not snap_time:
        return {}

    # Get totals from that snapshot grouped by warehouse
    result = await db.execute(
        select(
            WbStockSnapshot.warehouse_name,
            func.sum(WbStockSnapshot.quantity).label("total_qty"),
        )
        .where(
            WbStockSnapshot.project_id == project_id,
            WbStockSnapshot.synced_at == snap_time,
        )
        .group_by(WbStockSnapshot.warehouse_name)
    )
    return {r.warehouse_name: int(r.total_qty or 0) for r in result}


@cached(prefix="reports:stock_warehouses_articles", ttl=3600)
async def get_warehouse_stocks_by_article(
    db: AsyncSession,
    project_id: int,
    search: str | None = None,
) -> dict:
    """Get stocks grouped by article (nm_id), with per-warehouse breakdown."""
    # All current stock rows
    query = select(
        WbWarehouseStock.nm_id,
        WbWarehouseStock.vendor_code,
        WbWarehouseStock.subject,
        WbWarehouseStock.brand,
        WbWarehouseStock.warehouse_name,
        WbWarehouseStock.quantity,
        WbWarehouseStock.in_way_to_client,
        WbWarehouseStock.in_way_from_client,
    ).where(
        WbWarehouseStock.project_id == project_id,
    )

    if search:
        from sqlalchemy import String, cast, or_

        safe = search.replace("%", r"\%").replace("_", r"\_")
        query = query.where(
            or_(
                WbWarehouseStock.vendor_code.ilike(f"%{safe}%", escape="\\"),
                cast(WbWarehouseStock.nm_id, String).ilike(f"%{safe}%", escape="\\"),
            )
        )

    result = await db.execute(query.order_by(WbWarehouseStock.nm_id))

    articles_map: dict[int, dict] = {}
    for r in result:
        nm_id = r.nm_id
        if nm_id not in articles_map:
            articles_map[nm_id] = {
                "nm_id": nm_id,
                "vendor_code": r.vendor_code or "",
                "subject": r.subject or "",
                "brand": r.brand or "",
                "total_qty": 0,
                "in_way_to_client": 0,
                "in_way_from_client": 0,
                "warehouses": [],
            }
        art = articles_map[nm_id]
        qty = int(r.quantity or 0)
        iwt = int(r.in_way_to_client or 0)
        iwf = int(r.in_way_from_client or 0)
        art["total_qty"] += qty
        art["in_way_to_client"] += iwt
        art["in_way_from_client"] += iwf
        art["warehouses"].append(
            {
                "name": r.warehouse_name,
                "quantity": qty,
                "in_way_to_client": iwt,
                "in_way_from_client": iwf,
            }
        )

    articles = sorted(articles_map.values(), key=lambda a: a["total_qty"], reverse=True)

    return {
        "articles": articles,
        "total_articles": len(articles),
    }


@cached(prefix="reports:stock_history", ttl=3600)
async def get_stock_history(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
    warehouse: str | None = None,
) -> dict:
    """Get daily stock history from snapshots for chart and table."""
    from datetime import datetime

    from sqlalchemy import Date, cast

    dt_from = datetime.strptime(date_from, "%Y-%m-%d")
    dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

    # Get the latest snapshot per day
    # Subquery: for each day, find max synced_at
    day_col = cast(WbStockSnapshot.synced_at, Date).label("snap_date")

    base_where = [
        WbStockSnapshot.project_id == project_id,
        WbStockSnapshot.synced_at >= dt_from,
        WbStockSnapshot.synced_at <= dt_to,
    ]
    if warehouse:
        base_where.append(WbStockSnapshot.warehouse_name == warehouse)

    # Get latest synced_at per day
    latest_per_day = (
        select(
            day_col,
            func.max(WbStockSnapshot.synced_at).label("max_synced"),
        )
        .where(*base_where)
        .group_by(day_col)
        .subquery()
    )

    # For each day's latest snapshot, sum quantities
    from sqlalchemy import and_

    join_cond = and_(
        WbStockSnapshot.synced_at == latest_per_day.c.max_synced,
        WbStockSnapshot.project_id == project_id,
        *([WbStockSnapshot.warehouse_name == warehouse] if warehouse else []),
    )

    result = await db.execute(
        select(
            latest_per_day.c.snap_date,
            func.sum(WbStockSnapshot.quantity).label("total_qty"),
            func.sum(WbStockSnapshot.in_way_to_client).label("total_in_way_to"),
            func.sum(WbStockSnapshot.in_way_from_client).label("total_in_way_from"),
            func.count(func.distinct(WbStockSnapshot.nm_id)).label("articles_count"),
        )
        .join(WbStockSnapshot, join_cond)
        .group_by(latest_per_day.c.snap_date)
        .order_by(latest_per_day.c.snap_date)
    )

    days = []
    for r in result:
        qty = int(r.total_qty or 0)
        iwt = int(r.total_in_way_to or 0)
        iwf = int(r.total_in_way_from or 0)
        days.append(
            {
                "date": str(r.snap_date),
                "total_qty": qty,
                "in_way_to_client": iwt,
                "in_way_from_client": iwf,
                "total": qty + iwt + iwf,
                "articles_count": int(r.articles_count or 0),
            }
        )

    # Get list of warehouses for filter
    wh_result = await db.execute(
        select(func.distinct(WbStockSnapshot.warehouse_name))
        .where(
            WbStockSnapshot.project_id == project_id,
        )
        .order_by(WbStockSnapshot.warehouse_name)
    )
    warehouse_names = [r[0] for r in wh_result]

    return {
        "days": days,
        "warehouses": warehouse_names,
    }
