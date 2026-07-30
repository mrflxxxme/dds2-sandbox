# ruff: noqa: RUF001, RUF002, RUF003
"""Слоты сдачи приёмки WB для активных заявок (Лист логиста → поставки).

Для каждой активной заявки (IN_PROGRESS / READY / VEHICLE_ASSIGNED) с привязанной
ФБО-поставкой берём её склад назначения WB и достаём календарь коэффициентов
приёмки этого склада (free / paid / closed по дням) — те же данные, что и
`/warehouse/acceptance-limits`, но привязанные к конкретной поставке. Это даёт
логисту увидеть, в какие ещё дни можно сдать поставку, кроме плановой даты.

Один общий вызов `get_acceptance_limits()` на всю выдачу (кэш коэффициентов 1ч,
WB rate limit 6 req/min) + матч по складу — без N+1 и без миграции. Связка
склад→коэффициенты идёт по НОРМАЛИЗОВАННОМУ имени (`WbFboSupply` хранит только
`warehouse_name`, не numeric warehouseID); если склад не нашёлся — строка с
`matched=false`, а не молча пустой список.
"""

from datetime import date

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyKind, AssemblyRequest, AssemblyStatus
from backend.models.wb_fbo import WbFboSupply
from backend.services.warehouse_acceptance_service import (
    _normalize_acceptance_wh,
    get_acceptance_limits,
)
from backend.utils.time import utcnow

# Заявки «в работе по логистике» — те, что ещё предстоит сдать на WB.
_ACTIVE_STATUSES = (
    AssemblyStatus.IN_PROGRESS.value,
    AssemblyStatus.READY.value,
    AssemblyStatus.VEHICLE_ASSIGNED.value,
)

# Защитный кап: active-статусы транзитны и self-limiting (десятки заявок), но
# страхуемся от вырожденного проекта, чтобы не тянуть неограниченную выборку.
_MAX_ACTIVE_SUPPLIES = 500

# package_type заявки → ключ типа упаковки в календаре коэффициентов.
_PACKAGE_TYPE_TO_BOX_KEY = {
    "BOX": "box",
    "MONOPALLET": "mono",
    "SUPERSAFE": "super",
}


# ─── pure helper (no I/O — unit-tested) ──────────────────────────────────────


def _build_supply_acceptance_slots(requests: list[dict], limits: dict) -> dict:
    """Сматчить каждую активную заявку с календарём приёмки её склада.

    requests: [{id, number, status, package_type, effective_warehouse,
                wb_supply_id, planned_date, actual_date, wb_status}]
    limits: AcceptanceLimitsResponse-shaped dict (`warehouses[]`, `dates[]`)
            из `get_acceptance_limits` / `_build_acceptance_limits`.

    Матч по (нормализованное имя склада, box_type). `matched=false` и `days=[]`,
    если для склада+типа в календаре нет записи (склад закрыт совсем / не
    сматчилось имя / ключ без scope «Поставки»).
    """
    # Два индекса: по сырому имени склада WB и по канон-имени. Несколько
    # физических складов могут нормализоваться в одно канон-имя («Астана»/«Астана 2»
    # → «Астана Карагандинское шоссе»), поэтому матч строго по канону = last-wins и
    # рискует выбрать закрытый склад-двойник. Точное совпадение сырого имени поставки
    # (`warehouse_name` хранит конкретный склад) приоритетнее.
    by_raw: dict[tuple[str, str], dict] = {}
    by_key: dict[tuple[str, str], dict] = {}
    for entry in limits.get("warehouses", []):
        by_key[(entry["canonical_name"], entry["box_type"])] = entry
        raw_name = entry.get("warehouse_name")
        if raw_name:
            by_raw[(raw_name, entry["box_type"])] = entry

    rows: list[dict] = []
    for r in requests:
        eff = r.get("effective_warehouse")
        canon = _normalize_acceptance_wh(eff)
        box = _PACKAGE_TYPE_TO_BOX_KEY.get(r.get("package_type") or "BOX", "box")
        entry = by_raw.get((eff, box)) if eff else None
        if entry is None:
            entry = by_key.get((canon, box))
        rows.append(
            {
                "assembly_request_id": r["id"],
                "assembly_number": r["number"],
                "status": r["status"],
                "wb_supply_id": r.get("wb_supply_id"),
                "warehouse_name": eff,
                "canonical_name": canon,
                "warehouse_id": entry["warehouse_id"] if entry else None,
                "box_type": box,
                "package_type": r.get("package_type") or "BOX",
                "planned_date": r.get("planned_date"),
                "actual_date": r.get("actual_date"),
                "wb_fbo_status": r.get("wb_status"),
                "matched": entry is not None,
                "days": entry["days"] if entry else [],
            }
        )

    rows.sort(
        key=lambda x: (
            x["canonical_name"] or "￿",
            x["planned_date"] or date.max,
            x["assembly_number"],
        )
    )
    return {"rows": rows, "dates": limits.get("dates", [])}


