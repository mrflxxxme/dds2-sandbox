"""
Supply Chain — Vehicle (CostOrder) CRUD, status management and overview.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.cost import CostOrder, CostOrderItem
from backend.models.enums import VehicleStatus
from backend.models.planning import LeadTime
from backend.models.supply_chain import FactoryOrder, FactoryOrderItem
from backend.models.warehouse import (
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
)
from backend.schemas.supply_chain import (
    CONTAINER_TRANSPORT_MAP,
    AddItemsToVehicleRequest,
    VehicleCostSummary,
    VehicleCreate,
    VehicleItemSchema,
    VehicleSchema,
    VehicleStatusUpdate,
    VehicleUpdate,
)
from backend.services.warehouse_stock_engine import _next_number, _resolve_barcode
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


def _safe_decimal(val: Decimal | None) -> Decimal | None:
    """Return None for NaN/Infinity Decimals (bad data in DB)."""
    if val is None:
        return None
    if not val.is_finite():
        return None
    return val


# Status transition rules: current → allowed next statuses
VALID_TRANSITIONS: dict[str, list[str]] = {
    VehicleStatus.FORMING: [VehicleStatus.SHIPPED],
    VehicleStatus.SHIPPED: [VehicleStatus.CUSTOMS],
    VehicleStatus.CUSTOMS: [VehicleStatus.DELIVERED],
}


async def list_vehicles(
    db: AsyncSession,
    project_id: int,
) -> list[VehicleSchema]:
    """List all vehicles (CostOrders with supply chain status) with aggregated data."""
    result = await db.execute(
        select(CostOrder)
        .options(selectinload(CostOrder.items))
        .where(
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712
            CostOrder.status.isnot(None),
        )
        .order_by(CostOrder.created_at.desc())
        .limit(500)
    )
    vehicles = result.scalars().all()
    return [await _enrich_vehicle(db, project_id, v) for v in vehicles]


async def get_vehicle(
    db: AsyncSession,
    project_id: int,
    order_no: str,
) -> VehicleSchema | None:
    """Get single vehicle with items and factory order context."""
    result = await db.execute(
        select(CostOrder)
        .options(selectinload(CostOrder.items))
        .where(
            CostOrder.order_no == order_no,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712
        )
    )
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        return None
    return await _enrich_vehicle(db, project_id, vehicle)


async def create_vehicle(
    db: AsyncSession,
    project_id: int,
    data: VehicleCreate,
) -> VehicleSchema:
    """Create a new vehicle (CostOrder) for forming."""
    transport_type = CONTAINER_TRANSPORT_MAP.get(data.container_type, "AUTO")
    vehicle = CostOrder(
        project_id=project_id,
        order_no=data.order_no,
        container_type=data.container_type,
        transport_type=transport_type,
        delivery_cost_cny=data.delivery_cost_cny,
        delivery_cost_usd=data.delivery_cost_usd,
        rate_cny=data.rate_cny,
        rate_usd=data.rate_usd,
        rate_eur=data.rate_eur,
        ship_date=data.ship_date,
        invoice_no=data.invoice_no,
        target_warehouse_id=data.target_warehouse_id,
        note=data.note,
        status=VehicleStatus.FORMING,
    )
    db.add(vehicle)
    await db.commit()
    await db.refresh(vehicle, attribute_names=["items"])
    return await _enrich_vehicle(db, project_id, vehicle)


async def update_vehicle(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    data: VehicleUpdate,
) -> VehicleSchema | None:
    """Update vehicle fields. Most fields only in FORMING; invoice_no/dt_number always."""
    result = await db.execute(
        select(CostOrder)
        .options(selectinload(CostOrder.items))
        .where(
            CostOrder.order_no == order_no,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712
        )
    )
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        return None

    always_editable = {"invoice_no", "dt_number", "note"}
    update_data = data.model_dump(exclude_unset=True)

    if vehicle.status != VehicleStatus.FORMING:
        restricted = set(update_data.keys()) - always_editable
        if restricted:
            raise ValueError("Редактирование возможно только в статусе ФОРМИРОВАНИЕ")

    for field, value in update_data.items():
        if field == "container_type" and value:
            vehicle.container_type = value
            vehicle.transport_type = CONTAINER_TRANSPORT_MAP.get(value, "AUTO")
        else:
            setattr(vehicle, field, value)

    await db.commit()
    await db.refresh(vehicle, attribute_names=["items"])
    return await _enrich_vehicle(db, project_id, vehicle)


async def add_items_to_vehicle(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    data: AddItemsToVehicleRequest,
) -> dict:
    """
    Add items from factory orders to a vehicle (from vehicle side).
    Validates remaining qty and updates assigned_qty on FactoryOrderItem.
    """
    # Get vehicle
    result = await db.execute(
        select(CostOrder).where(
            CostOrder.order_no == order_no,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712
        )
    )
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise ValueError(f"Vehicle {order_no} not found")

    added = 0
    for item_req in data.items:
        # Get factory order item
        fo_item_result = await db.execute(
            select(FactoryOrderItem)
            .join(FactoryOrder)
            .where(
                FactoryOrderItem.id == item_req.factory_order_item_id,
                FactoryOrder.project_id == project_id,
                FactoryOrder.is_deleted == False,  # noqa: E712
            )
        )
        fo_item = fo_item_result.scalar_one_or_none()
        if not fo_item:
            raise ValueError(f"FactoryOrderItem {item_req.factory_order_item_id} not found")

        remaining = fo_item.qty - fo_item.assigned_qty
        if item_req.qty > remaining:
            raise ValueError(f"Cannot assign {item_req.qty} of {fo_item.barcode}: " f"only {remaining} remaining")

        # Resolve subject/article from Nomenclature if missing on factory order item
        subject = fo_item.subject
        article_seller = fo_item.article_seller
        if not subject or not article_seller:
            from backend.models.cost import Nomenclature

            nom_result = await db.execute(
                select(Nomenclature).where(
                    Nomenclature.barcode == fo_item.barcode,
                    Nomenclature.project_id == project_id,
                )
            )
            nom = nom_result.scalar_one_or_none()
            if nom:
                subject = subject or nom.subject
                article_seller = article_seller or nom.article_seller

        # Create CostOrderItem
        cost_item = CostOrderItem(
            project_id=project_id,
            order_no=vehicle.order_no,
            barcode=fo_item.barcode,
            subject=subject,
            article_seller=article_seller,
            qty=item_req.qty,
            price_cny=fo_item.price_cny,
            weight_kg=fo_item.weight_kg,
            factory_order_item_id=fo_item.id,
        )
        db.add(cost_item)
        fo_item.assigned_qty += item_req.qty
        added += 1

    await db.commit()
    return {"ok": True, "added": added}


async def remove_item_from_vehicle(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    item_id: int,
) -> dict:
    """Remove a CostOrderItem from vehicle and restore assigned_qty."""
    result = await db.execute(
        select(CostOrderItem)
        .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
        .where(
            CostOrderItem.id == item_id,
            CostOrderItem.order_no == order_no,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712
        )
    )
    cost_item = result.scalar_one_or_none()
    if not cost_item:
        raise ValueError(f"Item {item_id} not found in vehicle {order_no}")

    # Restore assigned_qty on factory order item
    if cost_item.factory_order_item_id:
        fo_item_result = await db.execute(
            select(FactoryOrderItem).where(
                FactoryOrderItem.id == cost_item.factory_order_item_id,
            )
        )
        fo_item = fo_item_result.scalar_one_or_none()
        if fo_item:
            fo_item.assigned_qty = max(0, fo_item.assigned_qty - cost_item.qty)

    await db.delete(cost_item)
    await db.commit()
    return {"ok": True}


async def get_available_items(
    db: AsyncSession,
    project_id: int,
) -> list[dict]:
    """
    Get all factory order items with remaining qty > 0,
    grouped by factory order.
    """
    result = await db.execute(
        select(FactoryOrder)
        .options(selectinload(FactoryOrder.items))
        .where(
            FactoryOrder.project_id == project_id,
            FactoryOrder.is_deleted == False,  # noqa: E712
        )
        .order_by(FactoryOrder.order_number)
        .limit(200)
    )
    orders = result.scalars().all()

    # Load nomenclature for enrichment
    from backend.models.cost import Nomenclature

    nom_result = await db.execute(select(Nomenclature).where(Nomenclature.project_id == project_id))
    nom_map = {n.barcode: n for n in nom_result.scalars().all()}

    groups = []
    for order in orders:
        available_items = []
        for item in order.items:
            remaining = item.qty - item.assigned_qty
            if remaining > 0:
                nom = nom_map.get(item.barcode)
                subject = item.subject or (nom.subject if nom else None)
                article_seller = item.article_seller or (nom.article_seller if nom else None)
                available_items.append(
                    {
                        "id": item.id,
                        "barcode": item.barcode,
                        "subject": subject,
                        "article_seller": article_seller,
                        "qty": item.qty,
                        "assigned_qty": item.assigned_qty,
                        "remaining_qty": remaining,
                        "price_cny": str(item.price_cny),
                        "box_size": item.box_size,
                        "pcs_per_box": item.pcs_per_box,
                        "weight_kg": str(item.weight_kg) if item.weight_kg else None,
                    }
                )
        if available_items:
            groups.append(
                {
                    "order_id": order.id,
                    "order_number": order.order_number,
                    "factory_name": order.factory_name,
                    "items": available_items,
                }
            )
    return groups


async def _enrich_vehicle(
    db: AsyncSession,
    project_id: int,
    vehicle: CostOrder,
) -> VehicleSchema:
    """Enrich CostOrder with aggregated data and factory order context for items."""
    items_data = []
    total_qty = 0
    total_cny = Decimal("0")
    total_weight = Decimal("0")
    total_volume = Decimal("0")
    has_weight = False
    has_volume = False

    # Batch-load FactoryOrderItem + FactoryOrder data to avoid N+1
    fo_item_ids = [ci.factory_order_item_id for ci in vehicle.items if ci.factory_order_item_id is not None]
    fo_item_map: dict[int, tuple] = {}
    if fo_item_ids:
        fo_batch_result = await db.execute(
            select(
                FactoryOrderItem.id,
                FactoryOrderItem.box_size,
                FactoryOrderItem.pcs_per_box,
                FactoryOrder.order_number,
            )
            .join(FactoryOrder, FactoryOrderItem.factory_order_id == FactoryOrder.id)
            .where(
                FactoryOrderItem.id.in_(fo_item_ids),
                FactoryOrder.project_id == project_id,
            )
        )
        for row in fo_batch_result:
            fo_item_map[row[0]] = (row[1], row[2], row[3])

    for cost_item in vehicle.items:
        total_qty += cost_item.qty
        total_cny += cost_item.price_cny * cost_item.qty

        # Enrich with factory order item data (from pre-loaded batch)
        box_size = None
        pcs_per_box = None
        fo_order_number = None

        if cost_item.factory_order_item_id:
            fo_data = fo_item_map.get(cost_item.factory_order_item_id)
            if fo_data:
                box_size, pcs_per_box, fo_order_number = fo_data

        w = _safe_decimal(cost_item.weight_kg)
        v = _safe_decimal(cost_item.volume_m3)
        if w:
            total_weight += w * cost_item.qty
            has_weight = True
        if v:
            total_volume += v * cost_item.qty
            has_volume = True

        items_data.append(
            VehicleItemSchema(
                id=cost_item.id,
                order_no=cost_item.order_no,
                barcode=cost_item.barcode,
                subject=cost_item.subject,
                article_seller=cost_item.article_seller,
                qty=cost_item.qty,
                price_cny=cost_item.price_cny,
                weight_kg=_safe_decimal(cost_item.weight_kg),
                volume_m3=_safe_decimal(cost_item.volume_m3),
                cost_rub=_safe_decimal(cost_item.cost_rub),
                delivery_rub=_safe_decimal(cost_item.delivery_rub),
                duty_rub=_safe_decimal(cost_item.duty_rub),
                vat_rub=_safe_decimal(cost_item.vat_rub),
                total_rub=_safe_decimal(cost_item.total_rub),
                factory_order_item_id=cost_item.factory_order_item_id,
                box_size=box_size,
                pcs_per_box=pcs_per_box,
                factory_order_number=fo_order_number,
            )
        )

    # Calculate estimated arrival from lead time
    estimated_arrival = None
    base_date = vehicle.actual_ship_date or vehicle.ship_date
    if base_date:
        transport = vehicle.transport_type or "AUTO"
        lead_days = await _get_lead_time_days(db, project_id, transport)
        if lead_days:
            estimated_arrival = base_date + timedelta(days=lead_days)

    # Cost summary from items
    cost_summary = None
    if items_data:
        tc = Decimal("0")
        td = Decimal("0")
        tdu = Decimal("0")
        tv = Decimal("0")
        tr = Decimal("0")
        has_costs = False
        for ci in vehicle.items:
            if ci.total_rub is not None:
                has_costs = True
                qty = Decimal(str(ci.qty))
                tc += (ci.cost_rub or Decimal("0")) * qty
                td += (ci.delivery_rub or Decimal("0")) * qty
                tdu += (ci.duty_rub or Decimal("0")) * qty
                tv += (ci.vat_rub or Decimal("0")) * qty
                tr += (ci.total_rub or Decimal("0")) * qty
        if has_costs:
            cost_summary = VehicleCostSummary(
                total_cost_rub=tc,
                total_delivery_rub=td,
                total_duty_rub=tdu,
                total_vat_rub=tv,
                total_rub=tr,
            )

    return VehicleSchema(
        id=vehicle.id,
        order_no=vehicle.order_no,
        status=vehicle.status,
        transport_type=vehicle.transport_type,
        container_type=vehicle.container_type,
        ship_date=vehicle.ship_date,
        actual_ship_date=vehicle.actual_ship_date,
        actual_arrival_date=vehicle.actual_arrival_date,
        estimated_arrival_date=estimated_arrival,
        delivery_cost_cny=vehicle.delivery_cost_cny,
        delivery_cost_usd=vehicle.delivery_cost_usd,
        rate_cny=vehicle.rate_cny,
        rate_usd=vehicle.rate_usd,
        rate_eur=vehicle.rate_eur,
        invoice_no=vehicle.invoice_no,
        note=vehicle.note,
        dt_number=vehicle.dt_number,
        target_warehouse_id=vehicle.target_warehouse_id,
        inbound_receipt_id=vehicle.inbound_receipt_id,
        created_at=vehicle.created_at,
        items=items_data,
        items_count=len(items_data),
        total_qty=total_qty,
        total_cny=total_cny,
        total_weight_kg=total_weight if has_weight else None,
        total_volume_m3=total_volume if has_volume else None,
        cost_summary=cost_summary,
    )


async def _get_lead_time_days(
    db: AsyncSession,
    project_id: int,
    transport_type: str,
) -> int | None:
    """Get lead time days for transport type from project settings."""
    result = await db.execute(
        select(LeadTime.days).where(
            LeadTime.project_id == project_id,
            LeadTime.direction == transport_type,
        )
    )
    return result.scalar_one_or_none()


async def update_vehicle_status(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    data: VehicleStatusUpdate,
) -> dict:
    """
    Update CostOrder (vehicle) status with transition validation.

    FORMING → SHIPPED: recalc costs, generate payment plan, set actual_ship_date.
    SHIPPED → CUSTOMS: optionally set dt_number.
    CUSTOMS → DELIVERED: auto-create InboundReceipt.
    """
    result = await db.execute(
        select(CostOrder)
        .options(selectinload(CostOrder.items))
        .where(
            CostOrder.order_no == order_no,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712
        )
    )
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise ValueError(f"Vehicle (CostOrder) with order_no={order_no} not found")

    # Validate transition
    current = vehicle.status or VehicleStatus.FORMING
    allowed = VALID_TRANSITIONS.get(current, [])
    if data.status not in allowed:
        raise ValueError(
            f"Нельзя перейти из {current} в {data.status}. "
            f"Допустимо: {', '.join(allowed) if allowed else 'нет переходов'}"
        )

    if data.target_warehouse_id is not None:
        vehicle.target_warehouse_id = data.target_warehouse_id

    result_data: dict = {"ok": True, "order_no": order_no, "status": data.status}
    receipt = None

    # FORMING → SHIPPED
    if data.status == VehicleStatus.SHIPPED:
        if not vehicle.items:
            raise ValueError("Нельзя отгрузить пустую машину — добавьте позиции")
        vehicle.actual_ship_date = utcnow().date()
        # Recalculate costs
        from backend.services.cost.items import recalculate_order_items

        updated, err = await recalculate_order_items(db, project_id, order_no)
        if err:
            raise ValueError(f"Ошибка пересчёта: {err}")
        # Generate payment plan
        from backend.services.cost.plan_gen import generate_payment_plan

        await generate_payment_plan(db, project_id, order_no)

    # SHIPPED → CUSTOMS: optionally set dt_number
    if data.status == VehicleStatus.CUSTOMS and data.dt_number:
        vehicle.dt_number = data.dt_number

    # CUSTOMS → DELIVERED: auto-create InboundReceipt
    if data.status == VehicleStatus.DELIVERED and vehicle.target_warehouse_id:
        receipt = await _create_inbound_from_vehicle(db, project_id, vehicle)
        vehicle.inbound_receipt_id = receipt.id

    vehicle.status = data.status
    await db.commit()

    if receipt:
        result_data["inbound_receipt_id"] = receipt.id
        result_data["inbound_receipt_number"] = receipt.number

    return result_data


async def recalculate_vehicle(
    db: AsyncSession,
    project_id: int,
    order_no: str,
) -> VehicleCostSummary:
    """Recalculate costs for vehicle items (preview, no status change)."""
    from backend.services.cost.items import recalculate_order_items

    updated, err = await recalculate_order_items(db, project_id, order_no)
    if err:
        raise ValueError(err)

    # Aggregate from recalculated items
    items_result = await db.execute(select(CostOrderItem).where(CostOrderItem.order_no == order_no))
    items = items_result.scalars().all()

    total_cost = Decimal("0")
    total_delivery = Decimal("0")
    total_duty = Decimal("0")
    total_vat = Decimal("0")
    total_rub = Decimal("0")

    for item in items:
        qty = Decimal(str(item.qty))
        total_cost += (item.cost_rub or Decimal("0")) * qty
        total_delivery += (item.delivery_rub or Decimal("0")) * qty
        total_duty += (item.duty_rub or Decimal("0")) * qty
        total_vat += (item.vat_rub or Decimal("0")) * qty
        total_rub += (item.total_rub or Decimal("0")) * qty

    return VehicleCostSummary(
        total_cost_rub=total_cost,
        total_delivery_rub=total_delivery,
        total_duty_rub=total_duty,
        total_vat_rub=total_vat,
        total_rub=total_rub,
    )


async def _create_inbound_from_vehicle(
    db: AsyncSession,
    project_id: int,
    vehicle: CostOrder,
) -> InboundReceipt:
    """
    Create InboundReceipt from CostOrder items when vehicle is delivered.
    Uses the same pattern as warehouse_inbound.create_receipt.
    """
    number = await _next_number(db, project_id, "IN", InboundReceipt)

    receipt = InboundReceipt(
        project_id=project_id,
        warehouse_id=vehicle.target_warehouse_id,
        number=number,
        status=InboundStatus.EXPECTED,
        planned_date=utcnow().date(),
        comment=f"Auto-created from vehicle {vehicle.order_no}",
        cost_order_id=vehicle.id,
    )
    db.add(receipt)
    await db.flush()  # get receipt.id

    for cost_item in vehicle.items:
        try:
            nom = await _resolve_barcode(db, project_id, cost_item.barcode)
            item = InboundReceiptItem(
                project_id=project_id,
                receipt_id=receipt.id,
                nomenclature_id=nom.id,
                barcode=cost_item.barcode,
                expected_qty=cost_item.qty,
                actual_qty=0,
            )
            db.add(item)
        except ValueError:
            logger.warning(
                "Barcode %s not found in nomenclature for project %d, " "skipping inbound receipt item (vehicle %s)",
                cost_item.barcode,
                project_id,
                vehicle.order_no,
            )

    return receipt


async def get_supply_chain_overview(db: AsyncSession, project_id: int) -> dict:
    """
    Aggregated supply chain overview:
    - count of vehicles by status
    - total items across all active vehicles
    - factory orders count
    """
    from backend.models.supply_chain import FactoryOrder

    # Count factory orders
    fo_result = await db.execute(
        select(func.count(FactoryOrder.id)).where(
            FactoryOrder.project_id == project_id,
            FactoryOrder.is_deleted == False,  # noqa: E712
        )
    )
    factory_orders_count = fo_result.scalar() or 0

    # Count vehicles by status
    status_result = await db.execute(
        select(CostOrder.status, func.count(CostOrder.id))
        .where(
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712
            CostOrder.status.isnot(None),
        )
        .group_by(CostOrder.status)
    )
    vehicles_by_status = {row[0]: row[1] for row in status_result}

    # Total items in active (non-delivered) vehicles
    active_statuses = [VehicleStatus.FORMING, VehicleStatus.SHIPPED, VehicleStatus.CUSTOMS]
    items_result = await db.execute(
        select(func.sum(CostOrderItem.qty))
        .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
        .where(
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712
            CostOrder.status.in_(active_statuses),
        )
    )
    total_items_in_transit = items_result.scalar() or 0

    return {
        "factory_orders_count": factory_orders_count,
        "vehicles_by_status": vehicles_by_status,
        "total_items_in_transit": total_items_in_transit,
    }
