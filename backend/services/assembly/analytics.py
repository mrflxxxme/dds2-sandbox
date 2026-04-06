"""
Assembly Request service — FBO sync and logistics analytics.

Part of the assembly service package. See backend/DOMAIN_ASSEMBLY.md for spec.
One-way dependency: analytics -> crud (never crud -> analytics).
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models.assembly import (
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
)
from backend.models.cost import Nomenclature
from backend.models.warehouse import Warehouse
from backend.models.wb_fbo import WbFboSupply, WbFboSupplyItem
from backend.schemas.assembly import AssemblyItemResponse, RefreshFromFboResponse
from backend.services.warehouse_service import _resolve_barcode

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

    # Load FBO supply items
    fbo_items_result = await db.execute(
        select(WbFboSupplyItem).where(
            WbFboSupplyItem.supply_id == req.wb_fbo_supply_id,
        )
    )
    fbo_items = fbo_items_result.scalars().all()

    # Build maps by barcode
    current_map: dict[str, AssemblyRequestItem] = {item.barcode: item for item in req.items}
    fbo_map: dict[str, WbFboSupplyItem] = {item.barcode: item for item in fbo_items}

    added = 0
    removed = 0
    changed = 0

    # Remove items not in FBO anymore
    for barcode, item in current_map.items():
        if barcode not in fbo_map:
            await db.delete(item)  # AssemblyRequestItem has no SoftDeleteMixin
            removed += 1

    # Add new / update existing
    for barcode, fbo_item in fbo_map.items():
        if barcode in current_map:
            existing = current_map[barcode]
            if existing.quantity != fbo_item.quantity:
                existing.quantity = fbo_item.quantity
                changed += 1
        else:
            # New barcode - resolve nomenclature
            nom = await _resolve_barcode(db, project_id, barcode)
            new_item = AssemblyRequestItem(
                project_id=project_id,
                assembly_request_id=req.id,
                nomenclature_id=nom.id,
                barcode=barcode,
                quantity=fbo_item.quantity,
            )
            db.add(new_item)
            added += 1

    await db.commit()
    await db.refresh(req, ["items"])

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

    # Base filters — always applied
    base_filters = [
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
        AssemblyRequest.status.in_([AssemblyStatus.SHIPPED, AssemblyStatus.DELIVERED]),
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
