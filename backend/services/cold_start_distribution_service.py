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
from typing import TYPE_CHECKING, Any

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
from backend.services.warehouse_speed import get_anchors_for_okrug

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

# Гарантия СЗФО (аудит 2026-07-09, порог согласован с пользователем): при партии
# ≥ NW_GUARANTEE_MIN_PACKS × min_pack Северо-Западный ФО получает минимум min_pack
# за счёт крупнейшего ФО. Без неё доля СЗФО (~9%) на малых партиях давала floor→0
# (bump пропускает нулевые округа) либо пул «<min_pack → крупнейшему» — новинки
# системно не заезжали в СЗФО и его локализация не набиралась. Гарантию собирает
# caller по ИСХОДНОЙ bench-доле (ДО concentrate_share_to_target: концентрация
# может срезать СЗФО из долей целиком). Для коробочных SKU то же правило в коробах
# держит фронт (coldStartSeed.seedNewcomerWholeBoxes, NW_GUARANTEE_MIN_BOXES).
NW_GUARANTEE_MIN_PACKS = 4
NW_DISTRICT_KEY = "northwest"

# ДВ (Дальневосточный) снабжаем со склада максимум на эту долю заказов — дальше
# товар физически не довезти. Излишек доли ДВ перекидываем на Урал (Екатеринбург —
# ворота в Сибирь/ДВ: туда довозим, а WB дотягивает последнюю милю восточнее).
FAR_EAST_MAX_SHARE: float = 0.06

# Как делится излишек доли ДВ между складами-воришками (по speed-карте реально
# обслуживают ДВ: WB развозит ДВ-заказы с них). Сумма долей = 1.0. Ключ ДВ-якоря
# (Екатеринбург) — сколько остаётся на нём; остальное уводится на другие склады.
FAR_EAST_EXCESS_ANCHOR: str = "Екатеринбург - Перспективная 14"
FAR_EAST_EXCESS_ROUTING: dict[str, float] = {
    FAR_EAST_EXCESS_ANCHOR: 0.40,  # Екатеринбург
    "Электросталь": 0.35,
    "Сарапул": 0.25,
}


def _cap_far_east_share(bench_share: dict[str, float]) -> dict[str, float]:
    """ДВ-доля сверх FAR_EAST_MAX_SHARE перекидывается на Урал. Возвращает НОВЫЙ
    dict (не мутирует вход — bench_share может быть общим FALLBACK_DISTRICT_SHARE).
    Часть этой доли позже уходит на Электросталь на уровне складов — см.
    `_route_far_east_excess` (ФО-доля не умеет целиться в конкретный склад)."""
    fe = bench_share.get("far_east_siberia", 0.0)
    if fe <= FAR_EAST_MAX_SHARE:
        return bench_share
    capped = dict(bench_share)
    capped["far_east_siberia"] = FAR_EAST_MAX_SHARE
    capped["ural"] = capped.get("ural", 0.0) + (fe - FAR_EAST_MAX_SHARE)
    return capped


