"""
Stock Analytics Service — forecast stock depletion based on sales trends.

Uses WbFunnelDaily data:
- stocks_wb: current warehouse stock
- orders_count / orders_sum_rub: daily orders
- vendor_code, subject, brand: product metadata
"""

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select, func, desc, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.models import WbFunnelDaily, WbWarehouseStock

logger = logging.getLogger("dds.stock_analytics")


# ─── Pure computation helpers (testable without DB) ──────────────────────────

def compute_days_left(stocks: int, avg_daily: float) -> int:
    """How many days until stock runs out at current sales rate."""
    if avg_daily <= 0 or stocks <= 0:
        return 0
    return int(stocks / avg_daily)


def compute_trend_pct(current_avg: float, prev_avg: float) -> float:
    """Trend % change between two periods."""
    if prev_avg <= 0:
        return 0.0
    return round((current_avg - prev_avg) / prev_avg * 100, 1)


def classify_traffic_light(days_left: int) -> str:
    """Classify stock status as traffic light color."""
    if days_left < 7:
        return "red"
    elif days_left <= 14:
        return "orange"
    elif days_left <= 29:
        return "yellow"
    else:
        return "green"


def build_traffic_light_counts(articles: list[dict]) -> dict[str, int]:
    """Count articles in each traffic light category."""
    counts = {"red": 0, "orange": 0, "yellow": 0, "green": 0}
    for a in articles:
        color = a.get("traffic_light", "green")
        counts[color] = counts.get(color, 0) + 1
    return counts


# ─── Main service function ───────────────────────────────────────────────────

