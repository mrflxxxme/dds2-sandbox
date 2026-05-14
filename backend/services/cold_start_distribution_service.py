# ruff: noqa: RUF002, RUF003
"""Cold-start distribution — распределение SKU по WB-складам без своей истории.

MVP "холодного старта": когда у проекта мало (или нет) собственных заказов
по артикулу, прикидываем оптимальное распределение по WB-складам через
bench соседнего проекта-донора (--bench-from в симуляторе) или общероссийский
WB-фолбэк по долям ФО.

Алгоритм (портирован из scripts/cold_start_simulation.py):
1. Бенчмарк по ФО = доля заказов в окне `window_days`. Если своих заказов
   < MIN_ORDERS_FOR_OWN_BENCH — fallback на bench соседнего проекта или
   статичные WB-доли FALLBACK_DISTRICT_SHARE.
2. Для каждого ФО — главный склад = top-1 по orders в bench-проекте,
   не из excluded.
3. Распределяем total_qty по ФО → главному складу ФО (skip abroad/unknown).
4. Min-pack: ФО с qty < min_pack пулятся в крупнейший ФО.
5. Вычитаем уже идущие сборки/транзит на этот склад (active assemblies).

Read-only: никаких записей в БД.
"""

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.warehouse import Warehouse, WarehouseType
from backend.schemas.cold_start import (
    AllocationItem,
    ColdStartRfWarehouse,
    ColdStartTableResponse,
    ColdStartTableRow,
    DistributeMeta,
    DistributeRequest,
    DistributeResponse,
    SkuInfo,
)
from backend.services.settings_service import get_excluded_warehouses
from backend.services.warehouse_district import (
    DISTRICT_LABELS,
    DISTRICT_ORDER,
    WAREHOUSE_TO_DISTRICT,
    okrug_to_district,
)
from backend.services.warehouse_geo_data import ACCEPTANCE_TO_STOCK_NAME

if TYPE_CHECKING:
    pass

logger = logging.getLogger("dds.cold_start_distribution")

# ─── Константы (взяты из scripts/cold_start_simulation.py) ──────────────────

MIN_ORDERS_FOR_OWN_BENCH: int = 100  # ниже этого — fallback

# Общероссийский WB-фолбэк (примерные доли заказов по ФО, если у проекта
# совсем нет истории). Числа из scripts/cold_start_simulation.py.
FALLBACK_DISTRICT_SHARE: dict[str, float] = {
    "central": 0.30,
    "volga": 0.16,
    "south_caucasus": 0.18,
    "far_east_siberia": 0.13,
    "northwest": 0.09,
    "ural": 0.09,
    "abroad": 0.05,
}

# Дефолтные центры ФО на случай отсутствия трафика по складам в bench-источнике.
_DEFAULT_MAIN_WAREHOUSES: dict[str, str] = {
    "central": "Электросталь",
    "volga": "Казань",
    "south_caucasus": "Краснодар",
    "far_east_siberia": "Новосибирск",
    "northwest": "СПБ Шушары",
    "ural": "Екатеринбург - Перспективная 14",
}

_SKIP_DISTRICTS_DEFAULT: frozenset[str] = frozenset({"abroad", "unknown"})


# ─── Async DB-fetchers (parametrized SQL, project_id-фильтр везде) ─────────


async def fetch_district_share(db: AsyncSession, project_id: int, window_days: int) -> tuple[dict[str, float], int]:
    """Возвращает (доли заказов по ФО, всего заказов за окно).

    Группирует wb_orders за последние N дней по oblast_okrug_name+country_name,
    нормализует через okrug_to_district() в каноничные ключи ФО.
    """
    sql = text(
        """
        SELECT oblast_okrug_name, country_name, COUNT(*) AS cnt
        FROM wb_orders
        WHERE project_id = :project_id
          AND order_date >= CURRENT_DATE - make_interval(days => :window_days)
          AND is_cancel = false
        GROUP BY oblast_okrug_name, country_name
        """
    )
    result = await db.execute(sql, {"project_id": project_id, "window_days": int(window_days)})
    by_district: dict[str, int] = defaultdict(int)
    total = 0
    for row in result.mappings():
        district = okrug_to_district(row["oblast_okrug_name"], row["country_name"])
        by_district[district] += row["cnt"]
        total += row["cnt"]
    if total == 0:
        return {}, 0
    return {k: v / total for k, v in by_district.items()}, total


