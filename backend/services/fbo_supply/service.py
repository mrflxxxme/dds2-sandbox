"""
FBO Supply service — query operations.

Functions:
- list_warehouses: Unique warehouse names for filter dropdown
- list_fbo_supplies: List with search/filter/sort/pagination
- get_fbo_supply_items: Items for a supply (with lazy-load from WB API)

Link/unlink is done via AssemblyRequest create/delete in assembly module —
the FBO page is read-only for the supply ↔ assembly relationship.
"""

import logging
from datetime import date
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.warehouse import OutboundShipment
from backend.models.wb_fbo import WbFboSupply, WbFboSupplyItem, WbSupplyStatus

from .mappers import _update_supply_from_fbw_detail, _upsert_supply_items_fbw

logger = logging.getLogger(__name__)


def _archive_clause(accounting_started_at: date | None, archived_view: bool) -> Any:
    """
    Build WHERE clause to split supplies into «active» vs «archive» buckets.

    Tri-state is_archived overrides the auto rule (date-based):
        NULL  → auto: archived iff created_at_wb < accounting_started_at
        TRUE  → always archived (manual)
        FALSE → always active (manual restore)
    """
    if archived_view:
        # Archive page: explicit TRUE OR (auto: NULL + before cut-off)
        if accounting_started_at:
            auto_archive = and_(
                WbFboSupply.is_archived.is_(None),
                func.date(WbFboSupply.created_at_wb) < accounting_started_at,
            )
            return or_(WbFboSupply.is_archived.is_(True), auto_archive)
        return WbFboSupply.is_archived.is_(True)

    # Active page: explicit FALSE OR (auto: NULL + on/after cut-off, or no cut-off)
    if accounting_started_at:
        auto_active = and_(
            WbFboSupply.is_archived.is_(None),
            func.date(WbFboSupply.created_at_wb) >= accounting_started_at,
        )
        return or_(WbFboSupply.is_archived.is_(False), auto_active)
    # No cut-off configured: show everything except explicit archive
    return WbFboSupply.is_archived.is_not(True)


# ─── List supplies (with search/filter/sort/pagination) ────────────────────


async def get_fbo_summary(
    db: AsyncSession,
    project_id: int,
    accounting_started_at: date | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    warehouse: str | None = None,
    status: str | None = None,
    archived_view: bool = False,
) -> dict[str, int]:
    """
    Counts for FBO supplies dashboard: total, accepted, accepted without
    assembly request, accepted with partial acceptance. Used to highlight
    orphan ACCEPTED supplies (WB принял, но в DDS нет заявки на сборку),
    so the user can spot missed linkage and start a receipt for rejected qty.

    Honors all global filters (period, archive bucket, warehouse, status) so
    dashboard counters match the list view exactly. partial_only/without_assembly
    are intentionally excluded — those toggles drill into the list, but the
    cards should keep showing the global counters they highlight.
    """
    cutoff: list[Any] = [_archive_clause(accounting_started_at, archived_view)]
    if date_from:
        cutoff.append(func.date(WbFboSupply.created_at_wb) >= date_from)
    if date_to:
        cutoff.append(func.date(WbFboSupply.created_at_wb) <= date_to)
    if warehouse:
        cutoff.append(WbFboSupply.warehouse_name == warehouse)
    if status:
        statuses = [s.strip() for s in status.split(",")]
        if len(statuses) == 1:
            cutoff.append(WbFboSupply.wb_status == statuses[0])
        else:
            cutoff.append(WbFboSupply.wb_status.in_(statuses))

    base = select(WbFboSupply).where(WbFboSupply.project_id == project_id, *cutoff).subquery()

    total = (await db.execute(select(func.count()).select_from(base))).scalar() or 0

    accepted_q = select(WbFboSupply).where(
        WbFboSupply.project_id == project_id,
        WbFboSupply.wb_status == WbSupplyStatus.ACCEPTED,
        *cutoff,
    )
    accepted = (await db.execute(select(func.count()).select_from(accepted_q.subquery()))).scalar() or 0

    linked_ids = select(AssemblyRequest.wb_fbo_supply_id).where(
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.wb_fbo_supply_id.is_not(None),
        AssemblyRequest.is_deleted == False,  # noqa: E712
    )
    accepted_no_asm_q = accepted_q.where(WbFboSupply.id.not_in(linked_ids))
    accepted_without_assembly = (
        await db.execute(select(func.count()).select_from(accepted_no_asm_q.subquery()))
    ).scalar() or 0

    partial_q = accepted_q.where(
        WbFboSupply.total_qty > 0,
        WbFboSupply.accepted_qty < WbFboSupply.total_qty,
        WbFboSupply.return_processed_at.is_(None),
    )
    accepted_partial = (await db.execute(select(func.count()).select_from(partial_q.subquery()))).scalar() or 0

    excess_q = accepted_q.where(
        WbFboSupply.total_qty > 0,
        WbFboSupply.accepted_qty > WbFboSupply.total_qty,
        WbFboSupply.excess_processed_at.is_(None),
    )
    accepted_excess = (await db.execute(select(func.count()).select_from(excess_q.subquery()))).scalar() or 0

    return {
        "total": int(total),
        "accepted": int(accepted),
        "accepted_without_assembly": int(accepted_without_assembly),
        "accepted_partial": int(accepted_partial),
        "accepted_excess": int(accepted_excess),
    }


