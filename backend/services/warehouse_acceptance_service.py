# ruff: noqa: RUF001, RUF002, RUF003
"""WB FBO acceptance check + redistribute service.

Calls WB Supplies API `POST /api/v1/acceptance/options` for a batch of
(barcode, qty) and:

1. Maps WB warehouseID → name via `/api/v1/warehouses` (cached separately).
2. For each SKU, checks whether each warehouse in the planned distribution
   accepts that barcode (canBox / canMonopallet / canSupersafe).
3. Picks a single `package_type` per SKU:
     * BOX        — if every involved warehouse has canBox=True
     * MONOPALLET — else if every involved warehouse has canMonopallet=True
     * SUPERSAFE  — else if every involved warehouse has canSupersafe=True
     * SKU is unsplittable here → fallback BOX with warnings
   (One AssemblyRequest = one transport unit, см. DOMAIN_ASSEMBLY.md)
4. For warehouses fully closed for the chosen package_type, qty is moved to
   the nearest open warehouse in the same федеральный округ; if no open WH
   in district — to any open WH (warned).

Rate limit on WB API: 6 req/min. Availability кэшируется ПЕР-БАРКОД на 10 минут:
смена количеств/состава батча/вкладки не инвалидирует соседние баркоды — живой
вызов WB идёт только по недостающим. Distribution пересчитывается на каждый
запрос локально (флаги — уровня баркод × склад × упаковка, qty на них не влияет).
"""

import json
import logging
from collections import defaultdict

from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import _redis_client, get_redis
from backend.integrations.wb_api import WBApiClient
from backend.services.funnel.wb_api_client import get_wb_key
from backend.services.warehouse_district import warehouse_to_district
from backend.services.warehouse_geo_data import ACCEPTANCE_TO_STOCK_NAME
from backend.services.warehouse_need_service import _normalize_wb_warehouse
from backend.utils.time import utcnow


def _normalize_acceptance_wh(name: str | None) -> str:
    """Map WB acceptance-API warehouse name to canonical WAREHOUSE_COORDS name.

    WB API возвращает имена вроде «Склад Владивосток», «Новосемейкино»,
    «Краснодар (Тихорецкая)». В нашем cold-start / distribution используются
    canonical-имена из WAREHOUSE_COORDS («Владивосток», «Самара (Новосемейкино)»,
    «Краснодар»). Без явного алиаса qty не сматчится → склад выглядит закрытым.

    Алгоритм:
      1. Если есть прямой алиас (full name) — применить.
      2. Иначе убрать суффикс в скобках («Краснодар (Тихорецкая)» → «Краснодар»).
      3. Если у срезанной формы есть алиас — применить.
      4. Иначе оставить как есть.
    """
    if not name:
        return ""
    if name in ACCEPTANCE_TO_STOCK_NAME:
        return ACCEPTANCE_TO_STOCK_NAME[name]
    stripped = _normalize_wb_warehouse(name)
    if stripped in ACCEPTANCE_TO_STOCK_NAME:
        return ACCEPTANCE_TO_STOCK_NAME[stripped]
    return stripped


def _is_spec_acceptance_wh(name: str | None) -> bool:
    """Спец-склад WB (крупногабарит СГТ, Питание, Горючее, СЦ, виртуальный)?

    Для обычной FBO-поставки такие склады не нужны и засоряют выдачу — исключаем
    из проверки приёмки по СЫРОМУ имени WB (до нормализации), иначе «Электросталь:
    Питание» алиасится в «Электросталь» (ACCEPTANCE_TO_STOCK_NAME) и доступность
    спец-склада ложно подмешивается в обычный. Зеркало фронтового isSpecWarehouse.
    """
    if not name:
        return False
    n = name.strip()
    if n.startswith("Виртуальный") or n.startswith("СЦ ") or " СЦ " in n:
        return True
    return any(s in n for s in (" СГТ", "Питание", "Горючее"))


logger = logging.getLogger("dds.warehouse_acceptance")

CACHE_TTL_SECONDS = 600  # 10 min пер-баркод — WB rate limit 6 req/min, флаги меняются редко
WAREHOUSE_NAMES_CACHE_TTL = 3600  # 1 hour — IDs are stable
COEFFICIENTS_CACHE_TTL = 3600  # 1 hour — WB обновляет ~раз в день в 03:00 МСК

# WB box type ID → ours canonical key.
# ВАЖНО (исправлено 2026-06-22 по живым данным tariffs/v1/acceptance/coefficients
# и WB-кабинету; подтверждено пользователем на BZ-YY8820A/30x60 / Шушары):
#   • 2 = КОРОБА      (самый массовый тип — открыт на 39 складах из выборки;
#                      Склад Шушары box открыт все 14 дней, что видно в кабинете)
#   • 5 = МОНОПАЛЛЕТЫ (Екатеринбург - Перспективная 14 boxTypeID=5 = высокое
#                      хранение 15–27.5 ₽/л — характерно для моно; 29 складов)
#   • 6 = СУПЕРСЕЙФ   (нишевый — открыт лишь на 7 складах; те же ставки что у короба)
# Совпадает с офиц. WB-доками (2=Короба, 5=Монопаллеты, 6=Суперсейф). Прежний
# маппинг {2:super, 6:box} был ПЕРЕПУТАН (2↔6) — для коробочных SKU читался
# коэффициент суперсейфа (редкий→закрыт) вместо короба → ложный ⌛ «нет лимита» и
# пол-ёмкости «терялось». Ещё раньше {2:mono,5:super,6:box} тоже был неверным.
_BOX_TYPE_ID_TO_KEY = {
    2: "box",
    5: "mono",
    6: "super",
}


# ─── pure helpers (no I/O — fully unit-tested) ──────────────────────────────