async def fetch_warehouse_traffic(db: AsyncSession, project_id: int, window_days: int) -> dict[str, int]:
    """Заказы за окно по каждому WB-складу-источнику для bench-проекта."""
    sql = text(
        """
        SELECT warehouse_name, COUNT(*) AS cnt
        FROM wb_orders
        WHERE project_id = :project_id
          AND order_date >= CURRENT_DATE - make_interval(days => :window_days)
          AND is_cancel = false
          AND warehouse_name IS NOT NULL
        GROUP BY warehouse_name
        """
    )
    result = await db.execute(sql, {"project_id": project_id, "window_days": int(window_days)})
    return {row["warehouse_name"]: row["cnt"] for row in result.mappings()}


async def fetch_sku(db: AsyncSession, project_id: int, nm_id: int) -> dict | None:
    """Возвращает информацию по SKU (article_seller, brand, rf_qty, wb_qty).

    rf_qty — суммарный остаток на ФФ (warehouse_stock).
    wb_qty — текущий остаток на всех WB-складах (wb_warehouse_stocks).
    """
    sql = text(
        """
        SELECT n.id AS internal_id,
               n.article_wb AS nm_id,
               n.article_seller,
               n.subject,
               n.brand,
               COALESCE(
                 (SELECT SUM(ws.quantity)
                  FROM warehouse_stock ws
                  WHERE ws.nomenclature_id = n.id AND ws.project_id = :project_id),
                 0
               ) AS rf_qty,
               COALESCE(
                 (SELECT SUM(wws.quantity)
                  FROM wb_warehouse_stocks wws
                  WHERE wws.nm_id = n.article_wb AND wws.project_id = :project_id
                    AND wws.quantity > 0),
                 0
               ) AS wb_qty
        FROM nomenclature n
        WHERE n.project_id = :project_id AND n.article_wb = :nm_id
        LIMIT 1
        """
    )
    result = await db.execute(sql, {"project_id": project_id, "nm_id": nm_id})
    row = result.mappings().first()
    return dict(row) if row else None


async def fetch_rf_warehouses(db: AsyncSession, project_id: int) -> list[ColdStartRfWarehouse]:
    """Активные ФФ-склады проекта (id+name), отсортированные по sort_order."""
    result = await db.execute(
        select(Warehouse.id, Warehouse.name)
        .where(
            Warehouse.project_id == project_id,
            Warehouse.warehouse_type == WarehouseType.FULFILLMENT,
            Warehouse.is_deleted.is_(False),
            Warehouse.is_active.is_(True),
        )
        .order_by(Warehouse.sort_order)
    )
    return [ColdStartRfWarehouse(id=int(r.id), name=str(r.name)) for r in result]


def _canonicalize_asm_warehouse(name: str) -> str:
    """Нормализация имени target-склада сборки к каноничной форме bench-traffic.

    `assembly_requests.wb_warehouse_name_manual` хранит то что выбрал юзер
    («Краснодар (Тихорецкая)», «Новосемейкино», «Склад Шушары»). `wb_orders.warehouse_name`
    приходит уже каноничным от WB API («Краснодар», «Самара (Новосемейкино)», «СПБ Шушары»).
    Без маппинга per-warehouse subtraction (`max(0, alloc - asm[wh])`) промахивается.
    """
    return ACCEPTANCE_TO_STOCK_NAME.get(name, name)


async def fetch_active_assemblies_for_sku(db: AsyncSession, project_id: int, nm_id: int) -> dict[str, int]:
    """Сколько уже едет/собрано в каждый WB-склад для этого SKU.

    Учитываются только активные сборки (PENDING/READY/SHIPPED/VEHICLE_ASSIGNED/IN_PROGRESS).
    Iron rule: assembly_requests — SoftDeleteMixin → фильтр is_deleted = false.
    Ключи result — каноничные имена (см. `_canonicalize_asm_warehouse`).
    """
    sql = text(
        """
        SELECT COALESCE(ar.wb_warehouse_name_manual, '?') AS wb_target,
               SUM(ari.quantity) AS qty
        FROM assembly_request_items ari
        JOIN assembly_requests ar ON ar.id = ari.assembly_request_id
        WHERE ari.project_id = :project_id
          AND ari.nomenclature_id = :nm_id
          AND ar.is_deleted = false
          AND ar.status IN ('PENDING', 'READY', 'SHIPPED', 'VEHICLE_ASSIGNED', 'IN_PROGRESS')
        GROUP BY ar.wb_warehouse_name_manual
        """
    )
    result = await db.execute(sql, {"project_id": project_id, "nm_id": nm_id})
    out: dict[str, int] = {}
    for row in result.mappings():
        raw = row["wb_target"]
        if raw == "?":
            continue
        canon = _canonicalize_asm_warehouse(raw)
        out[canon] = out.get(canon, 0) + int(row["qty"])
    return out


