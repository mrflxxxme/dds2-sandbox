# ruff: noqa: RUF001, RUF002, RUF003
"""Сервис приоритетов скорости доставки складов WB.

Источник истины: `backend/data/wb_warehouse_speed.json` (выгрузка из
tg:@POSTAVLENOru_BOT, лист `bot_data`). Структура:
  {
    "version": "...",
    "source": "...",
    "okrug_keys": [...],
    "cities": [
      {"city": "Нижний Новгород", "okrug": "volga",
       "priorities": [{"warehouse": "Владимир", "hours": 19}, ...]}
    ]
  }

Что даёт сервис:
  • `get_priority_score(warehouse, okrug)` — насколько склад быстр для ФО.
  • `sort_warehouses_by_priority(warehouses, okrug)` — упорядочить по приоритету.
  • `is_stealer_for_okrug(warehouse, target_okrug)` — склад другого ФО стоит
    в top-2 цепочки хотя бы одного города target (реально отбирает спрос).
  • `get_stealers_for_okrug(target_okrug)` — список «воришек» с количеством городов.
  • `get_anchors_for_okrug(target_okrug)` — якорные склады ФО (top-1/top-2).
  • `compute_realistic_ceiling(loaded_warehouses)` — доля городов, для которых
    первый загруженный priority — local (anchor, не stealer).
  • `evaluate_basket(loaded_warehouses)` — структурные ошибки корзины.

Все функции чистые, без БД. JSON-таблица lazy-load один раз.

Используется в `warehouse_need_service.py` для распределения по приоритету
(вместо пропорционального share округа), и во фронтенде через эндпоинт
`/warehouse/speed/*` для бейджей 🐭, warnings и realistic-ceiling KPI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from backend.services.warehouse_district import (
    DISTRICT_LABELS,
    DISTRICT_UNKNOWN,
    canonical_warehouse_name,
    warehouse_to_district,
)

_SPEED_TABLE_PATH = Path(__file__).resolve().parent.parent / "data" / "wb_warehouse_speed.json"
_MAX_PRIORITY_SLOTS = 6

# Склад считается «воришкой» чужого ФО, только если стоит в top-N цепочки
# хотя бы одного города этого ФО. Глубокие слоты (3+) — fallback-маршруты WB,
# туда почти ничего не едет; бинарный флаг по ЛЮБОЙ глубине душил якоря:
# СПБ Шушары получал штраф 0.6 за слоты #4/#6 у Читы и Алматы (аудит 2026-07-09).
_STEALER_MAX_DEPTH = 2

# Склады-«близнецы» одного города, открытые ПОЗЖЕ выгрузки speed-карты
# (v2026-05-14): их нет ни в одной priority-цепочке из 97 городов → спрос на
# них не мапится никогда. Твин наследует слот БАЗОВОГО склада цепочки, только
# если база excluded/закрыта (приоритет базы не трогаем). Ключи и значения —
# в stock-каноне WAREHOUSE_COORDS. При обновлении speed-карты — пересмотреть.
_CHAIN_TWINS: dict[str, tuple[str, ...]] = {
    "СПБ Шушары": ("Санкт-Петербург Уткина Заводь",),
}

WarningKind = Literal["anchor_missing", "stealer_without_anchor", "low_ceiling"]
Severity = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class CityPriority:
    """Priority chain одного города (имена складов уже канонизированы)."""

    city: str
    okrug: str
    warehouses: tuple[str, ...]
    hours: tuple[int, ...]


@dataclass(frozen=True)
class BasketWarning:
    """Структурное замечание о корзине загруженных складов."""

    kind: WarningKind
    severity: Severity
    okrug: str
    message: str
    stealer: str | None = None
    suggested_anchors: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def _load_speed_table() -> dict:
    if not _SPEED_TABLE_PATH.exists():
        return {"version": "missing", "cities": []}
    with _SPEED_TABLE_PATH.open(encoding="utf-8") as f:
        data: dict = json.load(f)
        return data


@lru_cache(maxsize=1)
def _all_cities() -> list[CityPriority]:
    raw = _load_speed_table()
    out: list[CityPriority] = []
    for c in raw.get("cities", []):
        whs: list[str] = []
        hours: list[int] = []
        for p in c.get("priorities", [])[:_MAX_PRIORITY_SLOTS]:
            canon = canonical_warehouse_name(p.get("warehouse"))
            if not canon:
                continue
            whs.append(canon)
            hours.append(int(p.get("hours", 0)))
        out.append(
            CityPriority(
                city=c["city"],
                okrug=c["okrug"],
                warehouses=tuple(whs),
                hours=tuple(hours),
            )
        )
    return out


@lru_cache(maxsize=1)
def _cities_by_okrug() -> dict[str, list[CityPriority]]:
    res: dict[str, list[CityPriority]] = {}
    for c in _all_cities():
        res.setdefault(c.okrug, []).append(c)
    return res


def speed_table_meta() -> dict:
    """Метаданные источника (для UI: «версия 2026-05-14, 97 городов»)."""
    raw = _load_speed_table()
    return {
        "version": raw.get("version", "unknown"),
        "source": raw.get("source", ""),
        "cities_count": len(_all_cities()),
        "okrug_keys": raw.get("okrug_keys", []),
    }


def iter_all_cities() -> list[CityPriority]:
    """Публичный read-only getter для списка городов с priority-цепочками.

    Возвращает кэшированный список (lru_cache на _all_cities) — мутировать нельзя.
    Используется роутером `/warehouse/speed/cities` для отдачи всей таблицы.
    """
    return _all_cities()


@lru_cache(maxsize=16)
def _localizable_cities_count(okrug: str) -> int:
    """Число городов ФО, в чью priority-chain входит хотя бы один склад ЭТОГО ЖЕ ФО.

    «Нелокализуемые» города (Псков/Мурманск/Архангельск/… — WB возит туда
    только чужими складами) не должны размывать скор якорей: продавец не
    может на них повлиять никаким выбором склада. Без этого якорь СЗФО
    (Шушары, top-1 у 3 из 4 достижимых городов) весил 0.300 против 1.000
    у якоря УФО и проигрывал cap на каждом SKU (аудит 2026-07-09).

    Fallback: если у ФО вообще нет своих складов в карте — len(всех городов),
    чтобы не делить на ноль (скоры складов там всё равно межокружные).
    """
    cities = _cities_by_okrug().get(okrug, [])
    n = sum(1 for c in cities if any(warehouse_to_district(wh) == okrug for wh in c.warehouses))
    return n or len(cities)


@lru_cache(maxsize=1024)
def get_priority_score(warehouse: str, okrug: str) -> float:
    """Скор склада как быстрого для городов этого ФО.

    Для каждого города ФО, в priority list которого встречается склад,
    прибавляем 1/(idx+1) (priority 1 → +1.0, p2 → +0.5, p3 → +0.33, …).
    Делим на число ЛОКАЛИЗУЕМЫХ городов ФО (см. _localizable_cities_count).

    Для склада СВОЕГО ФО скор нормализован [0..1]: 1.0 = первый приоритет
    во всех локализуемых городах (его города-вхождения локализуемы по
    определению). Для чужого склада скор может превышать 1.0 — потребители
    сравнивают скоры только внутри одного ФО, порядок сохраняется.
    0.0 = склад вообще не встречается в priority chains этого ФО.
    """
    canon = canonical_warehouse_name(warehouse)
    if not canon:
        return 0.0
    cities = _cities_by_okrug().get(okrug, [])
    if not cities:
        return 0.0
    score = 0.0
    for c in cities:
        if canon in c.warehouses:
            idx = c.warehouses.index(canon)
            score += 1.0 / (idx + 1)
    return score / _localizable_cities_count(okrug)


def find_priority_warehouse(
    city: str | None,
    open_warehouses: set[str] | list[str],
    okrug: str | None = None,
) -> str | None:
    """Найти первый open склад в priority-chain города из speed-карты.

    Используется в warehouse_need_service.localization_optimized как
    fallback ДО haversine: WB маршрутизирует заказы по часам доставки
    (priority-chain города), а не по евклидову расстоянию. Если top-1
    склад города закрыт (excluded в настройках проекта), заказ должен
    попасть на priority-2 ТОГО ЖЕ ГОРОДА, а не на ближайший по карте.

    Стратегия:
      1. Если `city` известен и есть в speed-карте — идём по priority-chain
         города, возвращаем первый open склад.
      2. Иначе если `okrug` известен — берём агрегатный priority-score
         каждого склада по городам ФО (`get_priority_score`), сортируем
         desc, возвращаем первый open с score > 0.
      3. Иначе None — пусть caller fallback'нется на haversine.
    """
    open_set = set(open_warehouses)

    # Цепочки городов канонизированы в speed-канон (_all_cities →
    # canonical_warehouse_name: «Тула» → «Алексин (Тула)»), а open_warehouses
    # приходит в stock-каноне (ключи WAREHOUSE_COORDS: «Тула»). Сравнение
    # `wh in open_set` без канонизации входа никогда не матчило Тулу —
    # top-1 у 11 городов ЦФО был недостижим через chain-walk, спрос уезжал
    # в Коледино/Электросталь/Воронеж (аудит 2026-07-14). Канонизируем обе
    # стороны, возвращаем ОРИГИНАЛЬНОЕ имя caller'а: downstream
    # (warehouse_need_service, матрица) живёт в stock-каноне.
    open_by_canon: dict[str, str] = {}
    for w in sorted(open_set):  # sorted: детерминизм при коллизии канонов
        cw = canonical_warehouse_name(w)
        if cw and cw not in open_by_canon:
            open_by_canon[cw] = w

    if city:
        for c in _all_cities():
            if c.city == city:
                for wh in c.warehouses:
                    if wh in open_by_canon:
                        return open_by_canon[wh]
                    # Твин наследует слот базы, когда база excluded/закрыта:
                    # склады, открытые ПОЗЖЕ выгрузки speed-карты, не входят
                    # ни в одну цепочку и были недостижимы (Уткина Заводь).
                    for _twin in _CHAIN_TWINS.get(wh, ()):
                        _tc = canonical_warehouse_name(_twin)
                        if _tc in open_by_canon:
                            return open_by_canon[_tc]
                # Город найден, но все приоритеты excluded — пробуем okrug
                # ниже как fallback (а не сразу haversine — он точно хуже).
                if not okrug:
                    okrug = c.okrug
                break

    if okrug:
        scored: list[tuple[str, float]] = []
        for wh in open_set:
            score = get_priority_score(wh, okrug)
            if score > 0:
                scored.append((wh, score))
        if scored:
            # Сначала склады СВОЕГО ФО: агрегатный score чужого хаба (Электросталь
            # для northwest ≈1.47) обгонял родные Шушары (0.75) — немапленные
            # города/пригороды СЗФО (Мурино, Гатчина…) утекали в ЦФО и рушили
            # локализацию округа (аудит 2026-07-09/14). Чужой хаб — только если
            # в своём ФО нет ни одного склада с ненулевым скором.
            scored.sort(key=lambda kv: (warehouse_to_district(kv[0]) != okrug, -kv[1], kv[0]))
            return scored[0][0]

    return None


def sort_warehouses_by_priority(warehouses: list[str], okrug: str) -> list[str]:
    """Упорядочить переданный список складов по приоритету скорости для ФО.

    Самый быстрый для городов ФО — первый. Стабильная сортировка:
    при равных скорах — лексикографически по канон-имени.
    Имена нормализуются (alias-map) перед сравнением, возвращаются
    в исходном виде из входа.
    """
    if not warehouses:
        return []
    seen: dict[str, str] = {}  # canonical → original
    for w in warehouses:
        c = canonical_warehouse_name(w)
        if c and c not in seen:
            seen[c] = w
    scored = sorted(
        seen.items(),
        key=lambda kv: (-get_priority_score(kv[0], okrug), kv[0]),
    )
    return [orig for _, orig in scored]


def is_stealer_for_okrug(warehouse: str, target_okrug: str) -> bool:
    """Принадлежит другому ФО и стоит в top-2 цепочки города target_okrug.

    Учитываются только слоты idx < _STEALER_MAX_DEPTH: туда WB реально
    маршрутизирует заказы. Глубокое (3+) появление в чужой цепочке —
    fallback-маршрут, не кража спроса, штрафовать за него нельзя
    (кейс Шушар: слоты #4/#6 у far_east давали бинарный thief-флаг).
    """
    canon = canonical_warehouse_name(warehouse)
    if not canon:
        return False
    own = warehouse_to_district(canon)
    if own in (DISTRICT_UNKNOWN, target_okrug):
        return False
    cities = _cities_by_okrug().get(target_okrug, [])
    return any(canon in c.warehouses[:_STEALER_MAX_DEPTH] for c in cities)


@lru_cache(maxsize=16)
def get_stealers_for_okrug(target_okrug: str) -> tuple[tuple[str, int], ...]:
    """Склады других ФО, стоящие в top-2 priority chains городов target_okrug.

    Возвращает `((warehouse_canon, cities_count), ...)` по убыванию count,
    где count — число городов, у которых склад в top-2 (та же семантика,
    что is_stealer_for_okrug: глубокие fallback-слоты не считаются).
    """
    cities = _cities_by_okrug().get(target_okrug, [])
    counts: dict[str, int] = {}
    for c in cities:
        for wh in c.warehouses[:_STEALER_MAX_DEPTH]:
            own = warehouse_to_district(wh)
            if own != target_okrug and own != DISTRICT_UNKNOWN:
                counts[wh] = counts.get(wh, 0) + 1
    return tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


@lru_cache(maxsize=16)
def get_anchors_for_okrug(target_okrug: str) -> tuple[tuple[str, int], ...]:
    """Anchor-склады ФО — те же ФО, встречающиеся как priority 1 или 2.

    Возвращает `((warehouse_canon, cities_count_top2), ...)` desc.
    Сюда не попадают «приёмочные» склады других ФО — только локальные.
    """
    cities = _cities_by_okrug().get(target_okrug, [])
    counts: dict[str, int] = {}
    for c in cities:
        for wh in c.warehouses[:2]:
            own = warehouse_to_district(wh)
            if own == target_okrug:
                counts[wh] = counts.get(wh, 0) + 1
    return tuple(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def compute_realistic_ceiling(loaded_warehouses: list[str]) -> float:
    """Доля городов, где первый ЗАГРУЖЕННЫЙ priority — local (anchor).

    Алгоритм:
      для каждого города идём по его priority chain в порядке скорости,
      на первом встретившемся складе из loaded — стоп.
      Если этот склад принадлежит тому же ФО что и город → +1 (локально).
      Если другой ФО (stealer) → 0 (нелокально, потеря неизбежна).
      Если ни один из priorities не загружен → 0 (товар придёт сильно позже).

    Возвращает 0.0..1.0. Удобно для KPI «Реалистичный потолок ИЛ».
    """
    canon_set: set[str | None] = {canonical_warehouse_name(w) for w in loaded_warehouses if w}
    canon_set.discard(None)
    cities = _all_cities()
    if not cities:
        return 0.0
    local_count = 0
    total = 0
    for c in cities:
        if not c.warehouses:
            continue
        total += 1
        for wh in c.warehouses:
            if wh in canon_set:
                if warehouse_to_district(wh) == c.okrug:
                    local_count += 1
                break
    return local_count / total if total else 0.0


def evaluate_basket(loaded_warehouses: list[str]) -> list[BasketWarning]:
    """Структурные ошибки текущей корзины загруженных складов.

    Сейчас детектируется один паттерн: stealer без anchor для своего ФО.
    Пример: грузим Воронеж (stealer для ПФО), но Казани/Сарапула/Пензы нет —
    Нижний/Саранск/Саратов уйдут нелокально. Предлагаем top-2 anchor ФО.
    """
    canon: set[str | None] = {canonical_warehouse_name(w) for w in loaded_warehouses if w}
    canon.discard(None)
    warnings_list: list[BasketWarning] = []

    for okrug in _cities_by_okrug():
        anchors = get_anchors_for_okrug(okrug)
        stealers = get_stealers_for_okrug(okrug)
        loaded_anchors = [a for a, _ in anchors if a in canon]
        loaded_stealers = [s for s, _ in stealers if s in canon]
        if loaded_stealers and not loaded_anchors:
            top_anchors = tuple(a for a, _ in anchors[:3])
            warnings_list.append(
                BasketWarning(
                    kind="stealer_without_anchor",
                    severity="warning",
                    okrug=okrug,
                    message=(
                        f"{DISTRICT_LABELS.get(okrug, okrug)}: грузите "
                        f"{', '.join(loaded_stealers[:3])} без anchor-склада — "
                        f"добавьте {', '.join(top_anchors[:2])}"
                    ),
                    stealer=loaded_stealers[0],
                    suggested_anchors=top_anchors[:2],
                )
            )
    return warnings_list