def concentrate_share_to_target(
    bench_share: dict[str, float],
    target_pct: int = 75,
    skip_districts: set[str] | None = None,
) -> dict[str, float]:
    """Сконцентрировать доли ФО «до целевой локализации» (по умолчанию 75%).

    Новинку (нет сигнала спроса) не размазываем тонким слоем по всем округам, а
    сидируем КОНЦЕНТРИРОВАННО: округа берутся в порядке убывания bench_share и
    включаются, пока кумулятивная доля (нормированная к ЛОКАЛИЗУЕМОЙ базе =
    сумме долей не-skip ФО) не пересечёт ``target_pct``. Округ, на котором кумулятив
    пересёк порог, — последний включённый; «хвост» дальних/мелких ФО сверх target
    обнуляется (seed остаётся на ФФ — «не перетаривать тонким слоем по дальним»).

    Доли включённых ФО пере-нормируются обратно к ИСХОДНОЙ сумме всех долей
    (вкл. skip и хвост) → масса хвоста вливается в топ, total раздачи сохраняется
    бит-в-бит (товар не теряется; downstream distribute берёт total_qty целиком).

    Это district-level зеркало `localization_target.greedy_allocate_to_target`
    (тот работает на уровне складов; здесь структура — доли по ФО, поэтому отдельная
    кумулятивно-share-до-target реализация В ТОМ ЖЕ духе). skip-ФО (abroad/unknown)
    не сидируемы и в базу target не входят, но и не зануляют общий total.

    Возвращает НОВЫЙ dict (не мутирует вход). target_pct ≥ 100 → возвращает копию
    без изменений (все локализуемые ФО остаются).
    """
    skip = skip_districts if skip_districts is not None else set(_SKIP_DISTRICTS_DEFAULT)
    if not bench_share:
        return {}

    total_all = sum(bench_share.values())  # база нормировки результата (вкл. skip/хвост)
    local_items = [(d, s) for d, s in bench_share.items() if d not in skip and s > 0]
    local_base = sum(s for _, s in local_items)
    if local_base <= 0 or total_all <= 0:
        return dict(bench_share)
    if target_pct >= 100:
        return dict(bench_share)

    target_frac = target_pct / 100.0
    # Округа по убыванию доли; tie-break по имени для детерминизма.
    ordered = sorted(local_items, key=lambda kv: (-kv[1], kv[0]))

    kept: list[str] = []
    cum = 0.0
    for d, s in ordered:
        kept.append(d)
        cum += s / local_base
        if cum >= target_frac:
            break  # округ, пересёкший порог, — последний включённый

    kept_base = sum(bench_share[d] for d in kept)
    if kept_base <= 0:
        return dict(bench_share)

    # Пере-нормируем доли включённых ФО к ИСХОДНОЙ сумме (масса хвоста+skip
    # вливается пропорционально) → Σ результата == Σ входа (total сохранён).
    scale = total_all / kept_base
    return {d: bench_share[d] * scale for d in kept}


def _route_far_east_excess(alloc: dict[str, int], excess_qty: int) -> None:
    """Распределить излишек ДВ по складам-воришкам (FAR_EAST_EXCESS_ROUTING).
    `_cap_far_east_share` кладёт весь излишек на Екатеринбург (anchor); здесь часть
    уводим на остальных получателей (Электросталь/Сарапул). Мутирует alloc на месте;
    total сохраняется (Екатеринбург теряет ровно столько, сколько получают другие)."""
    anchor = FAR_EAST_EXCESS_ANCHOR  # Екатеринбург — на нём сейчас весь излишек
    moves = {wh: round(share * excess_qty) for wh, share in FAR_EAST_EXCESS_ROUTING.items() if wh != anchor}
    move_out = sum(moves.values())
    if move_out <= 0 or alloc.get(anchor, 0) < move_out:
        return
    alloc[anchor] -= move_out
    for wh, qty in moves.items():
        if qty > 0:
            alloc[wh] = alloc.get(wh, 0) + qty