def _aggregate_coefficients(
    raw_coefficients: list[dict],
    wh_id_to_name: dict[int, str],
    *,
    paid_threshold: int = 1,
) -> dict[tuple[str, str], dict]:
    """Group coefficients by (canonical_warehouse_name, package_type_key).

    Returns {(canon_wh, "box"|"mono"|"super"): {
        "free_days_14": int,   # дней с coefficient ∈ {0..paid_threshold} И allowUnload=true
        "paid_days_14": int,   # дней с coefficient > paid_threshold И allowUnload=true
        "closed_days_14": int, # дней с coefficient=-1 ИЛИ allowUnload=false
        "min_coefficient": int | None,  # лучший (минимальный) коэф среди allowUnload=true
        "total_days": int,
    }}.

    paid_threshold=1 — стандарт WB: «бесплатно» = coefficient ∈ {0, 1}, «платно» = ≥2.
    """
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for e in raw_coefficients:
        wid = e.get("warehouseID")
        if wid is None:
            continue
        name = wh_id_to_name.get(int(wid)) or e.get("warehouseName")
        if not name:
            continue
        # Спец-склады (СГТ/Питание/Горючее/СЦ/виртуальные) нормализуются в канон
        # базового склада → их открытые дни ложно подмешивались бы в meta
        # реального склада (прод-баг Казань-моно: реальный склад -1 все дни, а
        # «Казань СГТ»/«Казань: Питание» coef=0 → free_days>0 → ложный «лимит»).
        # Зеркало _flags_for_warehouse и _build_acceptance_limits.
        if _is_spec_acceptance_wh(name):
            continue
        canon = _normalize_acceptance_wh(name)
        if not canon:
            continue
        box_type_id = e.get("boxTypeID")
        if box_type_id is None:
            continue
        type_key = _BOX_TYPE_ID_TO_KEY.get(int(box_type_id))
        if type_key is None:
            continue
        grouped[(canon, type_key)].append(e)

    out: dict[tuple[str, str], dict] = {}
    for key, entries in grouped.items():
        free = paid = closed = 0
        min_coef: int | None = None
        for e in entries:
            coef = e.get("coefficient")
            allow = bool(e.get("allowUnload"))
            if coef == -1 or not allow:
                closed += 1
                continue
            if coef is None:
                continue
            if coef <= paid_threshold:
                free += 1
            else:
                paid += 1
            if min_coef is None or coef < min_coef:
                min_coef = coef
        out[key] = {
            "free_days_14": free,
            "paid_days_14": paid,
            "closed_days_14": closed,
            "min_coefficient": min_coef,
            "total_days": len(entries),
        }
    return out


def _flags_for_warehouse(
    raw_warehouses: list[dict],
    wh_id_to_name: dict[int, str],
    coefficients_by_canon: dict[tuple[str, str], dict] | None = None,
    *,
    require_free_days: bool = False,
    require_acceptance_day: bool = False,
) -> dict[str, dict]:
    """Map raw WB warehouses[] → {canonical_name: {warehouse_id, can_box, ...}}.

    `can_X` по дефолту = `options.canX` (как кабинет WB). Реальная per-barcode
    доступность определяется именно `acceptance/options`, а coefficients — это
    тарифы (платные коэффициенты ≥2) и публичные slot-окна, которые НЕ
    являются source of truth для доступности (для одеяла_193х203_9кг кабинет
    показывает Склад Владивосток МОНО как доступный, хотя в coefficients
    `coefficient=-1, allowUnload=False` все 15 дней — расхождение зафиксировано
    2026-05-10).

    `require_free_days=True` (opt-in) — строгий режим: AND с
    `coefficients.free_days_14 > 0`. Используй когда нужны ТОЛЬКО бесплатные
    публичные слоты в ближайшие 14 дней.

    Per-(canon, type) coefficients (free_days_14 / paid_days_14 /
    min_coefficient) добавляются в `box_meta` / `mono_meta` / `super_meta` —
    для tooltip («бесплатно N/14 дн») независимо от `require_free_days`.

    Multiple sub-warehouses with the same canonical name are OR-merged.
    """
    coef = coefficients_by_canon or {}

    def _check_can(canon: str, type_key: str, raw_can: bool) -> tuple[bool, dict | None]:
        meta = coef.get((canon, type_key))
        if not raw_can:
            return False, meta
        if require_free_days:
            if meta is None:
                # Coefficients не получены (нет ключа / fail) — оставляем как сказал options.
                return True, None
            return meta["free_days_14"] > 0, meta
        if require_acceptance_day:
            # Лимит приёмки (как на /acceptance-limits): склад открыт для этого типа
            # упаковки, если в ближайшие 14 дней есть ≥1 день приёмки — бесплатный
            # ИЛИ платный (coef ≥ 0 / allowUnload=true). Полностью закрытый лимит
            # (coef −1 / allowUnload=false все дни → free=paid=0 при total>0) →
            # склад закрыт для этого типа. Нет данных по (склад,тип) → fail-open
            # (оставляем как сказал options, чтобы не закрыть склад без оснований).
            if meta is None or meta.get("total_days", 0) == 0:
                return True, meta
            return (meta["free_days_14"] + meta["paid_days_14"]) > 0, meta
        return True, meta

    result: dict[str, dict] = {}
    for w in raw_warehouses:
        wid = w.get("warehouseID")
        if wid is None:
            continue
        name = wh_id_to_name.get(int(wid))
        if not name:
            continue
        # Спец-склады (СГТ/Питание/Горючее/СЦ/виртуальные) — вне обычной FBO,
        # отсекаем по сырому имени ДО нормализации (иначе мёржатся в базовый склад).
        if _is_spec_acceptance_wh(name):
            continue
        norm = _normalize_acceptance_wh(name)
        if not norm:
            continue
        raw_box = bool(w.get("canBox"))
        raw_mono = bool(w.get("canMonopallet"))
        raw_super = bool(w.get("canSupersafe"))
        can_box, box_meta = _check_can(norm, "box", raw_box)
        can_mono, mono_meta = _check_can(norm, "mono", raw_mono)
        can_super, super_meta = _check_can(norm, "super", raw_super)

        flags = {
            "warehouse_id": wid,
            "can_box": can_box,
            "can_monopallet": can_mono,
            "can_supersafe": can_super,
            "box_meta": box_meta,
            "mono_meta": mono_meta,
            "super_meta": super_meta,
        }
        prev = result.get(norm)
        if prev is None:
            result[norm] = flags
        else:
            # OR-merge by flag; meta — берём более разрешительный (с большим free_days)
            def _better_meta(a: dict | None, b: dict | None) -> dict | None:
                if a is None:
                    return b
                if b is None:
                    return a
                return a if a.get("free_days_14", 0) >= b.get("free_days_14", 0) else b

            result[norm] = {
                "warehouse_id": prev["warehouse_id"],
                "can_box": prev["can_box"] or flags["can_box"],
                "can_monopallet": prev["can_monopallet"] or flags["can_monopallet"],
                "can_supersafe": prev["can_supersafe"] or flags["can_supersafe"],
                "box_meta": _better_meta(prev.get("box_meta"), box_meta),
                "mono_meta": _better_meta(prev.get("mono_meta"), mono_meta),
                "super_meta": _better_meta(prev.get("super_meta"), super_meta),
            }
    return result


