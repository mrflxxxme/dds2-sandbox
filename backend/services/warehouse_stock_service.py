"""
Warehouse Stock Service — sync, view, and restocking need calculation.

Handles WbWarehouseStock sync from WB API and need computation per warehouse.
"""

import logging
from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.models import WbFunnelDaily, WbStockSnapshot, WbWarehouseStock
from backend.utils.time import utcnow

logger = logging.getLogger("dds.stock_analytics")


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

    count = 0
    batch = []
    for item in items:
        nm_id = item.get("nmId")
        wh_name = item.get("warehouseName", "")
        if not nm_id or not wh_name:
            continue

        batch.append(
            {
                "project_id": project_id,
                "nm_id": nm_id,
                "vendor_code": item.get("supplierArticle", ""),
                "subject": item.get("subject", ""),
                "brand": item.get("brand", ""),
                "warehouse_name": wh_name,
                "quantity": item.get("quantity", 0),
                "quantity_full": item.get("quantityFull", 0),
                "in_way_to_client": item.get("inWayToClient", 0),
                "in_way_from_client": item.get("inWayFromClient", 0),
            }
        )

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


async def get_warehouse_need(
    db: AsyncSession,
    project_id: int,
    supply_days: int = 14,
    analysis_days: int = 14,
    mode: str = "actual",
) -> dict:
    """Compute restocking need per warehouse per article.

    Args:
        supply_days: target stock level in days
        analysis_days: lookback period for avg daily orders
        mode="actual": uses WB supplier/orders warehouseName
        mode="hypothetical": maps order regionName → nearest open warehouse via geo
    """
    from backend.models.assembly import (
        AssemblyRequest,
        AssemblyRequestItem,
        AssemblyStatus,
    )
    from backend.models.cost import Nomenclature
    from backend.models.warehouse import (
        Warehouse,
        WarehouseDeliveryTime,
        WarehouseStock,
        WarehouseType,
    )
    from backend.services.funnel.wb_api_client import (
        fetch_supplier_orders,
        get_wb_key,
    )
    from backend.services.warehouse_geo import (
        WAREHOUSE_COORDS,
        find_nearest_warehouse,
        find_nearest_warehouse_by_city,
        get_country_filtered_warehouses,
    )

    today = date.today()
    trend_start = today - timedelta(days=analysis_days)

    # 1. Fetch orders from WB API
    api_key = await get_wb_key(db, project_id, "wb")
    wh_orders_map: dict[tuple[str, int], int] = {}
    vendor_map: dict[int, str] = {}
    brand_map: dict[int, str] = {}
    subject_map: dict[int, str] = {}
    actual_days = analysis_days

    open_warehouses: list[str] = list(WAREHOUSE_COORDS.keys())
    city_map: dict[str, str] = {}
    okrug_map: dict[str, str] = {}
    if mode == "hypothetical":
        from backend.models.order_city import OrderCityMap

        city_rows = await db.execute(
            select(OrderCityMap.srid, OrderCityMap.city, OrderCityMap.okrug).where(
                OrderCityMap.project_id == project_id
            )
        )
        for row in city_rows.fetchall():
            city_map[row.srid] = row.city
            if row.okrug:
                okrug_map[row.srid] = row.okrug

        from backend.services.settings_service import get_excluded_warehouses

        excluded = await get_excluded_warehouses(db, project_id)
        if excluded:
            open_warehouses = [w for w in open_warehouses if w not in excluded]
            logger.info("Excluded warehouses for project %s: %s", project_id, excluded)

    if api_key:
        orders = await fetch_supplier_orders(api_key, trend_start.isoformat())
        for order in orders:
            nm_id = order.get("nmId")
            qty = order.get("quantity", 1)
            if not nm_id:
                continue

            if mode == "hypothetical":
                srid = str(order.get("srid", ""))
                wh_name = None
                okrug = okrug_map.get(srid)
                country_wh = get_country_filtered_warehouses(okrug, open_warehouses)
                city = city_map.get(srid)
                if city:
                    wh_name = find_nearest_warehouse_by_city(city, country_wh)
                if not wh_name:
                    region = order.get("regionName", "")
                    wh_name = find_nearest_warehouse(region, country_wh)
                if not wh_name:
                    wh_name = order.get("warehouseName", "")
            else:
                wh_name = order.get("warehouseName", "")

            if not wh_name:
                continue

            key = (wh_name, nm_id)
            wh_orders_map[key] = wh_orders_map.get(key, 0) + qty
            if nm_id not in vendor_map:
                vendor_map[nm_id] = order.get("supplierArticle", f"#{nm_id}")
            if nm_id not in brand_map and order.get("brand"):
                brand_map[nm_id] = order["brand"]
            if nm_id not in subject_map and order.get("subject"):
                subject_map[nm_id] = order["subject"]

    # Also get vendor codes from WbFunnelDaily for completeness
    funnel_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            WbFunnelDaily.vendor_code,
            WbFunnelDaily.brand,
            WbFunnelDaily.subject,
        )
        .where(
            WbFunnelDaily.project_id == project_id,
        )
        .group_by(WbFunnelDaily.nm_id, WbFunnelDaily.vendor_code, WbFunnelDaily.brand, WbFunnelDaily.subject)
    )
    for r in funnel_result:
        if r.nm_id not in vendor_map:
            vendor_map[r.nm_id] = r.vendor_code or f"#{r.nm_id}"
        if r.nm_id not in brand_map and r.brand:
            brand_map[r.nm_id] = r.brand
        if r.nm_id not in subject_map and r.subject:
            subject_map[r.nm_id] = r.subject

    # 2. Get warehouse stocks (WB)
    wh_result = await db.execute(
        select(
            WbWarehouseStock.warehouse_name,
            WbWarehouseStock.nm_id,
            WbWarehouseStock.vendor_code,
            WbWarehouseStock.quantity,
        )
        .where(
            WbWarehouseStock.project_id == project_id,
        )
        .order_by(WbWarehouseStock.warehouse_name)
    )

    stock_lookup: dict[tuple[str, int], int] = {}
    all_nm_ids: set[int] = set()

    for r in wh_result:
        stock_lookup[(r.warehouse_name, r.nm_id)] = int(r.quantity or 0)
        all_nm_ids.add(r.nm_id)
        if r.nm_id not in vendor_map:
            vendor_map[r.nm_id] = r.vendor_code or f"#{r.nm_id}"

    for _wh, nm in wh_orders_map:
        all_nm_ids.add(nm)

    # 3. Determine which warehouses to show
    if mode == "hypothetical":
        wh_names_to_show = set()
        for wh, _ in wh_orders_map:
            wh_names_to_show.add(wh)
        for wh, _ in stock_lookup:
            wh_names_to_show.add(wh)
    else:
        wh_names_to_show = set(wh for (wh, _) in stock_lookup)

    # 4. Build warehouse → articles need map
    wh_data: dict[str, dict] = {}

    for wh_name in wh_names_to_show:
        if wh_name not in wh_data:
            wh_data[wh_name] = {"name": wh_name, "total_need": 0, "articles": {}}

        for nm_id in all_nm_ids:
            stock = stock_lookup.get((wh_name, nm_id), 0)
            total_orders_at_wh = wh_orders_map.get((wh_name, nm_id), 0)

            if total_orders_at_wh == 0 and stock == 0:
                continue

            avg_d = round(total_orders_at_wh / max(actual_days, 1), 2)
            need = compute_need(stock, avg_d, supply_days)

            wh_data[wh_name]["articles"][nm_id] = {
                "nm_id": nm_id,
                "vendor_code": vendor_map.get(nm_id, f"#{nm_id}"),
                "stock": stock,
                "avg_daily": avg_d,
                "need": need,
            }
            wh_data[wh_name]["total_need"] += need

    warehouses = sorted(wh_data.values(), key=lambda w: w["total_need"], reverse=True)

    # ── NEW: RF (fulfillment) warehouses ────────────────────────────────────
    rf_warehouses_result = await db.execute(
        select(Warehouse)
        .where(
            Warehouse.project_id == project_id,
            Warehouse.warehouse_type == WarehouseType.FULFILLMENT,
            Warehouse.is_deleted.is_(False),
            Warehouse.is_active.is_(True),
        )
        .order_by(Warehouse.sort_order)
    )
    rf_warehouses = rf_warehouses_result.scalars().all()
    rf_wh_ids = [w.id for w in rf_warehouses]

    # ── NEW: RF stock per warehouse per nm_id ───────────────────────────────
    rf_stock_map: dict[int, dict[int, int]] = {}  # nm_id -> {warehouse_id: qty}
    if rf_wh_ids:
        rf_stock_result = await db.execute(
            select(
                WarehouseStock.warehouse_id,
                Nomenclature.article_wb,
                func.sum(WarehouseStock.quantity).label("qty"),
            )
            .join(Nomenclature, Nomenclature.id == WarehouseStock.nomenclature_id)
            .where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id.in_(rf_wh_ids),
                Nomenclature.article_wb.isnot(None),
            )
            .group_by(WarehouseStock.warehouse_id, Nomenclature.article_wb)
        )
        for row in rf_stock_result:
            nm = row.article_wb
            if nm not in rf_stock_map:
                rf_stock_map[nm] = {}
            rf_stock_map[nm][row.warehouse_id] = int(row.qty or 0)

    # ── NEW: In-assembly (reserved) quantities per nm_id / warehouse ────────
    active_statuses = [
        AssemblyStatus.PENDING,
        AssemblyStatus.IN_PROGRESS,
        AssemblyStatus.READY,
        AssemblyStatus.VEHICLE_ASSIGNED,
    ]
    in_assembly_map: dict[int, int] = {}  # nm_id -> total
    in_assembly_per_wh: dict[int, dict[int, int]] = {}  # nm_id -> {wh_id: qty}

    assembly_result = await db.execute(
        select(
            Nomenclature.article_wb,
            AssemblyRequest.warehouse_id,
            func.sum(AssemblyRequestItem.quantity).label("qty"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .join(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_(active_statuses),
            Nomenclature.article_wb.isnot(None),
        )
        .group_by(Nomenclature.article_wb, AssemblyRequest.warehouse_id)
    )
    for row in assembly_result:
        nm = row.article_wb
        qty = int(row.qty or 0)
        in_assembly_map[nm] = in_assembly_map.get(nm, 0) + qty
        if nm not in in_assembly_per_wh:
            in_assembly_per_wh[nm] = {}
        in_assembly_per_wh[nm][row.warehouse_id] = qty

    # ── NEW: In-transit (SHIPPED) quantities per nm_id ──────────────────────
    in_transit_map: dict[int, dict] = {}  # nm_id -> {"qty": N, "date": date|None}

    shipped_result = await db.execute(
        select(
            Nomenclature.article_wb,
            func.sum(AssemblyRequestItem.quantity).label("qty"),
            func.min(AssemblyRequest.delivery_date).label("delivery_date"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .join(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status == AssemblyStatus.SHIPPED,
            Nomenclature.article_wb.isnot(None),
        )
        .group_by(Nomenclature.article_wb)
    )
    for row in shipped_result:
        nm = row.article_wb
        in_transit_map[nm] = {
            "qty": int(row.qty or 0),
            "date": row.delivery_date.isoformat() if row.delivery_date else None,
        }

    # ── NEW: Реализация за analysis_days из wb_finance_rows (продажи - возвраты) ─
    from sqlalchemy import case

    from backend.models.wb_finance import WbFinanceRow

    date_30d_ago = today - timedelta(days=analysis_days)
    revenue_result = await db.execute(
        select(
            WbFinanceRow.nm_id,
            func.sum(
                case(
                    (WbFinanceRow.doc_type_name == "Продажа", WbFinanceRow.retail_price_withdisc_rub),
                    (WbFinanceRow.doc_type_name == "Возврат", -WbFinanceRow.retail_price_withdisc_rub),
                    else_=0,
                )
            ).label("realization"),
        )
        .where(
            WbFinanceRow.project_id == project_id,
            WbFinanceRow.rr_dt >= date_30d_ago,
            WbFinanceRow.doc_type_name.in_(["Продажа", "Возврат"]),
            WbFinanceRow.nm_id > 0,
        )
        .group_by(WbFinanceRow.nm_id)
    )
    revenue_map: dict[int, float] = {r.nm_id: float(r.realization or 0) for r in revenue_result}

    # ── NEW: WB stock totals per nm_id ──────────────────────────────────────
    wb_stock_total: dict[int, int] = {}
    for (_wh, nm), qty in stock_lookup.items():
        wb_stock_total[nm] = wb_stock_total.get(nm, 0) + qty

    # ── NEW: Average delivery time ──────────────────────────────────────────
    avg_delivery_days = 0
    dt_result = await db.execute(
        select(
            WarehouseDeliveryTime.delivery_days,
            Warehouse.assembly_days,
            Warehouse.wb_acceptance_days,
        )
        .join(Warehouse, Warehouse.id == WarehouseDeliveryTime.warehouse_id)
        .where(
            WarehouseDeliveryTime.project_id == project_id,
        )
    )
    dt_rows = dt_result.fetchall()
    if dt_rows:
        total_days_sum = 0
        for row in dt_rows:
            total_days_sum += (row.assembly_days or 0) + (row.delivery_days or 0) + (row.wb_acceptance_days or 0)
        avg_delivery_days = round(total_days_sum / len(dt_rows))

    # ── NEW: Build enriched article list with need totals ───────────────────
    # Pre-compute total_need per nm_id across all WB warehouses
    article_need_map: dict[int, int] = {}
    for wh_info in wh_data.values():
        for nm_id, art_info in wh_info["articles"].items():
            article_need_map[nm_id] = article_need_map.get(nm_id, 0) + art_info["need"]

    # Summary accumulators
    sum_total_need = 0
    sum_total_can_send = 0
    sum_total_deficit = 0
    deficit_count = 0
    can_send_count = 0
    no_wb_count = 0

    enriched_articles = []
    for nm_id in all_nm_ids:
        total_need = article_need_map.get(nm_id, 0)

        # RF stocks per warehouse
        rf_stocks_for_nm: dict[int, dict] = {}
        total_rf_stock = 0
        for wh in rf_warehouses:
            stock_qty = rf_stock_map.get(nm_id, {}).get(wh.id, 0)
            in_asm_qty = in_assembly_per_wh.get(nm_id, {}).get(wh.id, 0)
            available = max(0, stock_qty - in_asm_qty)
            rf_stocks_for_nm[wh.id] = {"stock": stock_qty, "available": available}
            total_rf_stock += stock_qty

        in_asm_total = in_assembly_map.get(nm_id, 0)
        total_rf_available = max(0, total_rf_stock - in_asm_total)

        transit_info = in_transit_map.get(nm_id, {"qty": 0, "date": None})
        in_transit_qty = transit_info["qty"]
        in_transit_date = transit_info["date"]

        # can_send = MIN(total_available_rf, total_need) rounded DOWN to nearest 10
        can_send_raw = min(total_rf_available, total_need)
        can_send = max(0, (can_send_raw // 10) * 10)

        # deficit = MAX(0, total_need - in_transit - can_send)
        deficit = max(0, total_need - in_transit_qty - can_send)

        stocks_wb = wb_stock_total.get(nm_id, 0)

        enriched_articles.append(
            {
                "nm_id": nm_id,
                "vendor_code": vendor_map.get(nm_id, f"#{nm_id}"),
                "brand": brand_map.get(nm_id, ""),
                "subject": subject_map.get(nm_id, ""),
                "total_need": total_need,
                "revenue_30d": revenue_map.get(nm_id, 0),
                "rf_stocks": rf_stocks_for_nm,
                "in_assembly": in_asm_total,
                "in_transit": in_transit_qty,
                "in_transit_date": in_transit_date,
                "can_send": can_send,
                "deficit": deficit,
                "stocks_wb": stocks_wb,
            }
        )

        # Summary accumulators
        sum_total_need += total_need
        sum_total_can_send += can_send
        sum_total_deficit += deficit
        if deficit > 0:
            deficit_count += 1
        if can_send > 0:
            can_send_count += 1
        if stocks_wb == 0 and total_rf_stock > 0:
            no_wb_count += 1

    enriched_articles.sort(key=lambda a: a["vendor_code"])

    brands = sorted(set(b for b in brand_map.values() if b))
    subjects = sorted(set(s for s in subject_map.values() if s))

    return {
        "warehouses": warehouses,
        "articles": enriched_articles,
        "brands": brands,
        "subjects": subjects,
        "supply_days": supply_days,
        "analysis_days": analysis_days,
        "mode": mode,
        "total_warehouses": len(warehouses),
        "total_articles": len(enriched_articles),
        # NEW fields
        "rf_warehouses": [{"id": w.id, "name": w.name, "assembly_days": w.assembly_days or 0} for w in rf_warehouses],
        "summary": {
            "total_need": sum_total_need,
            "total_can_send": sum_total_can_send,
            "total_deficit": sum_total_deficit,
            "avg_delivery_days": avg_delivery_days,
            "deficit_count": deficit_count,
            "can_send_count": can_send_count,
            "no_wb_count": no_wb_count,
        },
    }