# ─── Pure logic (sync, безопасно тестировать без БД) ───────────────────────


def _sort_key_traffic_then_priority(district: str) -> Callable[[tuple[str, int]], tuple[int, float, str]]:
    """Composite-ключ сортировки складов внутри ФО.

    Primary: трафик в проекте (desc) — куда WB реально вёз.
    Secondary: priority_score в speed-карте POSTAVLENO (desc) — при близком
        трафике anchor speed-карты побеждает stealer.

    Tiebreak на этом уровне = небольшое preference anchor'ам когда история
    проекта неоднозначна (например, у двух складов ПФО по 100 заказов —
    лидер в speed-карте получает приоритет).
    """
    from backend.services.warehouse_speed import get_priority_score

    def key(item: tuple[str, int]) -> tuple[int, float, str]:
        wh, traffic = item
        score = get_priority_score(wh, district)
        return (-traffic, -score, wh)  # asc по всем → traffic desc, score desc, name asc

    return key


def pick_main_warehouse_per_district(warehouse_traffic: dict[str, int], excluded: set[str]) -> dict[str, str]:
    """{district → главный склад этого ФО, не из excluded}.

    Главный = top-1 по composite ключу (traffic desc, priority_score desc).
    Если все склады ФО исключены — ФО не попадает в результат.
    """
    by_district: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for wh, cnt in warehouse_traffic.items():
        district = WAREHOUSE_TO_DISTRICT.get(wh)
        if district is None:
            continue  # склад не в эталонном справочнике
        by_district[district].append((wh, cnt))
    result: dict[str, str] = {}
    for district, items in by_district.items():
        items.sort(key=_sort_key_traffic_then_priority(district))
        for wh, _ in items:
            if wh not in excluded:
                result[district] = wh
                break
    return result


def pick_warehouses_per_district(
    warehouse_traffic: dict[str, int],
    excluded: set[str],
    top_n: int | None = 3,
) -> dict[str, list[tuple[str, int]]]:
    """{district → [(warehouse, traffic), ...]} top-N складов округа, не из excluded.

    Сортировка composite: traffic desc, priority_score speed-карты desc, name asc.
    top_n=None — все склады округа.
    """
    by_district: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for wh, cnt in warehouse_traffic.items():
        if wh in excluded:
            continue
        district = WAREHOUSE_TO_DISTRICT.get(wh)
        if district is None:
            continue
        by_district[district].append((wh, cnt))
    result: dict[str, list[tuple[str, int]]] = {}
    for district, items in by_district.items():
        items.sort(key=_sort_key_traffic_then_priority(district))
        result[district] = items[:top_n] if top_n else items
    return result