def _pick_package_type(
    distribution: dict[str, int],
    availability: dict[str, dict],
) -> tuple[str, list[str]]:
    """Pick a single package_type that satisfies as many warehouses as possible.

    Preference order: BOX > MONOPALLET > SUPERSAFE (cheapest acceptance first).
    Returns (package_type, warnings).
    """
    if not distribution:
        return "BOX", []

    targets = [w for w, q in distribution.items() if q > 0]
    if not targets:
        return "BOX", []

    def _all_support(flag_key: str) -> bool:
        return all(availability.get(w, {}).get(flag_key, False) for w in targets)

    if _all_support("can_box"):
        return "BOX", []
    if _all_support("can_monopallet"):
        return "MONOPALLET", ["WB не принимает короб — упаковка моно-паллетой."]
    if _all_support("can_supersafe"):
        return "SUPERSAFE", ["WB принимает только в режиме super-safe."]

    # Mixed — выбираем тип с наибольшим покрытием
    box_cnt = sum(1 for w in targets if availability.get(w, {}).get("can_box"))
    mono_cnt = sum(1 for w in targets if availability.get(w, {}).get("can_monopallet"))
    if mono_cnt >= box_cnt:
        return "MONOPALLET", ["Часть складов недоступна — выбран MONOPALLET, лишний qty будет перераспределён."]
    return "BOX", ["Часть складов недоступна — выбран BOX, лишний qty будет перераспределён."]


# package_type → (can_* flag key, *_meta key)
_PKG_FLAG_META: dict[str, tuple[str, str]] = {
    "BOX": ("can_box", "box_meta"),
    "MONOPALLET": ("can_monopallet", "mono_meta"),
    "SUPERSAFE": ("can_supersafe", "super_meta"),
}


def _meta_limit_days(meta: dict | None) -> int:
    """Free+paid дней приёмки в 14-окне (0 = нет публичного лимита → ⌛)."""
    if not meta:
        return 0
    return int(meta.get("free_days_14", 0)) + int(meta.get("paid_days_14", 0))


def _coefficients_present(availability: dict[str, dict]) -> bool:
    """Загружен ли календарь коэффициентов (хоть по одному складу/типу).

    Real ⌛-склад (options=yes, но НЕТ в календаре WB) даёт meta=None ПРИ
    загруженных коэффициентах → его надо блокировать (нет лимита). А полное
    отсутствие меты = коэффициенты не загрузились (network/degradation) →
    fail-open, как во всей acceptance-логике (иначе транзиентный сбой WB
    заблокировал бы ВСЕ склады и уронил распределение)."""
    return any(
        av.get(mk) for av in availability.values() for mk in ("box_meta", "mono_meta", "super_meta")
    )


def _is_open_for(
    package_type: str,
    flags: dict | None,
    preorder_allowed: set[str] | None = None,
    wh_name: str | None = None,
) -> bool:
    """Можно ли реально отгрузить `package_type` на склад.

    `preorder_allowed is None` — legacy-режим: смотрим ТОЛЬКО опции кабинета
    (`can_*`), лимит игнорируем. Используется чистыми юнит-тестами.

    `preorder_allowed` передан (set, возможно пустой) — limit+whitelist-aware
    режим прод-пути: склад открыт для типа ⟺ `can_X` И (есть реальный лимит
    приёмки `free+paid > 0` ИЛИ склад в whitelist предзаявки). ⌛-без-лимита
    (включая отсутствие меты — WB не дал коэффициентов) без whitelist = НЕ
    отгрузить: на такой склад товар не кладём, а перераспределяем.
    """
    if not flags:
        return False
    entry = _PKG_FLAG_META.get(package_type)
    if entry is None:
        return False
    flag_key, meta_key = entry
    if not flags.get(flag_key):
        return False
    if preorder_allowed is None:
        return True
    if _meta_limit_days(flags.get(meta_key)) > 0:
        return True
    canon = _normalize_acceptance_wh(wh_name) if wh_name else ""
    return bool(canon) and canon in preorder_allowed


def _best_package_type_for_wh(
    flags: dict | None,
    preorder_allowed: set[str] | None = None,
    wh_name: str | None = None,
) -> str | None:
    """Cheapest package_type a single warehouse accepts (BOX > MONO > SUPER).

    Returns None if the warehouse rejects all three types (treated as closed).
    `preorder_allowed` включает limit+whitelist-aware режим (см. `_is_open_for`):
    ⌛-тип без whitelist больше не считается «принимается».
    """
    if not flags:
        return None
    for pkg in ("BOX", "MONOPALLET", "SUPERSAFE"):
        if _is_open_for(pkg, flags, preorder_allowed, wh_name):
            return pkg
    return None


def _pick_destination(
    candidates: list[str],
    okrug: str,
    new_dist: dict[str, int],
    *,
    priority: bool,
) -> str | None:
    """Выбрать склад-получатель среди `candidates`.

    `priority=True` (limit-aware прод-путь, выбор пользователя «строго по
    приоритету/индексу») — первый по speed-приоритету для ФО получателя
    (`sort_warehouses_by_priority`; тай-брейк — канон-имя). `priority=False`
    (legacy) — крупнейший по уже накопленному qty, тай-брейк по имени.
    """
    if not candidates:
        return None
    if priority:
        from backend.services.warehouse_speed import sort_warehouses_by_priority

        ordered = sort_warehouses_by_priority(candidates, okrug)
        return ordered[0] if ordered else None
    return max(candidates, key=lambda w: (new_dist.get(w, 0), w))