async def get_partial_acceptance_summary(
    db: AsyncSession,
    project_id: int,
    accounting_started_at: date | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    warehouse: str | None = None,
    status: str | None = None,
    archived_view: bool = False,
) -> dict[str, Any]:
    """
    Недоприёмка dashboard: aggregates across ALL partial-accepted supplies
    (total_qty > accepted_qty). Buckets:

      - unaccepted_total   sum(total_qty - accepted_qty)  (весь дельта)
      - unprocessed        where return_processed_at IS NULL
      - returned_to_stock  return_type IN (GOODS, DEFECT)  → sum(return_qty)
      - utilized           return_type = UTILIZED          → sum(return_qty)

    Plus items_breakdown: per-barcode aggregate (qty not accepted across supplies).

    Honors the same global filters as the list view (period, archive, warehouse,
    status) — otherwise dashboard cards would count supplies hidden from the
    table (e.g. 15k шт за всю историю при 24 видимых поставках за 30 дней).
    """  # — cyrillic product terms
    cutoff: list[Any] = [_archive_clause(accounting_started_at, archived_view)]
    if date_from:
        cutoff.append(func.date(WbFboSupply.created_at_wb) >= date_from)
    if date_to:
        cutoff.append(func.date(WbFboSupply.created_at_wb) <= date_to)
    if warehouse:
        cutoff.append(WbFboSupply.warehouse_name == warehouse)
    if status:
        statuses = [s.strip() for s in status.split(",")]
        if len(statuses) == 1:
            cutoff.append(WbFboSupply.wb_status == statuses[0])
        else:
            cutoff.append(WbFboSupply.wb_status.in_(statuses))

    # Supply-level aggregates
    supplies_q = select(WbFboSupply).where(
        WbFboSupply.project_id == project_id,
        WbFboSupply.wb_status == WbSupplyStatus.ACCEPTED,
        WbFboSupply.total_qty > 0,
        WbFboSupply.accepted_qty < WbFboSupply.total_qty,
        *cutoff,
    )
    result = await db.execute(supplies_q)
    supplies = list(result.scalars().all())

    # unaccepted_total/unprocessed считаются по items (gross sum), не по
    # supply-net-delta: иначе cards не сходятся с «Показать позиции» если в  # noqa: RUF003
    # одной поставке часть артикулов с недоприёмкой, часть с излишком  # noqa: RUF003
    # (supply-net зачитывает, item-sum — нет). returned_to_stock/utilized
    # остаются по supply.return_qty — это явное действие пользователя.
    supply_ids_all = [s.id for s in supplies]
    unprocessed_ids = [s.id for s in supplies if s.return_processed_at is None]

    async def _sum_items_delta(ids: list[int]) -> int:
        if not ids:
            return 0
        row = await db.execute(
            select(func.sum(WbFboSupplyItem.quantity - WbFboSupplyItem.accepted_qty)).where(
                WbFboSupplyItem.supply_id.in_(ids),
                WbFboSupplyItem.project_id == project_id,
                WbFboSupplyItem.quantity > WbFboSupplyItem.accepted_qty,
            )
        )
        return int(row.scalar() or 0)

    unaccepted_total = await _sum_items_delta(supply_ids_all)
    unprocessed = await _sum_items_delta(unprocessed_ids)
    returned_to_stock = sum(int(s.return_qty or 0) for s in supplies if s.return_type in ("GOODS", "DEFECT"))
    utilized = sum(int(s.return_qty or 0) for s in supplies if s.return_type == "UTILIZED")

    # Per-barcode breakdown — which goods are affected by недоприёмка
    # (sum delta across all affected supplies). UI shows a collapsible list.
    breakdown: list[dict[str, Any]] = []
    if supply_ids_all:
        items_result = await db.execute(
            select(
                WbFboSupplyItem.barcode,
                WbFboSupplyItem.product_name,
                WbFboSupplyItem.article_seller,
                func.sum(WbFboSupplyItem.quantity - WbFboSupplyItem.accepted_qty).label("delta"),
            )
            .where(
                WbFboSupplyItem.supply_id.in_(supply_ids_all),
                WbFboSupplyItem.project_id == project_id,
                WbFboSupplyItem.quantity > WbFboSupplyItem.accepted_qty,
            )
            .group_by(
                WbFboSupplyItem.barcode,
                WbFboSupplyItem.product_name,
                WbFboSupplyItem.article_seller,
            )
            .order_by(func.sum(WbFboSupplyItem.quantity - WbFboSupplyItem.accepted_qty).desc())
        )
        for row in items_result.all():
            breakdown.append(
                {
                    "barcode": row.barcode,
                    "product_name": row.product_name,
                    "article_seller": row.article_seller,
                    "delta": int(row.delta or 0),
                }
            )

    return {
        "unaccepted_total": int(unaccepted_total),
        "unprocessed": int(unprocessed),
        "returned_to_stock": int(returned_to_stock),
        "utilized": int(utilized),
        "items_breakdown": breakdown,
    }


