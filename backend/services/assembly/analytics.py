# ruff: noqa: RUF001, RUF002, RUF003
"""
Assembly Request service — FBO sync and logistics analytics.

Part of the assembly service package. See backend/DOMAIN_ASSEMBLY.md for spec.
One-way dependency: analytics -> crud (never crud -> analytics).
"""

import statistics
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.models.assembly import (
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
    AssemblyStatusHistory,
)
from backend.models.cost import Nomenclature
from backend.models.warehouse import Warehouse
from backend.models.wb_fbo import WbFboSupply, WbFboSupplyItem
from backend.schemas.assembly import AssemblyItemResponse, RefreshFromFboResponse
from backend.services.warehouse_service import _resolve_barcode
from backend.utils.time import utcnow

# --- FBO sync ---------------------------------------------------------------


async def refresh_from_fbo(
    db: AsyncSession,
    project_id: int,
    request_id: int,
) -> RefreshFromFboResponse:
    """
    Re-sync items from linked WbFboSupply.
    Available: PENDING -> VEHICLE_ASSIGNED (not SHIPPED, not CANCELLED).
    """
    from .crud import get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    if req.status in (AssemblyStatus.SHIPPED, AssemblyStatus.DELIVERED, AssemblyStatus.CANCELLED):
        raise ValueError(f"Cannot refresh in status {req.status}")

    if not req.wb_fbo_supply_id:
        raise ValueError("Cannot refresh items: no FBO supply linked")

    # Load FBO supply (для wb_status — определяет что считать «фактом»)
    supply_result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == req.wb_fbo_supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    supply = supply_result.scalar_one_or_none()
    if not supply:
        raise ValueError("Linked FBO supply not found")

    # Force-pull WB detail to refresh warehouse_name (если изменился в WB
    # либо ещё не enriched). Best-effort, не блокирующее.
    from .crud import _try_force_enrich_supply

    await _try_force_enrich_supply(db, project_id, supply, force=True)
    if supply.warehouse_name and req.wb_warehouse_name_manual != supply.warehouse_name:
        req.wb_warehouse_name_manual = supply.warehouse_name

    # Load FBO supply items
    fbo_items_result = await db.execute(
        select(WbFboSupplyItem).where(
            WbFboSupplyItem.supply_id == req.wb_fbo_supply_id,
        )
    )
    fbo_items = fbo_items_result.scalars().all()

    # Если поставка уже принята WB (ACCEPTED) — подтягиваем фактически принятое
    # (accepted_qty), а не заявленное (quantity). Иначе синхим по quantity.
    use_accepted = supply.wb_status == "ACCEPTED"

    def _effective_qty(it: WbFboSupplyItem) -> int:
        return it.accepted_qty if use_accepted else it.quantity

    # Build maps by barcode
    current_map: dict[str, AssemblyRequestItem] = {item.barcode: item for item in req.items}
    fbo_map: dict[str, WbFboSupplyItem] = {item.barcode: item for item in fbo_items}

    added = 0
    removed = 0
    changed = 0

    # Remove items not in FBO anymore
    for barcode, item in current_map.items():
        if barcode not in fbo_map:
            await db.delete(item)  # no-soft-delete-check: AssemblyRequestItem has no SoftDeleteMixin
            removed += 1

    # Add new / update existing
    for barcode, fbo_item in fbo_map.items():
        qty = _effective_qty(fbo_item)
        if barcode in current_map:
            existing = current_map[barcode]
            if qty == 0:
                # WB не принял эту позицию — убираем из заявки
                await db.delete(existing)  # no-soft-delete-check: AssemblyRequestItem has no SoftDeleteMixin
                removed += 1
            elif existing.quantity != qty:
                existing.quantity = qty
                changed += 1
        else:
            if qty == 0:
                continue  # новую позицию с 0 не добавляем
            # New barcode - resolve nomenclature
            nom = await _resolve_barcode(db, project_id, barcode)
            new_item = AssemblyRequestItem(
                project_id=project_id,
                assembly_request_id=req.id,
                nomenclature_id=nom.id,
                barcode=barcode,
                quantity=qty,
            )
            db.add(new_item)
            added += 1

    await db.commit()
    await db.refresh(req, ["items"])
    await invalidate_cache("reports:assembly_flow")

    return RefreshFromFboResponse(
        added=added,
        removed=removed,
        changed=changed,
        items=[
            AssemblyItemResponse(
                id=item.id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                quantity=item.quantity,
            )
            for item in req.items
        ],
    )


