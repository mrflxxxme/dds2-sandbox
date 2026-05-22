# ruff: noqa: RUF002
"""Router: /api/v1/warehouse/speed/* — справочный API «Приоритет складов WB».

Эти эндпоинты обслуживают одноимённую страницу. Данных из БД нет —
исходник `backend/data/wb_warehouse_speed.json` (выгрузка от
tg:@POSTAVLENOru_BOT). Логика — pure-функции в
`backend/services/warehouse_speed.py`; роутер только HTTP-обвязка +
сериализация в Pydantic.

Эндпоинты:
  GET  /warehouse/speed/meta         — версия источника, число городов
  GET  /warehouse/speed/okrug-info   — anchors/stealers top-5 по каждому ФО
  GET  /warehouse/speed/evaluate     — оценка корзины загруженных складов
  GET  /warehouse/speed/cities       — полный список priority-цепочек
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.schemas.warehouse_speed import (
    BasketEvaluation,
    BasketWarningDTO,
    CityPriorityRow,
    CitySpeedDTO,
    OkrugBasketStats,
    OkrugInfo,
    SpeedMeta,
    WarehouseCount,
)
from backend.services.warehouse_district import (
    DISTRICT_ABROAD,
    DISTRICT_LABELS,
    DISTRICT_ORDER,
    DISTRICT_UNKNOWN,
    canonical_warehouse_name,
    warehouse_to_district,
)
from backend.services.warehouse_speed import (
    compute_realistic_ceiling,
    evaluate_basket,
    get_anchors_for_okrug,
    get_stealers_for_okrug,
    iter_all_cities,
    speed_table_meta,
)

router = APIRouter(prefix="/warehouse/speed", tags=["Warehouse Speed"])

_TOP_N = 5  # top-N anchors / stealers в /okrug-info
_HIDDEN_OKRUGS = {DISTRICT_ABROAD, DISTRICT_UNKNOWN}


# ─── helpers ────────────────────────────────────────────────────────────────


def _parse_warehouses(raw: str) -> list[str]:
    """Распарсить `?warehouses=A,B,C` → list[str] без пустых элементов."""
    return [item.strip() for item in raw.split(",") if item and item.strip()]


def _visible_okrug_keys() -> list[str]:
    """Все коды ФО (по DISTRICT_ORDER) кроме abroad/unknown."""
    return [okrug for okrug in DISTRICT_ORDER if okrug not in _HIDDEN_OKRUGS]


# ─── /meta ──────────────────────────────────────────────────────────────────


@router.get("/meta", response_model=SpeedMeta)
async def get_speed_meta() -> SpeedMeta:
    """Метаданные источника (версия выгрузки, число городов, список ФО)."""
    meta = speed_table_meta()
    return SpeedMeta(
        version=str(meta.get("version", "unknown")),
        source=str(meta.get("source", "")),
        cities_count=int(meta.get("cities_count", 0)),
        okrug_keys=list(meta.get("okrug_keys", [])),
    )


# ─── /okrug-info ────────────────────────────────────────────────────────────


@router.get("/okrug-info", response_model=list[OkrugInfo])
async def get_okrug_info() -> list[OkrugInfo]:
    """Для каждого ФО — топ-5 anchor и топ-5 stealer складов.

    `anchors_top`  — склады своего ФО, встречающиеся в priority-цепочках
    городов ФО (отсортировано по числу городов desc, ограничено _TOP_N).
    `stealers_top` — склады других ФО, всё равно попадающие в priority
    городов этого ФО.
    """
    out: list[OkrugInfo] = []
    for okrug in _visible_okrug_keys():
        anchors = get_anchors_for_okrug(okrug)[:_TOP_N]
        stealers = get_stealers_for_okrug(okrug)[:_TOP_N]
        out.append(
            OkrugInfo(
                okrug_key=okrug,
                okrug_label=DISTRICT_LABELS.get(okrug, okrug),
                anchors_top=[WarehouseCount(warehouse_name=name, cities_count=count) for name, count in anchors],
                stealers_top=[WarehouseCount(warehouse_name=name, cities_count=count) for name, count in stealers],
            )
        )
    return out


# ─── /evaluate ──────────────────────────────────────────────────────────────


@router.get("/evaluate", response_model=BasketEvaluation)
async def evaluate_basket_endpoint(
    warehouses: str = Query(
        ...,
        description="Comma-separated имена складов, например 'Казань,Электросталь'",
    ),
    include_cities: bool = Query(
        default=False,
        description="Если true — в ответ кладём полный список cities[]",
    ),
) -> BasketEvaluation:
    """Оценить корзину загруженных складов.

    Возвращает:
      * `realistic_ceiling_pct` — потолок ИЛ в процентах,
      * счётчики local/stealer/uncovered/total городов,
      * массив warnings (kind/severity/okrug/...),
      * срез per_okrug (anchor/stealer-склады корзины + покрытие).
      * cities[] при `?include_cities=true`.
    """
    loaded = _parse_warehouses(warehouses)
    canon_loaded: list[str] = []
    canon_set: set[str] = set()
    for w in loaded:
        c = canonical_warehouse_name(w)
        if c and c not in canon_set:
            canon_set.add(c)
            canon_loaded.append(c)

    ceiling = compute_realistic_ceiling(loaded)
    warnings_raw = evaluate_basket(loaded)

    # Подсчёт local/stealer/uncovered и per_okrug — один проход по таблице.
    cities = iter_all_cities()
    cities_local = 0
    cities_stealer = 0
    cities_uncovered = 0
    cities_total = 0

    okrug_local_cnt: dict[str, int] = {}
    okrug_total_cnt: dict[str, int] = {}

    for city in cities:
        if not city.warehouses:
            continue
        cities_total += 1
        okrug_total_cnt[city.okrug] = okrug_total_cnt.get(city.okrug, 0) + 1

        matched: str | None = None
        for wh in city.warehouses:
            if wh in canon_set:
                matched = wh
                break

        if matched is None:
            cities_uncovered += 1
            continue
        if warehouse_to_district(matched) == city.okrug:
            cities_local += 1
            okrug_local_cnt[city.okrug] = okrug_local_cnt.get(city.okrug, 0) + 1
        else:
            cities_stealer += 1

    # per_okrug: anchor/stealer-склады корзины, покрытие.
    per_okrug: list[OkrugBasketStats] = []
    for okrug in _visible_okrug_keys():
        anchors_all = {name for name, _ in get_anchors_for_okrug(okrug)}
        stealers_all = {name for name, _ in get_stealers_for_okrug(okrug)}
        anchors_in_basket = [name for name in canon_loaded if name in anchors_all]
        stealers_in_basket = [name for name in canon_loaded if name in stealers_all]
        per_okrug.append(
            OkrugBasketStats(
                okrug_key=okrug,
                okrug_label=DISTRICT_LABELS.get(okrug, okrug),
                anchors_in_basket=anchors_in_basket,
                stealers_in_basket=stealers_in_basket,
                cities_covered_locally=okrug_local_cnt.get(okrug, 0),
                cities_total=okrug_total_cnt.get(okrug, 0),
            )
        )

    warnings_dto = [
        BasketWarningDTO(
            kind=w.kind,
            severity=w.severity,
            okrug=w.okrug,
            okrug_label=DISTRICT_LABELS.get(w.okrug, w.okrug),
            message=w.message,
            stealer=w.stealer,
            suggested_anchors=list(w.suggested_anchors),
        )
        for w in warnings_raw
    ]

    cities_dto: list[CitySpeedDTO] | None = None
    if include_cities:
        cities_dto = [
            CitySpeedDTO(
                city=c.city,
                okrug_key=c.okrug,
                okrug_label=DISTRICT_LABELS.get(c.okrug, c.okrug),
                priorities=[
                    CityPriorityRow(warehouse_name=w, hours=h) for w, h in zip(c.warehouses, c.hours, strict=False)
                ],
            )
            for c in cities
        ]

    return BasketEvaluation(
        loaded_warehouses=canon_loaded,
        realistic_ceiling_pct=round(ceiling * 100.0, 2),
        cities_local=cities_local,
        cities_stealer=cities_stealer,
        cities_uncovered=cities_uncovered,
        cities_total=cities_total,
        warnings=warnings_dto,
        per_okrug=per_okrug,
        cities=cities_dto,
    )


# ─── /cities ────────────────────────────────────────────────────────────────


@router.get("/cities", response_model=list[CitySpeedDTO])
async def list_cities() -> list[CitySpeedDTO]:
    """Полный список городов с их priority-цепочками."""
    return [
        CitySpeedDTO(
            city=c.city,
            okrug_key=c.okrug,
            okrug_label=DISTRICT_LABELS.get(c.okrug, c.okrug),
            priorities=[
                CityPriorityRow(
                    warehouse_name=w,
                    hours=h,
                    own_okrug=warehouse_to_district(w),
                )
                for w, h in zip(c.warehouses, c.hours, strict=False)
            ],
        )
        for c in iter_all_cities()
    ]