async def list_warehouses(
    db: AsyncSession,
    project_id: int,
) -> list[str]:
    """Get unique warehouse names for filter dropdown."""
    result = await db.execute(
        select(WbFboSupply.warehouse_name)
        .where(
            WbFboSupply.project_id == project_id,
            WbFboSupply.warehouse_name.is_not(None),
        )
        .distinct()
        .order_by(WbFboSupply.warehouse_name)
    )
    return [row[0] for row in result.fetchall()]


async def list_fbo_supplies(
    db: AsyncSession,
    project_id: int,
    *,
    search: str | None = None,
    status: str | None = None,
    warehouse: str | None = None,
    source_warehouse_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort_by: str = "created_at_wb",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    exclude_with_assembly: bool = False,
    without_assembly: bool = False,
    partial_only: bool = False,
    excess_only: bool = False,
    accounting_started_at: date | None = None,
    archived_view: bool = False,
) -> tuple[list[dict], int]:
    """
    List FBO supplies with filtering, search, sorting, and pagination.

    Returns: (enriched_dicts, total_count)
    """
    base_query = select(WbFboSupply).where(
        WbFboSupply.project_id == project_id,
    )

    # Search by wb_supply_id or name
    if search:
        _esc = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        search_term = f"%{_esc}%"
        base_query = base_query.where(
            or_(
                WbFboSupply.wb_supply_id.ilike(search_term, escape="\\"),
                WbFboSupply.name.ilike(search_term, escape="\\"),
            )
        )

    # Filter by status (comma-separated for multi-status filter)
    if status:
        statuses = [s.strip() for s in status.split(",")]
        if len(statuses) == 1:
            base_query = base_query.where(WbFboSupply.wb_status == statuses[0])
        else:
            base_query = base_query.where(WbFboSupply.wb_status.in_(statuses))

    # Filter by warehouse
    if warehouse:
        base_query = base_query.where(WbFboSupply.warehouse_name == warehouse)

    # Filter by «склад забора» — our warehouse from the linked OutboundShipment.
    # Supplies without an outbound shipment are excluded (no pickup warehouse).
    if source_warehouse_id is not None:
        pickup_shipment_ids = select(OutboundShipment.id).where(
            OutboundShipment.project_id == project_id,
            OutboundShipment.is_deleted.is_(False),
            OutboundShipment.warehouse_id == source_warehouse_id,
        )
        base_query = base_query.where(WbFboSupply.outbound_shipment_id.in_(pickup_shipment_ids))

    # Filter by date range
    if date_from:
        base_query = base_query.where(
            func.date(WbFboSupply.created_at_wb) >= date_from,
        )
    if date_to:
        base_query = base_query.where(
            func.date(WbFboSupply.created_at_wb) <= date_to,
        )

    # Split into active/archive bucket (auto by date + manual override)
    base_query = base_query.where(_archive_clause(accounting_started_at, archived_view))

    # Exclude supplies that already have an active assembly request
    if exclude_with_assembly:
        active_statuses = [
            AssemblyStatus.PENDING,
            AssemblyStatus.IN_PROGRESS,
            AssemblyStatus.READY,
            AssemblyStatus.VEHICLE_ASSIGNED,
            AssemblyStatus.SHIPPED,
        ]
        # wb_fbo_supply_id.is_not(None): Postgres NOT IN with NULL in subquery
        # makes every comparison NULL (i.e. false), so an active AR with
        # wb_fbo_supply_id=NULL would silently zero out the entire supply list.
        active_assembly_ids = select(AssemblyRequest.wb_fbo_supply_id).where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_(active_statuses),
            AssemblyRequest.wb_fbo_supply_id.is_not(None),
        )
        base_query = base_query.where(WbFboSupply.id.not_in(active_assembly_ids))

    # Supplies that WB already ACCEPTED but have NO assembly request in DDS.
    # Non-final statuses (ACTIVE/IN_PROGRESS/CANCELLED) are excluded: they are
    # either in progress (AR may arrive later) or cancelled (not actionable).
    # Matches the "Без заявки на сборку" summary card (see get_fbo_summary).
    if without_assembly:
        any_assembly_ids = select(AssemblyRequest.wb_fbo_supply_id).where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.wb_fbo_supply_id.is_not(None),
            AssemblyRequest.is_deleted.is_(False),
        )
        base_query = base_query.where(
            WbFboSupply.wb_status == WbSupplyStatus.ACCEPTED,
            WbFboSupply.id.not_in(any_assembly_ids),
        )

    # Partial acceptance — only for ACCEPTED supplies where WB rejected part of
    # the shipment (non-final statuses always have accepted_qty=0 and are not
    # yet a "недоприёмка"). return_processed_at IS NULL excludes already-handled.
    if partial_only:
        base_query = base_query.where(
            and_(
                WbFboSupply.wb_status == WbSupplyStatus.ACCEPTED,
                WbFboSupply.total_qty > 0,
                WbFboSupply.accepted_qty < WbFboSupply.total_qty,
                WbFboSupply.return_processed_at.is_(None),
            )
        )

    # Excess (overshoot): WB accepted MORE than we shipped, not yet written off.
    if excess_only:
        base_query = base_query.where(
            and_(
                WbFboSupply.wb_status == WbSupplyStatus.ACCEPTED,
                WbFboSupply.total_qty > 0,
                WbFboSupply.accepted_qty > WbFboSupply.total_qty,
                WbFboSupply.excess_processed_at.is_(None),
            )
        )

    # Count total
    count_query = select(func.count()).select_from(base_query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Sort
    sort_column = getattr(WbFboSupply, sort_by, WbFboSupply.created_at_wb)
    if sort_order == "asc":
        base_query = base_query.order_by(sort_column.asc())
    else:
        base_query = base_query.order_by(sort_column.desc())

    # Pagination
    base_query = base_query.limit(limit).offset(offset)

    result = await db.execute(base_query)
    supplies = result.scalars().all()

    # Enrich with assembly request info (single query for the page)
    supply_ids = [s.id for s in supplies]
    assembly_map: dict[int, tuple[int, str, str]] = {}
    # source_warehouse_map: supply_id → warehouse_id from the AR that shipped
    # this FBO supply. Used on UI to preselect the return-warehouse in the
    # "Оформить возврат" modal (goods should come back to the warehouse they
    # were picked from). Looks up ANY AR (active or finalized) for the supply.
    source_warehouse_map: dict[int, int] = {}
    if supply_ids:
        active_statuses = [
            AssemblyStatus.PENDING,
            AssemblyStatus.IN_PROGRESS,
            AssemblyStatus.READY,
            AssemblyStatus.VEHICLE_ASSIGNED,
            AssemblyStatus.SHIPPED,
        ]
        asm_result = await db.execute(
            select(
                AssemblyRequest.wb_fbo_supply_id,
                AssemblyRequest.id,
                AssemblyRequest.number,
                AssemblyRequest.status,
                AssemblyRequest.warehouse_id,
            ).where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted.is_(False),
                AssemblyRequest.wb_fbo_supply_id.in_(supply_ids),
            )
        )
        for row in asm_result.all():
            # Prefer active AR for badge, but fallback to terminal states
            # (DELIVERED/CANCELLED) so the UI always shows the AR number,
            # not just an empty «Отгружена» placeholder.
            is_active = row.status in active_statuses
            existing = assembly_map.get(row.wb_fbo_supply_id)
            if existing is None or (is_active and existing[2] not in active_statuses):
                assembly_map[row.wb_fbo_supply_id] = (row.id, row.number, row.status)
            if row.warehouse_id is not None:
                source_warehouse_map[row.wb_fbo_supply_id] = row.warehouse_id

    # Enrich with reassignment info (single query each direction)
    reassign_target_wb_id: dict[int, str] = {}
    target_ids_needed = {s.reassigned_to_supply_id for s in supplies if s.reassigned_to_supply_id}
    if target_ids_needed:
        target_result = await db.execute(
            select(WbFboSupply.id, WbFboSupply.wb_supply_id).where(
                WbFboSupply.id.in_(target_ids_needed),
                WbFboSupply.project_id == project_id,
            )
        )
        reassign_target_wb_id = {tgt_row.id: tgt_row.wb_supply_id for tgt_row in target_result.all()}

    reassign_source_wb_ids: dict[int, list[str]] = {}
    if supply_ids:
        source_result = await db.execute(
            select(WbFboSupply.reassigned_to_supply_id, WbFboSupply.wb_supply_id).where(
                WbFboSupply.reassigned_to_supply_id.in_(supply_ids),
                WbFboSupply.project_id == project_id,
            )
        )
        for src_row in source_result.all():
            reassign_source_wb_ids.setdefault(src_row.reassigned_to_supply_id, []).append(src_row.wb_supply_id)

    # Enrich with outbound shipment info (single query, JOIN Warehouse for name)
    # supply_id -> (number, status, warehouse_id, warehouse_name, shipped_date)
    from backend.models.warehouse import Warehouse

    shipment_map: dict[int, tuple[str, str, int, str | None, date | None]] = {}
    shipment_ids = [s.outbound_shipment_id for s in supplies if s.outbound_shipment_id]
    if shipment_ids:
        ship_result = await db.execute(
            select(
                OutboundShipment.id,
                OutboundShipment.number,
                OutboundShipment.status,
                OutboundShipment.warehouse_id,
                OutboundShipment.shipped_date,
                Warehouse.name,
            )
            .join(Warehouse, Warehouse.id == OutboundShipment.warehouse_id)
            .where(
                OutboundShipment.project_id == project_id,
                OutboundShipment.is_deleted.is_(False),
                OutboundShipment.id.in_(shipment_ids),
            )
        )
        for ship_row in ship_result.all():
            shipment_map[ship_row.id] = (
                ship_row.number,
                ship_row.status,
                ship_row.warehouse_id,
                ship_row.name,
                ship_row.shipped_date,
            )

    # Build enriched dicts
    enriched = []
    for s in supplies:
        d = {c.key: getattr(s, c.key) for c in WbFboSupply.__table__.columns}
        asm = assembly_map.get(s.id)
        d["assembly_request_id"] = asm[0] if asm else None
        d["assembly_request_number"] = asm[1] if asm else None
        d["assembly_request_status"] = asm[2] if asm else None
        d["source_warehouse_id"] = source_warehouse_map.get(s.id)
        ship = shipment_map.get(s.outbound_shipment_id) if s.outbound_shipment_id else None
        d["outbound_shipment_number"] = ship[0] if ship else None
        d["outbound_shipment_status"] = ship[1] if ship else None
        d["outbound_shipment_warehouse_id"] = ship[2] if ship else None
        d["outbound_shipment_warehouse_name"] = ship[3] if ship else None
        d["outbound_shipment_shipped_date"] = ship[4] if ship else None
        d["reassigned_to_wb_supply_id"] = (
            reassign_target_wb_id.get(s.reassigned_to_supply_id) if s.reassigned_to_supply_id else None
        )
        d["reassigned_from_wb_supply_ids"] = reassign_source_wb_ids.get(s.id, [])
        enriched.append(d)

    return enriched, total


