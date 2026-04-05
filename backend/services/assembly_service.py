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

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.cache import cached, invalidate_cache
from backend.models.assembly import (
    ASSEMBLY_TRANSITIONS,
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
    AssemblyStatusHistory,
)
from backend.models.cost import Nomenclature
from backend.models.warehouse import (
    MovementType,
    OutboundShipment,
    OutboundShipmentItem,
    OutboundStatus,
    Warehouse,
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


async def _log_status_change(
    db: AsyncSession,
    project_id: int,
    request_id: int,
    old_status: str | None,
    new_status: str,
    changed_by: str = "user",
    comment: str | None = None,
) -> None:
    """Record a status transition in assembly_status_history."""
    history = AssemblyStatusHistory(
        project_id=project_id,
        assembly_request_id=request_id,
        old_status=old_status,
        new_status=new_status,
        changed_at=utcnow(),
        changed_by=changed_by,
        comment=comment,
    )
    db.add(history)


async def _validate_stock_for_ship(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    items: list[AssemblyRequestItem],
) -> list[dict]:
    """
    Check stock availability for all items before shipping.
    Returns list of deficits with product names for readable error messages.
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
            # Get product name from nomenclature
            nom_result = await db.execute(
                select(Nomenclature).where(
                    Nomenclature.id == item.nomenclature_id,
                )
            )
            nom = nom_result.scalar_one_or_none()
            name = (nom.subject or nom.article_seller or item.barcode) if nom else item.barcode
            deficits.append(
                {
                    "name": name,
                    "barcode": item.barcode,
                    "need": item.quantity,
                    "have": have,
                }
            )
    return deficits


async def _build_items_with_stock(
    db: AsyncSession,
    request: AssemblyRequest,
) -> list[dict]:
    """Build items list with product_name and stock_quantity.

    Uses batch queries instead of per-item N+1 queries.
    """
    if not request.items:
        return []

    nom_ids = [item.nomenclature_id for item in request.items if item.nomenclature_id]

    # Batch load nomenclature names
    nom_map: dict[int, Nomenclature] = {}
    if nom_ids:
        nom_result = await db.execute(select(Nomenclature).where(Nomenclature.id.in_(nom_ids)))
        nom_map = {n.id: n for n in nom_result.scalars().all()}

    # Batch load stock quantities
    stock_map: dict[int, int] = {}
    if nom_ids:
        stock_result = await db.execute(
            select(WarehouseStock.nomenclature_id, WarehouseStock.quantity).where(
                WarehouseStock.project_id == request.project_id,
                WarehouseStock.warehouse_id == request.warehouse_id,
                WarehouseStock.nomenclature_id.in_(nom_ids),
            )
        )
        for row in stock_result.all():
            stock_map[row.nomenclature_id] = stock_map.get(row.nomenclature_id, 0) + row.quantity

    items_out: list[dict] = []
    for item in request.items:
        nom = nom_map.get(item.nomenclature_id)
        product_name = (nom.subject or nom.article_seller or item.barcode) if nom else item.barcode

        items_out.append(
            {
                "id": item.id,
                "nomenclature_id": item.nomenclature_id,
                "barcode": item.barcode,
                "quantity": item.quantity,
                "product_name": product_name,
                "brand": nom.brand if nom else None,
                "stock_quantity": stock_map.get(item.nomenclature_id, 0),
            }
        )
    return items_out


async def _build_response(
    db: AsyncSession,
    request: AssemblyRequest,
) -> dict:
    """
    Build AssemblyRequestResponse dict from ORM model.
    Loads warehouse.name, wb_fbo_supply.name, wb_fbo_supply.warehouse_name.
    Computes total_weight_kg = pallets_count * pallet_weight_kg.
    """
    # Relationships are pre-loaded via selectinload in list/get queries.
    # Refresh only for direct calls (e.g. after create/update).
    try:
        _ = request.warehouse
    except Exception:
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
        "wb_supply_id_wb": request.wb_fbo_supply.wb_supply_id if request.wb_fbo_supply else None,
        "wb_fbo_status": request.wb_fbo_supply.wb_status if request.wb_fbo_supply else None,
        "wb_fbo_planned_date": request.wb_fbo_supply.planned_date if request.wb_fbo_supply else None,
        "wb_fbo_actual_date": request.wb_fbo_supply.actual_date if request.wb_fbo_supply else None,
        "outbound_shipment_id": request.outbound_shipment_id,
        "estimated_ready_date": request.estimated_ready_date,
        "actual_ready_date": request.actual_ready_date,
        "pallets_count": request.pallets_count,
        "pallet_weight_kg": request.pallet_weight_kg,
        "total_weight_kg": total_weight,
        "vehicle_info": request.vehicle_info,
        "vehicle_brand": request.vehicle_brand,
        "driver_phone": request.driver_phone,
        "pickup_date": request.pickup_date,
        "pickup_time_slot": request.pickup_time_slot,
        "pickup_cost": request.pickup_cost,
        "delivery_date": request.delivery_date,
        "vehicle_assigned_at": request.vehicle_assigned_at,
        "shipped_at": request.shipped_at,
        "comment": request.comment,
        "wb_warehouse_name_manual": request.wb_warehouse_name_manual,
        "effective_wb_warehouse": (
            request.wb_fbo_supply.warehouse_name if request.wb_fbo_supply else request.wb_warehouse_name_manual
        ),
        "items": (items := await _build_items_with_stock(db, request)),
        "brands": ", ".join(sorted({i["brand"] for i in items if i.get("brand")})) or None,
        "created_at": request.created_at,
        "updated_at": request.updated_at,
    }


# --- WB warehouses ----------------------------------------------------------


async def list_wb_warehouses(
    db: AsyncSession,
    project_id: int,
) -> list[str]:
    """
    Get distinct WB warehouse names from assembly requests and FBO supplies.
    Returns sorted list of unique non-null warehouse names.
    """
    # From assembly_requests.wb_warehouse_name_manual
    manual_q = (
        select(AssemblyRequest.wb_warehouse_name_manual)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted == False,  # noqa: E712
            AssemblyRequest.wb_warehouse_name_manual.isnot(None),
        )
        .distinct()
    )

    # From wb_fbo_supplies.warehouse_name
    fbo_q = (
        select(WbFboSupply.warehouse_name)
        .where(
            WbFboSupply.project_id == project_id,
            WbFboSupply.warehouse_name.isnot(None),
        )
        .distinct()
    )

    manual_result = await db.execute(manual_q)
    fbo_result = await db.execute(fbo_q)

    names: set[str] = set()
    for row in manual_result.scalars().all():
        if row:
            names.add(row)
    for row in fbo_result.scalars().all():
        if row:
            names.add(row)

    return sorted(names)


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
    brand: str | None = None,
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
        statuses = [s.strip() for s in status.split(",")]
        if len(statuses) == 1:
            base = base.where(AssemblyRequest.status == statuses[0])
        else:
            base = base.where(AssemblyRequest.status.in_(statuses))
    if date_from is not None:
        base = base.where(AssemblyRequest.created_at >= date_from)
    if date_to is not None:
        from datetime import datetime, time

        end = datetime.combine(date_to, time.max)
        base = base.where(AssemblyRequest.created_at <= end)
    if search:
        # Escape % and _ in search string before ILIKE
        escaped = search.replace("%", r"\%").replace("_", r"\_")
        # Search by assembly number OR WB supply ID (e.g. FBW-37969677)
        supply_ids_q = select(WbFboSupply.id).where(
            WbFboSupply.project_id == project_id,
            WbFboSupply.wb_supply_id.ilike(f"%{escaped}%", escape="\\"),
        )
        base = base.where(
            or_(
                AssemblyRequest.number.ilike(f"%{escaped}%", escape="\\"),
                AssemblyRequest.wb_fbo_supply_id.in_(supply_ids_q),
            )
        )

    if brand:
        brand_noms = select(Nomenclature.id).where(
            Nomenclature.project_id == project_id,
            Nomenclature.brand == brand,
        )
        brand_requests = (
            select(AssemblyRequestItem.assembly_request_id)
            .where(
                AssemblyRequestItem.nomenclature_id.in_(brand_noms),
            )
            .distinct()
        )
        base = base.where(AssemblyRequest.id.in_(brand_requests))

    # Total count
    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Items
    items_q = (
        base.options(
            selectinload(AssemblyRequest.items),
            selectinload(AssemblyRequest.warehouse),
            selectinload(AssemblyRequest.wb_fbo_supply),
        )
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
        .options(
            selectinload(AssemblyRequest.items),
            selectinload(AssemblyRequest.warehouse),
            selectinload(AssemblyRequest.wb_fbo_supply),
        )
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
    fbo_supply = None
    if payload.wb_fbo_supply_id is not None:
        fbo_result = await db.execute(
            select(WbFboSupply).where(
                WbFboSupply.id == payload.wb_fbo_supply_id,
                WbFboSupply.project_id == project_id,
            )
        )
        fbo_supply = fbo_result.scalar_one_or_none()
        if not fbo_supply:
            raise ValueError("FBO supply not found")
        if fbo_supply.wb_status not in ("ACTIVE", "ON_DELIVERY", "IN_PROGRESS"):
            raise ValueError("FBO supply must be ACTIVE, ON_DELIVERY or IN_PROGRESS")

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
    # Auto-set wb_warehouse_name_manual from FBO supply if linked
    wb_wh_manual = payload.wb_warehouse_name_manual
    if payload.wb_fbo_supply_id is not None and fbo_supply and not wb_wh_manual:
        wb_wh_manual = fbo_supply.warehouse_name

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
        wb_warehouse_name_manual=wb_wh_manual,
    )
    db.add(assembly_req)
    await db.flush()

    # 5. Resolve barcodes and create items
    resolved_items: list[AssemblyRequestItem] = []
    for item_data in payload.items:
        nom = await _resolve_barcode(db, project_id, item_data.barcode)
        item = AssemblyRequestItem(
            project_id=project_id,
            assembly_request_id=assembly_req.id,
            nomenclature_id=nom.id,
            barcode=item_data.barcode,
            quantity=item_data.quantity,
        )
        db.add(item)
        resolved_items.append(item)

    # 6. Validate stock availability
    await db.flush()
    deficits = await _validate_stock_for_ship(db, project_id, payload.warehouse_id, resolved_items)
    if deficits:
        lines = [f"  {d['name']} (ШК {d['barcode']}): нужно {d['need']}, на складе {d['have']}" for d in deficits]
        raise ValueError(f"Недостаточно остатков на складе ({len(deficits)} поз.):\n" + "\n".join(lines))

    await _log_status_change(db, project_id, assembly_req.id, None, AssemblyStatus.PENDING, changed_by="user")

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

    if req.status == AssemblyStatus.CANCELLED:
        raise ValueError(f"Cannot edit in status {req.status}")

    # SHIPPED / DELIVERED — allow only cost, pallets and vehicle fields
    _is_closed = req.status in (AssemblyStatus.SHIPPED, AssemblyStatus.DELIVERED)

    # Update FBO supply link — not allowed in closed statuses
    if payload.wb_fbo_supply_id is not None:
        if _is_closed:
            raise ValueError("Cannot change FBO supply in status " + req.status)
        # Validate the FBO supply
        fbo_result = await db.execute(
            select(WbFboSupply).where(
                WbFboSupply.id == payload.wb_fbo_supply_id,
                WbFboSupply.project_id == project_id,
            )
        )
        fbo_supply = fbo_result.scalar_one_or_none()
        if not fbo_supply:
            raise ValueError("FBO supply not found")
        if fbo_supply.wb_status not in ("ACTIVE", "ON_DELIVERY", "IN_PROGRESS"):
            raise ValueError("FBO supply must be ACTIVE, ON_DELIVERY or IN_PROGRESS")

        # Check no other active assembly request for this FBO supply (except current)
        existing_result = await db.execute(
            select(AssemblyRequest)
            .where(
                AssemblyRequest.wb_fbo_supply_id == payload.wb_fbo_supply_id,
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted == False,  # noqa: E712
                AssemblyRequest.status != AssemblyStatus.CANCELLED,
                AssemblyRequest.id != req.id,
            )
            .limit(1)
        )
        if existing_result.scalar_one_or_none():
            raise ValueError("FBO supply already has an active assembly request")

        req.wb_fbo_supply_id = payload.wb_fbo_supply_id
        # Auto-set wb_warehouse_name_manual from FBO supply
        req.wb_warehouse_name_manual = fbo_supply.warehouse_name

    # Update scalar fields — not allowed in closed statuses
    if not _is_closed:
        if payload.pallets_count is not None:
            req.pallets_count = payload.pallets_count
        if payload.pallet_weight_kg is not None:
            req.pallet_weight_kg = payload.pallet_weight_kg
        if payload.comment is not None:
            req.comment = payload.comment
        if payload.estimated_ready_date is not None:
            req.estimated_ready_date = payload.estimated_ready_date
        if payload.wb_warehouse_name_manual is not None:
            req.wb_warehouse_name_manual = payload.wb_warehouse_name_manual

    # Vehicle & cost fields — editable in any non-cancelled status (including SHIPPED/DELIVERED)
    if payload.pickup_cost is not None:
        req.pickup_cost = payload.pickup_cost
    if payload.vehicle_info is not None:
        req.vehicle_info = payload.vehicle_info
    if payload.vehicle_brand is not None:
        req.vehicle_brand = payload.vehicle_brand
    if payload.driver_phone is not None:
        req.driver_phone = payload.driver_phone

    # Update items: allowed until READY (PENDING and IN_PROGRESS)
    if payload.items is not None:
        if req.status not in (AssemblyStatus.PENDING, AssemblyStatus.IN_PROGRESS):
            raise ValueError("Items can only be edited before READY status")
        # Delete existing items
        await db.execute(delete(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == req.id))
        # Create new items
        for item_data in payload.items:
            nom = await _resolve_barcode(db, project_id, item_data.barcode)
            item = AssemblyRequestItem(
                project_id=project_id,
                assembly_request_id=req.id,
                nomenclature_id=nom.id,
                barcode=item_data.barcode,
                quantity=item_data.quantity,
            )
            db.add(item)

    await db.commit()
    # Expunge all cached objects so selectinload re-fetches fresh data from DB
    db.expunge_all()
    return await get_assembly_request(db, project_id, req.id)


# --- Status transitions ----------------------------------------------------


async def start_assembly(db: AsyncSession, project_id: int, request_id: int) -> AssemblyRequest:
    """PENDING -> IN_PROGRESS. No side effects."""
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.IN_PROGRESS)
    old = req.status
    req.status = AssemblyStatus.IN_PROGRESS
    await _log_status_change(db, project_id, req.id, old, AssemblyStatus.IN_PROGRESS)
    await db.commit()
    await db.refresh(req)
    return req


async def mark_ready(db: AsyncSession, project_id: int, request_id: int) -> AssemblyRequest:
    """IN_PROGRESS -> READY. Set actual_ready_date = today.

    Requires pallets_count > 0 and pallet_weight_kg > 0.
    """
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    if not req.wb_fbo_supply_id:
        raise ValueError("Нельзя перевести заявку в статус ГОТОВО без привязанной поставки WB")
    if not req.pallets_count or req.pallets_count <= 0:
        raise ValueError("Укажите количество палет перед завершением сборки")
    if not req.pallet_weight_kg or req.pallet_weight_kg <= 0:
        raise ValueError("Укажите вес палеты перед завершением сборки")

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.READY)
    old = req.status
    req.status = AssemblyStatus.READY
    req.actual_ready_date = date.today()
    await _log_status_change(db, project_id, req.id, old, AssemblyStatus.READY)
    await db.commit()
    await db.refresh(req)
    return req


async def assign_vehicle(db: AsyncSession, project_id: int, request_id: int, payload: AssignVehicle) -> AssemblyRequest:
    """READY -> VEHICLE_ASSIGNED. Set vehicle_info, vehicle_assigned_at."""
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.VEHICLE_ASSIGNED)
    old = req.status
    req.status = AssemblyStatus.VEHICLE_ASSIGNED
    req.vehicle_info = payload.vehicle_info
    req.vehicle_brand = payload.vehicle_brand
    req.driver_phone = payload.driver_phone
    req.pickup_date = payload.pickup_date
    req.pickup_time_slot = payload.pickup_time_slot
    req.pickup_cost = payload.pickup_cost
    req.delivery_date = payload.delivery_date
    req.vehicle_assigned_at = utcnow()
    await _log_status_change(db, project_id, req.id, old, AssemblyStatus.VEHICLE_ASSIGNED, comment=payload.vehicle_info)
    await db.commit()
    await db.refresh(req)
    return req


async def unassign_vehicle(db: AsyncSession, project_id: int, request_id: int) -> AssemblyRequest:
    """VEHICLE_ASSIGNED -> READY. Clear vehicle info, return to ready for shipping."""
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.READY)
    old = req.status
    req.status = AssemblyStatus.READY
    req.vehicle_info = None
    req.vehicle_brand = None
    req.driver_phone = None
    req.pickup_date = None
    req.pickup_time_slot = None
    req.pickup_cost = None
    req.delivery_date = None
    req.vehicle_assigned_at = None
    await _log_status_change(db, project_id, req.id, old, AssemblyStatus.READY, comment="Отмена назначения машины")
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
        lines = [f"  {d['name']} (ШК {d['barcode']}): нужно {d['need']}, на складе {d['have']}" for d in deficits]
        raise ValueError(f"Недостаточно остатков на складе ({len(deficits)} поз.):\n" + "\n".join(lines))

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
            project_id=project_id,
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
    old = req.status
    req.outbound_shipment_id = shipment.id
    req.shipped_at = utcnow()
    req.status = AssemblyStatus.SHIPPED
    await _log_status_change(db, project_id, req.id, old, AssemblyStatus.SHIPPED)

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
    await _log_status_change(db, project_id, req.id, current, AssemblyStatus.CANCELLED)
    await db.commit()
    await db.refresh(req)

    await invalidate_cache("reports:balance")

    return req


# --- Bulk operations --------------------------------------------------------


async def assign_vehicle_bulk(
    db: AsyncSession,
    project_id: int,
    ids: list[int],
    payload: AssignVehicle,
) -> list[AssemblyRequest]:
    """
    Bulk assign vehicle to multiple requests.
    """
    results = []
    for req_id in ids:
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


# --- Deliver ----------------------------------------------------------------


async def deliver_request(
    db: AsyncSession,
    project_id: int,
    request_id: int,
    changed_by: str = "user",
    comment: str | None = None,
) -> AssemblyRequest:
    """SHIPPED -> DELIVERED."""
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")
    _check_transition(AssemblyStatus(req.status), AssemblyStatus.DELIVERED)
    old = req.status
    req.status = AssemblyStatus.DELIVERED
    await _log_status_change(
        db, project_id, req.id, old, AssemblyStatus.DELIVERED, changed_by=changed_by, comment=comment
    )
    await db.commit()
    await db.refresh(req)
    return req


# --- History ----------------------------------------------------------------


async def get_assembly_history(
    db: AsyncSession,
    project_id: int,
    request_id: int,
) -> list[AssemblyStatusHistory]:
    """Get status change history for an assembly request."""
    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")
    result = await db.execute(
        select(AssemblyStatusHistory)
        .where(AssemblyStatusHistory.assembly_request_id == request_id)
        .order_by(AssemblyStatusHistory.changed_at.asc())
    )
    return result.scalars().all()


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
            {
                "id": item.id,
                "nomenclature_id": item.nomenclature_id,
                "barcode": item.barcode,
                "quantity": item.quantity,
            }
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
        from datetime import timedelta

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
