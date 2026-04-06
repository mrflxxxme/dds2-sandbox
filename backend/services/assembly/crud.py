"""
Assembly Request service — CRUD operations and helpers.

Part of the assembly service package. See backend/DOMAIN_ASSEMBLY.md for spec.
"""

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.assembly import (
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
)
from backend.models.cost import Nomenclature
from backend.models.warehouse import (
    WarehouseStock,
    WarehouseType,
)
from backend.models.wb_fbo import WbFboSupply
from backend.schemas.assembly import (
    AssemblyRequestCreate,
    AssemblyRequestUpdate,
)
from backend.services.warehouse_service import (
    _next_number,
    _resolve_barcode,
    get_warehouse,
)

from .status import _log_status_change

# --- Helpers ----------------------------------------------------------------


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
