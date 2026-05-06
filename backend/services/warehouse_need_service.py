# ruff: noqa: RUF002, RUF003
"""
Warehouse Need Service — restocking need calculation per warehouse per article.

Extracted from warehouse_stock_service.py for maintainability.
Uses compute_need() from warehouse_stock_service for the core formula.
"""

import logging
import re
from datetime import date, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models import WbFunnelDaily, WbWarehouseStock
from backend.services.warehouse_stock_service import compute_need

logger = logging.getLogger("dds.stock_analytics")

_WH_PARENS_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _normalize_wb_warehouse(name: str | None) -> str:
    """Strip trailing parenthesized suffix so FBO and order/stock names match.

    'Краснодар (Тихорецкая)' -> 'Краснодар'
    'Самара (Новосемейкино)' -> 'Самара'
    'СЦ Симферополь (Молодежненское)' -> 'СЦ Симферополь'
    """
    if not name:
        return ""
    return _WH_PARENS_RE.sub("", name).strip()


@cached(prefix="reports:warehouse_need", ttl=300)
async def get_warehouse_need(
    db: AsyncSession,
    project_id: int,
    supply_days: int = 14,
    analysis_days: int = 14,
    mode: str = "actual",
    localization_optimized: bool = False,
    only_available: bool = False,
) -> dict:
    """Compute restocking need per warehouse per article.

    Args:
        supply_days: target stock level in days
        analysis_days: lookback period for avg daily orders
        mode="actual": uses WB supplier/orders warehouseName
        mode="hypothetical": maps order regionName -> nearest open warehouse via geo
        localization_optimized: if True — игнорируем фактический warehouseName и
            всегда привязываем заказ к ближайшему ДОСТУПНОМУ (не excluded)
            складу по координатам региона покупателя. Дополнительно делает
            DISTRICT-POOLING сборок и транзита: assembly+transit, направленные
            на ЛЮБОЙ склад одного ФО, вычитаются пропорционально из спроса
            всех складов того же ФО (а не только из конкретного склада-цели).
            Имеет приоритет над `mode`.
        only_available: if True — каждый need в матрице урезается жадно по
            фактическому ФФ-остатку артикула. Сумма needs артикула во всех
            WB-колонках ≤ available_at_ff. Используется чтобы в матрице
            показывать «реально могу отправить», а не «идеальную потребность».
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
    from backend.models.wb_fbo import WbFboSupply
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

    # Excluded warehouses apply to BOTH modes — used to skip e.g. all SC (sorting
    # centers) from per-WB demand. Stored already-normalized (no parens).
    from backend.services.settings_service import get_excluded_warehouses

    excluded_list = await get_excluded_warehouses(db, project_id)
    excluded_set = {_normalize_wb_warehouse(w) for w in excluded_list if w}
    if excluded_set:
        open_warehouses = [w for w in open_warehouses if w not in excluded_set]
        logger.info("Excluded warehouses for project %s: %s", project_id, sorted(excluded_set))

    # city_map нужен для hypothetical (точнее по городу) И для
    # localization_optimized (если region не дал nearest — пробуем по городу).
    if mode == "hypothetical" or localization_optimized:
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

    if api_key:
        orders = await fetch_supplier_orders(api_key, trend_start.isoformat())
        for order in orders:
            nm_id = order.get("nmId")
            qty = order.get("quantity", 1)
            if not nm_id:
                continue

            if localization_optimized:
                # Игнорируем фактический warehouseName — берём ближайший
                # доступный склад по координатам покупателя. open_warehouses
                # уже отфильтрован от excluded (см. выше). Цепочка fallback:
                # city → region → пропускаем заказ (нет доступного склада).
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
                # Если ни city, ни region не дали nearest (например, region
                # отсутствует в REGION_COORDS) — заказ некуда привязать,
                # пропускаем (а не падаем на excluded warehouseName).
                if not wh_name:
                    continue
            elif mode == "hypothetical":
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

            wh_name = _normalize_wb_warehouse(wh_name)
            if not wh_name or wh_name in excluded_set:
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

    for r in wh_result:  # type: ignore[assignment]
        wh_norm = _normalize_wb_warehouse(r.warehouse_name)
        if not wh_norm or wh_norm in excluded_set:
            continue
        key = (wh_norm, r.nm_id)
        # Several physical sub-warehouses can collapse into one normalized name;
        # sum their quantities so the analytics view sees a single column.
        stock_lookup[key] = stock_lookup.get(key, 0) + int(r.quantity or 0)
        all_nm_ids.add(r.nm_id)
        if r.nm_id not in vendor_map:
            vendor_map[r.nm_id] = r.vendor_code or f"#{r.nm_id}"

    for _wh, nm in wh_orders_map:
        all_nm_ids.add(nm)

    # WB stock totals per nm_id — нужно ДО district-pooling и only_available
    # блоков, которые суммируют общий WB-сток для total_need_global.
    wb_stock_total: dict[int, int] = {}
    for (_wh, nm), qty in stock_lookup.items():
        wb_stock_total[nm] = wb_stock_total.get(nm, 0) + qty

    # 3. Determine which warehouses to show
    if mode == "hypothetical" or localization_optimized:
        wh_names_to_show = set()
        for wh, _ in wh_orders_map:
            wh_names_to_show.add(wh)
        for wh, _ in stock_lookup:
            wh_names_to_show.add(wh)
    else:
        wh_names_to_show = set(wh for (wh, _) in stock_lookup)

    # raw_need_per_article[nm_id] = full demand across all WB warehouses (pre-allocation)
    # wh_data[wh][articles][nm].need = post-allocation (minus already-allocated assemblies/transit)
    # The actual build of wh_data happens after we know target-warehouse allocations
    # (assembly_target_map / transit_target_map below).
    raw_need_per_article: dict[int, int] = {}
    wh_data: dict[str, dict] = {}

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

    # -- One representative barcode per nm_id (for prefilled assembly form) --
    # Primary: nomenclature. Fallback: WbFboSupplyItem — covers nm_ids that
    # appear in WB stocks/orders but were not yet imported into local catalog.
    from backend.models.wb_fbo import WbFboSupplyItem

    barcode_map: dict[int, str] = {}
    barcode_result = await db.execute(
        select(Nomenclature.article_wb, func.min(Nomenclature.barcode).label("barcode"))
        .where(
            Nomenclature.project_id == project_id,
            Nomenclature.article_wb.isnot(None),
            Nomenclature.barcode != "",
        )
        .group_by(Nomenclature.article_wb)
    )
    for row in barcode_result:  # type: ignore[assignment]
        if row.barcode:
            barcode_map[row.article_wb] = row.barcode

    fbo_bc_result = await db.execute(
        select(WbFboSupplyItem.nm_id, func.min(WbFboSupplyItem.barcode).label("barcode"))
        .where(
            WbFboSupplyItem.project_id == project_id,
            WbFboSupplyItem.nm_id.isnot(None),
            WbFboSupplyItem.barcode != "",
        )
        .group_by(WbFboSupplyItem.nm_id)
    )
    for row in fbo_bc_result:  # type: ignore[assignment]
        if row.nm_id and row.barcode and row.nm_id not in barcode_map:
            barcode_map[row.nm_id] = row.barcode

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
        for row in rf_stock_result:  # type: ignore[assignment]
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
    for row in assembly_result:  # type: ignore[assignment]
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
    for row in shipped_result:  # type: ignore[assignment]
        nm = row.article_wb
        in_transit_map[nm] = {
            "qty": int(row.qty or 0),
            "date": row.delivery_date.isoformat() if row.delivery_date else None,
        }

    # -- Allocation per target WB warehouse: assembly + in-transit --
    # target = COALESCE(WbFboSupply.warehouse_name, AssemblyRequest.wb_warehouse_name_manual)
    target_label = func.coalesce(
        WbFboSupply.warehouse_name,
        AssemblyRequest.wb_warehouse_name_manual,
    ).label("target_wh")

    assembly_target_map: dict[int, dict[str, int]] = {}  # nm_id -> {wb_warehouse: qty}
    transit_target_map: dict[int, dict[str, int]] = {}

    target_alloc_result = await db.execute(
        select(
            Nomenclature.article_wb,
            AssemblyRequest.status,
            target_label,
            func.sum(AssemblyRequestItem.quantity).label("qty"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .join(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
        .outerjoin(WbFboSupply, WbFboSupply.id == AssemblyRequest.wb_fbo_supply_id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_([*active_statuses, AssemblyStatus.SHIPPED]),
            Nomenclature.article_wb.isnot(None),
        )
        .group_by(Nomenclature.article_wb, AssemblyRequest.status, target_label)
    )
    for row in target_alloc_result:  # type: ignore[assignment]
        nm = row.article_wb
        target = _normalize_wb_warehouse(row.target_wh)
        qty = int(row.qty or 0)
        if not target or qty <= 0:
            continue
        bucket = transit_target_map if row.status == AssemblyStatus.SHIPPED else assembly_target_map
        if nm not in bucket:
            bucket[nm] = {}
        bucket[nm][target] = bucket[nm].get(target, 0) + qty

    # 4. Build warehouse -> articles need map (per-warehouse need is post-allocation)
    for wh_name in wh_names_to_show:
        if wh_name not in wh_data:
            wh_data[wh_name] = {"name": wh_name, "total_need": 0, "articles": {}}

        for nm_id in all_nm_ids:
            stock = stock_lookup.get((wh_name, nm_id), 0)
            total_orders_at_wh = wh_orders_map.get((wh_name, nm_id), 0)

            if total_orders_at_wh == 0 and stock == 0:
                continue

            avg_d = round(total_orders_at_wh / max(actual_days, 1), 2)
            raw_need = compute_need(stock, avg_d, supply_days)

            # Full (pre-allocation) demand goes into the per-article total
            raw_need_per_article[nm_id] = raw_need_per_article.get(nm_id, 0) + raw_need

            # Subtract assemblies and in-transit already heading to THIS WB warehouse
            target_asm = assembly_target_map.get(nm_id, {}).get(wh_name, 0)
            target_transit = transit_target_map.get(nm_id, {}).get(wh_name, 0)
            need = max(0, raw_need - target_asm - target_transit)

            wh_data[wh_name]["articles"][nm_id] = {
                "nm_id": nm_id,
                "vendor_code": vendor_map.get(nm_id, f"#{nm_id}"),
                "stock": stock,
                "avg_daily": avg_d,
                "need": need,
            }
            wh_data[wh_name]["total_need"] += need

    # 4.5. District-pooling (idealized localization only)
    # Перераспределяем учёт сборок+транзита по ФО: если в ЦФО есть сборка 100
    # шт в Электросталь, то спрос всех складов ЦФО (Коледино, Подольск, Эл-сталь)
    # уменьшается пропорционально, а не только Электросталь.
    if localization_optimized:
        from backend.services.warehouse_district import warehouse_to_district

        # Восстанавливаем raw_need_at_wh (отменяем per-wh вычитание из шага 4)
        for wh_name in list(wh_data.keys()):
            for nm_id, art in wh_data[wh_name]["articles"].items():
                target_asm = assembly_target_map.get(nm_id, {}).get(wh_name, 0)
                target_transit = transit_target_map.get(nm_id, {}).get(wh_name, 0)
                art["need"] += target_asm + target_transit

        # Группируем asm+transit по (district, nm)
        district_pool: dict[tuple[str, int], int] = {}
        for nm, wh_qty in assembly_target_map.items():
            for wh, q in wh_qty.items():
                d = warehouse_to_district(wh)
                district_pool[(d, nm)] = district_pool.get((d, nm), 0) + q
        for nm, wh_qty in transit_target_map.items():
            for wh, q in wh_qty.items():
                d = warehouse_to_district(wh)
                district_pool[(d, nm)] = district_pool.get((d, nm), 0) + q

        # Группируем raw_need по (district, nm) — для пропорционального дележа
        raw_need_district: dict[tuple[str, int], int] = {}
        for wh_name in list(wh_data.keys()):
            d = warehouse_to_district(wh_name)
            for nm_id, art in wh_data[wh_name]["articles"].items():
                raw_need_district[(d, nm_id)] = raw_need_district.get((d, nm_id), 0) + art["need"]

        # Применяем pooled-subtraction пропорционально доле склада в спросе ФО
        for wh_name in list(wh_data.keys()):
            d = warehouse_to_district(wh_name)
            for nm_id, art in wh_data[wh_name]["articles"].items():
                district_total = raw_need_district.get((d, nm_id), 0)
                district_alloc = district_pool.get((d, nm_id), 0)
                if district_total > 0 and district_alloc > 0:
                    share = art["need"] / district_total
                    art["need"] = max(0, art["need"] - round(share * district_alloc))

        # Пересчитываем total_need per warehouse
        for wh_name in list(wh_data.keys()):
            wh_data[wh_name]["total_need"] = sum(a["need"] for a in wh_data[wh_name]["articles"].values())

    # 4.6. Only-available: жадный cap по can_send артикула (= min(rf_avail,
    # total_need_global)). Если total_need_global=0 (профицит покрыт другими
    # складами) — все клетки артикула обнуляются. Если total_need=N>0 —
    # распределяем N шт между WB-складами в порядке убывания их per-cell need.
    if only_available:
        # Считаем can_send_per_nm = min(rf_avail, total_need_global)
        can_send_per_nm: dict[int, int] = {}
        for nm_id in all_nm_ids:
            total_rf_stock_local = 0
            for wh in rf_warehouses:  # type: ignore[assignment]
                stock_qty = rf_stock_map.get(nm_id, {}).get(wh.id, 0)  # type: ignore[attr-defined]
                total_rf_stock_local += stock_qty
            in_asm_total_local = in_assembly_map.get(nm_id, 0)
            rf_avail_local = max(0, total_rf_stock_local - in_asm_total_local)

            total_orders_local = sum(qty for (wh, nm), qty in wh_orders_map.items() if nm == nm_id)
            avg_d_local = total_orders_local / max(actual_days, 1)
            total_wb_stock_local = wb_stock_total.get(nm_id, 0)
            transit_local = in_transit_map.get(nm_id, {"qty": 0}).get("qty", 0)
            global_need = max(
                0,
                round(avg_d_local * supply_days - total_wb_stock_local - in_asm_total_local - transit_local),
            )
            can_send_per_nm[nm_id] = min(rf_avail_local, global_need)

        # Greedy: больше всего «нуждающемуся» WB-складу — первому.
        for nm_id, cap_total in can_send_per_nm.items():
            wbs = []
            for wh_name in list(wh_data.keys()):
                arts = wh_data[wh_name]["articles"]
                if nm_id in arts and arts[nm_id]["need"] > 0:
                    wbs.append((wh_name, arts[nm_id]["need"]))
            wbs.sort(key=lambda x: -x[1])
            remaining = cap_total
            for wh_name, n in wbs:
                cap = min(n, remaining)
                wh_data[wh_name]["articles"][nm_id]["need"] = cap
                remaining = max(0, remaining - cap)

        for wh_name in list(wh_data.keys()):
            wh_data[wh_name]["total_need"] = sum(a["need"] for a in wh_data[wh_name]["articles"].values())

    warehouses = sorted(wh_data.values(), key=lambda w: w["total_need"], reverse=True)

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
        for row in dt_rows:  # type: ignore[assignment]
            total_days_sum += (row.assembly_days or 0) + (row.delivery_days or 0) + (row.wb_acceptance_days or 0)
        avg_delivery_days = round(total_days_sum / len(dt_rows))

    # -- Build enriched article list with need totals --
    # total_need per article теперь считается из глобального баланса
    # (avg_d * supply_days - WB_stock - in_assembly - in_transit) ниже,
    # raw_need_per_article использовался только в старой формуле и больше
    # не нужен на этом уровне.

    # Summary accumulators
    sum_total_need = 0
    sum_total_can_send = 0
    sum_total_deficit = 0
    deficit_count = 0
    can_send_count = 0
    no_wb_count = 0

    enriched_articles = []
    for nm_id in all_nm_ids:
        # RF stocks per warehouse
        rf_stocks_for_nm: dict[int, dict] = {}
        total_rf_stock = 0
        for wh in rf_warehouses:  # type: ignore[assignment]
            stock_qty = rf_stock_map.get(nm_id, {}).get(wh.id, 0)  # type: ignore[attr-defined]
            in_asm_qty = in_assembly_per_wh.get(nm_id, {}).get(wh.id, 0)  # type: ignore[attr-defined]
            available = max(0, stock_qty - in_asm_qty)
            rf_stocks_for_nm[wh.id] = {"stock": stock_qty, "available": available}  # type: ignore[attr-defined]
            total_rf_stock += stock_qty

        in_asm_total = in_assembly_map.get(nm_id, 0)
        total_rf_available = max(0, total_rf_stock - in_asm_total)

        transit_info = in_transit_map.get(nm_id, {"qty": 0, "date": None})
        in_transit_qty = transit_info["qty"]
        in_transit_date = transit_info["date"]

        # total_need = ОБЩАЯ потребность артикула на supply_days вперёд минус
        # ВСЕ доступные источники (любой WB-склад, сборка, транзит). Это
        # отличается от sum(per-WB needs) тем, что профицит на одном WB-складе
        # компенсирует дефицит на другом — для KPI «сколько ещё закупить».
        # Per-cell needs в матрице остаются per-WB-локализованными (показ
        # идеальной раскладки независимо от общего профицита).
        total_orders_for_nm = sum(qty for (wh, nm), qty in wh_orders_map.items() if nm == nm_id)
        total_avg_daily = total_orders_for_nm / max(actual_days, 1)
        total_wb_stock = wb_stock_total.get(nm_id, 0)
        gross_demand = total_avg_daily * supply_days
        total_need = max(0, round(gross_demand - total_wb_stock - in_asm_total - in_transit_qty))
        can_send = min(total_rf_available, total_need)
        deficit = max(0, total_need - can_send)

        stocks_wb = wb_stock_total.get(nm_id, 0)

        enriched_articles.append(
            {
                "nm_id": nm_id,
                "vendor_code": vendor_map.get(nm_id, f"#{nm_id}"),
                "barcode": barcode_map.get(nm_id, ""),
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
