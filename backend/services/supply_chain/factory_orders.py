"""
Supply Chain — Factory Orders CRUD and split-to-vehicles logic.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.cost import CostOrder, CostOrderItem, Nomenclature
from backend.models.enums import FactoryOrderStatus, VehicleStatus
from backend.models.supply_chain import FactoryOrder, FactoryOrderHistory, FactoryOrderItem
from backend.schemas.supply_chain import (
    FactoryOrderCreate,
    FactoryOrderItemCreate,
    FactoryOrderItemUpdate,
    FactoryOrderUpdate,
    SplitToVehiclesRequest,
)
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


# ─── History helpers ──────────────────────────────────────────────────────────


async def _add_history(
    db: AsyncSession,
    project_id: int,
    factory_order_id: int,
    event_type: str,
    *,
    old_status: str | None = None,
    new_status: str | None = None,
    details: str | None = None,
    changed_by: str | None = None,
) -> FactoryOrderHistory:
    entry = FactoryOrderHistory(
        project_id=project_id,
        factory_order_id=factory_order_id,
        event_type=event_type,
        old_status=old_status,
        new_status=new_status,
        details=details,
        changed_at=utcnow(),
        changed_by=changed_by,
    )
    db.add(entry)
    return entry


def _compute_status(order: FactoryOrder) -> str:
    """Compute status based on distribution progress."""
    items = order.items or []
    if not items:
        return FactoryOrderStatus.FORMING.value
    total_qty = sum(i.qty for i in items)
    total_assigned = sum(i.assigned_qty for i in items)
    if total_qty > 0 and total_assigned >= total_qty:
        return FactoryOrderStatus.DISTRIBUTED.value
    return order.status


async def _maybe_update_status(db: AsyncSession, order: FactoryOrder, changed_by: str | None = None) -> None:
    """Auto-transition status to DISTRIBUTED if all items are assigned."""
    new_status = _compute_status(order)
    if new_status != order.status:
        old_status = order.status
        order.status = new_status
        await _add_history(
            db,
            order.project_id,
            order.id,
            "status_change",
            old_status=old_status,
            new_status=new_status,
            details=f"Автосмена статуса: {old_status} → {new_status}",
            changed_by=changed_by,
        )


async def get_factory_order_history(
    db: AsyncSession,
    project_id: int,
    order_id: int,
) -> list[FactoryOrderHistory]:
    """Get history entries for a factory order."""
    result = await db.execute(
        select(FactoryOrderHistory)
        .where(
            FactoryOrderHistory.project_id == project_id,
            FactoryOrderHistory.factory_order_id == order_id,
        )
        .order_by(FactoryOrderHistory.changed_at.desc())
        .limit(200)
    )
    return list(result.scalars().all())


async def update_factory_order_status(
    db: AsyncSession,
    project_id: int,
    order_id: int,
    new_status: str,
    user_name: str | None = None,
) -> FactoryOrder | None:
    """Manually change factory order status (no history logged for manual changes)."""
    valid = {s.value for s in FactoryOrderStatus}
    if new_status not in valid:
        raise ValueError(f"Недопустимый статус: {new_status}. Допустимые: {', '.join(valid)}")

    order = await get_factory_order(db, project_id, order_id)
    if not order:
        return None

    if order.status == new_status:
        return order

    order.status = new_status
    await db.commit()
    await db.refresh(order, ["items"])
    return order


async def _enrich_items_from_nomenclature(
    db: AsyncSession,
    project_id: int,
    orders: list[FactoryOrder],
) -> None:
    """Fill missing subject/article_seller on items from Nomenclature."""
    nom_result = await db.execute(select(Nomenclature).where(Nomenclature.project_id == project_id))
    nom_map = {n.barcode: n for n in nom_result.scalars().all()}
    for order in orders:
        for item in order.items:
            nom = nom_map.get(item.barcode)
            if nom:
                if not item.subject:
                    item.subject = nom.subject
                if not item.article_seller:
                    item.article_seller = nom.article_seller


async def get_factory_orders(db: AsyncSession, project_id: int) -> list[FactoryOrder]:
    """List all factory orders with items, filtered by project_id + is_deleted."""
    result = await db.execute(
        select(FactoryOrder)
        .options(selectinload(FactoryOrder.items))
        .where(
            FactoryOrder.project_id == project_id,
            FactoryOrder.is_deleted == False,  # noqa: E712
        )
        .order_by(FactoryOrder.id.desc())
        .limit(500)
    )
    orders = list(result.scalars().all())
    await _enrich_items_from_nomenclature(db, project_id, orders)
    return orders


async def get_factory_order(db: AsyncSession, project_id: int, order_id: int) -> FactoryOrder | None:
    """Get a single factory order with items."""
    result = await db.execute(
        select(FactoryOrder)
        .options(selectinload(FactoryOrder.items))
        .where(
            FactoryOrder.id == order_id,
            FactoryOrder.project_id == project_id,
            FactoryOrder.is_deleted == False,  # noqa: E712
        )
    )
    order = result.scalar_one_or_none()
    if order:
        await _enrich_items_from_nomenclature(db, project_id, [order])
    return order


async def create_factory_order(
    db: AsyncSession,
    project_id: int,
    data: FactoryOrderCreate,
    user_name: str | None = None,
) -> FactoryOrder:
    """Create a factory order with optional items."""
    order = FactoryOrder(
        project_id=project_id,
        order_number=data.order_number,
        factory_name=data.factory_name,
        order_date=data.order_date,
        expected_ready_date=data.expected_ready_date,
        total_cny=data.total_cny,
        note=data.note,
        status=FactoryOrderStatus.FORMING.value,
    )
    db.add(order)
    await db.flush()  # get order.id

    if data.items:
        for item_data in data.items:
            item = FactoryOrderItem(
                project_id=project_id,
                factory_order_id=order.id,
                barcode=item_data.barcode,
                subject=item_data.subject,
                article_seller=item_data.article_seller,
                qty=item_data.qty,
                assigned_qty=0,
                price_cny=item_data.price_cny,
                box_size=item_data.box_size,
                pcs_per_box=item_data.pcs_per_box,
                weight_kg=item_data.weight_kg,
                note=item_data.note,
            )
            db.add(item)

    items_count = len(data.items) if data.items else 0
    await _add_history(
        db,
        project_id,
        order.id,
        "created",
        new_status=FactoryOrderStatus.FORMING.value,
        details=f"Заказ создан ({items_count} позиций)" if items_count else "Заказ создан",
        changed_by=user_name,
    )

    await db.commit()
    await db.refresh(order, ["items"])
    return order


async def update_factory_order(
    db: AsyncSession, project_id: int, order_id: int, data: FactoryOrderUpdate
) -> FactoryOrder | None:
    """Update factory order fields (not items)."""
    order = await get_factory_order(db, project_id, order_id)
    if not order:
        return None

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(order, field, value)

    await db.commit()
    await db.refresh(order, ["items"])
    return order


async def delete_factory_order(db: AsyncSession, project_id: int, order_id: int) -> bool:
    """Soft delete a factory order."""
    order = await get_factory_order(db, project_id, order_id)
    if not order:
        return False

    order.soft_delete()
    await db.commit()
    return True


async def add_items(
    db: AsyncSession,
    project_id: int,
    factory_order_id: int,
    items: list[FactoryOrderItemCreate],
    user_name: str | None = None,
) -> list[FactoryOrderItem]:
    """Add items to an existing factory order."""
    order = await get_factory_order(db, project_id, factory_order_id)
    if not order:
        raise ValueError("Factory order not found")

    created = []
    total_qty = 0
    for item_data in items:
        item = FactoryOrderItem(
            project_id=project_id,
            factory_order_id=order.id,
            barcode=item_data.barcode,
            subject=item_data.subject,
            article_seller=item_data.article_seller,
            qty=item_data.qty,
            assigned_qty=0,
            price_cny=item_data.price_cny,
            box_size=item_data.box_size,
            pcs_per_box=item_data.pcs_per_box,
            weight_kg=item_data.weight_kg,
            note=item_data.note,
        )
        db.add(item)
        created.append(item)
        total_qty += item_data.qty

    await _add_history(
        db,
        project_id,
        order.id,
        "items_added",
        details=f"Добавлено {len(items)} позиций ({total_qty} шт.)",
        changed_by=user_name,
    )

    await db.commit()
    for item in created:
        await db.refresh(item)
    return created


async def split_to_vehicles(
    db: AsyncSession,
    project_id: int,
    factory_order_id: int,
    data: SplitToVehiclesRequest,
    user_name: str | None = None,
) -> dict:
    """
    Assign factory order items to vehicles (CostOrder).

    For each assignment:
    1. Find or create CostOrder by order_no
    2. Create CostOrderItem linked to factory_order_item_id
    3. Update assigned_qty on FactoryOrderItem
    4. Validate: assigned_qty + new qty <= total qty
    """
    order = await get_factory_order(db, project_id, factory_order_id)
    if not order:
        raise ValueError("Factory order not found")

    # Build lookup of items by id
    items_by_id: dict[int, FactoryOrderItem] = {item.id: item for item in order.items}

    created_cost_items = 0
    vehicles_used: set[str] = set()
    total_assigned_qty = 0
    for assignment in data.assignments:
        # Validate item exists and belongs to this order
        fo_item = items_by_id.get(assignment.factory_order_item_id)
        if not fo_item:
            raise ValueError(
                f"FactoryOrderItem {assignment.factory_order_item_id} not found in order {factory_order_id}"
            )

        # Validate qty
        remaining = fo_item.qty - fo_item.assigned_qty
        if assignment.qty > remaining:
            raise ValueError(
                f"Cannot assign {assignment.qty} of item {fo_item.id} "
                f"(barcode={fo_item.barcode}): only {remaining} remaining "
                f"(total={fo_item.qty}, already assigned={fo_item.assigned_qty})"
            )

        # Find or create CostOrder (vehicle)
        result = await db.execute(
            select(CostOrder).where(
                CostOrder.order_no == assignment.vehicle_order_no,
                CostOrder.project_id == project_id,
                CostOrder.is_deleted == False,  # noqa: E712
            )
        )
        vehicle = result.scalar_one_or_none()

        if not vehicle:
            vehicle = CostOrder(
                project_id=project_id,
                order_no=assignment.vehicle_order_no,
                status=VehicleStatus.FORMING,
            )
            db.add(vehicle)
            await db.flush()

        # Create CostOrderItem linked to factory_order_item
        cost_item = CostOrderItem(
            project_id=project_id,
            order_no=vehicle.order_no,
            barcode=fo_item.barcode,
            subject=fo_item.subject,
            article_seller=fo_item.article_seller,
            qty=assignment.qty,
            price_cny=fo_item.price_cny,
            factory_order_item_id=fo_item.id,
        )
        db.add(cost_item)

        # Update assigned_qty
        fo_item.assigned_qty += assignment.qty
        created_cost_items += 1
        vehicles_used.add(assignment.vehicle_order_no)
        total_assigned_qty += assignment.qty

    # Log distribution event
    vehicles_str = ", ".join(sorted(vehicles_used))
    await _add_history(
        db,
        project_id,
        order.id,
        "distributed",
        details=f"Распределено {total_assigned_qty} шт. в машины: {vehicles_str}",
        changed_by=user_name,
    )

    # Auto-transition status if fully distributed
    await _maybe_update_status(db, order, changed_by=user_name)

    await db.commit()
    return {"ok": True, "created_cost_items": created_cost_items}


async def update_item(
    db: AsyncSession,
    project_id: int,
    order_id: int,
    item_id: int,
    data: FactoryOrderItemUpdate,
) -> FactoryOrderItem | None:
    """Update a single factory order item's fields.

    Validates:
    - Order belongs to project (P19 cross-tenant safety)
    - Item belongs to order
    - If qty changes: new qty >= assigned_qty
    """
    order = await get_factory_order(db, project_id, order_id)
    if not order:
        return None

    # Find item in order
    item: FactoryOrderItem | None = None
    for i in order.items:
        if i.id == item_id:
            item = i
            break
    if not item:
        return None

    update_data = data.model_dump(exclude_unset=True)

    # Validate qty change
    if "qty" in update_data and update_data["qty"] is not None:
        new_qty = update_data["qty"]
        if new_qty < item.assigned_qty:
            raise ValueError(f"Нельзя уменьшить кол-во ниже {item.assigned_qty} " f"(уже распределено по машинам)")

    for field, value in update_data.items():
        setattr(item, field, value)

    await db.commit()
    await db.refresh(item)
    return item


async def delete_item(
    db: AsyncSession,
    project_id: int,
    order_id: int,
    item_id: int,
) -> bool:
    """Delete a factory order item (physical delete).

    Validates:
    - Order belongs to project (P19 cross-tenant safety)
    - Item belongs to order
    - assigned_qty == 0 (not distributed to vehicles)
    """
    order = await get_factory_order(db, project_id, order_id)
    if not order:
        raise ValueError("Factory order not found")

    item: FactoryOrderItem | None = None
    for i in order.items:
        if i.id == item_id:
            item = i
            break
    if not item:
        raise ValueError("Item not found in this order")

    if item.assigned_qty > 0:
        raise ValueError(f"Нельзя удалить: позиция распределена по машинам " f"({item.assigned_qty} шт.)")

    await db.delete(item)
    await db.commit()
    return True
