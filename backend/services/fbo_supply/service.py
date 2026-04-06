"""
FBO Supply service — query + link operations.

Functions:
- list_warehouses: Unique warehouse names for filter dropdown
- list_fbo_supplies: List with search/filter/sort/pagination
- get_fbo_supply_items: Items for a supply (with lazy-load from WB API)
- link_supply_to_shipment / unlink_supply_from_shipment
"""

import logging
from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.warehouse import OutboundShipment
from backend.models.wb_fbo import WbFboSupply, WbFboSupplyItem, WbSupplyStatus

from .mappers import _update_supply_from_fbw_detail, _upsert_supply_items_fbw
from .sync import _auto_deliver_shipment

logger = logging.getLogger(__name__)


# ─── List supplies (with search/filter/sort/pagination) ────────────────────


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
    date_from: date | None = None,
    date_to: date | None = None,
    sort_by: str = "created_at_wb",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    exclude_with_assembly: bool = False,
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

    # Filter by date range
    if date_from:
        base_query = base_query.where(
            func.date(WbFboSupply.created_at_wb) >= date_from,
        )
    if date_to:
        base_query = base_query.where(
            func.date(WbFboSupply.created_at_wb) <= date_to,
        )

    # Exclude supplies that already have an active assembly request
    if exclude_with_assembly:
        active_statuses = [
            AssemblyStatus.PENDING,
            AssemblyStatus.IN_PROGRESS,
            AssemblyStatus.READY,
            AssemblyStatus.VEHICLE_ASSIGNED,
            AssemblyStatus.SHIPPED,
        ]
        active_assembly_ids = select(AssemblyRequest.wb_fbo_supply_id).where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_(active_statuses),
        )
        base_query = base_query.where(WbFboSupply.id.not_in(active_assembly_ids))

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
            ).where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted.is_(False),
                AssemblyRequest.status.in_(active_statuses),
                AssemblyRequest.wb_fbo_supply_id.in_(supply_ids),
            )
        )
        for row in asm_result.all():
            assembly_map[row.wb_fbo_supply_id] = (row.id, row.number, row.status)

    # Build enriched dicts
    enriched = []
    for s in supplies:
        d = {c.key: getattr(s, c.key) for c in WbFboSupply.__table__.columns}
        asm = assembly_map.get(s.id)
        d["assembly_request_id"] = asm[0] if asm else None
        d["assembly_request_number"] = asm[1] if asm else None
        d["assembly_request_status"] = asm[2] if asm else None
        enriched.append(d)

    return enriched, total


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

    # Lazy-load from WB API if no items cached or force refresh
    if (not items or force_refresh) and api_client and supply.wb_supply_id.isdigit():
        try:
            wb_id_int = int(supply.wb_supply_id)
            goods = await api_client.get_fbw_supply_goods(
                wb_id_int,
                limit=100,
                offset=0,
            )
            if goods:
                await _upsert_supply_items_fbw(db, project_id, supply.id, wb_id_int, goods)

            # Also fetch detail (warehouse_name, qty) if missing
            if not supply.warehouse_name:
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


# ─── Link / Unlink supply <-> OutboundShipment ───────────────────────────────


async def link_supply_to_shipment(
    db: AsyncSession,
    project_id: int,
    supply_id: int,
    outbound_shipment_id: int,
) -> WbFboSupply:
    """
    Link FBO supply to an OutboundShipment.
    Sets supply.outbound_shipment_id and shipment.wb_supply_id.
    """
    # Get supply
    result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    supply = result.scalar_one_or_none()
    if not supply:
        raise ValueError("FBO Supply not found")

    # Get shipment
    result = await db.execute(
        select(OutboundShipment).where(
            OutboundShipment.id == outbound_shipment_id,
            OutboundShipment.project_id == project_id,
            OutboundShipment.is_deleted == False,  # noqa: E712
        )
    )
    shipment = result.scalar_one_or_none()
    if not shipment:
        raise ValueError("Outbound shipment not found")

    # Link both sides
    supply.outbound_shipment_id = outbound_shipment_id
    shipment.wb_supply_id = supply.wb_supply_id

    # Auto-deliver if supply already ACCEPTED
    if supply.wb_status == WbSupplyStatus.ACCEPTED:
        await _auto_deliver_shipment(db, project_id, outbound_shipment_id)

    await db.commit()
    await db.refresh(supply)
    return supply


async def unlink_supply_from_shipment(
    db: AsyncSession,
    project_id: int,
    supply_id: int,
) -> WbFboSupply:
    """Unlink FBO supply from its OutboundShipment."""
    result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    supply = result.scalar_one_or_none()
    if not supply:
        raise ValueError("FBO Supply not found")

    if supply.outbound_shipment_id:
        # Clear shipment side
        result = await db.execute(
            select(OutboundShipment).where(
                OutboundShipment.id == supply.outbound_shipment_id,
                OutboundShipment.project_id == project_id,
                OutboundShipment.is_deleted == False,  # noqa: E712
            )
        )
        shipment = result.scalar_one_or_none()
        if shipment:
            shipment.wb_supply_id = None  # type: ignore[assignment]

    supply.outbound_shipment_id = None
    await db.commit()
    await db.refresh(supply)
    return supply