def _split_distribution_by_package_type(
    distribution: dict[str, int],
    availability: dict[str, dict],
    *,
    min_pack: int = 5,
    preorder_allowed: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split a single SKU distribution into per-package-type sub-assemblies.

    Каждая сборка = одна транспортная единица одного типа упаковки. Если разные
    склады принимают разные типы (Электросталь — только моно, Краснодар — только
    короб), вместо отбраковки половины складов разносим qty по двум сборкам.

    Алгоритм:
      1. Для каждого склада в distribution выбираем самый дешёвый из доступных
         (BOX > MONOPALLET > SUPERSAFE).
      2. Группируем склады по выбранному типу → splits.
      3. Закрытые склады (нет ни одного типа) → consolidate в крупнейший
         открытый ЦФО-склад; их qty присоединяется к split того типа, который
         этот ЦФО-склад поддерживает.
      4. После consolidation запускаем `redistribute_blocked_qty` на каждом
         split — на случай если склад в split'е тоже закрыт для своего типа
         (не должно происходить, но safety net).

    Returns: (splits, moves) где
      splits = [{"package_type": "BOX", "distribution": {...}, "warnings": [...]}]
      moves  = [{"from_warehouse", "to_warehouse", "quantity", "reason"}]
    """
    if not distribution:
        return [], []

    # Шаг 1: каждому складу — лучший доступный тип (limit+whitelist-aware, если
    # передан preorder_allowed И коэффициенты загружены: ⌛-тип без whitelist →
    # склад «закрыт» для него; при пустом календаре — fail-open).
    if preorder_allowed is not None and not _coefficients_present(availability):
        preorder_allowed = None
    priority = preorder_allowed is not None
    by_type: dict[str, dict[str, int]] = defaultdict(dict)
    closed: list[tuple[str, int]] = []
    for wh, qty in distribution.items():
        if qty <= 0:
            continue
        pkg = _best_package_type_for_wh(availability.get(wh), preorder_allowed, wh)
        if pkg is None:
            closed.append((wh, qty))
            continue
        by_type[pkg][wh] = qty

    # Шаг 2: закрытые → consolidate-to-center (ЦФО), package_type выбирается
    # по тому, что поддерживает склад-получатель.
    open_central = [
        wh
        for wh in availability
        if warehouse_to_district(wh) == "central"
        and _best_package_type_for_wh(availability.get(wh), preorder_allowed, wh) is not None
    ]
    open_globally = [
        wh
        for wh in availability
        if _best_package_type_for_wh(availability.get(wh), preorder_allowed, wh) is not None
    ]
    moves: list[dict] = []
    if closed:
        # Получатель: priority-режим — первый по speed-приоритету ФО; legacy —
        # крупнейший по уже накопленному qty. package_type — лучший у получателя.
        def _acc_for(wh: str) -> dict[str, int]:
            pkg = _best_package_type_for_wh(availability.get(wh), preorder_allowed, wh)
            return {wh: by_type.get(pkg, {}).get(wh, 0)} if pkg else {}

        dest_pool = open_central or open_globally
        dest_wh = None
        dest_pkg = None
        if dest_pool:
            acc: dict[str, int] = {}
            for wh in dest_pool:
                acc.update(_acc_for(wh))
            dest_wh = _pick_destination(dest_pool, "central", acc, priority=priority)
            dest_pkg = (
                _best_package_type_for_wh(availability.get(dest_wh), preorder_allowed, dest_wh)
                if dest_wh
                else None
            )
        reason_default = (
            "consolidated_to_center"
            if open_central
            else ("closed_no_central_open" if open_globally else "closed_no_open_anywhere")
        )

        for src, qty in closed:
            district = warehouse_to_district(src)
            if dest_wh is not None and dest_pkg is not None:
                by_type[dest_pkg][dest_wh] = by_type.get(dest_pkg, {}).get(dest_wh, 0) + qty
                reason = "closed_in_district" if district == "central" else reason_default
            else:
                reason = reason_default
            moves.append(
                {
                    "from_warehouse": src,
                    "to_warehouse": dest_wh,
                    "quantity": qty,
                    "reason": reason,
                }
            )

    # Шаг 3: формируем splits + post-pass min_pack для ЦФО на каждом
    splits: list[dict] = []
    for pkg in ("BOX", "MONOPALLET", "SUPERSAFE"):
        dist = by_type.get(pkg)
        if not dist:
            continue
        new_dist, sub_moves = redistribute_blocked_qty(
            dist,
            availability,
            pkg,
            min_pack=min_pack,
            preorder_allowed=preorder_allowed,
        )
        # Накапливаем move'ы из под-redistribute (обычно пусто — это safety-net)
        moves.extend(sub_moves)
        warnings: list[str] = []
        if pkg == "MONOPALLET":
            warnings.append("Часть складов принимает только моно-паллетой.")
        elif pkg == "SUPERSAFE":
            warnings.append("Часть складов принимает только super-safe.")
        splits.append(
            {
                "package_type": pkg,
                "distribution": new_dist,
                "warnings": warnings,
            }
        )

    return splits, moves


def redistribute_blocked_qty(
    distribution: dict[str, int],
    availability: dict[str, dict],
    package_type: str,
    *,
    mode: str = "consolidate_to_center",
    min_pack: int = 5,
    preorder_allowed: set[str] | None = None,
) -> tuple[dict[str, int], list[dict]]:
    """Move qty away from warehouses that don't accept this package_type.

    Modes:
      "consolidate_to_center" (default): qty закрытого склада идёт в крупнейший
        открытый склад **Центрального ФО** (Электросталь). Идея — заказы
        покупателей из недоступного региона WB всё равно повезёт из Москвы,
        поэтому туда и кладём товар. Бонус: на Центре скапливается больше qty,
        и внутри ЦФО разлёт по 3 складам становится возможным (min_pack pool).

      "spread_in_district": qty уходит в крупнейший открытый склад **того же ФО**
        (Невинномысск для Юга и т.д.). Географически локальнее, но если соседний
        склад в реальности WB-логистики не покрывает регион закрытого — товар
        останется лежать.

    Fallback в обоих режимах: если в нужной зоне нет открытых складов — берём
    крупнейший открытый globally. Совсем нет открытых → drop с to=None.

    Returns (new_distribution, moves).
    """
    if not distribution:
        return {}, []

    # Все известные склады — из distribution + из availability (даже если в
    # distribution=0). Это даёт полный набор кандидатов для перенаправления.
    all_known = set(distribution.keys()) | set(availability.keys())
    # Fail-open при незагруженном календаре коэффициентов (см. _coefficients_present).
    if preorder_allowed is not None and not _coefficients_present(availability):
        preorder_allowed = None
    priority = preorder_allowed is not None
    open_set = {w for w in all_known if _is_open_for(package_type, availability.get(w), preorder_allowed, w)}
    closed = [(w, q) for w, q in distribution.items() if w not in open_set and q > 0]

    new_dist = {w: q for w, q in distribution.items() if w in open_set}
    moves: list[dict] = []

    # Indexed by district for O(1) destination lookup
    open_by_district: dict[str, list[str]] = defaultdict(list)
    for w in open_set:
        open_by_district[warehouse_to_district(w)].append(w)

    open_globally = sorted(open_set, key=lambda w: -new_dist.get(w, 0))

    # Для consolidate_to_center нам нужны открытые склады ЦФО (central).
    # Если их нет — fallback на open_globally.
    central_open = open_by_district.get("central", [])

    for src, qty in closed:
        district = warehouse_to_district(src)
        if mode == "consolidate_to_center":
            # Все закрытые → в крупнейший открытый склад ЦФО (Электросталь).
            # Если ЦФО закрыт — fallback на open_globally.
            if central_open:
                dest = _pick_destination(central_open, "central", new_dist, priority=priority)
                reason = "consolidated_to_center" if district != "central" else "closed_in_district"
            elif open_globally:
                dest = _pick_destination(open_globally, district, new_dist, priority=priority)
                reason = "closed_no_central_open"
            else:
                dest = None
                reason = "closed_no_open_anywhere"
            if dest is not None:
                new_dist[dest] = new_dist.get(dest, 0) + qty
            moves.append(
                {
                    "from_warehouse": src,
                    "to_warehouse": dest,
                    "quantity": qty,
                    "reason": reason,
                }
            )
            continue

        # mode == "spread_in_district"
        candidates = open_by_district.get(district, [])
        if candidates:
            # priority: первый по speed-приоритету ФО; legacy: крупнейший по qty.
            dest = _pick_destination(candidates, district, new_dist, priority=priority)
            reason = "closed_in_district"
        elif open_globally:
            dest = _pick_destination(open_globally, district, new_dist, priority=priority)
            reason = "closed_no_open_in_district"
        else:
            dest = None
            reason = "closed_no_open_anywhere"

        if dest is not None:
            new_dist[dest] = new_dist.get(dest, 0) + qty
        moves.append(
            {
                "from_warehouse": src,
                "to_warehouse": dest,
                "quantity": qty,
                "reason": reason,
            }
        )

    # Post-pass для consolidate_to_center: если из закрытых складов qty уехал
    # в один центральный склад и скопилось много (≥ min_pack × N_открытых_ЦФО) —
    # разносим равномерно между ВСЕМИ открытыми складами ЦФО. Только если
    # реально что-то консолидировали (есть moves в central) — иначе исходное
    # распределение не трогаем.
    consolidated_amount = sum(
        m["quantity"] for m in moves if m["reason"] == "consolidated_to_center" and m["to_warehouse"] is not None
    )
    if mode == "consolidate_to_center" and consolidated_amount > 0 and len(central_open) >= 2:
        # Главный получатель — тот в чей qty шли консолидации.
        # Среди central_open — тот, у кого после redistribute больше всего.
        center_main = max(central_open, key=lambda w: new_dist.get(w, 0))
        center_qty = new_dist.get(center_main, 0)
        n = len(central_open)
        if center_qty >= min_pack * n:
            per_wh = center_qty // n
            leftover = center_qty - per_wh * n
            new_dist[center_main] = per_wh + leftover
            for w in central_open:
                if w == center_main:
                    continue
                new_dist[w] = new_dist.get(w, 0) + per_wh

    return new_dist, moves


# Box type display order: КОРОБ (дешевле) → МОНО → СУПЕР.
_BOX_TYPE_ORDER = {"box": 0, "mono": 1, "super": 2}


def _coef_to_float(v: object) -> float | None:
    """Coerce WB coefficient/tariff value (int | str | None) → float | None."""
    if v is None:
        return None
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _build_acceptance_limits(
    raw_coefficients: list[dict],
    wh_id_to_name: dict[int, str],
    *,
    warehouse_filter: str | None = None,
    paid_threshold: int = 1,
) -> dict:
    """Build the acceptance calendar (warehouse × package_type × day).

    Pure transform over WB `tariffs/v1/acceptance/coefficients` output →
    AcceptanceLimitsResponse-shaped dict (без `fetched_at` — его ставит
    async-обёртка). No I/O — fully unit-tested.

    Каждая запись = (warehouse_id, canonical_name, box_type); внутри —
    список дней, отсортированных по дате. `dates` — глобально отсортированные
    ISO-даты (колонки календаря).

    warehouse_filter — опц. подстрока (case-insensitive) по canonical ИЛИ
    raw WB-имени склада; пусто/None → все склады.

    День: is_closed = coefficient == -1 ИЛИ allowUnload=false;
          is_free   = НЕ closed И coefficient ∈ {0..paid_threshold}.
    """
    wf = (warehouse_filter or "").strip().lower()

    # (warehouse_id, canonical_name, raw_name, box_type) → {date: day_dict}
    grouped: dict[tuple[int, str, str, str], dict[str, dict]] = {}
    all_dates: set[str] = set()

    for e in raw_coefficients:
        wid = e.get("warehouseID")
        if wid is None:
            continue
        wid = int(wid)
        raw_name = wh_id_to_name.get(wid) or e.get("warehouseName") or ""
        if not raw_name:
            continue
        # Спец-склады (СГТ/Питание/Горючее/СЦ/виртуальные) — вне обычной FBO.
        # Их сырые имена алиасятся в канон базового склада (ACCEPTANCE_TO_STOCK_NAME),
        # поэтому без отсева они засоряют календарь и в supply-slots перетирают
        # реальный склад своим закрытым коробом (last-wins по канон-имени). Отсекаем
        # по СЫРОМУ имени ДО нормализации — зеркало `_flags_for_warehouse`.
        if _is_spec_acceptance_wh(raw_name):
            continue
        canon = _normalize_acceptance_wh(raw_name)
        if not canon:
            continue
        box_type_id = e.get("boxTypeID")
        if box_type_id is None:
            continue
        type_key = _BOX_TYPE_ID_TO_KEY.get(int(box_type_id))
        if type_key is None:
            continue
        if wf and wf not in canon.lower() and wf not in raw_name.lower():
            continue
        raw_date = e.get("date")
        if not raw_date:
            continue
        day = str(raw_date)[:10]  # YYYY-MM-DD
        all_dates.add(day)

        coef = e.get("coefficient")
        coef_f = _coef_to_float(coef)
        allow = bool(e.get("allowUnload"))
        is_closed = coef_f is None or coef_f == -1 or not allow
        is_free = (not is_closed) and coef_f is not None and coef_f <= paid_threshold

        key = (wid, canon, raw_name, type_key)
        # last entry per (key, date) wins — WB отдаёт 1 строку на дату/тип
        grouped.setdefault(key, {})[day] = {
            "date": day,
            "coefficient": coef_f if coef_f is not None else -1.0,
            "allow_unload": allow,
            "is_free": is_free,
            "is_closed": is_closed,
            "storage_coef": _coef_to_float(e.get("storageCoef")),
            "delivery_coef": _coef_to_float(e.get("deliveryCoef")),
        }

    warehouses: list[dict] = []
    for (wid, canon, raw_name, type_key), by_date in grouped.items():
        warehouses.append(
            {
                "warehouse_id": wid,
                "warehouse_name": raw_name,
                "canonical_name": canon,
                "box_type": type_key,
                "days": [by_date[d] for d in sorted(by_date)],
            }
        )

    warehouses.sort(key=lambda w: (w["canonical_name"], _BOX_TYPE_ORDER.get(w["box_type"], 9)))

    return {"warehouses": warehouses, "dates": sorted(all_dates)}


# ─── async public API ───────────────────────────────────────────────────────


def _barcode_cache_key(project_id: int, barcode: str) -> str:
    """Пер-баркодный ключ: доступность — свойство (баркод × склад × упаковка).

    Количества и состав батча в ключе НЕ участвуют: qty на флаги WB не влияет,
    а distribution пересчитывается на каждый запрос (`_apply_distribution`).
    Батч-ключ с qty (до 2026-07-03) промахивался почти на каждый чих UI →
    живой WB-вызов по ВСЕМ товарам при любом изменении → 429.
    """
    return f"wb:acceptance:bc:{project_id}:{barcode}"


async def get_acceptance_closed_warehouses(project_id: int) -> set[str]:
    """Set canonical имён WB-складов, ПОЛНОСТЬЮ закрытых для приёмки.

    «Закрыт» = во всех package_types (box/mono/super) free_days_14 = 0 И
    paid_days_14 = 0 (нет ни одного дня в ближайших 14, когда склад
    готов принять поставку). Это случается при индивидуальной квоте
    WB или явной остановке приёмки на складе.

    Источник — Redis-кэш `wb:acceptance_coefficients:{pid}` +
    `wb:acceptance_warehouses:{pid}`, которые наполняются при первом
    вызове `/warehouse/acceptance-check` (TTL 1ч). НЕ дёргает WB API
    самостоятельно — distribution-расчёт должен быть быстрым; если
    кэш пуст (нет scope, network fail, проект не запрашивал
    приёмку) — fail-open: возвращаем empty set, склады не блокируем.

    Используется в `warehouse_need_service.py` для отсева складов из
    `open_warehouses` ДО priority-chain и Hamilton-распределения.
    """
    redis = await get_redis() if _redis_client is None else _redis_client
    if redis is None:
        return set()
    try:
        coef_raw = await redis.get(f"wb:acceptance_coefficients:{project_id}")
        wh_raw = await redis.get(f"wb:acceptance_warehouses:{project_id}")
    except Exception as e:  # pragma: no cover — graceful degradation
        logger.warning(f"acceptance.closed_warehouses_cache_failed: {e}")
        return set()
    if not coef_raw:
        return set()
    try:
        coefficients: list[dict] = json.loads(coef_raw)
        wh_map: dict[int, str] = {int(k): v for k, v in json.loads(wh_raw).items()} if wh_raw else {}
    except Exception as e:  # pragma: no cover
        logger.warning(f"acceptance.closed_warehouses_parse_failed: {e}")
        return set()

    aggregated = _aggregate_coefficients(coefficients, wh_map)
    # Группируем per warehouse: считаем закрытым только если ВСЕ package_types
    # имеют 0 free + 0 paid.
    by_wh: dict[str, list[dict]] = defaultdict(list)
    for (canon, _type_key), meta in aggregated.items():
        by_wh[canon].append(meta)

    closed: set[str] = set()
    for wh, metas in by_wh.items():
        if all(m["free_days_14"] == 0 and m["paid_days_14"] == 0 for m in metas):
            closed.add(wh)
    return closed


async def get_acceptance_blocked_warehouses(db: AsyncSession, project_id: int) -> set[str]:
    """Closed-by-acceptance warehouses MINUS the preorder whitelist.

    «Закрыт по приёмке» (нет лимита ни по одному типу, см.
    `get_acceptance_closed_warehouses`) → по умолчанию склад блокируется:
    вырезается из расчёта потребности и новинок, его qty перераспределяется на
    ближайший открытый/разрешённый склад. Whitelist `preorder_allowed_warehouses`
    (настройка проекта) — список складов, на которые предзаявку по «нет лимита»
    делать МОЖНО: они НЕ блокируются и остаются в расчёте (с ⌛ предзаявкой).

    Имена сравниваются в каноничном пространстве (`_normalize_acceptance_wh`),
    чтобы whitelist (имена из `get_all_warehouses`, без скобок) совпал с closed
    (canonical WB-имена). Fail-open наследуется от `get_acceptance_closed_warehouses`:
    пустой кэш коэффициентов → пустой set → ничего не блокируем.
    """
    closed = await get_acceptance_closed_warehouses(project_id)
    if not closed:
        return set()
    from backend.services.settings_service import get_preorder_allowed_warehouses

    allowed = await get_preorder_allowed_warehouses(db, project_id)
    allowed_norm = {_normalize_acceptance_wh(w) for w in allowed if w}
    return {w for w in closed if _normalize_acceptance_wh(w) not in allowed_norm}


async def _load_warehouse_id_map(client: WBApiClient, project_id: int) -> dict[int, str]:
    """Cached WB warehouseID → name map (TTL 1 hour)."""
    cache_key = f"wb:acceptance_warehouses:{project_id}"
    redis = await get_redis() if _redis_client is None else _redis_client
    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return {int(k): v for k, v in json.loads(cached).items()}
        except Exception as e:  # pragma: no cover — graceful degradation
            logger.warning(f"acceptance.cache_get_failed: {e}")

    raw = await client.get_fbw_warehouses()
    mapping = {int(w["ID"]): w["name"] for w in raw if w.get("ID") and w.get("name")}

    if redis is not None:
        try:
            await redis.setex(
                cache_key,
                WAREHOUSE_NAMES_CACHE_TTL,
                json.dumps({str(k): v for k, v in mapping.items()}),
            )
        except Exception as e:  # pragma: no cover
            logger.warning(f"acceptance.cache_set_failed: {e}")

    return mapping


async def _load_coefficients(
    client: WBApiClient,
    project_id: int,
    *,
    force: bool = False,
) -> list[dict]:
    """Cached acceptance coefficients (TTL 1h, WB обновляется ~раз в день).

    Returns raw list. Если ключ без scope «Поставки» / 401 / network fail —
    возвращает [] (graceful degradation: в таком случае фильтр по free_days
    не применяется — система ведёт себя как до coefficients-фичи).
    """
    cache_key = f"wb:acceptance_coefficients:{project_id}"
    redis = await get_redis() if _redis_client is None else _redis_client
    if redis is not None and not force:
        try:
            cached = await redis.get(cache_key)
            if cached:
                result: list[dict] = json.loads(cached)
                return result
        except Exception as e:  # pragma: no cover
            logger.warning(f"acceptance.coef_cache_get_failed: {e}")

    try:
        raw: list[dict] = await client.get_acceptance_coefficients()
    except Exception as e:
        logger.warning(f"acceptance.coef_fetch_failed: {e}")
        return []

    if redis is not None and raw:
        try:
            await redis.setex(cache_key, COEFFICIENTS_CACHE_TTL, json.dumps(raw))
        except Exception as e:  # pragma: no cover
            logger.warning(f"acceptance.coef_cache_set_failed: {e}")
    return raw


async def get_acceptance_limits(
    db: AsyncSession,
    project_id: int,
    *,
    warehouse: str | None = None,
    force: bool = False,
) -> dict:
    """Build the WB acceptance calendar (лимиты складов на ближайшие 14 дней).

    Переиспользует кэш коэффициентов и карту warehouseID→name
    (`_load_coefficients`, `_load_warehouse_id_map`, TTL 1ч, WB rate limit
    6 req/min). `force=True` — обойти кэш и дёрнуть WB live (кнопка «🔄»).

    Возвращает AcceptanceLimitsResponse-shaped dict. Если ключ без scope
    «Поставки» / network fail — `_load_coefficients` отдаёт [] → пустой
    календарь (graceful). Если WB-ключ не настроен вовсе — ValueError
    (роутер → 400).
    """
    api_key = await get_wb_key(db, project_id, "wb")
    if not api_key:
        raise ValueError("WB API key not configured for this project")

    client = WBApiClient(api_key=api_key, project_id=project_id)
    wh_id_to_name = await _load_warehouse_id_map(client, project_id)
    raw_coefficients = await _load_coefficients(client, project_id, force=force)

    result = _build_acceptance_limits(raw_coefficients, wh_id_to_name, warehouse_filter=warehouse)
    result["fetched_at"] = utcnow()
    return result


async def check_acceptance_and_redistribute(
    db: AsyncSession,
    project_id: int,
    items: list[dict],
    force: bool = False,
) -> dict:
    """Main entrypoint — used by the `/warehouse/acceptance-check` endpoint.

    items: [{"nm_id": int, "barcode": str, "distribution": {wh_name: qty}}, ...]

    Returns: {
      "items": [{nm_id, barcode, availability: {wh: flags},
                  package_type, distribution (post-redistribute), warnings}],
      "moves": [{nm_id, barcode, from_warehouse, to_warehouse, quantity, reason}],
      "checked_at": ISO datetime,
      "cache_hit": bool,
    }
    """
    if not items:
        return {"items": [], "moves": [], "checked_at": utcnow(), "cache_hit": False}

    # Σ qty по баркоду — только для payload WB (поле обязательно, на флаги не влияет).
    qty_by_barcode: dict[str, int] = defaultdict(int)
    for it in items:
        bc = it.get("barcode")
        if bc:
            qty_by_barcode[bc] += sum((it.get("distribution") or {}).values())
    barcodes = list(qty_by_barcode)
    if not barcodes:
        return {"items": [], "moves": [], "checked_at": utcnow(), "cache_hit": False}

    # Whitelist «⌛ предзаявка без лимита»: склады, куда можно слать без публичного
    # лимита приёмки. Товар на ⌛-склад НЕ из whitelist перераспределяется на
    # свободный/whitelist склад по приоритету (см. _is_open_for / _pick_destination).
    from backend.services.settings_service import get_preorder_allowed_warehouses

    try:
        _allowed_raw = await get_preorder_allowed_warehouses(db, project_id)
        preorder_allowed: set[str] | None = {_normalize_acceptance_wh(w) for w in _allowed_raw if w}
    except Exception as e:  # pragma: no cover — graceful degradation (settings load)
        # Не смогли прочитать whitelist → None (полный legacy fail-open), НЕ set():
        # блокировать ⌛-склады по «пустому» whitelist на транзиентном сбое настроек
        # значило бы увести товар, который юзер, возможно, хотел предзаявить.
        logger.warning(f"acceptance.preorder_whitelist_load_failed: {e}")
        preorder_allowed = None

    redis = await get_redis() if _redis_client is None else _redis_client

    # Пер-баркодный кэш: живой вызов WB — только по недостающим баркодам.
    availability_per_barcode: dict[str, dict[str, dict]] = {}
    if redis is not None and not force:
        try:
            cached_vals = await redis.mget([_barcode_cache_key(project_id, bc) for bc in barcodes])
            for bc, raw_val in zip(barcodes, cached_vals):
                if raw_val is not None:
                    availability_per_barcode[bc] = json.loads(raw_val)
        except Exception as e:  # pragma: no cover — graceful degradation
            logger.warning(f"acceptance.cache_get_failed: {e}")
            availability_per_barcode = {}

    missing = [bc for bc in barcodes if bc not in availability_per_barcode]
    if not missing:
        # Distribution пересчитываем по СВЕЖИМ количествам запроса — кэш хранит
        # только availability.
        return _apply_distribution(
            items, availability_per_barcode, cache_hit=True, preorder_allowed=preorder_allowed
        )

    # Live WB API call — только missing (при force=true это все баркоды батча).
    api_key = await get_wb_key(db, project_id, "wb")
    if not api_key:
        raise ValueError("WB API key not configured for this project")

    client = WBApiClient(api_key=api_key, project_id=project_id)
    wh_id_to_name = await _load_warehouse_id_map(client, project_id)
    raw_coefficients = await _load_coefficients(client, project_id)
    coefficients_by_canon = _aggregate_coefficients(raw_coefficients, wh_id_to_name)
    payload = [{"barcode": bc, "quantity": max(1, qty_by_barcode[bc])} for bc in missing]
    raw = await client.get_acceptance_options(payload)

    # Каждый запрошенный баркод получает запись: не вернул WB / error → {} —
    # негативный кэш, иначе новинки без карточки дырявили бы кэш и заставляли
    # живой вызов на каждую проверку.
    fetched: dict[str, dict[str, dict]] = {bc: {} for bc in missing}
    for entry in raw.get("result", []):
        bc = entry.get("barcode")
        if not bc or bc not in fetched or entry.get("error"):
            continue
        # ВАЖНО: лимит приёмки НЕ блокирует доступность (can_X = options как WB-
        # кабинет). Закрытый/отсутствующий публичный лимит ≠ «нельзя отправить» —
        # на такой склад можно слать предзаявкой. Лимит несём в *_meta
        # (free/paid/closed_days_14) — фронт рисует ⌛ «нет лимита, можно предзаявку».
        fetched[bc] = _flags_for_warehouse(
            entry.get("warehouses") or [],
            wh_id_to_name,
            coefficients_by_canon,
        )
    availability_per_barcode.update(fetched)

    if redis is not None:
        try:
            for bc, av in fetched.items():
                await redis.setex(_barcode_cache_key(project_id, bc), CACHE_TTL_SECONDS, json.dumps(av))
        except Exception as e:  # pragma: no cover
            logger.warning(f"acceptance.cache_set_failed: {e}")

    return _apply_distribution(
        items, availability_per_barcode, cache_hit=False, preorder_allowed=preorder_allowed
    )


def _apply_distribution(
    items: list[dict],
    availability_per_barcode: dict[str, dict[str, dict]],
    *,
    cache_hit: bool,
    preorder_allowed: set[str] | None = None,
) -> dict:
    """Run pure split + redistribute over per-item distributions.

    Каждый SKU может иметь несколько `splits` — отдельные сборки под разные
    package_type. Например, Электросталь принимает только моно, а Краснодар
    только короб → split = [BOX→Краснодар, MONOPALLET→Электросталь].

    Для backwards-compat в response есть и единые `package_type`/`distribution`
    (берутся из крупнейшего по qty split'а).
    """
    out_items = []
    out_moves = []
    for it in items:
        nm_id = it.get("nm_id")
        barcode = it.get("barcode") or ""
        distribution = dict(it.get("distribution") or {})
        availability = availability_per_barcode.get(barcode, {})

        if not availability:
            out_items.append(
                {
                    "nm_id": nm_id,
                    "barcode": barcode,
                    "availability": {},
                    "package_type": "BOX",
                    "distribution": distribution,
                    "splits": [{"package_type": "BOX", "distribution": distribution, "warnings": []}],
                    "warnings": ["WB не вернул данные о приёмке для этого баркода (новинка / без карточки)."],
                }
            )
            continue

        splits, moves = _split_distribution_by_package_type(
            distribution, availability, preorder_allowed=preorder_allowed
        )

        for m in moves:
            out_moves.append(
                {
                    "nm_id": nm_id,
                    "barcode": barcode,
                    **m,
                }
            )

        item_warnings: list[str] = []
        if any(m["to_warehouse"] is None for m in moves):
            item_warnings.append("Не нашли открытый WB-склад — qty закрытых складов вычеркнут.")
        for s in splits:
            for w in s.get("warnings") or []:
                if w not in item_warnings:
                    item_warnings.append(w)

        # Backward-compat: package_type / distribution берём от крупнейшего split'а
        if splits:
            primary = max(splits, key=lambda s: sum(s["distribution"].values()))
            primary_pkg = primary["package_type"]
            primary_dist = primary["distribution"]
        else:
            primary_pkg = "BOX"
            primary_dist = {}

        # Strip flags to schema shape (включая meta из coefficients)
        def _meta_to_schema(m: dict | None) -> dict | None:
            if not m:
                return None
            return {
                "free_days_14": m.get("free_days_14", 0),
                "paid_days_14": m.get("paid_days_14", 0),
                "min_coefficient": m.get("min_coefficient"),
            }

        avail_schema = {
            wh: {
                "warehouse_id": flags["warehouse_id"],
                "can_box": flags["can_box"],
                "can_monopallet": flags["can_monopallet"],
                "can_supersafe": flags["can_supersafe"],
                "box_meta": _meta_to_schema(flags.get("box_meta")),
                "mono_meta": _meta_to_schema(flags.get("mono_meta")),
                "super_meta": _meta_to_schema(flags.get("super_meta")),
            }
            for wh, flags in availability.items()
        }

        out_items.append(
            {
                "nm_id": nm_id,
                "barcode": barcode,
                "availability": avail_schema,
                "package_type": primary_pkg,
                "distribution": primary_dist,
                "splits": splits,
                "warnings": item_warnings,
            }
        )

    return {
        "items": out_items,
        "moves": out_moves,
        "checked_at": utcnow(),
        "cache_hit": cache_hit,
    }


__all__ = [
    "check_acceptance_and_redistribute",
    "redistribute_blocked_qty",
    "_pick_package_type",
    "_flags_for_warehouse",
]