def distribute(
    total_qty: int,
    district_share: dict[str, float],
    min_pack: int,
    main_wh_per_district: dict[str, str],
    skip_districts: set[str] | None = None,
) -> dict[str, int]:
    """Главное: разнести total_qty по {warehouse → qty}.

    Шаги:
    1. Сырое распределение по ФО (округление вниз), остаток крупнейшему ФО.
    2. Pool: ФО с qty < min_pack отдают свои штуки крупнейшему ФО.
    3. ФО → главный склад ФО (если склад не назначен — pool в крупнейший
       доступный).

    Returns: {warehouse_name: quantity}.
    """
    skip = skip_districts if skip_districts is not None else set(_SKIP_DISTRICTS_DEFAULT)
    if total_qty <= 0:
        return {}

    # 1. Сырое распределение по ФО (округление вниз)
    raw: dict[str, int] = {}
    for district, share in district_share.items():
        if district in skip:
            continue
        raw[district] = int(total_qty * share)
    if not raw:
        return {}

    # Раздаём остаток (от округлений) крупнейшему ФО
    leftover = total_qty - sum(raw.values())
    if leftover > 0:
        biggest = max(raw, key=lambda k: district_share.get(k, 0))
        raw[biggest] += leftover

    # 2. Pool: ФО с qty < min_pack → отдают крупнейшему
    pooled: dict[str, int] = {}
    pool_sum = 0
    biggest = max(raw, key=lambda k: district_share.get(k, 0))
    for district, qty in raw.items():
        if qty < min_pack and district != biggest:
            pool_sum += qty
        else:
            pooled[district] = qty
    if pool_sum > 0:
        pooled[biggest] = pooled.get(biggest, 0) + pool_sum

    # 3. ФО → главный склад ФО
    by_warehouse: dict[str, int] = {}
    for district, qty in pooled.items():
        wh = main_wh_per_district.get(district)
        if not wh:
            # Нет главного склада в этом ФО (все исключены или нет в bench).
            # Pool в крупнейший ФО, у которого склад есть.
            for fallback_d in sorted(pooled, key=lambda k: -district_share.get(k, 0)):
                fallback_wh = main_wh_per_district.get(fallback_d)
                if fallback_wh:
                    by_warehouse[fallback_wh] = by_warehouse.get(fallback_wh, 0) + qty
                    break
        else:
            by_warehouse[wh] = by_warehouse.get(wh, 0) + qty
    return by_warehouse


def distribute_multi(
    total_qty: int,
    district_share: dict[str, float],
    min_pack: int,
    wh_per_district: dict[str, list[tuple[str, int]]],
    skip_districts: set[str] | None = None,
) -> dict[str, int]:
    """Как distribute(), но qty внутри округа делится между несколькими складами.

    Шаги:
    1. Сырое распределение по ФО (округление вниз), остаток крупнейшему ФО.
    2. Bump-up: ФО с 0 < qty < min_pack поднимаются до min_pack за счёт
       крупнейшего ФО (но biggest не должен упасть ниже min_pack). Это даёт
       равномерное географическое покрытие — мелкие ФО тоже получают партию.
    3. Внутри округа qty делится между складами пропорционально их трафику;
       остаток от округлений — крупнейшему складу округа.
       Если в округе qty достаточно (>= min_pack × N_складов) — малые склады
       тоже bump-ятся до min_pack за счёт biggest_wh; иначе всё в biggest_wh.
    4. Если в округе нет складов в bench — pool в крупнейший склад крупнейшего ФО.
    """
    skip = skip_districts if skip_districts is not None else set(_SKIP_DISTRICTS_DEFAULT)
    if total_qty <= 0:
        return {}

    raw: dict[str, int] = {}
    for district, share in district_share.items():
        if district in skip:
            continue
        raw[district] = int(total_qty * share)
    if not raw:
        return {}
    leftover = total_qty - sum(raw.values())
    if leftover > 0:
        biggest = max(raw, key=lambda k: district_share.get(k, 0))
        raw[biggest] += leftover

    # Bump-up: округа с qty < min_pack поднимаем до min_pack за счёт biggest.
    # Идём в порядке убывания доли — крупные ФО bump-аются первыми (приоритет
    # географического покрытия). Стоп когда biggest не может отдать (упадёт
    # ниже min_pack). Округа с qty=0 (доля × total < 1) — пропускаем (нет
    # «адреса» откуда брать товар; они и так пустые).
    biggest_d = max(raw, key=lambda k: district_share.get(k, 0))
    for d in sorted(raw.keys(), key=lambda k: -district_share.get(k, 0)):
        if d == biggest_d:
            continue
        if 0 < raw[d] < min_pack:
            need = min_pack - raw[d]
            if raw[biggest_d] - need >= min_pack:
                raw[biggest_d] -= need
                raw[d] = min_pack

    by_warehouse: dict[str, int] = {}
    for district, qty in raw.items():
        if qty <= 0:
            continue
        warehouses = wh_per_district.get(district)
        if not warehouses:
            for fallback_d in sorted(raw, key=lambda k: -district_share.get(k, 0)):
                fb_whs = wh_per_district.get(fallback_d)
                if fb_whs:
                    fb_wh = fb_whs[0][0]
                    by_warehouse[fb_wh] = by_warehouse.get(fb_wh, 0) + qty
                    break
            continue
        biggest_wh = warehouses[0][0]
        total_wh_traffic = sum(t for _, t in warehouses) or 1
        wh_raw: dict[str, int] = {}
        for wh, traffic in warehouses:
            wh_raw[wh] = int(qty * traffic / total_wh_traffic)
        wh_leftover = qty - sum(wh_raw.values())
        if wh_leftover > 0:
            wh_raw[biggest_wh] = wh_raw.get(biggest_wh, 0) + wh_leftover

        # Складов внутри округа bump-аем до min_pack по той же схеме,
        # но только если суммарного qty достаточно: иначе всё в biggest_wh.
        if qty >= min_pack * len(warehouses):
            for wh in sorted(wh_raw.keys(), key=lambda w: -dict(warehouses).get(w, 0)):
                if wh == biggest_wh:
                    continue
                if 0 < wh_raw[wh] < min_pack:
                    need = min_pack - wh_raw[wh]
                    if wh_raw[biggest_wh] - need >= min_pack:
                        wh_raw[biggest_wh] -= need
                        wh_raw[wh] = min_pack
        else:
            # Не хватает на bump — все мелкие → biggest_wh
            wh_pool = 0
            for wh, q in list(wh_raw.items()):
                if q < min_pack and wh != biggest_wh:
                    wh_pool += q
                    wh_raw[wh] = 0
            if wh_pool > 0:
                wh_raw[biggest_wh] = wh_raw.get(biggest_wh, 0) + wh_pool

        for wh, q in wh_raw.items():
            if q > 0:
                by_warehouse[wh] = by_warehouse.get(wh, 0) + q
    return by_warehouse


