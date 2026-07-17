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
from backend.services.warehouse_geo_data import ACCEPTANCE_TO_STOCK_NAME
from backend.services.warehouse_stock_service import compute_need

logger = logging.getLogger("dds.stock_analytics")

_WH_PARENS_RE = re.compile(r"\s*\([^)]*\)\s*$")

# WB API /supplier/orders отдаёт srid с префиксом 'eXX.r' или 'eXX.i'
# (e.g. 'eBw.r62e0deb30efa49d3b0f2c9e6cc87266c.0.0'), а Excel-выгрузка
# «Лента заказов» в `order_city_map` хранит только core 'hex32.X.X'
# ('62e0deb30efa49d3b0f2c9e6cc87266c.0.0'). Для match'инга нормализуем
# к единой форме — обрезаем префикс если он есть.
_SRID_PREFIX_RE = re.compile(r"^e[A-Za-z0-9]+\.[ri]")


def _normalize_srid(srid: str | None) -> str:
    """Привести srid к канонической форме без префикса 'eXX.r/i'."""
    if not srid:
        return ""
    return _SRID_PREFIX_RE.sub("", str(srid))


# Fallback transport time (дни) per district когда WarehouseDeliveryTime пуст
# для пары FF→WB. Реалистичный среднеотраслевой transit time от Москвы.
# Используется только если у пользователя нет per-pair настроек.
FALLBACK_TRANSPORT_DAYS_PER_DISTRICT: dict[str, int] = {
    "central": 2,
    "northwest": 4,
    "volga": 5,
    "south_caucasus": 6,
    "ural": 8,
    "far_east_siberia": 14,
    "abroad": 14,
    "unknown": 7,
}

# Override локального WB-склада для дальних ФО когда WB всё равно направляет
# большинство заказов с центральных складов, а не с нашего периферийного FF.
# Прецедент: project_id=4, ДВ+СФО за 14д = 3646 orders. Из них реально WB
# обслужил: Электросталь 568 (16%) + Перспективная 1703 (47%) + ЦФО-доноры
# 30%, а Владивосток только 79 (2%). При nearest_by_coords алгоритм считал
# что 100% ДВ+СФО уйдёт на наш Владивосток → переоценка локализации в 10-50×
# и overstock дальних регионов. Override: для `far_east_siberia` принудительно
# использовать Перспективную (главный WB-склад УФО, отлично доставляет в ДВ/СФО).
# Применяется только в localization_optimized режиме, только если Перспективная
# не в excluded. Иначе fallback на старую nearest_by_coords логику.
DISTRICT_PREFERRED_WH_OVERRIDE: dict[str, str] = {
    "far_east_siberia": "Екатеринбург - Перспективная 14",
}


# Growth-aware скорость заказов: плоское среднее за analysis_days запаздывает
# на растущих SKU (реальный кейс 2026-07-14, ШК 2043788816553: среднее 14д
# ≈10 шт/д при темпе последних дней ≈20 шт/д → недогруз ×2 и разрыв стока на WB
# ещё до приезда поставки). Скорость = max(среднее за окно, среднее за 7 и за 3
# ПОЛНЫХ дня — сегодняшний неполный день в короткие окна не входит). Затухающий
# SKU остаётся на среднем за окно (не режем вниз — культура «дозагруз до
# локализации», перезаклад безопаснее разрыва).
GROWTH_RECENT_WINDOWS: tuple[int, ...] = (7, 3)


def _bump_order_day(
    acc: dict[int, dict[str, int]], nm_id: int, order_date: str | None, qty: int
) -> None:
    """Копит заказы по дням per nm (только те, что вошли в спрос)."""
    day = (order_date or "")[:10]
    if not day:
        return
    per = acc.setdefault(nm_id, {})
    per[day] = per.get(day, 0) + qty


def _growth_factor(
    day_counts: dict[str, int], total_qty: int, actual_days: int, today: date
) -> float:
    """Множитель роста g ≥ 1: eff_скорость / плоское среднее за окно.

    eff = max(total/actual_days, Σ(последние w полных дней)/w for w in GROWTH_RECENT_WINDOWS).
    У заказов без даты нет вклада в короткие окна → g консервативно остаётся 1.
    """
    avg_full = total_qty / max(actual_days, 1)
    if avg_full <= 0:
        return 1.0
    today_iso = today.isoformat()
    best = avg_full
    for w in GROWTH_RECENT_WINDOWS:
        w_eff = min(w, actual_days)
        if w_eff <= 0:
            continue
        start_iso = (today - timedelta(days=w_eff)).isoformat()
        recent = sum(q for d, q in day_counts.items() if start_iso <= d < today_iso)
        best = max(best, recent / w_eff)
    return best / avg_full


def _bump_target_for_sku(sku_ppb: int | None, min_stock_per_main_warehouse: int) -> int:
    """Целевой минимум на главном складе ФО для bump-логики.

    Если у SKU задана кратность коробки (`Nomenclature.box_qty_override`) и
    `use_box_multiplicity=True` — шлём одну коробку (target=K). Иначе fallback
    на флэт-параметр `min_stock_per_main_warehouse` (исторический «минимум N шт»).
    """
    if sku_ppb is not None and sku_ppb > 0:
        return sku_ppb
    return max(0, min_stock_per_main_warehouse)


def _normalize_wb_warehouse(name: str | None) -> str:
    """Canonicalize warehouse name to the WAREHOUSE_COORDS form so backend's
    `data.warehouses[].name` matches acceptance-redistribute output on the
    frontend (both produce the same canonical key, columns collapse into one).

    Algorithm mirrors `_normalize_acceptance_wh`:
      1. Direct alias from ACCEPTANCE_TO_STOCK_NAME (acceptance/stocks variants
         to canonical, e.g. 'Новосемейкино' -> 'Самара (Новосемейкино)',
         'Склад Шушары' -> 'СПБ Шушары').
      2. Else strip trailing «(...)» (orders feed short forms like 'Самара',
         FBO short names) and look up alias on the stripped name.
      3. Else return stripped — unknown warehouse passes through.

    Examples:
      'Новосемейкино'             -> 'Самара (Новосемейкино)' (alias step 1)
      'Новосемейкино: Питание'    -> 'Самара (Новосемейкино)' (alias step 1)
      'Самара (Новосемейкино)'    -> 'Самара (Новосемейкино)' (alias step 1, parens kept)
      'Самара'                    -> 'Самара' (no alias, no parens — passthrough)
      'Краснодар (Тихорецкая)'    -> 'Краснодар' (alias step 1 strips suffix)
      'Склад Шушары'              -> 'СПБ Шушары' (alias step 1)
      'Электросталь: Питание'     -> 'Электросталь' (alias step 1)
      'СЦ Симферополь (Молодежненское)' -> 'СЦ Симферополь' (paren-strip step 2)

    NB: the «Самара (Новосемейкино)» key in ACCEPTANCE_TO_STOCK_NAME is
    self-aliased so the canonical form survives this function unchanged
    (otherwise step 2 would strip it down to «Самара» and re-create the split
    we are trying to fix).
    """
    if not name:
        return ""
    if name in ACCEPTANCE_TO_STOCK_NAME:
        return ACCEPTANCE_TO_STOCK_NAME[name]
    stripped = _WH_PARENS_RE.sub("", name).strip()
    if stripped in ACCEPTANCE_TO_STOCK_NAME:
        return ACCEPTANCE_TO_STOCK_NAME[stripped]
    return stripped


