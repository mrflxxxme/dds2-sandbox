"""
Warehouse Need Service — restocking need calculation per warehouse per article.

Extracted from warehouse_stock_service.py for maintainability.
Uses compute_need() from warehouse_stock_service for the core formula.
"""

import logging
from datetime import date, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily, WbWarehouseStock
from backend.services.warehouse_stock_service import compute_need

logger = logging.getLogger("dds.stock_analytics")


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
        mode="hypothetical": maps order regionName -> nearest open warehouse via geo
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

    # 4. Build warehouse -> articles need map
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

    # -- RF (fulfillment) warehouses --
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

    # -- RF stock per warehouse per nm_id --
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

    # -- In-assembly (reserved) quantities per nm_id / warehouse --
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

    # -- In-transit (SHIPPED) quantities per nm_id --
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

    # -- Revenue for analysis_days from wb_finance_rows (sales - returns) --
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

    # -- WB stock totals per nm_id --
    wb_stock_total: dict[int, int] = {}
    for (_wh, nm), qty in stock_lookup.items():
        wb_stock_total[nm] = wb_stock_total.get(nm, 0) + qty

    # -- Average delivery time --
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

    # -- Build enriched article list with need totals --
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
