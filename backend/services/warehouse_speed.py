# ruff: noqa: RUF002
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
  • `get_priority_score(warehouse, okrug)` — насколько склад быстр для ФО (0..1).
  • `sort_warehouses_by_priority(warehouses, okrug)` — упорядочить по приоритету.
  • `is_stealer_for_okrug(warehouse, target_okrug)` — склад другого ФО лезет в target.
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
        return json.load(f)


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


@lru_cache(maxsize=1024)
def get_priority_score(warehouse: str, okrug: str) -> float:
    """Скор склада как быстрого для городов этого ФО.

    Для каждого города ФО, в priority list которого встречается склад,
    прибавляем 1/(idx+1) (priority 1 → +1.0, p2 → +0.5, p3 → +0.33, …).
    Делим на число городов ФО → нормализованный [0..1].

    1.0 = склад первый приоритет во ВСЕХ городах ФО.
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
    return score / len(cities)


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
    """Принадлежит другому ФО, но WB всё равно направляет в target_okrug."""
    canon = canonical_warehouse_name(warehouse)
    if not canon:
        return False
    own = warehouse_to_district(canon)
    if own in (DISTRICT_UNKNOWN, target_okrug):
        return False
    cities = _cities_by_okrug().get(target_okrug, [])
    return any(canon in c.warehouses for c in cities)


@lru_cache(maxsize=16)
def get_stealers_for_okrug(target_okrug: str) -> tuple[tuple[str, int], ...]:
    """Все склады других ФО, встречающиеся в priority chains target_okrug.

    Возвращает `((warehouse_canon, cities_count), ...)` по убыванию count.
    """
    cities = _cities_by_okrug().get(target_okrug, [])
    counts: dict[str, int] = {}
    for c in cities:
        for wh in c.warehouses:
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