def _is_spec_warehouse(name: str) -> bool:
    """Спец/сортировочные склады — не для FBO cold-start (зеркало фронтового
    isSpecWarehouse). СЦ/СГТ/виртуальные/Питание/Горючее не должны быть anchor-
    кандидатами: фронт их прячет, иначе была бы скрытая аллокация."""
    if name.startswith("Виртуальный "):
        return True
    if name.startswith("СЦ "):
        return True
    if " СГТ" in name:
        return True
    if ": Питание" in name or ":Питание" in name:
        return True
    if ": Горючее" in name or ":Горючее" in name:
        return True
    return False


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
    guarantee_districts: set[str] | None = None,
) -> dict[str, int]:
    """Главное: разнести total_qty по {warehouse → qty}.

    Шаги:
    1. Сырое распределение по ФО (округление вниз), остаток крупнейшему ФО.
    2. Pool: ФО с qty < min_pack отдают свои штуки крупнейшему ФО.
    2b. Гарантия (guarantee_districts, обычно {northwest}): при партии
        ≥ NW_GUARANTEE_MIN_PACKS × min_pack непокрытый гарантированный ФО
        получает min_pack за счёт крупнейшего (см. NW_GUARANTEE_MIN_PACKS).
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

    # 2b. Гарантия СЗФО: концентрация/пул могли оставить гарантированный ФО без
    # штук — при достаточной партии выделяем min_pack от самого крупного
    # получателя (не роняя его ниже min_pack).
    if guarantee_districts and total_qty >= NW_GUARANTEE_MIN_PACKS * min_pack:
        for district in sorted(guarantee_districts):
            if district in skip or pooled.get(district, 0) > 0:
                continue
            donor = max(pooled, key=lambda k: pooled[k], default=None)
            if donor and donor != district and pooled[donor] - min_pack >= min_pack:
                pooled[donor] -= min_pack
                pooled[district] = min_pack

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
    guarantee_districts: set[str] | None = None,
) -> dict[str, int]:
    """Как distribute(), но qty внутри округа делится между несколькими складами.

    Шаги:
    1. Сырое распределение по ФО (округление вниз), остаток крупнейшему ФО.
    2. Bump-up: ФО с 0 < qty < min_pack поднимаются до min_pack за счёт
       крупнейшего ФО (но biggest не должен упасть ниже min_pack). Это даёт
       равномерное географическое покрытие — мелкие ФО тоже получают партию.
       Гарантированные ФО (guarantee_districts, обычно {northwest}) bump-аются
       и с qty=0 — при партии ≥ NW_GUARANTEE_MIN_PACKS × min_pack (floor и
       концентрация долей не оставляют СЗФО без новинок).
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

    # Гарантированные ФО участвуют в bump даже при raw=0 (и даже если их долю
    # срезала концентрация — тогда ФО нет в district_share и сортировка ниже
    # ставит его последним). Порог партии — как в distribute().
    guarantee: set[str] = {
        d
        for d in (guarantee_districts or ())
        if d not in skip and total_qty >= NW_GUARANTEE_MIN_PACKS * min_pack
    }
    for d in guarantee:
        raw.setdefault(d, 0)

    # Bump-up: округа с qty < min_pack поднимаем до min_pack за счёт biggest.
    # Идём в порядке убывания доли — крупные ФО bump-аются первыми (приоритет
    # географического покрытия). Стоп когда biggest не может отдать (упадёт
    # ниже min_pack). Округа с qty=0 (доля × total < 1) — пропускаем (нет
    # «адреса» откуда брать товар; они и так пустые), КРОМЕ гарантированных.
    biggest_d = max(raw, key=lambda k: district_share.get(k, 0))
    for d in sorted(raw.keys(), key=lambda k: -district_share.get(k, 0)):
        if d == biggest_d:
            continue
        if 0 < raw[d] < min_pack or (raw[d] == 0 and d in guarantee):
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