# --- Logistics Analytics ---------------------------------------------------


@cached(prefix="reports:logistics_analytics", ttl=300)
async def get_logistics_analytics(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    warehouse_ids: list[int] | None = None,
    brands: list[str] | None = None,
) -> dict:
    """
    Logistics cost analytics for shipped/delivered assembly requests.

    Returns summary, by_destination, and by_route breakdowns.
    """
    # Destination warehouse name: prefer manual, fallback to FBO supply
    dest_warehouse = func.coalesce(
        AssemblyRequest.wb_warehouse_name_manual,
        WbFboSupply.warehouse_name,
    ).label("dest_warehouse")

    src_warehouse = Warehouse.name.label("src_warehouse")

    # Base filters — always applied.
    # CLOSED (возврат: WB не принял, товар вернулся) тоже считается: перевозка
    # оплачена, поэтому логистика этой заявки остаётся в аналитике.
    base_filters = [
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
        AssemblyRequest.status.in_([AssemblyStatus.SHIPPED, AssemblyStatus.DELIVERED, AssemblyStatus.CLOSED]),
    ]

    # Optional filters
    if date_from is not None:
        base_filters.append(AssemblyRequest.shipped_at >= date_from)
    if date_to is not None:
        base_filters.append(AssemblyRequest.shipped_at < date_to + timedelta(days=1))
    if warehouse_ids:
        base_filters.append(AssemblyRequest.warehouse_id.in_(warehouse_ids))
    if brands:
        # Filter by brand via items -> nomenclature join
        brand_subq = (
            select(AssemblyRequestItem.assembly_request_id)
            .join(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
            .where(Nomenclature.brand.in_(brands))
            .distinct()
            .correlate(AssemblyRequest)
            .scalar_subquery()
        )
        base_filters.append(AssemblyRequest.id.in_(brand_subq))

    # --- Summary ---
    summary_q = select(
        func.coalesce(func.sum(AssemblyRequest.pickup_cost), Decimal("0")).label("total_cost"),
        func.coalesce(func.sum(AssemblyRequest.pallets_count), 0).label("total_pallets"),
        func.count().label("total_shipments"),
    ).where(*base_filters)
    summary_row = (await db.execute(summary_q)).one()

    total_pallets = int(summary_row.total_pallets)
    total_cost = summary_row.total_cost or Decimal("0")
    avg_cost_per_pallet = (total_cost / Decimal(str(total_pallets))) if total_pallets > 0 else Decimal("0")

    summary = {
        "total_cost": total_cost,
        "avg_cost_per_pallet": avg_cost_per_pallet.quantize(Decimal("0.01")),
        "total_pallets": total_pallets,
        "total_shipments": int(summary_row.total_shipments),
    }

    # --- By destination ---
    # avg_cost = average cost PER PALLET (not per shipment)
    cost_per_pallet = case(
        (AssemblyRequest.pallets_count > 0, AssemblyRequest.pickup_cost / AssemblyRequest.pallets_count),
        else_=AssemblyRequest.pickup_cost,
    )
    dest_q = (
        select(
            dest_warehouse,
            func.avg(cost_per_pallet).label("avg_cost"),
            func.sum(AssemblyRequest.pickup_cost).label("total_cost"),
            func.count().label("shipments_count"),
        )
        .outerjoin(WbFboSupply, AssemblyRequest.wb_fbo_supply_id == WbFboSupply.id)
        .where(*base_filters)
        .group_by(dest_warehouse)
        .order_by(func.sum(AssemblyRequest.pickup_cost).desc())
    )
    dest_rows = (await db.execute(dest_q)).all()

    by_destination = [
        {
            "dest_warehouse": row.dest_warehouse or "N/A",
            "avg_cost": (row.avg_cost or Decimal("0")).quantize(Decimal("0.01")),
            "total_cost": row.total_cost or Decimal("0"),
            "shipments_count": int(row.shipments_count),
        }
        for row in dest_rows
    ]

    # --- By route (src -> dest) ---
    route_q = (
        select(
            src_warehouse,
            dest_warehouse,
            func.avg(cost_per_pallet).label("avg_cost"),
            func.count().label("shipments_count"),
        )
        .join(Warehouse, AssemblyRequest.warehouse_id == Warehouse.id)
        .outerjoin(WbFboSupply, AssemblyRequest.wb_fbo_supply_id == WbFboSupply.id)
        .where(*base_filters)
        .group_by(src_warehouse, dest_warehouse)
        .order_by(func.count().desc())
    )
    route_rows = (await db.execute(route_q)).all()

    by_route = [
        {
            "src_warehouse": row.src_warehouse or "N/A",
            "dest_warehouse": row.dest_warehouse or "N/A",
            "avg_cost": (row.avg_cost or Decimal("0")).quantize(Decimal("0.01")),
            "shipments_count": int(row.shipments_count),
        }
        for row in route_rows
    ]

    return {
        "summary": summary,
        "by_destination": by_destination,
        "by_route": by_route,
    }


# --- Flow Analytics ----------------------------------------------------------

# Этапы, длительность которых измеряется (порядок = порядок в ответе).
_FLOW_STAGES: tuple[str, ...] = (
    AssemblyStatus.IN_PROGRESS.value,
    AssemblyStatus.READY.value,
    AssemblyStatus.VEHICLE_ASSIGNED.value,
    AssemblyStatus.SHIPPED.value,
)
# «В работе сейчас» (summary.active_count + кандидаты в аномалии):
# не CANCELLED / CLOSED / DELIVERED. PENDING — legacy, активных заявок в нём нет.
_FLOW_ACTIVE_STATUSES: tuple[AssemblyStatus, ...] = (
    AssemblyStatus.IN_PROGRESS,
    AssemblyStatus.READY,
    AssemblyStatus.VEHICLE_ASSIGNED,
    AssemblyStatus.SHIPPED,
)
_FLOW_LIMIT = 5000
_ANOMALY_PRIORITY = {
    "wb_accepted_not_shipped": 0,
    "stuck_shipment": 1,
    "stuck_assembly": 2,
    "shipped_not_accepted": 3,
}


def _days_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 86400.0


def _walk_history(
    req: AssemblyRequest,
    recs: list[AssemblyStatusHistory],
) -> tuple[dict[str, float], list[tuple[str | None, str, float | None]]]:
    """Walk one request's status history chronologically.

    Returns:
      - days spent per stage (sum of COMPLETED intervals; the current,
        unfinished stage is not counted — «от входа до следующего перехода»);
      - transition datapoints (old_status, new_status, days in old_status or
        None when not computable — e.g. the creation record old_status=None).

    Draft-created requests have no start record (old_status=None) in history —
    fallback: вход в IN_PROGRESS = created_at заявки.
    """
    stage_days: dict[str, float] = {}
    transitions: list[tuple[str | None, str, float | None]] = []

    cur_status: str | None = None
    cur_since: datetime = req.created_at
    if not any(r.old_status is None for r in recs):
        # No creation record → request was born directly in IN_PROGRESS (draft path).
        cur_status = AssemblyStatus.IN_PROGRESS.value

    for r in recs:
        dur: float | None = None
        if r.old_status is not None and cur_status == r.old_status:
            dur = max(0.0, _days_between(cur_since, r.changed_at))
        transitions.append((r.old_status, r.new_status, dur))
        if dur is not None and cur_status in _FLOW_STAGES and cur_status is not None:
            stage_days[cur_status] = stage_days.get(cur_status, 0.0) + dur
        cur_status = r.new_status
        cur_since = r.changed_at

    return stage_days, transitions


@cached(prefix="reports:assembly_flow", ttl=300)
async def get_assembly_flow_analytics(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    warehouse_ids: list[int] | None = None,
    assembly_threshold_days: int = 3,
    ship_threshold_days: int = 2,
    delivery_threshold_days: int = 7,
) -> dict:
    """Аналитика потока сборки: этапы, переходы, аномалии.

    Период (date_from/date_to, по created_at; None = без границы — «всё время»)
    применяется к stages / transitions / summary-метрикам. АНОМАЛИИ считаются
    по ТЕКУЩЕМУ состоянию активных заявок (IN_PROGRESS..SHIPPED) — период к ним
    не применяется. Каждая заявка попадает максимум в одну категорию аномалии
    (приоритет: wb_accepted_not_shipped > stuck_shipment > stuck_assembly >
    shipped_not_accepted).

    Критерий «ВБ принял поставку» — `WbFboSupply.wb_status == "ACCEPTED"`:
    единственный надёжный сигнал приёмки. Так же определяет приёмку весь
    остальной код (auto-deliver в fbo_supply/sync.py `_auto_deliver_assembly`,
    дашборд недоприёмки в fbo_supply/service.py); WB-статусы 4/5/6 (включая
    частичную приёмку) маппятся в ACCEPTED (fbo_supply/mappers.py).
    `actual_date` (factDate) не используется: заполняется не для всех поставок
    и может быть проставлен до фактической приёмки.
    """
    now = utcnow()

    # --- Load period requests (для stages/transitions/summary) ---
    period_filters = [
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
    ]
    if date_from is not None:
        period_filters.append(AssemblyRequest.created_at >= datetime.combine(date_from, time.min))
    if date_to is not None:
        period_filters.append(AssemblyRequest.created_at < datetime.combine(date_to + timedelta(days=1), time.min))
    if warehouse_ids:
        period_filters.append(AssemblyRequest.warehouse_id.in_(warehouse_ids))

    period_reqs = list(
        (
            await db.execute(
                select(AssemblyRequest)
                .where(*period_filters)
                .order_by(AssemblyRequest.created_at.desc())
                .limit(_FLOW_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    # --- Load active requests (текущее состояние — для аномалий и active_count) ---
    active_filters = [
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
        AssemblyRequest.status.in_(_FLOW_ACTIVE_STATUSES),
    ]
    if warehouse_ids:
        active_filters.append(AssemblyRequest.warehouse_id.in_(warehouse_ids))

    active_reqs = list(
        (
            await db.execute(
                select(AssemblyRequest)
                .where(*active_filters)
                .order_by(AssemblyRequest.created_at.desc())
                .limit(_FLOW_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    # --- History for both sets — one query ---
    all_ids = {r.id for r in period_reqs} | {r.id for r in active_reqs}
    history_map: dict[int, list[AssemblyStatusHistory]] = {}
    if all_ids:
        hist_rows = (
            (
                await db.execute(
                    select(AssemblyStatusHistory)
                    .where(
                        AssemblyStatusHistory.project_id == project_id,
                        AssemblyStatusHistory.assembly_request_id.in_(all_ids),
                    )
                    .order_by(AssemblyStatusHistory.changed_at.asc(), AssemblyStatusHistory.id.asc())
                    .limit(_FLOW_LIMIT * 10)
                )
            )
            .scalars()
            .all()
        )
        for h in hist_rows:
            history_map.setdefault(h.assembly_request_id, []).append(h)

    # --- Stages & transitions (по заявкам периода) ---
    stage_totals: dict[str, list[float]] = {s: [] for s in _FLOW_STAGES}
    trans_acc: dict[tuple[str | None, str], list[float | None]] = {}
    for req in period_reqs:
        stage_days, trans = _walk_history(req, history_map.get(req.id, []))
        for stage_name, days_val in stage_days.items():
            stage_totals[stage_name].append(days_val)
        for old, new, dur in trans:
            trans_acc.setdefault((old, new), []).append(dur)

    stages: list[dict] = [
        {
            "stage": stage_name,
            "avg_days": round(statistics.fmean(vals), 2) if vals else None,
            "median_days": round(statistics.median(vals), 2) if vals else None,
            "count": len(vals),
        }
        for stage_name, vals in stage_totals.items()  # insertion order = _FLOW_STAGES
    ]

    transitions: list[dict] = []
    for (old, new), durs in trans_acc.items():
        known = [d for d in durs if d is not None]
        transitions.append(
            {
                "from_status": old,
                "to_status": new,
                "count": len(durs),
                "avg_days": round(statistics.fmean(known), 2) if known else None,
            }
        )
    transitions.sort(key=lambda t: (-t["count"], t["from_status"] or "", t["to_status"]))

    # --- Summary metrics (период) ---
    cycle_vals = [_days_between(r.created_at, r.shipped_at) for r in period_reqs if r.shipped_at is not None]
    assembly_vals = [
        float((r.actual_ready_date - r.created_at.date()).days) for r in period_reqs if r.actual_ready_date is not None
    ]
    # Только DELIVERED («Принято ВБ»): CLOSED — возврат (ВБ не принял), не успех.
    completed_in_period = sum(1 for r in period_reqs if r.status == AssemblyStatus.DELIVERED)

    # --- WB supplies for active requests — one query ---
    supply_ids = {r.wb_fbo_supply_id for r in active_reqs if r.wb_fbo_supply_id is not None}
    supply_map: dict[int, tuple[str, str | None, str]] = {}
    if supply_ids:
        supply_rows = (
            await db.execute(
                select(
                    WbFboSupply.id,
                    WbFboSupply.wb_status,
                    WbFboSupply.warehouse_name,
                    WbFboSupply.wb_supply_id,
                ).where(
                    WbFboSupply.project_id == project_id,
                    WbFboSupply.id.in_(supply_ids),
                )
            )
        ).all()
        supply_map = {row.id: (row.wb_status, row.warehouse_name, row.wb_supply_id) for row in supply_rows}

    # --- Anomalies (текущее состояние, максимум одна категория на заявку) ---
    anomalies_raw: list[tuple[AssemblyRequest, str, datetime | None]] = []
    for req in active_reqs:
        recs = history_map.get(req.id, [])
        supply = supply_map.get(req.wb_fbo_supply_id) if req.wb_fbo_supply_id is not None else None
        wb_status = supply[0] if supply else None

        kind: str | None = None
        since: datetime | None = None

        # --- since: начало текущего этапа (по статусу) ---
        if req.status in (AssemblyStatus.READY, AssemblyStatus.VEHICLE_ASSIGNED):
            # Начало этапа отгрузки: точное время перехода в READY из истории →
            # actual_ready_date (Date → полночь) → последний переход → updated_at
            entered_ready = next(
                (r.changed_at for r in reversed(recs) if r.new_status == AssemblyStatus.READY),
                None,
            )
            if entered_ready is not None:
                since = entered_ready
            elif req.actual_ready_date is not None:
                since = datetime.combine(req.actual_ready_date, time.min)
            elif recs:
                since = recs[-1].changed_at
            else:
                since = req.updated_at
        elif req.status == AssemblyStatus.IN_PROGRESS:
            entered = next(
                (r.changed_at for r in reversed(recs) if r.new_status == AssemblyStatus.IN_PROGRESS),
                None,
            )
            since = entered or req.created_at  # draft-созданные — без стартовой записи
        elif req.status == AssemblyStatus.SHIPPED:
            since = req.shipped_at or (recs[-1].changed_at if recs else None) or req.updated_at

        # --- kind: одна категория по приоритету; пороги — в дробных днях ---
        if req.status != AssemblyStatus.SHIPPED and wb_status == "ACCEPTED":
            # ВБ уже принял поставку, а заявка не отгружена — «забыли отгрузить».
            # Любой активный статус до SHIPPED. Без порога: критично сразу.
            kind = "wb_accepted_not_shipped"
        elif req.status in (AssemblyStatus.READY, AssemblyStatus.VEHICLE_ASSIGNED):
            if since is not None and _days_between(since, now) > ship_threshold_days:
                kind = "stuck_shipment"
        elif req.status == AssemblyStatus.IN_PROGRESS:
            if since is not None and _days_between(since, now) > assembly_threshold_days:
                kind = "stuck_assembly"
        elif (
            req.status == AssemblyStatus.SHIPPED
            and since is not None
            and _days_between(since, now) > delivery_threshold_days
        ):
            kind = "shipped_not_accepted"

        if kind is not None:
            anomalies_raw.append((req, kind, since))

    # total_qty для аномалий — одним запросом (без N+1)
    anomaly_ids = [r.id for r, _, _ in anomalies_raw]
    qty_map: dict[int, int] = {}
    if anomaly_ids:
        qty_rows = (
            await db.execute(
                select(
                    AssemblyRequestItem.assembly_request_id,
                    func.coalesce(func.sum(AssemblyRequestItem.quantity), 0).label("qty"),
                )
                .where(
                    AssemblyRequestItem.project_id == project_id,
                    AssemblyRequestItem.assembly_request_id.in_(anomaly_ids),
                )
                .group_by(AssemblyRequestItem.assembly_request_id)
            )
        ).all()
        qty_map = {row.assembly_request_id: int(row.qty) for row in qty_rows}

    # Имена складов — для FK-резолва включаем soft-deleted (display only,
    # как get_created_groups с именами черновиков); project_id обязателен.
    wh_ids_used = {r.warehouse_id for r in active_reqs} | {r.warehouse_id for r in period_reqs}
    wh_name_map: dict[int, str] = {}
    if wh_ids_used:
        wh_rows = (
            await db.execute(
                select(Warehouse.id, Warehouse.name).where(
                    Warehouse.project_id == project_id,
                    Warehouse.id.in_(wh_ids_used),
                )
            )
        ).all()
        wh_name_map = {row.id: row.name for row in wh_rows}

    anomalies: list[dict] = []
    for req, kind, since in anomalies_raw:
        supply = supply_map.get(req.wb_fbo_supply_id) if req.wb_fbo_supply_id is not None else None
        anomalies.append(
            {
                "id": req.id,
                "number": req.number,
                "status": req.status,
                "warehouse_id": req.warehouse_id,
                "warehouse_name": wh_name_map.get(req.warehouse_id),
                "wb_warehouse_name": (supply[1] if supply else None) or req.wb_warehouse_name_manual,
                "kind": kind,
                "days_stuck": max(0, (now - since).days) if since is not None else 0,
                "since": since.isoformat() if since is not None else None,
                "total_qty": qty_map.get(req.id, 0),
                "wb_fbo_status": supply[0] if supply else None,
                "wb_supply_number": supply[2] if supply else None,
                "pallets_count": req.pallets_count,
            }
        )
    anomalies.sort(key=lambda a: (_ANOMALY_PRIORITY[a["kind"]], -a["days_stuck"]))

    # --- By warehouse ---
    active_by_wh: dict[int, int] = {}
    for r in active_reqs:
        active_by_wh[r.warehouse_id] = active_by_wh.get(r.warehouse_id, 0) + 1
    cycle_by_wh: dict[int, list[float]] = {}
    for r in period_reqs:
        if r.shipped_at is not None:
            cycle_by_wh.setdefault(r.warehouse_id, []).append(_days_between(r.created_at, r.shipped_at))
    anomaly_by_wh: dict[int, int] = {}
    for a in anomalies:
        anomaly_by_wh[a["warehouse_id"]] = anomaly_by_wh.get(a["warehouse_id"], 0) + 1

    by_warehouse: list[dict] = [
        {
            "warehouse_id": wid,
            "warehouse_name": wh_name_map.get(wid),
            "active_count": active_by_wh.get(wid, 0),
            "avg_cycle_days": round(statistics.fmean(cycle_by_wh[wid]), 2) if wid in cycle_by_wh else None,
            "anomaly_count": anomaly_by_wh.get(wid, 0),
        }
        for wid in set(active_by_wh) | set(cycle_by_wh) | set(anomaly_by_wh)
    ]
    by_warehouse.sort(key=lambda w: (-w["anomaly_count"], -w["active_count"], w["warehouse_id"]))

    return {
        "summary": {
            "active_count": len(active_reqs),
            "completed_in_period": completed_in_period,
            "avg_cycle_days": round(statistics.fmean(cycle_vals), 2) if cycle_vals else None,
            "avg_assembly_days": round(statistics.fmean(assembly_vals), 2) if assembly_vals else None,
            "anomaly_count": len(anomalies),
        },
        "stages": stages,
        "transitions": transitions,
        "by_warehouse": by_warehouse,
        "anomalies": anomalies,
        "thresholds": {
            "assembly_days": assembly_threshold_days,
            "ship_days": ship_threshold_days,
            "delivery_days": delivery_threshold_days,
        },
    }