# ─── Archive flag ───────────────────────────────────────────────────────────


async def restore_linked_from_archive(
    db: AsyncSession,
    project_id: int,
    accounting_started_at: date | None,
    user_id: int | None = None,
) -> int:
    """
    Bulk-restore from archive all supplies that were linked to our
    AssemblyRequest / OutboundShipment. Sets is_archived=FALSE so they
    reappear in the active list regardless of created_at_wb vs cut-off.
    Returns the number of restored supplies.
    """
    # Find currently-archived supplies linked to our processes.
    linked_asm_ids = select(AssemblyRequest.wb_fbo_supply_id).where(
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.wb_fbo_supply_id.is_not(None),
        AssemblyRequest.is_deleted.is_(False),
    )
    query = select(WbFboSupply).where(
        WbFboSupply.project_id == project_id,
        _archive_clause(accounting_started_at, archived_view=True),
        or_(
            WbFboSupply.outbound_shipment_id.is_not(None),
            WbFboSupply.id.in_(linked_asm_ids),
        ),
    )
    from backend.models.wb_fbo import FboAuditAction

    from .audit import log_action

    result = await db.execute(query)
    supplies = list(result.scalars().all())
    for s in supplies:
        old = s.is_archived
        s.is_archived = False
        await log_action(
            db,
            project_id=project_id,
            supply_id=s.id,
            action=FboAuditAction.BULK_RESTORE,
            payload={"old": old, "new": False},
            user_id=user_id,
        )
    await db.commit()
    return len(supplies)