# ─── Главная функция: compute_distribution ─────────────────────────────────


async def compute_distribution(db: AsyncSession, project_id: int, req: DistributeRequest) -> DistributeResponse:
    """Cold-start: рассчитать план распределения SKU по WB-складам.

    1. Загружаем excluded warehouses через settings_service.
    2. Загружаем bench-доли по ФО: свои → соседний проект → WB-фолбэк.
    3. Для bench-источника считаем трафик по складам, выбираем главный
       склад в каждом ФО (не из excluded).
    4. Загружаем SKU-метаданные и активные сборки.
    5. Распределяем total_qty (или весь rf_qty, если total_qty=None).
    6. Вычитаем уже идущие сборки на каждый склад.
    """
    # 1. SKU + остатки
    sku = await fetch_sku(db, project_id, req.nm_id)
    if not sku:
        # Возвращаем пустой ответ — endpoint конвертирует в 404 через HTTPException.
        # Но lifecycle: caller пусть сам решает. Здесь возвращаем минимальный ответ.
        # Чтобы endpoint мог вернуть 404 явно — кидаем исключение.
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=f"SKU nm_id={req.nm_id} not found in project")

    rf_qty = int(sku["rf_qty"])
    # active_asm нужен до total_qty: уже идущие штуки зарезервированы, повторно не распределяем.
    active_asm = await fetch_active_assemblies_for_sku(db, project_id, int(sku["internal_id"]))
    total_asm = sum(active_asm.values())
    total_qty = int(req.total_qty) if req.total_qty is not None else max(0, rf_qty - total_asm)

    # 2. Excluded warehouses (set для быстрой проверки)
    excluded_list = await get_excluded_warehouses(db, project_id)
    excluded: set[str] = {str(w).strip() for w in excluded_list}

    # 3. Бенчмарк по ФО
    own_share, own_total = await fetch_district_share(db, project_id, req.window_days)
    bench_source: str
    bench_share: dict[str, float]
    bench_wh_traffic: dict[str, int]
    bench_total: int

    if own_total >= MIN_ORDERS_FOR_OWN_BENCH:
        bench_share = own_share
        bench_wh_traffic = await fetch_warehouse_traffic(db, project_id, req.window_days)
        bench_source = "own"
        bench_total = own_total
    elif req.bench_from_project_id:
        n_share, n_total = await fetch_district_share(db, req.bench_from_project_id, req.window_days)
        if n_total > 0:
            bench_share = n_share
            bench_wh_traffic = await fetch_warehouse_traffic(db, req.bench_from_project_id, req.window_days)
            bench_source = f"neighbor:{req.bench_from_project_id}"
            bench_total = n_total
        else:
            bench_share = FALLBACK_DISTRICT_SHARE
            bench_wh_traffic = {}
            bench_source = "wb_fallback"
            bench_total = 0
    else:
        bench_share = FALLBACK_DISTRICT_SHARE
        bench_wh_traffic = {}
        bench_source = "wb_fallback"
        bench_total = 0

    # 4. Главный склад в каждом ФО (по трафику bench-источника)
    if bench_wh_traffic:
        main_wh = pick_main_warehouse_per_district(bench_wh_traffic, excluded)
    else:
        # Без данных по складам — дефолтные центры ФО, исключив excluded.
        main_wh = {d: wh for d, wh in _DEFAULT_MAIN_WAREHOUSES.items() if wh not in excluded}

    # 6. Распределение и вычитание уже идущих сборок (active_asm уже загружены выше)
    allocation = distribute(total_qty, bench_share, req.min_pack, main_wh)

    # 7. Сборка ответа в каноничном порядке ФО
    allocations: list[AllocationItem] = []
    for district in DISTRICT_ORDER:
        wh = main_wh.get(district)
        if not wh:
            continue
        allocated = allocation.get(wh, 0)
        already = active_asm.get(wh, 0)
        if allocated == 0 and already == 0:
            continue
        to_send = max(0, allocated - already)
        allocations.append(
            AllocationItem(
                district_key=district,
                district_label=DISTRICT_LABELS.get(district, district),
                warehouse=wh,
                share_pct=round(bench_share.get(district, 0.0) * 100, 2),
                allocated=allocated,
                already_in_assembly=already,
                to_send=to_send,
            )
        )

    return DistributeResponse(
        sku=SkuInfo(
            nm_id=int(sku["nm_id"]),
            article_seller=sku.get("article_seller"),
            subject=sku.get("subject"),
            brand=sku.get("brand"),
            rf_qty=rf_qty,
            wb_qty=int(sku["wb_qty"]),
        ),
        total_qty=total_qty,
        bench_source=bench_source,
        bench_total_orders=bench_total,
        allocations=allocations,
        district_shares={k: round(v, 6) for k, v in bench_share.items()},
        meta=DistributeMeta(
            min_pack=req.min_pack,
            window_days=req.window_days,
            excluded_warehouses=sorted(excluded),
        ),
    )


