"""
Supply Chain — Factory Orders CRUD and split-to-vehicles logic.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.cost import CostOrder, CostOrderItem, Nomenclature
from backend.models.supply_chain import FactoryOrder, FactoryOrderItem
from backend.schemas.supply_chain import (
    FactoryOrderCreate,
    FactoryOrderItemCreate,
    FactoryOrderUpdate,
    SplitToVehiclesRequest,
)

logger = logging.getLogger(__name__)


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


async def create_factory_order(db: AsyncSession, project_id: int, data: FactoryOrderCreate) -> FactoryOrder:
    """Create a factory order with optional items."""
    order = FactoryOrder(
        project_id=project_id,
        order_number=data.order_number,
        factory_name=data.factory_name,
        order_date=data.order_date,
        expected_ready_date=data.expected_ready_date,
        total_cny=data.total_cny,
        note=data.note,
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
) -> list[FactoryOrderItem]:
    """Add items to an existing factory order."""
    order = await get_factory_order(db, project_id, factory_order_id)
    if not order:
        raise ValueError("Factory order not found")

    created = []
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

    await db.commit()
    for item in created:
        await db.refresh(item)
    return created


async def split_to_vehicles(
    db: AsyncSession,
    project_id: int,
    factory_order_id: int,
    data: SplitToVehiclesRequest,
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

    await db.commit()
    return {"ok": True, "created_cost_items": created_cost_items}