async def set_archive_flag(
    db: AsyncSession,
    project_id: int,
    supply_id: int,
    is_archived: bool | None,
    user_id: int | None = None,
) -> WbFboSupply:
    """
    Override the auto archive rule for a single supply.
    is_archived=True → manually move to archive; False → restore from archive;
    None → reset to auto rule (date-based).
    """
    from backend.models.wb_fbo import FboAuditAction

    from .audit import log_action

    result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    supply = result.scalar_one_or_none()
    if supply is None:
        raise ValueError("FBO supply not found")
    old = supply.is_archived
    supply.is_archived = is_archived
    action = FboAuditAction.ARCHIVE if is_archived else FboAuditAction.UNARCHIVE
    await log_action(
        db,
        project_id=project_id,
        supply_id=supply_id,
        action=action,
        payload={"old": old, "new": is_archived},
        user_id=user_id,
    )
    await db.commit()
    await db.refresh(supply)
    return supply


# ─── Get supply items ───────────────────────────────────────────────────────


async def get_fbo_supply_items(
    db: AsyncSession,
    project_id: int,
    supply_id: int,
    api_client: Any = None,
    force_refresh: bool = False,
) -> list[WbFboSupplyItem]:
    """
    Get items for a specific FBO supply. Validates project_id ownership.
    Lazy-load: if no items in DB and api_client provided, fetch from WB API.
    force_refresh: always re-fetch from WB API and update DB.
    """
    # Verify supply belongs to project
    result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    supply = result.scalar_one_or_none()
    if not supply:
        raise ValueError("FBO Supply not found")

    items_result = await db.execute(
        select(WbFboSupplyItem)
        .where(
            WbFboSupplyItem.supply_id == supply_id,
        )
        .order_by(WbFboSupplyItem.id)
    )
    items = list(items_result.scalars().all())

    # Auto-refresh when supply looks stuck: ACCEPTED but accepted < total.
    # Before this, supplies with stale items (accepted_qty=0) would show zeros
    # in the UI indefinitely because `not items` was false.
    stale_acceptance = (
        supply.wb_status == WbSupplyStatus.ACCEPTED and supply.total_qty > 0 and supply.accepted_qty < supply.total_qty
    )

    # Lazy-load from WB API if no items cached, force refresh, or looks stuck
    if (not items or force_refresh or stale_acceptance) and api_client and supply.wb_supply_id.isdigit():
        try:
            wb_id_int = int(supply.wb_supply_id)
            goods = await api_client.get_fbw_supply_goods(
                wb_id_int,
                limit=100,
                offset=0,
            )
            if goods:
                await _upsert_supply_items_fbw(db, project_id, supply.id, wb_id_int, goods)
                # Sync supply-level counters from goods so list view matches items
                supply.accepted_qty = sum(int(g.get("acceptedQuantity") or 0) for g in goods)
                supply.total_qty = sum(int(g.get("quantity") or 0) for g in goods)

            # Also fetch detail (warehouse_name, qty). Always refresh on
            # force_refresh — user may have changed destination warehouse in WB
            # cabinet, and list API never returns warehouseName.
            if not supply.warehouse_name or force_refresh:
                try:
                    detail = await api_client.get_fbw_supply_detail(wb_id_int)
                    if detail:
                        _update_supply_from_fbw_detail(supply, detail)
                except Exception as e:
                    logger.warning(
                        "fbo_items.detail_fetch_error",
                        extra={"supply_id": supply_id, "error": str(e)},
                    )

            await db.commit()
            # Re-fetch from DB
            items_result2 = await db.execute(
                select(WbFboSupplyItem).where(WbFboSupplyItem.supply_id == supply_id).order_by(WbFboSupplyItem.id)
            )
            items = list(items_result2.scalars().all())
        except Exception as e:
            logger.warning(
                "fbo_items.lazy_load_error",
                extra={"supply_id": supply_id, "error": str(e)},
            )

    return items