# ─── Таблица cold-start: сегмент SKU + распределение для каждого ─────────


async def fetch_cold_start_segment(db: AsyncSession, project_id: int) -> list[dict]:
    """SKU-новинки с ФФ-остатком — для bootstrap-распределения по бенчмарку.

    Возвращает только SKU где:
      - есть ФФ-остаток (rf_qty > 0) — иначе распределять нечего, И
      - first_sale_date IS NULL ИЛИ first_sale_date >= today-14
        (т.е. нет статистики продаж, на которой можно строить локализацию)

    SKU "без продаж 14д" но с историей продаж старше 14д НЕ включаются —
    у них есть собственная история, для них работает обычная локализация.
    SKU только с WB-остатком (без ФФ) тоже НЕ включаются — товар уже на WB,
    распределять с ФФ нечего.
    """
    sql = text(
        """
        SELECT n.id AS internal_id,
               n.article_wb AS nm_id,
               n.article_seller,
               n.subject,
               n.brand,
               n.barcode,
               n.first_sale_date,
               COALESCE(
                 (SELECT SUM(ws.quantity) FROM warehouse_stock ws
                  WHERE ws.nomenclature_id = n.id AND ws.project_id = :project_id),
                 0
               ) AS rf_qty,
               COALESCE(
                 (SELECT jsonb_object_agg(ws.warehouse_id::text, ws.quantity)
                  FROM warehouse_stock ws
                  WHERE ws.nomenclature_id = n.id AND ws.project_id = :project_id
                    AND ws.quantity > 0),
                 '{}'::jsonb
               ) AS rf_by_wh,
               COALESCE(
                 (SELECT SUM(wws.quantity) FROM wb_warehouse_stocks wws
                  WHERE wws.nm_id = n.article_wb AND wws.project_id = :project_id
                    AND wws.quantity > 0),
                 0
               ) AS wb_qty,
               COALESCE(
                 (SELECT SUM(ari.quantity)
                  FROM assembly_request_items ari
                  JOIN assembly_requests ar ON ar.id = ari.assembly_request_id
                  WHERE ari.project_id = :project_id
                    AND ari.nomenclature_id = n.id
                    AND ar.is_deleted = false
                    AND ar.status IN ('PENDING','READY','SHIPPED','VEHICLE_ASSIGNED','IN_PROGRESS')),
                 0
               ) AS asm_qty,
               COALESCE(
                 (SELECT COUNT(*) FROM wb_orders wo
                  WHERE wo.nm_id = n.article_wb AND wo.project_id = :project_id
                    AND wo.is_cancel = false
                    AND wo.order_date >= CURRENT_DATE - make_interval(days => 14)),
                 0
               ) AS sales_14d,
               COALESCE(
                 (SELECT SUM(wo.price_with_disc) FROM wb_orders wo
                  WHERE wo.nm_id = n.article_wb AND wo.project_id = :project_id
                    AND wo.is_cancel = false
                    AND wo.order_date >= CURRENT_DATE - make_interval(days => 30)),
                 0
               ) AS revenue_30d
        FROM nomenclature n
        WHERE n.project_id = :project_id
        """
    )
    result = await db.execute(sql, {"project_id": project_id})
    rows = list(result.mappings())
    out: list[dict] = []
    for r in rows:
        rf = int(r["rf_qty"] or 0)
        wb = int(r["wb_qty"] or 0)
        asm = int(r["asm_qty"] or 0)
        sales = int(r["sales_14d"] or 0)
        if rf == 0:
            continue  # нет ФФ-остатка — нечего распределять
        first_sale = r["first_sale_date"]
        is_newcomer = first_sale is None
        if not is_newcomer:
            from datetime import date, timedelta

            if first_sale >= date.today() - timedelta(days=14):
                is_newcomer = True
        if not is_newcomer:
            continue  # есть история продаж — для bootstrap не нужен
        rf_by_wh_raw = r["rf_by_wh"] or {}
        # jsonb приходит как dict[str,int]; на всякий случай приводим типы.
        rf_by_warehouse: dict[int, int] = {int(k): int(v) for k, v in rf_by_wh_raw.items()}
        out.append(
            {
                "internal_id": int(r["internal_id"]),
                "nm_id": int(r["nm_id"]) if r["nm_id"] else 0,
                "article_seller": r["article_seller"],
                "subject": r["subject"],
                "brand": r["brand"],
                "barcode": r["barcode"] or "",
                "rf_qty": rf,
                "rf_by_warehouse": rf_by_warehouse,
                "wb_qty": wb,
                "asm_qty": asm,
                "sales_14d": sales,
                "revenue_30d": float(r["revenue_30d"] or 0),
                "is_newcomer": is_newcomer,
            }
        )
    return out


