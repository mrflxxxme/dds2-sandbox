"""
Warehouse Stock Service — sync, view, and restocking need calculation.

Handles WbWarehouseStock sync from WB API and need computation per warehouse.
"""

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.models import WbFunnelDaily, WbWarehouseStock

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
    """Get warehouse stocks grouped by warehouse."""
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
        supply_days: target stock level in days
        analysis_days: lookback period for avg daily orders
        mode="actual": uses WB supplier/orders warehouseName
        mode="hypothetical": maps order regionName → nearest open warehouse via geo
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

    stock_lookup: dict[tuple[str, int], int] = {}
    all_nm_ids: set[int] = set()

    for r in wh_result:
        stock_lookup[(r.warehouse_name, r.nm_id)] = int(r.quantity or 0)
        all_nm_ids.add(r.nm_id)
        if r.nm_id not in vendor_map:
            vendor_map[r.nm_id] = r.vendor_code or f"#{r.nm_id}"

    for (wh, nm) in wh_orders_map:
        all_nm_ids.add(nm)

    # 3. Determine which warehouses to show
    if mode == "hypothetical":
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

    article_list = sorted(
        [{"nm_id": nm, "vendor_code": vendor_map.get(nm, f"#{nm}"),
          "brand": brand_map.get(nm, ""), "subject": subject_map.get(nm, "")}
         for nm in all_nm_ids],
        key=lambda a: a["vendor_code"],
    )

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