_HAMILTON_OKRUGS = (
    "central",
    "northwest",
    "south_caucasus",
    "volga",
    "ural",
    "far_east_siberia",
)


def _hamilton_priority_weight(wh: str) -> float:
    """Вес склада («схема воришек») в распределении cap шага 4.6 get_warehouse_need.

    Потребитель — greedy_allocate_to_target (распределение до целевой
    локализации): якоря наливаются первыми, воришки — в последнюю очередь.

    Формула: `(1 + priority_score(склад, свой ФО)) × penalty`, где
      • priority_score — доля top-слотов склада в цепочках ЛОКАЛИЗУЕМЫХ
        городов своего ФО (см. get_priority_score: нелокализуемые города,
        куда WB возит только чужими складами, скор не размывают);
      • penalty = 0.6, если склад стоит в top-2 цепочки города ЧУЖОГО ФО
        (is_stealer_for_okrug) — реально отбирает чужой спрос. Глубокие
        fallback-слоты (3+) штраф НЕ дают: за них душился якорь СЗФО
        (Шушары, слоты #4/#6 у far_east → вес 0.78; аудит 2026-07-09).

    Ориентиры на карте 2026-05-14: якоря — СПБ Шушары ≈1.75, Невинномысск
    ≈1.76; штрафованные якоря — Казань ≈0.98 (top-1 Сыктывкара, СЗФО),
    ЕКБ-Перспективная 1.20 (top-слоты Сибири); воришки — Электросталь ≈0.86,
    Коледино ≈0.75, Владимир ≈0.68; склады вне speed-карты и unknown/abroad —
    нейтральные 1.0.
    """
    from backend.services.warehouse_district import warehouse_to_district
    from backend.services.warehouse_speed import get_priority_score, is_stealer_for_okrug

    own = warehouse_to_district(wh)
    if own in ("abroad", "unknown"):
        # У зарубежных и неизвестных складов нет «своего ФО» в speed-карте —
        # оставляем нейтральный вес (без штрафа).
        return 1.0
    score = get_priority_score(wh, own)
    is_thief = any(is_stealer_for_okrug(wh, ok) for ok in _HAMILTON_OKRUGS if ok != own)
    penalty = 0.6 if is_thief else 1.0
    return (1.0 + score) * penalty