async def compute_cold_start_table(
    db: AsyncSession,
    project_id: int,
    window_days: int,
    min_pack: int,
    bench_from_project_id: int | None = None,
) -> ColdStartTableResponse:
    """Cold-start таблица: список SKU из сегмента + per-SKU allocation.

    Бенчмарк (доли по ФО + главный склад) считается ОДИН раз для всех SKU.
    Для каждого SKU делается distribute(rf_qty, ...) с тем же бенчмарком.
    """
    excluded_list = await get_excluded_warehouses(db, project_id)
    excluded: set[str] = {str(w).strip() for w in excluded_list}

    # bench: свои → сосед → fallback
    own_share, own_total = await fetch_district_share(db, project_id, window_days)
    if own_total >= MIN_ORDERS_FOR_OWN_BENCH:
        bench_share = own_share
        bench_wh_traffic = await fetch_warehouse_traffic(db, project_id, window_days)
        bench_source = "own"
        bench_total = own_total
    elif bench_from_project_id:
        n_share, n_total = await fetch_district_share(db, bench_from_project_id, window_days)
        if n_total > 0:
            bench_share = n_share
            bench_wh_traffic = await fetch_warehouse_traffic(db, bench_from_project_id, window_days)
            bench_source = f"neighbor:{bench_from_project_id}"
            bench_total = n_total
        else:
            bench_share = FALLBACK_DISTRICT_SHARE
            bench_wh_traffic = {}
            bench_source = "wb_fallback"
            bench_total = 0
    else:
        bench_share = FALLBACK_DISTRICT_SHARE
        bench_wh_traffic = {}
        bench_source = "wb_fallback"
        bench_total = 0

    if bench_wh_traffic:
        wh_per_district = pick_warehouses_per_district(bench_wh_traffic, excluded, top_n=3)
    else:
        wh_per_district = {d: [(wh, 1)] for d, wh in _DEFAULT_MAIN_WAREHOUSES.items() if wh not in excluded}

    # main_warehouses meta — все склады каждого ФО (top-3), РФ-округа (abroad/unknown скрыты)
    # share_pct = доля округа × доля склада в трафике округа
    main_wh_meta = []
    for d in DISTRICT_ORDER:
        if d in {"abroad", "unknown"}:
            continue
        warehouses = wh_per_district.get(d) or []
        if not warehouses:
            continue
        district_share_val = bench_share.get(d, 0.0)
        district_traffic_total = sum(t for _, t in warehouses) or 1
        for wh, traffic in warehouses:
            wh_share = (traffic / district_traffic_total) * district_share_val
            main_wh_meta.append(
                {
                    "district_key": d,
                    "district_label": DISTRICT_LABELS.get(d, d),
                    "warehouse": wh,
                    "share_pct": round(wh_share * 100, 2),
                }
            )

    # ФФ-склады проекта (id+name) — для разбивки rf_qty по локациям на UI.
    rf_warehouses = await fetch_rf_warehouses(db, project_id)

    # сегмент SKU
    segment = await fetch_cold_start_segment(db, project_id)

    rows: list[ColdStartTableRow] = []
    for sku in segment:
        # asm_by_warehouse нужен в обеих ветках: и для прозрачности «куда едет»,
        # и для per-warehouse subtraction в активной ветке распределения.
        active_asm = await fetch_active_assemblies_for_sku(db, project_id, sku["internal_id"])

        # ФФ-остаток минус уже идущие сборки — иначе размазываем «фантом» по складам где asm под другим именем (Новосемейкино vs Самара).
        total_qty = max(0, sku["rf_qty"] - sku["asm_qty"])
        if total_qty <= 0:
            rows.append(
                ColdStartTableRow(
                    nm_id=sku["nm_id"],
                    article_seller=sku["article_seller"],
                    subject=sku["subject"],
                    brand=sku["brand"],
                    barcode=sku.get("barcode") or None,
                    rf_qty=sku["rf_qty"],
                    rf_by_warehouse=sku.get("rf_by_warehouse", {}),
                    wb_qty=sku["wb_qty"],
                    in_assembly_total=sku["asm_qty"],
                    asm_by_warehouse=active_asm,
                    sales_14d=sku["sales_14d"],
                    revenue_30d=sku["revenue_30d"],
                    is_newcomer=sku["is_newcomer"],
                    allocations={},
                    total_allocated=0,
                )
            )
            continue

        allocation = distribute_multi(total_qty, bench_share, min_pack, wh_per_district)
        final_alloc: dict[str, int] = {}
        for wh, qty in allocation.items():
            asm = active_asm.get(wh, 0)
            final_alloc[wh] = max(0, qty - asm)

        rows.append(
            ColdStartTableRow(
                nm_id=sku["nm_id"],
                article_seller=sku["article_seller"],
                subject=sku["subject"],
                brand=sku["brand"],
                barcode=sku.get("barcode") or None,
                rf_qty=sku["rf_qty"],
                rf_by_warehouse=sku.get("rf_by_warehouse", {}),
                wb_qty=sku["wb_qty"],
                in_assembly_total=sku["asm_qty"],
                asm_by_warehouse=active_asm,
                sales_14d=sku["sales_14d"],
                revenue_30d=sku["revenue_30d"],
                is_newcomer=sku["is_newcomer"],
                allocations=final_alloc,
                total_allocated=sum(final_alloc.values()),
            )
        )

    return ColdStartTableResponse(
        rows=rows,
        main_warehouses=main_wh_meta,
        rf_warehouses=rf_warehouses,
        bench_source=bench_source,
        bench_total_orders=bench_total,
        meta=DistributeMeta(
            min_pack=min_pack,
            window_days=window_days,
            excluded_warehouses=sorted(excluded),
        ),
    )