# ─── Reassignment (link under-delivery micro-supply to parent) ────────────


async def get_reassignment_candidates(
    db: AsyncSession,
    project_id: int,
    source_supply_id: int,
) -> dict[str, Any]:
    """
    Find supplies with under-acceptance where at least one item matches the
    source supply's barcodes. Candidates are returned for the user to pick —
    we never auto-link, because WB does not tell us which source belongs to
    which target.

    Ordering: same warehouse first, then FIFO by created_at_wb.
    """
    source_result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == source_supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    source = source_result.scalar_one_or_none()
    if source is None:
        raise ValueError("FBO supply not found")

    if source.reassigned_to_supply_id is not None:
        return {"source_supply_id": source_supply_id, "candidates": []}

    source_items_result = await db.execute(
        select(WbFboSupplyItem.barcode).where(
            WbFboSupplyItem.supply_id == source_supply_id,
            WbFboSupplyItem.project_id == project_id,
        )
    )
    source_barcodes = {row[0] for row in source_items_result.all() if row[0]}
    if not source_barcodes:
        return {"source_supply_id": source_supply_id, "candidates": []}

    target_candidates_result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.project_id == project_id,
            WbFboSupply.id != source_supply_id,
            WbFboSupply.wb_status == WbSupplyStatus.ACCEPTED,
            WbFboSupply.total_qty > 0,
            WbFboSupply.accepted_qty < WbFboSupply.total_qty,
            WbFboSupply.reassigned_to_supply_id.is_(None),
            WbFboSupply.is_archived.is_not(True),
        )
    )
    targets = list(target_candidates_result.scalars().all())
    if not targets:
        return {"source_supply_id": source_supply_id, "candidates": []}

    target_ids = [t.id for t in targets]
    items_result = await db.execute(
        select(WbFboSupplyItem).where(
            WbFboSupplyItem.supply_id.in_(target_ids),
            WbFboSupplyItem.project_id == project_id,
            WbFboSupplyItem.barcode.in_(source_barcodes),
            WbFboSupplyItem.quantity > WbFboSupplyItem.accepted_qty,
        )
    )
    items_by_supply: dict[int, list[WbFboSupplyItem]] = {}
    for item in items_result.scalars().all():
        items_by_supply.setdefault(item.supply_id, []).append(item)

    candidates: list[dict[str, Any]] = []
    for t in targets:
        matched = items_by_supply.get(t.id, [])
        if not matched:
            continue
        matched_qty = sum(max(0, i.quantity - i.accepted_qty) for i in matched)
        candidates.append(
            {
                "id": t.id,
                "wb_supply_id": t.wb_supply_id,
                "warehouse_name": t.warehouse_name,
                "created_at_wb": t.created_at_wb,
                "total_qty": t.total_qty,
                "accepted_qty": t.accepted_qty,
                "same_warehouse": t.warehouse_name == source.warehouse_name,
                "matched_qty": matched_qty,
                "matched_items": [
                    {
                        "barcode": i.barcode,
                        "article_seller": i.article_seller,
                        "product_name": i.product_name,
                        "quantity": i.quantity,
                        "accepted_qty": i.accepted_qty,
                        "unaccepted_delta": max(0, i.quantity - i.accepted_qty),
                    }
                    for i in matched
                ],
            }
        )

    candidates.sort(key=lambda c: (not c["same_warehouse"], c["created_at_wb"]))

    return {"source_supply_id": source_supply_id, "candidates": candidates}


