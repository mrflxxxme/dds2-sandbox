"""
Assembly Request service: business logic for assembly workflow.

See backend/DOMAIN_ASSEMBLY.md for spec + dependency map.

Depends on (from warehouse_service.py):
  - _resolve_barcode(db, project_id, barcode) -> Nomenclature   [L146]
  - _update_stock(db, ..., delta, movement_type, ...)           [L163]
  - _next_number(db, project_id, prefix, model_class)           [L122]
  - get_warehouse(db, project_id, warehouse_id) -> Warehouse     [L48]

Implementation notes:
  - Use _update_stock from warehouse_service (import it, don't duplicate)
  - Use _resolve_barcode for barcode -> nomenclature_id resolution
  - Use _next_number with prefix="ASM" for auto-numbering
  - OutboundShipment creation: follow pattern from create_shipment() [L520]
  - Stock validation before ship: check WarehouseStock.quantity >= need
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.cache import invalidate_cache
from backend.models.assembly import (
    ASSEMBLY_TRANSITIONS,
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
)
from backend.models.warehouse import (
    MovementType,
    OutboundShipment,
    OutboundShipmentItem,
    OutboundStatus,
    WarehouseStock,
    WarehouseType,
)
from backend.models.wb_fbo import WbFboSupply, WbFboSupplyItem
from backend.schemas.assembly import (
    AssemblyRequestCreate,
    AssemblyRequestUpdate,
    AssignVehicle,
    RefreshFromFboResponse,
)
from backend.services.warehouse_service import (
    _next_number,
    _resolve_barcode,
    _update_stock,
    get_warehouse,
)
from backend.utils.time import utcnow

# --- Helpers ----------------------------------------------------------------


def _check_transition(current: AssemblyStatus, target: AssemblyStatus) -> None:
    """Validate status transition. Raises ValueError if not allowed."""
    allowed = ASSEMBLY_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(f"Cannot transition from {current} to {target}. Allowed: {allowed or 'none'}")


async def _validate_stock_for_ship(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    items: list[AssemblyRequestItem],
) -> list[dict]:
    """
    Check stock availability for all items before shipping.
    Returns list of deficits: [{"barcode": ..., "need": ..., "have": ...}]
    Empty list = OK to ship.
    """
    deficits: list[dict] = []
    for item in items:
        result = await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id == warehouse_id,
                WarehouseStock.nomenclature_id == item.nomenclature_id,
            )
        )
        stock = result.scalar_one_or_none()
        have = stock.quantity if stock else 0
        if have < item.quantity:
            deficits.append(
                {
                    "barcode": item.barcode,
                    "need": item.quantity,
                    "have": have,
                }
            )
    return deficits


async def _build_response(
    db: AsyncSession,
    request: AssemblyRequest,
) -> dict:
    """
    Build AssemblyRequestResponse dict from ORM model.
    Loads warehouse.name, wb_fbo_supply.name, wb_fbo_supply.warehouse_name.
    Computes total_weight_kg = pallets_count * pallet_weight_kg.
    """
    await db.refresh(request, ["warehouse", "wb_fbo_supply", "items"])

    pallets = request.pallets_count or 0
    weight = request.pallet_weight_kg or Decimal("0")
    total_weight = Decimal(str(pallets)) * weight

    return {
        "id": request.id,
        "warehouse_id": request.warehouse_id,
        "warehouse_name": request.warehouse.name if request.warehouse else None,
        "number": request.number,
        "status": request.status,
        "wb_fbo_supply_id": request.wb_fbo_supply_id,
        "wb_supply_name": request.wb_fbo_supply.name if request.wb_fbo_supply else None,
        "wb_warehouse_name": request.wb_fbo_supply.warehouse_name if request.wb_fbo_supply else None,
        "outbound_shipment_id": request.outbound_shipment_id,
        "estimated_ready_date": request.estimated_ready_date,
        "actual_ready_date": request.actual_ready_date,
        "pallets_count": request.pallets_count,
        "pallet_weight_kg": request.pallet_weight_kg,
        "total_weight_kg": total_weight,
        "vehicle_info": request.vehicle_info,
        "vehicle_assigned_at": request.vehicle_assigned_at,
        "shipped_at": request.shipped_at,
        "comment": request.comment,
        "items": [
            {
                "id": item.id,
                "nomenclature_id": item.nomenclature_id,
                "barcode": item.barcode,
                "quantity": item.quantity,
            }
            for item in request.items
        ],
        "created_at": request.created_at,
        "updated_at": request.updated_at,
    }


# --- CRUD -------------------------------------------------------------------


async def list_assembly_requests(
    db: AsyncSession,
    project_id: int,
    *,
    warehouse_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AssemblyRequest], int]:
    """
    List assembly requests with filters, pagination.
    """
    base = select(AssemblyRequest).where(
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
    )

    if warehouse_id is not None:
        base = base.where(AssemblyRequest.warehouse_id == warehouse_id)
    if status is not None:
        base = base.where(AssemblyRequest.status == status)
    if date_from is not None:
        base = base.where(AssemblyRequest.created_at >= date_from)
    if date_to is not None:
        from datetime import datetime, time

        end = datetime.combine(date_to, time.max)
        base = base.where(AssemblyRequest.created_at <= end)
    if search:
        # Escape % and _ in search string before ILIKE
        escaped = search.replace("%", r"\%").replace("_", r"\_")
        base = base.where(AssemblyRequest.number.ilike(f"%{escaped}%"))

    # Total count
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Items
    items_q = (
        base.options(selectinload(AssemblyRequest.items))
        .order_by(AssemblyRequest.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(items_q)
    items = result.scalars().all()

    return items, total


async def get_assembly_request(
    db: AsyncSession,
    project_id: int,
    request_id: int,
) -> AssemblyRequest | None:
    """
    Get single assembly request with items loaded.
    """
    result = await db.execute(
        select(AssemblyRequest)
        .options(selectinload(AssemblyRequest.items))
        .where(
            AssemblyRequest.id == request_id,
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted == False,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def create_assembly_request(
    db: AsyncSession,
    project_id: int,
    payload: AssemblyRequestCreate,
) -> AssemblyRequest:
    """
    Create assembly request from payload.
    """
    # 1. Validate warehouse exists + type == FULFILLMENT
    wh = await get_warehouse(db, project_id, payload.warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")
    if wh.warehouse_type != WarehouseType.FULFILLMENT:
        raise ValueError("Assembly requests can only be created for FULFILLMENT warehouses")

    # 2. Validate FBO supply exists, is ACTIVE, has no active assembly request
    fbo_result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == payload.wb_fbo_supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    fbo_supply = fbo_result.scalar_one_or_none()
    if not fbo_supply:
        raise ValueError("FBO supply not found")
    if fbo_supply.wb_status != "ACTIVE":
        raise ValueError("FBO supply must be ACTIVE")

    # Check no active assembly request for this FBO supply
    existing_result = await db.execute(
        select(AssemblyRequest)
        .where(
            AssemblyRequest.wb_fbo_supply_id == payload.wb_fbo_supply_id,
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted == False,  # noqa: E712
            AssemblyRequest.status != AssemblyStatus.CANCELLED,
        )
        .limit(1)
    )
    if existing_result.scalar_one_or_none():
        raise ValueError("FBO supply already has an active assembly request")

    # 3. Generate number
    number = await _next_number(db, project_id, "ASM", AssemblyRequest)

    # 4. Create request
    assembly_req = AssemblyRequest(
        project_id=project_id,
        warehouse_id=payload.warehouse_id,
        number=number,
        status=AssemblyStatus.PENDING,
        wb_fbo_supply_id=payload.wb_fbo_supply_id,
        estimated_ready_date=payload.estimated_ready_date,
        pallets_count=payload.pallets_count,
        pallet_weight_kg=payload.pallet_weight_kg,
        comment=payload.comment,
    )
    db.add(assembly_req)
    await db.flush()

    # 5. Resolve barcodes and create items
    for item_data in payload.items:
        nom = await _resolve_barcode(db, project_id, item_data.barcode)
        item = AssemblyRequestItem(
            assembly_request_id=assembly_req.id,
            nomenclature_id=nom.id,
            barcode=item_data.barcode,
            quantity=item_data.quantity,
        )
        db.add(item)

    await db.commit()
    await db.refresh(assembly_req)
    return assembly_req


async def update_assembly_request(
    db: AsyncSession,
    project_id: int,
    request_id: int,
    payload: AssemblyRequestUpdate,
) -> AssemblyRequest:
    """
    Update editable fields.
    """
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    if req.status in (AssemblyStatus.SHIPPED, AssemblyStatus.CANCELLED):
        raise ValueError(f"Cannot edit in status {req.status}")

    # Update scalar fields (allowed until SHIPPED)
    if payload.pallets_count is not None:
        req.pallets_count = payload.pallets_count
    if payload.pallet_weight_kg is not None:
        req.pallet_weight_kg = payload.pallet_weight_kg
    if payload.comment is not None:
        req.comment = payload.comment
    if payload.estimated_ready_date is not None:
        req.estimated_ready_date = payload.estimated_ready_date

    # Update items: only in PENDING status
    if payload.items is not None:
        if req.status != AssemblyStatus.PENDING:
            raise ValueError("Items can only be edited in PENDING status")
        # Delete existing items
        await db.execute(delete(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == req.id))
        # Create new items
        for item_data in payload.items:
            nom = await _resolve_barcode(db, project_id, item_data.barcode)
            item = AssemblyRequestItem(
                assembly_request_id=req.id,
                nomenclature_id=nom.id,
                barcode=item_data.barcode,
                quantity=item_data.quantity,
            )
            db.add(item)

    await db.commit()
    await db.refresh(req)
    return req


# --- Status transitions ----------------------------------------------------


async def start_assembly(db: AsyncSession, project_id: int, request_id: int) -> AssemblyRequest:
    """PENDING -> IN_PROGRESS. No side effects."""
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.IN_PROGRESS)
    req.status = AssemblyStatus.IN_PROGRESS
    await db.commit()
    await db.refresh(req)
    return req


async def mark_ready(db: AsyncSession, project_id: int, request_id: int) -> AssemblyRequest:
    """IN_PROGRESS -> READY. Set actual_ready_date = today."""
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.READY)
    req.status = AssemblyStatus.READY
    req.actual_ready_date = date.today()
    await db.commit()
    await db.refresh(req)
    return req


async def assign_vehicle(db: AsyncSession, project_id: int, request_id: int, payload: AssignVehicle) -> AssemblyRequest:
    """READY -> VEHICLE_ASSIGNED. Set vehicle_info, vehicle_assigned_at."""
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.VEHICLE_ASSIGNED)
    req.status = AssemblyStatus.VEHICLE_ASSIGNED
    req.vehicle_info = payload.vehicle_info
    req.vehicle_assigned_at = utcnow()
    await db.commit()
    await db.refresh(req)
    return req


async def ship_request(db: AsyncSession, project_id: int, request_id: int) -> AssemblyRequest:
    """
    VEHICLE_ASSIGNED -> SHIPPED.
    """
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.SHIPPED)

    # 1. Validate stock
    deficits = await _validate_stock_for_ship(db, project_id, req.warehouse_id, req.items)
    if deficits:
        details = "; ".join(f"{d['barcode']}: need {d['need']}, have {d['have']}" for d in deficits)
        raise ValueError(f"Insufficient stock: {details}")

    # 2. Deduct stock
    for item in req.items:
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=req.warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=-item.quantity,
            movement_type=MovementType.OUTBOUND,
            reference_type="ASSEMBLY",
            reference_id=req.id,
        )

    # 3. Load FBO supply for linking
    fbo_result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == req.wb_fbo_supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    fbo_supply = fbo_result.scalar_one_or_none()

    # 4. Create OutboundShipment
    ship_number = await _next_number(db, project_id, "OUT", OutboundShipment)
    shipment = OutboundShipment(
        project_id=project_id,
        warehouse_id=req.warehouse_id,
        number=ship_number,
        status=OutboundStatus.SHIPPED,
        destination=fbo_supply.warehouse_name if fbo_supply else None,
        wb_supply_id=fbo_supply.wb_supply_id if fbo_supply else None,
        shipped_date=date.today(),
    )
    db.add(shipment)
    await db.flush()

    # 5. Create shipment items
    for item in req.items:
        ship_item = OutboundShipmentItem(
            shipment_id=shipment.id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            quantity=item.quantity,
        )
        db.add(ship_item)

    # 6. Link FBO supply
    if fbo_supply:
        fbo_supply.outbound_shipment_id = shipment.id

    # 7. Update assembly request
    req.outbound_shipment_id = shipment.id
    req.shipped_at = utcnow()
    req.status = AssemblyStatus.SHIPPED

    await db.commit()
    await db.refresh(req)

    await invalidate_cache("reports:balance")

    return req


async def cancel_request(db: AsyncSession, project_id: int, request_id: int) -> AssemblyRequest:
    """
    Any status -> CANCELLED.
    If current status == SHIPPED -> rollback first.
    """
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    current = AssemblyStatus(req.status)
    _check_transition(current, AssemblyStatus.CANCELLED)

    if current == AssemblyStatus.SHIPPED:
        # Rollback stock
        for item in req.items:
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=req.warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=+item.quantity,
                movement_type=MovementType.OUTBOUND,
                reference_type="ASSEMBLY_CANCEL",
                reference_id=req.id,
            )

        # Unlink FBO supply
        fbo_result = await db.execute(
            select(WbFboSupply).where(
                WbFboSupply.id == req.wb_fbo_supply_id,
                WbFboSupply.project_id == project_id,
            )
        )
        fbo_supply = fbo_result.scalar_one_or_none()
        if fbo_supply:
            fbo_supply.outbound_shipment_id = None

        # Soft-delete OutboundShipment
        if req.outbound_shipment_id:
            ship_result = await db.execute(
                select(OutboundShipment).where(
                    OutboundShipment.id == req.outbound_shipment_id,
                    OutboundShipment.project_id == project_id,
                    OutboundShipment.is_deleted == False,  # noqa: E712
                )
            )
            shipment = ship_result.scalar_one_or_none()
            if shipment:
                shipment.soft_delete()

        # Clear shipment link
        req.outbound_shipment_id = None
        req.shipped_at = None

    req.status = AssemblyStatus.CANCELLED
    await db.commit()
    await db.refresh(req)

    await invalidate_cache("reports:balance")

    return req


# --- Bulk operations --------------------------------------------------------


async def assign_vehicle_bulk(
    db: AsyncSession,
    project_id: int,
    ids: list[int],
    vehicle_info: str,
) -> list[AssemblyRequest]:
    """
    Bulk assign vehicle to multiple requests.
    """
    results = []
    for req_id in ids:
        payload = AssignVehicle(vehicle_info=vehicle_info)
        req = await assign_vehicle(db, project_id, req_id, payload)
        results.append(req)
    return results


async def ship_bulk(
    db: AsyncSession,
    project_id: int,
    ids: list[int],
) -> list[AssemblyRequest]:
    """
    Bulk ship multiple requests.
    """
    results = []
    for req_id in ids:
        req = await ship_request(db, project_id, req_id)
        results.append(req)
    return results


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
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    if req.status in (AssemblyStatus.SHIPPED, AssemblyStatus.CANCELLED):
        raise ValueError(f"Cannot refresh in status {req.status}")

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
            {
                "id": item.id,
                "nomenclature_id": item.nomenclature_id,
                "barcode": item.barcode,
                "quantity": item.quantity,
            }
            for item in req.items
        ],
    )