async def get_stock_analytics(
    db: AsyncSession,
    project_id: int,
    trend_days: int = 7,
    subject_filter: Optional[str] = None,
    brand_filter: Optional[str] = None,
    article_filter: Optional[str] = None,
) -> dict:
    """Build stock analytics report.

    Args:
        trend_days: period (7/14/30) for average daily sales calculation
        subject_filter: filter by product category
        brand_filter: filter by brand
        article_filter: filter by vendor_code (partial match)
    """
    today = date.today()
    date_30d_ago = today - timedelta(days=30)
    date_trend_ago = today - timedelta(days=trend_days)

    # ── 1. Find latest data date ──
    latest_date_result = await db.execute(
        select(func.max(WbFunnelDaily.date)).where(
            WbFunnelDaily.project_id == project_id,
        )
    )
    data_date = latest_date_result.scalar()
    if not data_date:
        return _empty_result()

    # ── 2. Latest stocks per nm_id (last available date) ──
    sub = select(
        WbFunnelDaily.nm_id,
        WbFunnelDaily.vendor_code,
        WbFunnelDaily.subject,
        WbFunnelDaily.brand,
        WbFunnelDaily.stocks_wb,
        func.row_number().over(
            partition_by=WbFunnelDaily.nm_id,
            order_by=desc(WbFunnelDaily.date),
        ).label("rn"),
    ).where(
        WbFunnelDaily.project_id == project_id,
    ).subquery()

    stocks_result = await db.execute(
        select(sub).where(sub.c.rn == 1)
    )
    stocks_map: dict[int, dict] = {}
    for r in stocks_result:
        stocks_map[r.nm_id] = {
            "nm_id": r.nm_id,
            "vendor_code": r.vendor_code or "",
            "subject": r.subject or "",
            "brand": r.brand or "",
            "stocks_wb": int(r.stocks_wb or 0),
        }

    # ── 3. Orders aggregated (last 30 days) ──
    orders_30d_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.sum(WbFunnelDaily.orders_count).label("orders_30d"),
            func.sum(WbFunnelDaily.orders_sum_rub).label("orders_sum_30d"),
        ).where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.date >= date_30d_ago,
            WbFunnelDaily.date <= data_date,
        ).group_by(WbFunnelDaily.nm_id)
    )
    orders_30d_map = {
        r.nm_id: {"orders_30d": int(r.orders_30d or 0), "orders_sum_30d": float(r.orders_sum_30d or 0)}
        for r in orders_30d_result
    }

    # ── 4. Average daily orders for trend period ──
    actual_trend_start = max(date_30d_ago, date_trend_ago)
    trend_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.sum(WbFunnelDaily.orders_count).label("total_orders"),
            func.count(func.distinct(WbFunnelDaily.date)).label("days_count"),
        ).where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.date >= actual_trend_start,
            WbFunnelDaily.date <= data_date,
        ).group_by(WbFunnelDaily.nm_id)
    )
    avg_daily_map: dict[int, float] = {}
    for r in trend_result:
        days = int(r.days_count or 1)
        avg_daily_map[r.nm_id] = round(int(r.total_orders or 0) / max(days, 1), 2)

    # ── 5. Previous period for trend comparison ──
    prev_start = actual_trend_start - timedelta(days=trend_days)
    prev_end = actual_trend_start - timedelta(days=1)
    prev_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.sum(WbFunnelDaily.orders_count).label("total_orders"),
            func.count(func.distinct(WbFunnelDaily.date)).label("days_count"),
        ).where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.date >= prev_start,
            WbFunnelDaily.date <= prev_end,
        ).group_by(WbFunnelDaily.nm_id)
    )
    prev_avg_map: dict[int, float] = {}
    for r in prev_result:
        days = int(r.days_count or 1)
        prev_avg_map[r.nm_id] = round(int(r.total_orders or 0) / max(days, 1), 2)

    # ── 6. Stock forecast for 30 future days ──
    forecast_days = 30
    today_date = date.today()
    sorted_dates = [str(today_date + timedelta(days=i)) for i in range(forecast_days)]

    # ── 7. Build articles list ──
    articles = []
    for nm_id, stock_info in stocks_map.items():
        vendor_code = stock_info["vendor_code"]
        subject = stock_info["subject"]
        brand = stock_info["brand"]

        # Apply filters
        if subject_filter and subject_filter != subject:
            continue
        if brand_filter and brand_filter != brand:
            continue
        if article_filter and article_filter.lower() not in vendor_code.lower():
            continue

        stocks = stock_info["stocks_wb"]
        o30 = orders_30d_map.get(nm_id, {})
        avg_daily = avg_daily_map.get(nm_id, 0)
        prev_avg = prev_avg_map.get(nm_id, 0)

        days_left = compute_days_left(stocks, avg_daily)
        trend_pct = compute_trend_pct(avg_daily, prev_avg)
        traffic = classify_traffic_light(days_left)

        # Build forecast: projected stock for each future day
        forecast = []
        for i in range(forecast_days):
            projected = max(0, int(stocks - avg_daily * i + 0.5))
            forecast.append(projected)

        articles.append({
            "nm_id": nm_id,
            "vendor_code": vendor_code,
            "subject": subject,
            "brand": brand,
            "orders_30d": o30.get("orders_30d", 0),
            "orders_sum_30d": o30.get("orders_sum_30d", 0),
            "trend_pct": trend_pct,
            "avg_daily": avg_daily,
            "stocks_wb": stocks,
            "days_left": days_left,
            "traffic_light": traffic,
            "forecast": forecast,
        })

    # ── 7b. Filter out articles with no recent orders ──
    # Keep articles that had any orders in the last 30 days
    filtered_articles = [a for a in articles if a["orders_30d"] > 0]
    articles = filtered_articles

    # Sort by days_left ascending (most critical first)
    articles.sort(key=lambda a: a["days_left"])

    # ── 8. Summaries ──
    total_orders_30d = sum(a["orders_30d"] for a in articles)
    total_avg_daily = sum(a["avg_daily"] for a in articles)
    critical_articles = [a for a in articles if a["traffic_light"] == "red"]
    most_critical = None
    if critical_articles:
        mc = critical_articles[0]
        most_critical = {"article": mc["vendor_code"], "days_left": mc["days_left"]}

    traffic_light = build_traffic_light_counts(articles)

    # Collect filter options
    subjects = sorted(set(a["subject"] for a in articles if a["subject"]))
    brands = sorted(set(a["brand"] for a in articles if a["brand"]))

    return {
        "data_date": str(data_date),
        "total_articles": len(articles),
        "orders_30d": total_orders_30d,
        "avg_daily": round(total_avg_daily, 2),
        "critical_count": len(critical_articles),
        "most_critical": most_critical,
        "traffic_light": traffic_light,
        "articles": articles,
        "dates": sorted_dates,
        "subjects": subjects,
        "brands": brands,
    }