async def reassign_to_supply(
    db: AsyncSession,
    project_id: int,
    source_supply_id: int,
    target_supply_id: int,
    user_id: int | None = None,
) -> dict[str, Any]:
    """
    Link source supply → target supply: add source item accepted qty to target's
    matching items (by barcode), archive source with reassigned_to_supply_id set.

    Source qty for a barcode is bounded by the target's unaccepted delta for
    that same barcode — any overflow on that barcode is rejected (user must
    pick a different target or split manually). This keeps target's
    accepted_qty consistent with WB's own total_qty.
    """
    if source_supply_id == target_supply_id:
        raise ValueError("Source and target must differ")

    ids = [source_supply_id, target_supply_id]
    # Row-level lock to prevent concurrent reassigns into the same target
    # (two races could both read stale accepted_qty and over-increment).
    result = await db.execute(
        select(WbFboSupply)
        .where(
            WbFboSupply.id.in_(ids),
            WbFboSupply.project_id == project_id,
        )
        .with_for_update()
    )
    supplies = {s.id: s for s in result.scalars().all()}
    source = supplies.get(source_supply_id)
    target = supplies.get(target_supply_id)
    if source is None or target is None:
        raise ValueError("FBO supply not found")
    if source.reassigned_to_supply_id is not None:
        raise ValueError("Source supply is already reassigned")
    if target.reassigned_to_supply_id is not None:
        raise ValueError("Target supply is itself a доприёмка — pick its parent")
    if target.wb_status != WbSupplyStatus.ACCEPTED:
        raise ValueError("Target supply must be ACCEPTED")

    source_items_result = await db.execute(
        select(WbFboSupplyItem).where(
            WbFboSupplyItem.supply_id == source_supply_id,
            WbFboSupplyItem.project_id == project_id,
        )
    )
    source_items = list(source_items_result.scalars().all())
    if not source_items:
        raise ValueError("Source supply has no items to reassign")

    target_items_result = await db.execute(
        select(WbFboSupplyItem)
        .where(
            WbFboSupplyItem.supply_id == target_supply_id,
            WbFboSupplyItem.project_id == project_id,
        )
        .with_for_update()
    )
    target_items_by_barcode: dict[str, list[WbFboSupplyItem]] = {}
    for item in target_items_result.scalars().all():
        target_items_by_barcode.setdefault(item.barcode, []).append(item)

    reassigned_qty = 0
    # Track per-item deltas so we can revert precisely (target.items[i].accepted_qty)
    target_item_deltas: list[tuple[int, int]] = []
    for src in source_items:
        src_qty = int(src.accepted_qty or 0)
        if src_qty <= 0:
            continue
        targets_for_barcode = target_items_by_barcode.get(src.barcode, [])
        remaining = src_qty
        for tgt in targets_for_barcode:
            if remaining <= 0:
                break
            unaccepted = max(0, int(tgt.quantity) - int(tgt.accepted_qty or 0))
            if unaccepted <= 0:
                continue
            take = min(remaining, unaccepted)
            tgt.accepted_qty = int(tgt.accepted_qty or 0) + take
            remaining -= take
            reassigned_qty += take
            target_item_deltas.append((tgt.id, take))
        if remaining > 0:
            raise ValueError(
                f"Barcode {src.barcode}: source has {src_qty} шт but target has "
                f"only {src_qty - remaining} шт недоприёмки — pick a different target"
            )

    if reassigned_qty == 0:
        raise ValueError("No matching barcodes between source and target")

    target.accepted_qty = int(target.accepted_qty or 0) + reassigned_qty
    old_is_archived = source.is_archived  # snapshot for revert
    source.reassigned_to_supply_id = target.id
    source.is_archived = True

    from backend.models.wb_fbo import FboAuditAction

    from .audit import log_action

    await log_action(
        db,
        project_id=project_id,
        supply_id=source.id,
        action=FboAuditAction.REASSIGN,
        payload={
            "target_supply_id": target.id,
            "target_wb_supply_id": target.wb_supply_id,
            "reassigned_qty": reassigned_qty,
            "target_items": target_item_deltas,
            "old_is_archived": old_is_archived,
        },
        user_id=user_id,
    )

    await db.commit()
    await db.refresh(source)
    await db.refresh(target)

    return {
        "source_supply_id": source.id,
        "target_supply_id": target.id,
        "target_wb_supply_id": target.wb_supply_id,
        "reassigned_qty": reassigned_qty,
    }