# ─── async public API ────────────────────────────────────────────────────────


async def _load_active_supply_rows(db: AsyncSession, project_id: int) -> list[dict]:
    """Активные заявки + поля их ФБО-поставки (склад/дата/№), одним запросом."""
    stmt = (
        select(
            AssemblyRequest.id,
            AssemblyRequest.number,
            AssemblyRequest.status,
            AssemblyRequest.package_type,
            AssemblyRequest.wb_warehouse_name_manual,
            WbFboSupply.wb_supply_id,
            WbFboSupply.warehouse_name,
            WbFboSupply.planned_date,
            WbFboSupply.actual_date,
            WbFboSupply.wb_status,
        )
        .select_from(AssemblyRequest)
        .outerjoin(
            WbFboSupply,
            and_(
                AssemblyRequest.wb_fbo_supply_id == WbFboSupply.id,
                WbFboSupply.project_id == project_id,  # tenant-safe джойн (не полагаемся на FK)
            ),
        )
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted == False,  # noqa: E712
            AssemblyRequest.status.in_(_ACTIVE_STATUSES),
            # Зеркала FBS не сдаются на склады WB: их wb_warehouse_name_manual —
            # имя склада ПРОДАВЦА, матч по нему давал фантомные строки слотов.
            AssemblyRequest.kind != AssemblyKind.FBS.value,
        )
        .limit(_MAX_ACTIVE_SUPPLIES)
    )
    res = await db.execute(stmt)

    rows: list[dict] = []
    for row in res.all():
        # effective склад: ФБО (warehouse_name) или ручное переопределение.
        eff = row.warehouse_name or row.wb_warehouse_name_manual
        if not eff:
            continue  # без склада назначения слоты показать не от чего
        rows.append(
            {
                "id": row.id,
                "number": row.number,
                "status": row.status,
                "package_type": row.package_type,
                "effective_warehouse": eff,
                "wb_supply_id": row.wb_supply_id,
                "planned_date": row.planned_date,
                "actual_date": row.actual_date,
                "wb_status": row.wb_status,
            }
        )
    return rows


async def get_supply_acceptance_slots(
    db: AsyncSession,
    project_id: int,
    *,
    force: bool = False,
) -> dict:
    """Активные заявки + календарь слотов приёмки их складов WB.

    Один вызов `get_acceptance_limits` на всю страницу (кэш 1ч). `force=True`
    обходит кэш (UI «🔄»). Если активных заявок нет — пустая выдача без обращения
    к WB. ValueError (от `get_acceptance_limits`), если WB-ключ не настроен →
    роутер отдаёт 400.
    """
    requests = await _load_active_supply_rows(db, project_id)
    if not requests:
        return {"rows": [], "dates": [], "fetched_at": utcnow()}

    limits = await get_acceptance_limits(db, project_id, warehouse=None, force=force)
    result = _build_supply_acceptance_slots(requests, limits)
    result["fetched_at"] = limits["fetched_at"]
    return result