async def compute_distribution(
    db: AsyncSession,
    project_id: int,
    req: DistributeRequest,
    localization_target: int = 75,
) -> DistributeResponse:
    """Cold-start: рассчитать план распределения SKU по WB-складам.

    1. Загружаем excluded warehouses через settings_service.
    2. Загружаем bench-доли по ФО: свои → соседний проект → WB-фолбэк.
    3. Для bench-источника считаем трафик по складам, выбираем главный
       склад в каждом ФО (не из excluded).
    4. Загружаем SKU-метаданные и активные сборки.
    5. Распределяем total_qty (или весь rf_qty, если total_qty=None) — КОНЦЕНТРИРОВАННО
       до ``localization_target``% локализации (хвост дальних ФО не сидируется).
    6. Вычитаем уже идущие сборки на каждый склад (учёт транзита — статусы вкл. SHIPPED).
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

    # 2. Excluded warehouses (set для быстрой проверки) + закрытые по приёмке
    # (нет лимита) минус whitelist предзаявок — единый отсев складов.
    from backend.services.warehouse_acceptance_service import get_acceptance_blocked_warehouses

    excluded_list = await get_excluded_warehouses(db, project_id)
    excluded: set[str] = {str(w).strip() for w in excluded_list}
    excluded |= await get_acceptance_blocked_warehouses(db, project_id)

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

    # Гарантия СЗФО собирается по ИСХОДНОЙ доле — концентрация ниже может
    # срезать northwest из bench_share целиком (см. NW_GUARANTEE_MIN_PACKS).
    nw_guarantee: set[str] = {NW_DISTRICT_KEY} if bench_share.get(NW_DISTRICT_KEY, 0) > 0 else set()

    # Концентрация «до target% локализации»: топ-ФО по доле, хвост дальних не
    # сидируется (seed остаётся на ФФ). Применяем ДО far-east-капа, чтобы кап
    # бил по уже сконцентрированной раздаче.
    bench_share = concentrate_share_to_target(bench_share, localization_target)

    fe_excess_share = max(0.0, bench_share.get("far_east_siberia", 0.0) - FAR_EAST_MAX_SHARE)
    bench_share = _cap_far_east_share(bench_share)

    # 4. Главный склад в каждом ФО (по трафику bench-источника)
    if bench_wh_traffic:
        main_wh = pick_main_warehouse_per_district(bench_wh_traffic, excluded)
    else:
        # Без данных по складам — дефолтные центры ФО, исключив excluded.
        main_wh = {d: wh for d, wh in _DEFAULT_MAIN_WAREHOUSES.items() if wh not in excluded}

    # 6. Распределение и вычитание уже идущих сборок (active_asm уже загружены выше)
    allocation = distribute(total_qty, bench_share, req.min_pack, main_wh, guarantee_districts=nw_guarantee)
    if fe_excess_share > 0:
        _route_far_east_excess(allocation, round(fe_excess_share * total_qty))

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
                 (SELECT jsonb_object_agg(wws.warehouse_name, wws.quantity)
                  FROM wb_warehouse_stocks wws
                  WHERE wws.nm_id = n.article_wb AND wws.project_id = :project_id
                    AND wws.quantity > 0),
                 '{}'::jsonb
               ) AS wb_by_wh,
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
        # WB-сток per-склад: канонизируем имена в то же пространство, что и
        # anchor-склады распределения (иначе вычет «уже на WB» не сматчится).
        wb_by_wh_raw = r["wb_by_wh"] or {}
        wb_by_warehouse: dict[str, int] = {}
        for wh_name, q in wb_by_wh_raw.items():
            canon = _canonicalize_asm_warehouse(str(wh_name))
            wb_by_warehouse[canon] = wb_by_warehouse.get(canon, 0) + int(q)
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
                "wb_by_warehouse": wb_by_warehouse,
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
    ship_fraction: float = 0.55,
    ship_floor: int = 50,
    localization_target: int = 75,
) -> ColdStartTableResponse:
    """Cold-start таблица: список SKU из сегмента + per-SKU allocation.

    ship_fraction (0..1) — доля свободного ФФ-остатка, которую разрешено отгрузить
    на WB за раз. У новинки нет сигнала спроса → «сеем» часть (дефолт 55%), остаток
    держим буфером на ФФ под добор по факту продаж. Кап режет ТОЛЬКО тотал;
    per-склад пропорции и вычет WB/сборки/пути сохраняются.

    ship_floor — пол: если свободного ФФ ≤ ship_floor шт, буфер держать бессмысленно
    → отгружаем 100% (кап не действует). Формула капа:
    `max(round(ФФ × ship_fraction), min(ФФ, ship_floor))`.

    localization_target (%) — раздаём seed КОНЦЕНТРИРОВАННО до этой локализации:
    топ-ФО по доле, хвост дальних/мелких округов сверх target НЕ сидируется (seed
    остаётся на ФФ). Та же логика «до 75% / не перетаривать», что и для обычных SKU.
    target=100 → концентрация выключена (сидируются все ФО, как раньше).

    Бенчмарк (доли по ФО + главный склад) считается ОДИН раз для всех SKU.
    Для каждого SKU делается distribute(rf_qty, ...) с тем же бенчмарком.
    """
    # Excluded + закрытые по приёмке (нет лимита) минус whitelist предзаявок —
    # склад без лимита и не в whitelist вырезается из расчёта новинок.
    from backend.services.warehouse_acceptance_service import get_acceptance_blocked_warehouses

    excluded_list = await get_excluded_warehouses(db, project_id)
    excluded: set[str] = {str(w).strip() for w in excluded_list}
    excluded |= await get_acceptance_blocked_warehouses(db, project_id)

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

    # Гарантия СЗФО — по ИСХОДНОЙ доле (до концентрации), см. NW_GUARANTEE_MIN_PACKS.
    nw_guarantee: set[str] = {NW_DISTRICT_KEY} if bench_share.get(NW_DISTRICT_KEY, 0) > 0 else set()

    # Концентрация «до target% локализации» (как у обычных SKU): топ-ФО по доле,
    # хвост дальних/мелких округов сверх target НЕ сидируется. Применяем ДО far-east
    # капа, чтобы кап бил по уже сконцентрированной раздаче.
    bench_share = concentrate_share_to_target(bench_share, localization_target)

    # ДВ-кап: излишек доли Дальневосточного ФО (со склада не довезти) → на Урал
    # (часть позже уводится на Электросталь — см. _route_far_east_excess).
    fe_excess_share = max(0.0, bench_share.get("far_east_siberia", 0.0) - FAR_EAST_MAX_SHARE)
    bench_share = _cap_far_east_share(bench_share)

    if bench_wh_traffic:
        wh_per_district = pick_warehouses_per_district(bench_wh_traffic, excluded, top_n=3)
    else:
        wh_per_district = {d: [(wh, 1)] for d, wh in _DEFAULT_MAIN_WAREHOUSES.items() if wh not in excluded}

    # Принцип /warehouse/speed: СОСТАВ кандидатов per-ФО — speed-anchor склады
    # (быстрые для своего ФО из speed-карты POSTAVLENO): товар уходит на склад,
    # с которого WB замкнёт первый (быстрый) приоритет города. ВЕС внутри ФО —
    # реальный трафик заказов склада, НЕ cities_count: вес по числу городов давал
    # Калининграду 25% доли СЗФО (Шушары:Калининград = 3:1 городов) при ~0.3%
    # реального трафика (809:6 доставок за 14д) — аудит 2026-07-09. Трафик-веса
    # применяются, только когда трафик есть у ВСЕХ кандидатов ФО: нулевой вес
    # одного из якорей хоронил бы новый склад без истории и ломал «не перетаривать»
    # (вся цель ФО уезжает на перетаренный склад с трафиком). Иначе — cities_count.
    # Имена канонизируем в stock-пространство (чтобы совпали с wb_by_warehouse
    # при вычете). ФО без anchor — fallback на трафик.
    canon_traffic: dict[str, int] = {}
    for wh, t in bench_wh_traffic.items():
        c = _canonicalize_asm_warehouse(wh)
        canon_traffic[c] = canon_traffic.get(c, 0) + t
    for d in DISTRICT_ORDER:
        if d in {"abroad", "unknown"}:
            continue
        anchors = [(_canonicalize_asm_warehouse(wh), cnt) for wh, cnt in get_anchors_for_okrug(d)]
        speed_whs = [(wh, cnt) for wh, cnt in anchors if wh not in excluded and not _is_spec_warehouse(wh) and cnt > 0][
            :3
        ]
        if speed_whs:
            traffic_whs = [(wh, canon_traffic.get(wh, 0)) for wh, _ in speed_whs]
            if all(t > 0 for _, t in traffic_whs):
                speed_whs = traffic_whs
            wh_per_district[d] = speed_whs

    # Канонизируем имена складов в stock-пространство (трафик заказов зовёт склад
    # иначе, чем сток/матрица: «Алексин (Тула)» vs «Тула») + мёржим дубли по канону.
    # Иначе аллокация уходит на имя, которого нет среди колонок матрицы (не рисуется —
    # «отправить N, склад не показан»), и per-склад вычет WB/сборок ниже промахивается
    # (active_asm/wb_by_warehouse — уже канон, а wh был сырой).
    _canon_wpd: dict[str, list[tuple[str, int]]] = {}
    for d, whs in wh_per_district.items():
        merged: dict[str, int] = {}
        for wh, cnt in whs:
            c = _canonicalize_asm_warehouse(wh)
            merged[c] = merged.get(c, 0) + cnt
        _canon_wpd[d] = list(merged.items())
    wh_per_district = _canon_wpd

    # main_warehouses meta — все склады каждого ФО (top-3), РФ-округа (abroad/unknown скрыты)
    # share_pct = доля округа × доля склада в трафике округа
    main_wh_meta: list[dict[str, Any]] = []
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
                    "warehouse": wh,  # уже канонизирован (wh_per_district нормализован в корне)
                    "share_pct": round(wh_share * 100, 2),
                }
            )

    # Заголовки %: отразить переброс излишка ДВ (зеркало _route_far_east_excess на
    # уровне долей) — Екатеринбург отдаёт, Электросталь/Сарапул получают. Сарапул
    # добавляем КОЛОНКОЙ (он не top-3 anchor — иначе его аллокация была бы скрыта,
    # «Итого» > суммы видимых колонок).
    if fe_excess_share > 0:
        by_wh = {m["warehouse"]: m for m in main_wh_meta}
        move_out_pct = round(fe_excess_share * (1 - FAR_EAST_EXCESS_ROUTING[FAR_EAST_EXCESS_ANCHOR]) * 100, 2)
        anchor_meta = by_wh.get(FAR_EAST_EXCESS_ANCHOR)
        if anchor_meta:
            anchor_meta["share_pct"] = round(max(0.0, anchor_meta["share_pct"] - move_out_pct), 2)
        for wh, share in FAR_EAST_EXCESS_ROUTING.items():
            if wh == FAR_EAST_EXCESS_ANCHOR:
                continue
            add_pct = round(fe_excess_share * share * 100, 2)
            if add_pct <= 0:
                continue
            if wh in by_wh:
                by_wh[wh]["share_pct"] = round(by_wh[wh]["share_pct"] + add_pct, 2)
            else:
                d_wh = WAREHOUSE_TO_DISTRICT.get(wh, "far_east_siberia")
                if d_wh in {"abroad", "unknown"}:
                    d_wh = "far_east_siberia"
                main_wh_meta.append(
                    {
                        "district_key": d_wh,
                        "district_label": DISTRICT_LABELS.get(d_wh, d_wh),
                        "warehouse": wh,
                        "share_pct": add_pct,
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
                    wb_by_warehouse=sku.get("wb_by_warehouse", {}),
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

        # Принцип speed + учёт уже имеющегося на WB:
        #   network = свободный ФФ + (WB-сток + сборки) на target-складах ФО,
        #   target[wh] = benchmark-доля network (speed-взвешенно по anchor),
        #   ship[wh]   = max(0, target − WB[wh] − asm[wh]).
        # Перетаренные склады → 0 (доля остаётся на ФФ), недотаренные —
        # допоставляются до benchmark. asm учитывается один раз (был вычтен из
        # total_qty и добавлен в network → сокращается).
        wb_by_wh: dict[str, int] = sku.get("wb_by_warehouse", {})
        target_whs = {wh for whs in wh_per_district.values() for wh, _ in whs}
        existing_at_targets = sum(wb_by_wh.get(wh, 0) + active_asm.get(wh, 0) for wh in target_whs)
        network_total = total_qty + existing_at_targets
        targets = distribute_multi(
            network_total, bench_share, min_pack, wh_per_district, guarantee_districts=nw_guarantee
        )
        if fe_excess_share > 0:
            _route_far_east_excess(targets, round(fe_excess_share * network_total))
        final_alloc: dict[str, int] = {}
        for wh, target in targets.items():
            ship = target - wb_by_wh.get(wh, 0) - active_asm.get(wh, 0)
            if ship > 0:
                final_alloc[wh] = ship

        # Кап «сеять, не вытряхивать весь ФФ»: отгружаем не более ship_fraction
        # свободного ФФ (дефолт 55%), остаток — буфер на ФФ под добор по факту.
        # ПОЛ: ниже ship_floor шт буфер бессмысленен → отгружаем 100% (= min(ФФ, floor)).
        # Заодно ловит превышение из-за min_pack-bump в distribute_multi.
        # Урезаем пропорционально + largest-remainder (пропорции складов сохраняются).
        ship_cap = max(round(total_qty * ship_fraction), min(total_qty, ship_floor))
        ship_total = sum(final_alloc.values())
        if ship_total > ship_cap:
            scale = ship_cap / ship_total if ship_total > 0 else 0
            trimmed = {wh: int(q * scale) for wh, q in final_alloc.items()}
            leftover = ship_cap - sum(trimmed.values())
            for wh in sorted(trimmed, key=lambda w: -final_alloc[w]):
                if leftover <= 0:
                    break
                trimmed[wh] += 1
                leftover -= 1
            final_alloc = {wh: q for wh, q in trimmed.items() if q > 0}

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
                # Per-склад WB-сток (канонизирован) — нужен фронту для coverage-aware
                # пересчёта при ручном override «Распределить» (вычет WB/сборки/пути).
                wb_by_warehouse=sku.get("wb_by_warehouse", {}),
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