def _empty_result() -> dict:
    return {
        "data_date": None,
        "total_articles": 0,
        "orders_30d": 0,
        "avg_daily": 0,
        "critical_count": 0,
        "most_critical": None,
        "traffic_light": {"red": 0, "orange": 0, "yellow": 0, "green": 0},
        "articles": [],
        "dates": [],
        "subjects": [],
        "brands": [],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Warehouse stocks — sync + views
# ═══════════════════════════════════════════════════════════════════════════════

def compute_need(stock_qty: int, avg_daily: float, need_days: int) -> int:
    """How many items need to be shipped to cover need_days of demand.
    Negative means surplus (no need to ship).
    """
    needed = avg_daily * need_days
    deficit = needed - stock_qty
    return max(0, int(deficit + 0.5))  # round up


async def sync_warehouse_stocks(
    db: AsyncSession,
    project_id: int,
    items: list[dict],
) -> int:
    """Upsert warehouse stock data from WB API response.
    items = [{warehouseName, nmId, supplierArticle, quantity, quantityFull,
              inWayToClient, inWayFromClient, subject, brand, ...}]
    Returns count of upserted rows.
    """
    if not items:
        return 0

    # Delete old data for this project first (full refresh)
    await db.execute(
        delete(WbWarehouseStock).where(WbWarehouseStock.project_id == project_id)
    )

    count = 0
    batch = []
    for item in items:
        nm_id = item.get("nmId")
        wh_name = item.get("warehouseName", "")
        if not nm_id or not wh_name:
            continue

        batch.append({
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
        })

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

    await db.commit()
    logger.info(f"Synced {count} warehouse stock rows for project {project_id}")
    return count


async def get_warehouse_stocks(
    db: AsyncSession,
    project_id: int,
) -> dict:
    """Get warehouse stocks grouped by warehouse.
    Returns {warehouses: [{name, total_qty, articles_count}], total_warehouses, total_qty}
    """
    result = await db.execute(
        select(
            WbWarehouseStock.warehouse_name,
            func.sum(WbWarehouseStock.quantity).label("total_qty"),
            func.count(func.distinct(WbWarehouseStock.nm_id)).label("articles_count"),
        ).where(
            WbWarehouseStock.project_id == project_id,
        ).group_by(WbWarehouseStock.warehouse_name)
        .order_by(func.sum(WbWarehouseStock.quantity).desc())
    )

    warehouses = []
    total_qty = 0
    for r in result:
        qty = int(r.total_qty or 0)
        total_qty += qty
        warehouses.append({
            "name": r.warehouse_name,
            "total_qty": qty,
            "articles_count": int(r.articles_count or 0),
        })

    return {
        "warehouses": warehouses,
        "total_warehouses": len(warehouses),
        "total_qty": total_qty,
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
        supply_days: target stock level in days (how many days of supply to maintain)
        analysis_days: lookback period in days for calculating avg daily orders
        mode="actual": uses WB supplier/orders warehouseName (which warehouse shipped).
        mode="hypothetical": maps order regionName → nearest open warehouse via geo.
    """
    from backend.services.funnel.wb_api_client import (
        get_wb_key,
        fetch_supplier_orders,
        fetch_acceptance_options,
        fetch_warehouse_stocks,
    )
    from backend.services.warehouse_geo import (
        find_nearest_warehouse, find_nearest_warehouse_by_city,
        get_country_filtered_warehouses, WAREHOUSE_COORDS,
        WB_API_ID_TO_STOCK_NAME,
    )
    
    today = date.today()
    trend_start = today - timedelta(days=analysis_days)

    # 1. Fetch orders from WB API
    api_key = await get_wb_key(db, project_id, "wb")
    wh_orders_map: dict[tuple[str, int], int] = {}  # (warehouse, nm_id) -> total_orders
    vendor_map: dict[int, str] = {}
    brand_map: dict[int, str] = {}
    subject_map: dict[int, str] = {}
    actual_days = analysis_days

    open_warehouses: list[str] = list(WAREHOUSE_COORDS.keys())
    city_map: dict[str, str] = {}  # srid → city
    okrug_map: dict[str, str] = {}  # srid → okrug
    if mode == "hypothetical":
        # Load city + okrug mapping from uploaded Excel data
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

        # Load excluded warehouses from project settings
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

            # Determine warehouse based on mode
            if mode == "hypothetical":
                srid = str(order.get("srid", ""))
                wh_name = None

                # Step 0: Filter by country (RU vs KZ)
                okrug = okrug_map.get(srid)
                country_wh = get_country_filtered_warehouses(okrug, open_warehouses)

                # Step 1: Try city-level (from uploaded Excel, nearest by distance)
                city = city_map.get(srid)
                if city:
                    wh_name = find_nearest_warehouse_by_city(city, country_wh)

                # Step 2: Fallback to region-level (from API regionName, nearest by distance)
                if not wh_name:
                    region = order.get("regionName", "")
                    wh_name = find_nearest_warehouse(region, country_wh)

                # Step 3: Final fallback to actual warehouse
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
    
    # Also get vendor codes, brand, subject from WbFunnelDaily for completeness
    funnel_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            WbFunnelDaily.vendor_code,
            WbFunnelDaily.brand,
            WbFunnelDaily.subject,
        ).where(
            WbFunnelDaily.project_id == project_id,
        ).group_by(WbFunnelDaily.nm_id, WbFunnelDaily.vendor_code, WbFunnelDaily.brand, WbFunnelDaily.subject)
    )
    for r in funnel_result:
        if r.nm_id not in vendor_map:
            vendor_map[r.nm_id] = r.vendor_code or f"#{r.nm_id}"
        if r.nm_id not in brand_map and r.brand:
            brand_map[r.nm_id] = r.brand
        if r.nm_id not in subject_map and r.subject:
            subject_map[r.nm_id] = r.subject

    # 2. Get warehouse stocks
    wh_result = await db.execute(
        select(
            WbWarehouseStock.warehouse_name,
            WbWarehouseStock.nm_id,
            WbWarehouseStock.vendor_code,
            WbWarehouseStock.quantity,
        ).where(
            WbWarehouseStock.project_id == project_id,
        ).order_by(WbWarehouseStock.warehouse_name)
    )

    # Build stock lookup: (warehouse, nm_id) -> qty
    stock_lookup: dict[tuple[str, int], int] = {}
    stock_vendor: dict[int, str] = {}
    all_nm_ids: set[int] = set()

    for r in wh_result:
        stock_lookup[(r.warehouse_name, r.nm_id)] = int(r.quantity or 0)
        all_nm_ids.add(r.nm_id)
        if r.nm_id not in vendor_map:
            vendor_map[r.nm_id] = r.vendor_code or f"#{r.nm_id}"

    # In hypothetical mode, also include articles from orders that may not have stock
    for (wh, nm) in wh_orders_map:
        all_nm_ids.add(nm)

    # 3. Determine which warehouses to show
    if mode == "hypothetical":
        # Show warehouses from geo mapping that have orders or stock
        wh_names_to_show = set()
        for (wh, _) in wh_orders_map:
            wh_names_to_show.add(wh)
        for (wh, _) in stock_lookup:
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

            # Skip if no orders and no stock at this warehouse for this article
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

    # Sort warehouses by total need descending
    warehouses = sorted(wh_data.values(), key=lambda w: w["total_need"], reverse=True)

    # Build article list with brand/subject for filtering
    article_list = sorted(
        [{
            "nm_id": nm,
            "vendor_code": vendor_map.get(nm, f"#{nm}"),
            "brand": brand_map.get(nm, ""),
            "subject": subject_map.get(nm, ""),
        } for nm in all_nm_ids],
        key=lambda a: a["vendor_code"],
    )

    # Collect unique brands/subjects for filters
    brands = sorted(set(b for b in brand_map.values() if b))
    subjects = sorted(set(s for s in subject_map.values() if s))

    return {
        "warehouses": warehouses,
        "articles": article_list,
        "brands": brands,
        "subjects": subjects,
        "supply_days": supply_days,
        "analysis_days": analysis_days,
        "mode": mode,
        "total_warehouses": len(warehouses),
        "total_articles": len(article_list),
    }