@cached(prefix="reports:warehouse_need", ttl=300)
async def get_warehouse_need(
    db: AsyncSession,
    project_id: int,
    supply_days: int = 14,
    analysis_days: int = 14,
    mode: str = "actual",
    localization_optimized: bool = False,
    only_available: bool = False,
    min_stock_per_main_warehouse: int = 0,
    localization_target: int = 100,
    bootstrap_shape: bool = False,
) -> dict:
    """Compute restocking need per warehouse per article.

    Скорость заказов — growth-aware: max(среднее за analysis_days, среднее за
    последние 7 и 3 ПОЛНЫХ дня) per SKU (см. GROWTH_RECENT_WINDOWS) — плоское
    среднее занижало потребность растущих SKU до 2×. Глобальный горизонт
    total_need/can_send = supply_days + спрос-взвешенное плечо доставки
    (клетки всегда считали supply+lead — кап без плеча их рационировал).

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
        localization_target: целевая локализация (%) для жадного распределения
            can_send по WB-складам (шаг 4.6, only_available). cap концентрируется
            на anchor-складах до достижения target по ФО (с учётом сток+сборка+
            транзит); остаток can_send НЕ размазывается на дальние склады.
            Дефолт 100% (решение юзера 2026-07-14: покрывать ПОЛНУЮ потребность,
            когда can_send позволяет; 75% раньше рационировал жирные SKU —
            кейс DIVANDEK: потребность 1286, раздавалось 863). Приоритет
            якорей greedy сохраняется — меняется только точка остановки.
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
    from backend.services.warehouse_district import okrug_to_district
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
    # HIGH-2: непривязанный (зарубеж/СНГ) спрос — заказы, которые в режиме
    # localization_optimized некуда привязать (нет ни city, ни region nearest).
    # Раньше они МОЛЧА дропались (`if not wh_name: continue`) → total_need в
    # loc-режиме занижался, а procurement-KPI зависел от режима. Теперь копим
    # их отдельно: в per-cell распределение они НЕ идут (нельзя локализовать),
    # но в глобальный total_need попадают → KPI инвариантен к режиму, а СНГ
    # естественно становится «не-локальным» спросом.
    unmapped_demand: dict[int, int] = {}
    # Заказы по дням per nm (для growth-aware скорости). Копятся ТОЛЬКО для
    # заказов, вошедших в спрос (mapped или unmapped) — окно/фильтры не меняются.
    orders_per_day: dict[int, dict[str, int]] = {}
    # Сырые склады отгрузки WB per (wh, nm) — веса для спрос-взвешенного плеча.
    # Именно СЫРОЙ warehouseName (физический склад, обслуживший заказ), а не
    # mode-зависимая привязка: total_need обязан оставаться mode-инвариантным
    # (HIGH-2), поэтому и плечо не может зависеть от режима.
    raw_wh_orders_for_lead: dict[tuple[str, int], int] = {}
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

    # Дополнительно отсекаем склады закрытые ПО ПРИЁМКЕ WB — индивидуальная
    # квота / 0 free+paid дней в 14 для всех package_types. Из cached snapshot
    # `wb:acceptance_coefficients:{pid}` (TTL 1ч). Fail-open: если кэша нет —
    # склады не блокируем (поведение как до фичи).
    from backend.services.warehouse_acceptance_service import (
        get_acceptance_blocked_warehouses,
    )

    # «Закрытые по приёмке» МИНУС whitelist `preorder_allowed_warehouses`: склад
    # без лимита блокируется (вырезается из расчёта), кроме явно разрешённых под
    # предзаявку. См. get_acceptance_blocked_warehouses.
    acceptance_closed = await get_acceptance_blocked_warehouses(db, project_id)
    # НЕ вливаем в excluded_set: закрытый по приёмке склад — не ЦЕЛЬ отгрузки
    # (вырезан из open_warehouses → спрос ремапится, greedy не наливает), но его
    # WB-СТОК реален и обязан оставаться в покрытии (wb_stock_total, existing ФО,
    # district-pooling) — иначе временное закрытие квоты WB «испаряет» сток и
    # движок предлагает двойную отгрузку/перекуп (аудит 2026-07-14, HIGH).
    if acceptance_closed:
        before = len(open_warehouses)
        open_warehouses = [w for w in open_warehouses if w not in acceptance_closed]
        logger.info(
            "Acceptance-closed warehouses for project %s: %s (open: %d → %d)",
            project_id,
            sorted(acceptance_closed),
            before,
            len(open_warehouses),
        )

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

    # Дальше — WB API (rate-limit очередь может ждать минутами): отпускаем
    # read-транзакцию/серверный коннект pgbouncer СЕЙЧАС, иначе параллельные
    # расчёты потребности держат пул как idle-in-tx (клин локалки 2026-07-16).
    await db.commit()

    if api_key:
        orders = await fetch_supplier_orders(api_key, trend_start.isoformat())
        for order in orders:
            nm_id = order.get("nmId")
            qty = order.get("quantity", 1)
            if not nm_id:
                continue

            # Вес плеча — по физическому складу отгрузки (mode-инвариантно).
            _raw_wh = _normalize_wb_warehouse(order.get("warehouseName"))
            if _raw_wh and qty > 0:
                _raw_key = (_raw_wh, nm_id)
                raw_wh_orders_for_lead[_raw_key] = raw_wh_orders_for_lead.get(_raw_key, 0) + qty

            if localization_optimized:
                # Игнорируем фактический warehouseName — берём ближайший
                # доступный склад по координатам покупателя. open_warehouses
                # уже отфильтрован от excluded (см. выше). Цепочка fallback:
                # district-override → city → region → пропускаем заказ.
                srid = _normalize_srid(order.get("srid"))
                wh_name = None
                okrug = okrug_map.get(srid)
                # Override для дальних ФО: WB всё равно направит большинство
                # на свои главные склады (Перспективная для ДВ+СФО), а не на
                # наш периферийный FF. См. DISTRICT_PREFERRED_WH_OVERRIDE.
                district = okrug_to_district(okrug)
                preferred = DISTRICT_PREFERRED_WH_OVERRIDE.get(district)
                if preferred and preferred in open_warehouses:
                    wh_name = preferred
                if not wh_name:
                    # Priority-chain города из speed-карты — точнее haversine:
                    # WB маршрутизирует по часам доставки, а не по евклиду.
                    # Если top-1 склад города excluded — берём priority-2
                    # ТОГО ЖЕ города (не ближайший по координатам).
                    from backend.services.warehouse_speed import find_priority_warehouse

                    city = city_map.get(srid)
                    wh_name = find_priority_warehouse(city, open_warehouses, okrug=district)
                if not wh_name:
                    country_wh = get_country_filtered_warehouses(okrug, open_warehouses)
                    city = city_map.get(srid)
                    if city:
                        wh_name = find_nearest_warehouse_by_city(city, country_wh)
                if not wh_name:
                    region = order.get("regionName", "")
                    country_wh = get_country_filtered_warehouses(okrug, open_warehouses)
                    wh_name = find_nearest_warehouse(region, country_wh)
                # Если ни city, ни region не дали nearest (например, region
                # отсутствует в REGION_COORDS — типично для зарубеж/СНГ) —
                # заказ некуда привязать локально. НЕ дропаем (как раньше):
                # копим в unmapped_demand, чтобы учесть в глобальном total_need
                # (procurement-KPI инвариантен к режиму), но в per-cell матрицу
                # такой спрос не попадает — его нельзя локализовать.
                if not wh_name:
                    unmapped_demand[nm_id] = unmapped_demand.get(nm_id, 0) + qty
                    _bump_order_day(orders_per_day, nm_id, order.get("date"), qty)
                    continue
            elif mode == "hypothetical":
                srid = _normalize_srid(order.get("srid"))
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
            # acceptance_closed отдельно: закрытый по приёмке склад — не цель
            # спроса (как раньше), хотя его сток теперь остаётся в покрытии.
            if not wh_name or wh_name in excluded_set or wh_name in acceptance_closed:
                continue

            key = (wh_name, nm_id)
            wh_orders_map[key] = wh_orders_map.get(key, 0) + qty
            _bump_order_day(orders_per_day, nm_id, order.get("date"), qty)
            if nm_id not in vendor_map:
                vendor_map[nm_id] = order.get("supplierArticle", f"#{nm_id}")
            if nm_id not in brand_map and order.get("brand"):
                brand_map[nm_id] = order["brand"]
            if nm_id not in subject_map and order.get("subject"):
                subject_map[nm_id] = order["subject"]

    # ── Growth-aware скорость: g per nm и «эффективный» спрос ────────────────
    # Все места, где спрос выводится из заказов (клетки, district-pooling,
    # greedy 4.6, глобальный total_need), считают от eff-карт (raw × g).
    # Сырые карты остаются для отчётных полей (avg_daily_base) и весов плеча.
    raw_total_by_nm: dict[int, int] = {}
    for (_wh, _nm), _q in wh_orders_map.items():
        raw_total_by_nm[_nm] = raw_total_by_nm.get(_nm, 0) + _q
    for _nm, _q in unmapped_demand.items():
        raw_total_by_nm[_nm] = raw_total_by_nm.get(_nm, 0) + _q
    growth_by_nm: dict[int, float] = {
        nm: _growth_factor(orders_per_day.get(nm, {}), total, actual_days, today)
        for nm, total in raw_total_by_nm.items()
    }
    wh_orders_eff: dict[tuple[str, int], float] = {
        k: q * growth_by_nm.get(k[1], 1.0) for k, q in wh_orders_map.items()
    }
    unmapped_eff: dict[int, float] = {
        nm: q * growth_by_nm.get(nm, 1.0) for nm, q in unmapped_demand.items()
    }

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

    # HIGH-2: непривязанный (зарубеж/СНГ) спрос обязан попасть в total_need
    # даже если у SKU нет ни WB-стока, ни локализованных заказов — иначе SKU,
    # продающийся ТОЛЬКО за рубеж, выпадет из procurement-KPI.
    for nm in unmapped_demand:
        all_nm_ids.add(nm)

    # SKU с RF-стоком, но без WbWarehouseStock и orders — это новинки. Они
    # должны попасть в общий ответ, чтобы пользователь видел их в основной
    # таблице с разбивкой по ФФ-складам, а не только в отдельной cold-start
    # карточке. Distribution по WB-складам для них считается на стороне
    # frontend через cold-start allocations (есть в /reports/cold_start_table).
    rf_only_result = await db.execute(
        select(Nomenclature.article_wb, Nomenclature.article_seller, Nomenclature.brand, Nomenclature.subject)
        .join(WarehouseStock, WarehouseStock.nomenclature_id == Nomenclature.id)
        .where(
            Nomenclature.project_id == project_id,
            Nomenclature.article_wb.isnot(None),
            WarehouseStock.project_id == project_id,
            WarehouseStock.quantity > 0,
        )
        .group_by(Nomenclature.article_wb, Nomenclature.article_seller, Nomenclature.brand, Nomenclature.subject)
    )
    for r in rf_only_result:  # type: ignore[assignment]
        if r.article_wb is None:
            continue
        all_nm_ids.add(r.article_wb)
        if r.article_wb not in vendor_map and r.article_seller:
            vendor_map[r.article_wb] = r.article_seller
        if r.article_wb not in brand_map and r.brand:
            brand_map[r.article_wb] = r.brand
        if r.article_wb not in subject_map and r.subject:
            subject_map[r.article_wb] = r.subject

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

    # -- Lead time (assembly + transport + WB acceptance) per WB-warehouse --
    # Используется в формуле `required = avg_d × (supply_days + lead_time)`:
    # пока товар едет, продолжаются продажи и съедают существующий WB-сток.
    # Если у пользователя в WarehouseDeliveryTime настроено per-pair → берём min
    # по всем FF (greedy быстрейший FF доступен для отгрузки). Иначе fallback
    # по district через FALLBACK_TRANSPORT_DAYS_PER_DISTRICT + assembly_days FF
    # + wb_acceptance_days FF.
    lead_time_per_wb: dict[str, int] = {}
    if rf_warehouses:
        dt_pairs_result = await db.execute(
            select(
                WarehouseDeliveryTime.warehouse_id,
                WarehouseDeliveryTime.wb_warehouse_name,
                WarehouseDeliveryTime.delivery_days,
            ).where(WarehouseDeliveryTime.project_id == project_id)
        )
        # ff_id -> {wb_name -> delivery_days}
        per_pair: dict[int, dict[str, int]] = {}
        for row in dt_pairs_result:  # type: ignore[assignment]
            wb_canon = _normalize_wb_warehouse(row.wb_warehouse_name)
            per_pair.setdefault(row.warehouse_id, {})[wb_canon] = int(row.delivery_days or 3)

        from backend.services.warehouse_district import warehouse_to_district

        # Универсум ключей — mode-инвариантный: wh_names_to_show зависит от режима
        # (actual = только сток-склады), а веса плеча идут по СЫРЫМ складам
        # отгрузки. Без объединения безсток-склад давал бы плечо в loc-режиме и
        # 0 в actual → total_need становился бы mode-вариантным (HIGH-2).
        # Лишние ключи безвредны: клетки/greedy ищут только wh_names_to_show.
        _lead_universe = set(wh_names_to_show) | {wh for (wh, _n) in raw_wh_orders_for_lead}
        for wh_name in _lead_universe:
            district = warehouse_to_district(wh_name)
            fallback_transport = FALLBACK_TRANSPORT_DAYS_PER_DISTRICT.get(district, 7)
            candidates: list[int] = []
            for ff in rf_warehouses:  # type: ignore[assignment]
                ff_id = ff.id  # type: ignore[attr-defined]
                assembly = int(ff.assembly_days or 3)  # type: ignore[attr-defined]
                acceptance = int(ff.wb_acceptance_days or 2)  # type: ignore[attr-defined]
                transport = per_pair.get(ff_id, {}).get(wh_name, fallback_transport)
                candidates.append(assembly + transport + acceptance)
            if candidates:
                lead_time_per_wb[wh_name] = min(candidates)
            else:
                lead_time_per_wb[wh_name] = 3 + fallback_transport + 2

    # ── Спрос-взвешенное плечо доставки per nm ──────────────────────────────
    # Пер-складские клетки давно считают горизонт supply+lead, а глобальный кап
    # (can_send/total_need) — только supply → структурное рационирование: к
    # приезду поставки часть покрытия уже съедена (дефект из аудита 2026-07-09).
    # Глобальный горизонт = supply_days + Σ(orders_wh × lead_wh)/Σorders_wh.
    # Веса — по СЫРОМУ складу отгрузки WB (raw_wh_orders_for_lead), а не по
    # mode-зависимой привязке: total_need mode-инвариантен (HIGH-2). Склады без
    # lead-записи (СЦ/зарубеж/не в матрице) веса не дают; совсем без весов → 0.
    _lead_num: dict[int, float] = {}
    _lead_den: dict[int, int] = {}
    for (_wh, _nm), _q in raw_wh_orders_for_lead.items():
        _lt = lead_time_per_wb.get(_wh)
        if _lt is None or _q <= 0:
            continue
        _lead_num[_nm] = _lead_num.get(_nm, 0.0) + _q * _lt
        _lead_den[_nm] = _lead_den.get(_nm, 0) + _q
    lead_by_nm: dict[int, float] = {
        nm: _lead_num[nm] / _lead_den[nm] for nm in _lead_num if _lead_den.get(nm)
    }

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
            # PRE_DISTRIBUTED — машина в пути: товар едет, заявка УЖЕ направлена на свой
            # WB-склад → уменьшает потребность ИМЕННО этого склада (как «в пути»). Берём
            # его ТОЛЬКО в WB-target-агрегат (ниже → transit_target_map), но НЕ в общий
            # active_statuses / in_assembly_per_wh — иначе зарезервируется не приехавший
            # ФФ-сток (фейк-резерв). См. критику C1 в .claude/PREDIST_DESIGN.md.
            AssemblyRequest.status.in_([*active_statuses, AssemblyStatus.SHIPPED, AssemblyStatus.PRE_DISTRIBUTED]),
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
        # PRE_DISTRIBUTED идёт в «в пути» (transit), как и SHIPPED: товар ещё не на ФФ,
        # но уже едет на свой WB-склад → вычитается из raw_need того склада (шаг 4).
        is_transit = row.status in (AssemblyStatus.SHIPPED, AssemblyStatus.PRE_DISTRIBUTED)
        bucket = transit_target_map if is_transit else assembly_target_map
        if nm not in bucket:
            bucket[nm] = {}
        bucket[nm][target] = bucket[nm].get(target, 0) + qty

    # 4. Build warehouse -> articles need map (per-warehouse need is post-allocation)
    for wh_name in wh_names_to_show:
        if wh_name not in wh_data:
            wh_data[wh_name] = {"name": wh_name, "total_need": 0, "articles": {}}

        for nm_id in all_nm_ids:
            stock = stock_lookup.get((wh_name, nm_id), 0)
            # Эффективный спрос (raw × g роста) — скорость для расчёта потребности.
            total_orders_at_wh = wh_orders_eff.get((wh_name, nm_id), 0.0)

            if total_orders_at_wh == 0 and stock == 0:
                continue

            avg_d = round(total_orders_at_wh / max(actual_days, 1), 2)
            # supply_days + lead_time: пока товар едет, продолжаются продажи и
            # съедают существующий WB-сток. Без lead_time планируется только
            # «после прибытия», и к этому моменту запас на WB уже исчерпан.
            effective_days = supply_days + lead_time_per_wb.get(wh_name, 0)
            raw_need = compute_need(stock, avg_d, effective_days)

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

        # Pool WB-stock surplus per district: если на каком-то складе ЦФО товара
        # больше чем local demand × (supply+lead) — этот surplus покрывает дефицит
        # других складов того же ФО через WB internal-rebalance (склады в ЦФО
        # рядом, доставка между ними быстрая).
        for wh_name in list(wh_data.keys()):
            d = warehouse_to_district(wh_name)
            for nm_id, art in wh_data[wh_name]["articles"].items():
                stock = stock_lookup.get((wh_name, nm_id), 0)
                avg_d_local = art.get("avg_daily", 0)
                eff_days = supply_days + lead_time_per_wb.get(wh_name, 0)
                demand_local = avg_d_local * eff_days
                local_surplus = stock - demand_local
                if local_surplus > 0:
                    district_pool[(d, nm_id)] = district_pool.get((d, nm_id), 0) + int(local_surplus)

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

    # 4.6. Only-available: жадный cap по can_send артикула. КАП РАСКЛАДКИ =
    # min(rf_avail, Σ ЛОКАЛЬНЫХ дефицитов клеток), а не нетто по сети
    # (avg×горизонт − ВЕСЬ WB-сток − сборка − транзит): излишек, лежащий в
    # чужом ФО, «компенсировал» дефицит Урала и раскладка рационировалась
    # (аудит 2026-07-14: 99 из 141 SKU резались; 120х160_серый — Σ локальных
    # дефицитов 237 при нетто-капе 94, потому что 533 шт стока лежат не там).
    # Канон юзера: у каждого ФО свой спрос — закрывать, если ФФ позволяет;
    # излишек чужого склада спрос ФО не обслуживает. Нетто-величина остаётся
    # ТОЛЬКО отчётным KPI «сколько докупить» (total_need, HIGH-2
    # mode-инвариантен). Бонус: кап и клетки теперь на ОДНОМ базисе (per-склад
    # lead, loc-привязка) → при достатке ФФ greedy наливает клетки полностью и
    # форма черновика == сырым клеткам машины (паритет двух экранов).
    if only_available:
        # can_send_per_nm = min(rf_avail, Σ need клеток nm)
        can_send_per_nm: dict[int, int] = {}
        cells_need_by_nm: dict[int, int] = {}
        for wh_name in list(wh_data.keys()):
            for _nm_c, _art_c in wh_data[wh_name]["articles"].items():
                cells_need_by_nm[_nm_c] = cells_need_by_nm.get(_nm_c, 0) + int(_art_c["need"] or 0)
        for nm_id in all_nm_ids:
            total_rf_stock_local = 0
            for wh in rf_warehouses:  # type: ignore[assignment]
                stock_qty = rf_stock_map.get(nm_id, {}).get(wh.id, 0)  # type: ignore[attr-defined]
                total_rf_stock_local += stock_qty
            in_asm_total_local = in_assembly_map.get(nm_id, 0)
            rf_avail_local = max(0, total_rf_stock_local - in_asm_total_local)
            can_send_per_nm[nm_id] = min(rf_avail_local, cells_need_by_nm.get(nm_id, 0))

        # Greedy «до целевой локализации» (по умолчанию 75%, kwarg
        # localization_target). Заменяет старый proportional-Hamilton (он
        # размазывал cap пропорционально и при leftover>headroom молча терял
        # штуки — HIGH-1). Теперь cap концентрируется на anchor-складах ровно
        # до target_pct локализации по ФО (с учётом сток+сборка+транзит),
        # остаток cap НЕ размазывается на дальние склады — остаётся на ФФ
        # («не перетаривать»). Единый авторитет логики — helper
        # localization_target.greedy_allocate_to_target.
        #
        # «Схема воришек» (_hamilton_priority_weight, модульный уровень —
        # формула и калибровка в его докстринге) сохранена: якорь ФО
        # (Шушары ≈1.75, Невинномысск ≈1.76) получает вес выше, а воришка —
        # склад в top-2 priority-chain города чужого ФО (`🐭` на
        # /warehouse/speed) — penalty 0.6 → заполняется в последнюю очередь.
        from backend.services.localization_target import greedy_allocate_to_target
        from backend.services.warehouse_district import warehouse_to_district as _wh_to_d
        for nm_id, cap_total in can_send_per_nm.items():
            # Склады nm с положительной остаточной потребностью (per-cell need).
            wbs = []
            for wh_name in list(wh_data.keys()):
                arts = wh_data[wh_name]["articles"]
                if nm_id in arts:
                    # Сырой (до greedy) остаточный дефицит клетки: веса для
                    # «Распределить все остатки» и паритет с сырыми клетками
                    # машины (need после greedy = налитое, не дефицит).
                    arts[nm_id]["need_raw"] = arts[nm_id]["need"]
                    if arts[nm_id]["need"] > 0:
                        wbs.append(wh_name)
            if not wbs or cap_total <= 0:
                for wh_name in list(wh_data.keys()):
                    arts = wh_data[wh_name]["articles"]
                    if nm_id in arts:
                        arts[nm_id]["need"] = 0
                continue

            # Входы helper'а — строим ПОФАЙЛОВО (на этот nm).
            #  wh_demand   — остаточная per-cell потребность склада (>0).
            #  wh_weight   — priority_weight (anchor↑, воришка↓) = «схема воришек».
            #  wh_okrug    — склад → канон-ключ ФО.
            #  demand_by_okrug   — валовый спрос ФО (база покрытия): сумма gross_wh.
            #                      gross_wh = avg_daily_склада × (supply + lead_wh) —
            #                      тот же горизонт, что у клеток и глобального капа.
            #  existing_by_okrug — уже есть/едет на ФО: WB-сток + в-сборке + в-пути.
            #  total_demand      — весь спрос nm (Σ gross_wh + unmapped_gross), вкл.
            #                      незалокализуемый СНГ → знаменатель локализации.
            #                      Горизонт unmapped = supply + lead_by_nm (то же
            #                      взвешенное плечо, что в глобальном капе и
            #                      total_need) — иначе СНГ-доля недовзвешена и
            #                      target_pct×total_demand занижает цель.
            wh_demand: dict[str, int] = {}
            wh_weight: dict[str, float] = {}
            wh_okrug: dict[str, str] = {}
            demand_by_okrug: dict[str, float] = {}
            existing_by_okrug: dict[str, float] = {}
            total_gross = 0.0
            # Спрос и покрытие ФО считаем по ВСЕМ складам с ячейкой nm, ВКЛЮЧАЯ
            # уже полностью покрытые (need==0): их WB-сток + в-сборке + в-пути тоже
            # локализуют округ. Иначе округ, где сток/транзит соседнего склада уже
            # закрыл спрос, выглядит менее покрытым → greedy доливает на оставшиеся
            # склады ФО сверх нужды (перетаривание округа, в т.ч. поверх уже едущего).
            for wh_name in list(wh_data.keys()):
                arts = wh_data[wh_name]["articles"]
                if nm_id not in arts:
                    continue
                okrug = _wh_to_d(wh_name)
                # Горизонт = supply+lead, как у клеток и глобального капа: база
                # покрытия без плеча рационировала greedy (клетки хотят больше,
                # чем «спрос ФО» позволяет налить).
                gross_wh = (wh_orders_eff.get((wh_name, nm_id), 0.0) / max(actual_days, 1)) * (
                    supply_days + lead_time_per_wb.get(wh_name, 0)
                )
                existing_wh = (
                    stock_lookup.get((wh_name, nm_id), 0)
                    + assembly_target_map.get(nm_id, {}).get(wh_name, 0)
                    + transit_target_map.get(nm_id, {}).get(wh_name, 0)
                )
                demand_by_okrug[okrug] = demand_by_okrug.get(okrug, 0.0) + gross_wh
                existing_by_okrug[okrug] = existing_by_okrug.get(okrug, 0.0) + existing_wh
                total_gross += gross_wh
                # Наливать cap можно ТОЛЬКО склады с остаточной потребностью (need>0);
                # покрытые склады лишь формируют картину локализации округа.
                need_wh = arts[nm_id]["need"]
                if need_wh > 0:
                    wh_demand[wh_name] = need_wh
                    wh_weight[wh_name] = _hamilton_priority_weight(wh_name)
                    wh_okrug[wh_name] = okrug

            # Сборка/транзит, направленные на склад БЕЗ клетки nm (сток 0 и
            # заказов 0 — типично сразу после коммита на пустой склад), тоже
            # покрывают свой ФО — иначе округ выглядит недо-покрытым и greedy
            # доливает поверх уже едущего (двойная отгрузка).
            for _src_map in (assembly_target_map.get(nm_id, {}), transit_target_map.get(nm_id, {})):
                for _twh, _tq in _src_map.items():
                    if (_tq or 0) <= 0:
                        continue
                    if nm_id in (wh_data.get(_twh) or {}).get("articles", {}):
                        continue  # у склада есть клетка — уже учтён выше
                    _tok = _wh_to_d(_twh)
                    existing_by_okrug[_tok] = existing_by_okrug.get(_tok, 0.0) + _tq

            unmapped_gross = (unmapped_eff.get(nm_id, 0.0) / max(actual_days, 1)) * (
                supply_days + lead_by_nm.get(nm_id, 0.0)
            )
            total_demand = total_gross + unmapped_gross

            allocated = greedy_allocate_to_target(
                cap_total,
                wh_demand,
                wh_weight,
                wh_okrug,
                demand_by_okrug,
                existing_by_okrug,
                total_demand,
                target_pct=float(localization_target),
            )
            for wh_name in wbs:
                wh_data[wh_name]["articles"][nm_id]["need"] = allocated.get(wh_name, 0)

        for wh_name in list(wh_data.keys()):
            wh_data[wh_name]["total_need"] = sum(a["need"] for a in wh_data[wh_name]["articles"].values())

    # 4.7. Min-stock per main warehouse: ensure top-1 склад каждого ФО имеет
    # минимум N шт (WB-stock + asm + transit + cell_alloc). Цель — разорвать
    # vicious-cycle «склад пуст → заказы туда не идут → bench не видит спрос
    # → алгоритм не предлагает» для дальних регионов (ДВФО, СФО), где WB
    # никогда не получит истории без bootstrap-загрузки.
    # Bump capped по ОСТАВШЕМУСЯ rf_avail (после Mode 1 cap).
    #
    # SKU-кратность коробки (Nomenclature.box_qty_override + use_box_multiplicity=True)
    # переопределяет min_stock per SKU: целевой минимум = одна коробка вместо N шт.
    # Bump-секция активируется если задан min_stock_per_main_warehouse > 0 ИЛИ есть
    # хотя бы один SKU с эффективной кратностью.
    # bootstrap_shape: включить bump и в СЫРУЮ форму (машина: only_available=false,
    # кап у неё клиентский по пулу) — иначе бутстрап пустых ФО есть только в
    # черновике и формы экранов расходятся (аудит паритета 2026-07-14).
    sku_ppb_map: dict[int, int] = {}
    if (only_available or bootstrap_shape) and all_nm_ids:
        ppb_result = await db.execute(
            select(Nomenclature.article_wb, Nomenclature.box_qty_override).where(
                Nomenclature.project_id == project_id,
                Nomenclature.article_wb.in_(all_nm_ids),
                Nomenclature.box_qty_override.isnot(None),
                Nomenclature.box_qty_override > 0,
                Nomenclature.use_box_multiplicity == True,  # noqa: E712
            )
        )
        for row in ppb_result:  # type: ignore[assignment]
            sku_ppb_map[row.article_wb] = int(row.box_qty_override)

    if (min_stock_per_main_warehouse > 0 or sku_ppb_map) and (only_available or bootstrap_shape):
        from collections import defaultdict

        from backend.services.warehouse_district import warehouse_to_district

        # Identify main warehouse per district (top-1 by total orders, not excluded)
        wh_total_orders: dict[str, int] = defaultdict(int)
        for (wh, _nm), qty in wh_orders_map.items():
            wh_total_orders[wh] += qty
        district_to_wh: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for wh, total in wh_total_orders.items():
            d = warehouse_to_district(wh)
            if d in ("abroad", "unknown"):
                continue
            district_to_wh[d].append((wh, total))
        main_per_district: dict[str, str] = {}
        for d, whs in district_to_wh.items():
            for wh, _ in sorted(whs, key=lambda x: -x[1]):
                main_per_district[d] = wh
                break
        # Детерминированный порядок обхода: при нехватке remaining_budget «кто
        # получит коробку» решал hash-порядок set. Приоритет — вес якоря
        # (воришки последними), тай-брейк по имени.
        main_whs = sorted(
            set(main_per_district.values()),
            key=lambda w: (-_hamilton_priority_weight(w), w),
        )

        # Apply bump per SKU per main warehouse
        for nm_id in all_nm_ids:
            bump_target = _bump_target_for_sku(sku_ppb_map.get(nm_id), min_stock_per_main_warehouse)
            if bump_target <= 0:
                continue
            total_rf_stock_local = sum(
                rf_stock_map.get(nm_id, {}).get(wh.id, 0)
                for wh in rf_warehouses  # type: ignore[attr-defined]
            )
            in_asm_total_local = in_assembly_map.get(nm_id, 0)
            rf_avail_local = max(0, total_rf_stock_local - in_asm_total_local)
            if only_available:
                already_allocated = can_send_per_nm.get(nm_id, 0)
                remaining_budget = max(0, rf_avail_local - already_allocated)
            else:
                # bootstrap_shape (машина): источник — пул машины, ФФ-бюджет не
                # применим (товар не на ФФ); кап наложит клиент min(pool, need).
                remaining_budget = 10**9
            if remaining_budget <= 0:
                continue

            # Покрытие ФО для этого SKU (сток + в-сборке + в-пути + уже распределённое
            # greedy). Бампим главный склад ФО ТОЛЬКО если весь ФО ещё НЕ насеян
            # (< одной коробки) — это бутстрап пустых дальних регионов. Если ФО уже
            # покрыт (стоком/транзитом на ЛЮБОМ его складе), коробка на главный =
            # перетаривание уже локализованного округа (см. /review MEDIUM + кейс юзера
            # «доливаем в Невинномысск/ЕКБ поверх уже едущего»).
            okrug_cov: dict[str, float] = {}
            okrug_demand: dict[str, float] = {}
            for _wh, _wd in wh_data.items():
                _c = _wd["articles"].get(nm_id)
                _d = warehouse_to_district(_wh)
                okrug_cov[_d] = okrug_cov.get(_d, 0.0) + (
                    stock_lookup.get((_wh, nm_id), 0)
                    + assembly_target_map.get(nm_id, {}).get(_wh, 0)
                    + transit_target_map.get(nm_id, {}).get(_wh, 0)
                    + (_c["need"] if _c else 0)
                )
                if _c:
                    okrug_demand[_d] = okrug_demand.get(_d, 0.0) + (_c.get("avg_daily", 0) or 0) * supply_days

            for wh_name in main_whs:
                if wh_name not in wh_data:
                    continue
                _md = warehouse_to_district(wh_name)
                _cov = okrug_cov.get(_md, 0.0)
                # Бампим ФО ТОЛЬКО если он пустой (cold-start bootstrap) ИЛИ недо-покрыт
                # относительно своего спроса. ФО, где сток/транзит уже ≥ коробки ИЛИ
                # ≥ спроса, НЕ трогаем — иначе перетаривание уже локализованного округа.
                if _cov >= bump_target or (_cov > 0 and _cov >= okrug_demand.get(_md, 0.0)):
                    continue
                arts = wh_data[wh_name]["articles"]
                if nm_id not in arts:
                    # Создаём cell если SKU не имеет current entry на этом складе
                    arts[nm_id] = {
                        "nm_id": nm_id,
                        "vendor_code": vendor_map.get(nm_id, f"#{nm_id}"),
                        "stock": stock_lookup.get((wh_name, nm_id), 0),
                        "avg_daily": 0,
                        "need": 0,
                    }
                art = arts[nm_id]
                current_at_wh = (
                    stock_lookup.get((wh_name, nm_id), 0)
                    + assembly_target_map.get(nm_id, {}).get(wh_name, 0)
                    + transit_target_map.get(nm_id, {}).get(wh_name, 0)
                    + art["need"]
                )
                if current_at_wh < bump_target:
                    bump = min(bump_target - current_at_wh, remaining_budget)
                    if bump > 0:
                        art["need"] += bump
                        remaining_budget -= bump
                if remaining_budget <= 0:
                    break

        # Re-sum per warehouse totals
        for wh_name in list(wh_data.keys()):
            wh_data[wh_name]["total_need"] = sum(a["need"] for a in wh_data[wh_name]["articles"].values())

    warehouses: list[dict] = sorted(wh_data.values(), key=lambda w: w["total_need"], reverse=True)
    # Аннотируем округом для UI — фронт показывает label под названием склада
    # (как в индексе локализации). 'unknown' для складов вне справочника.
    # priority_weight — вес «схемы воришек» (якорь↑/воришка↓): клиентский движок
    # машины режет дефицит пула в ТОМ ЖЕ порядке, что серверный greedy черновика
    # режет дефицит ФФ (паритет экранов, аудит 2026-07-14).
    for wh_entry in warehouses:
        wh_entry["district_key"] = warehouse_to_district(wh_entry["name"])
        wh_entry["priority_weight"] = round(_hamilton_priority_weight(wh_entry["name"]), 4)

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

    # Раскладочная потребность per nm = Σ ЛОКАЛЬНЫХ дефицитов клеток (до greedy;
    # в only_available клетки перезаписаны налитым — берём need_raw). Она кормит
    # can_send/deficit и колонку «Потр. 14д» матрицы; нетто-величина (total_need)
    # остаётся KPI «сколько докупить» (HIGH-2 mode-инвариантен).
    ship_need_by_nm: dict[int, int] = {}
    for _wh_sn in wh_data.values():
        for _nm_sn, _art_sn in _wh_sn["articles"].items():
            # max(need, need_raw): при дефиците ФФ greedy наливает МЕНЬШЕ raw —
            # потребность остаётся raw; bump 4.7 поднимает need ВЫШЕ raw
            # (бутстрап-короб) — эти штуки тоже потребность.
            _raw_sn = max(int(_art_sn["need"] or 0), int(_art_sn.get("need_raw", 0) or 0))
            ship_need_by_nm[_nm_sn] = ship_need_by_nm.get(_nm_sn, 0) + _raw_sn

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

        # total_need = ОБЩАЯ потребность артикула на (supply_days + плечо) вперёд
        # минус ВСЕ доступные источники (любой WB-склад, сборка, транзит). Это
        # отличается от sum(per-WB needs) тем, что профицит на одном WB-складе
        # компенсирует дефицит на другом — для KPI «сколько ещё закупить».
        # Per-cell needs в матрице остаются per-WB-локализованными (показ
        # идеальной раскладки независимо от общего профицита).
        # Скорость — growth-aware (raw × g): среднее за окно занижает потребность
        # растущих SKU; горизонт включает плечо — пока товар едет, сток съедается.
        raw_orders_for_nm = raw_total_by_nm.get(nm_id, 0)
        # HIGH-2: непривязанный (СНГ) спрос — часть общей потребности артикула
        # (procurement-KPI считает все заказы, а не только локализуемые);
        # raw_total_by_nm уже включает unmapped.
        growth = growth_by_nm.get(nm_id, 1.0)
        avg_daily_base = raw_orders_for_nm / max(actual_days, 1)
        total_avg_daily = avg_daily_base * growth
        lead_days = lead_by_nm.get(nm_id, 0.0)
        total_wb_stock = wb_stock_total.get(nm_id, 0)
        gross_demand = total_avg_daily * (supply_days + lead_days)
        total_need = max(0, round(gross_demand - total_wb_stock - in_asm_total - in_transit_qty))
        # «Могу отправить» — по РАСКЛАДОЧНОЙ потребности (Σ локальных дефицитов
        # клеток), консистентно с капом greedy 4.6; нетто total_need — KPI закупки.
        ship_need = ship_need_by_nm.get(nm_id, 0)
        can_send = min(total_rf_available, ship_need)
        deficit = max(0, ship_need - can_send)

        stocks_wb = wb_stock_total.get(nm_id, 0)
        # «На сколько дней хватит остатка на WB» при рабочей (growth-aware)
        # скорости; incl_inbound — вместе со сборкой и уже едущим.
        wb_days_left = round(stocks_wb / total_avg_daily, 1) if total_avg_daily > 0 else None
        wb_days_left_inbound = (
            round((stocks_wb + in_asm_total + in_transit_qty) / total_avg_daily, 1)
            if total_avg_daily > 0
            else None
        )

        enriched_articles.append(
            {
                "nm_id": nm_id,
                "vendor_code": vendor_map.get(nm_id, f"#{nm_id}"),
                "barcode": barcode_map.get(nm_id, ""),
                "brand": brand_map.get(nm_id, ""),
                "subject": subject_map.get(nm_id, ""),
                "total_need": total_need,
                # Раскладочная потребность: Σ локальных дефицитов складов (без
                # нетто-компенсации чужими излишками и без СНГ) — колонка
                # «Потр. 14д» матрицы и база can_send/deficit.
                "ship_need": ship_need,
                "revenue_30d": revenue_map.get(nm_id, 0),
                "rf_stocks": rf_stocks_for_nm,
                "in_assembly": in_asm_total,
                "in_transit": in_transit_qty,
                "in_transit_date": in_transit_date,
                "can_send": can_send,
                "deficit": deficit,
                "stocks_wb": stocks_wb,
                # Growth-aware скорость: base — плоское среднее за окно, eff —
                # рабочая (max из окна/7д/3д), growth_ratio = eff/base.
                "avg_daily_base": round(avg_daily_base, 2),
                "eff_avg_daily": round(total_avg_daily, 2),
                "growth_ratio": round(growth, 2),
                # Спрос-взвешенное плечо доставки (сборка+дорога+приёмка), дни.
                "lead_days": round(lead_days, 1),
                # Остатка на WB хватит на N дней (по eff-скорости); inbound — с
                # учётом «в сборке» и «в пути».
                "wb_days_left": wb_days_left,
                "wb_days_left_inbound": wb_days_left_inbound,
                # Per-(nm, wh) asm/transit нужны фронту для client-side bump
                # («Дораспределить»): чтобы не дополнять склад на 5шт если туда
                # уже едет 5шт транзитом — иначе двойной bump.
                # Закрытые/исключённые склады из карт вырезаем: фронт весит по
                # ним «Распределить все остатки» (presence-фолбэк) — остаток
                # уезжал бы на склад, куда приёмка заявку не пропустит. Для
                # ПОКРЫТИЯ ФО (greedy 4.6) карты выше используются полными.
                "asm_by_warehouse": {
                    wh: q for wh, q in assembly_target_map.get(nm_id, {}).items()
                    if wh not in excluded_set and wh not in acceptance_closed
                },
                "transit_by_warehouse": {
                    wh: q for wh, q in transit_target_map.get(nm_id, {}).items()
                    if wh not in excluded_set and wh not in acceptance_closed
                },
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
            # HIGH-2: суммарный непривязанный (зарубеж/СНГ) спрос за окно анализа.
            # Эти штуки учтены в total_need, но НЕ распределены по WB-складам.
            "unmapped_demand_qty": sum(unmapped_demand.values()),
            # Целевая локализация (%), до которой велось распределение (only_available).
            "localization_target": localization_target,
        },
    }
