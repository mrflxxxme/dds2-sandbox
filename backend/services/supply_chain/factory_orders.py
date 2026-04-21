"""
Supply Chain — Factory Orders CRUD and split-to-vehicles logic.
"""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.cost import CostOrder, CostOrderItem, Nomenclature
from backend.models.enums import FactoryOrderStatus, VehicleStatus
from backend.models.supply_chain import FactoryOrder, FactoryOrderHistory, FactoryOrderItem, Supplier
from backend.schemas.supply_chain import (
    FactoryOrderCreate,
    FactoryOrderItemCreate,
    FactoryOrderItemUpdate,
    FactoryOrderUpdate,
    SplitToVehiclesRequest,
)
from backend.services.supply_chain.supplier_catalog import invalidate_supplier_catalog as _invalidate_supplier_catalog
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
    """Compute status based on distribution progress.

    - No items or not fully distributed → FORMING
    - All items fully assigned to vehicles → DISTRIBUTED
    - Was DISTRIBUTED but assigned_qty dropped below qty → FORMING (reverse)
    - CLOSED is final — never modified here
    """
    items = [i for i in (order.items or []) if not i.is_deleted]
    if not items:
        return FactoryOrderStatus.FORMING.value
    total_qty = sum(i.qty for i in items)
    total_assigned = sum(i.assigned_qty for i in items)
    fully_distributed = total_qty > 0 and total_assigned >= total_qty

    if order.status == FactoryOrderStatus.CLOSED.value:
        return order.status
    if fully_distributed:
        return FactoryOrderStatus.DISTRIBUTED.value
    if order.status == FactoryOrderStatus.DISTRIBUTED.value:
        return FactoryOrderStatus.FORMING.value
    return order.status


async def _maybe_update_status(db: AsyncSession, order: FactoryOrder, changed_by: str | None = None) -> None:
    """Auto-transition status based on distribution progress (both directions)."""
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


async def refresh_factory_order_statuses(
    db: AsyncSession,
    project_id: int,
    factory_order_ids: set[int] | list[int],
    changed_by: str | None = None,
) -> None:
    """Re-compute and persist status for given factory orders.

    Used after vehicle-side mutations (add_items_to_vehicle, remove_item_from_vehicle,
    clear_all_vehicle_items, delete_vehicle) to keep FactoryOrder.status in sync
    with assigned_qty. Commits only if any status actually changed.
    """
    ids = sorted({int(i) for i in factory_order_ids if i is not None})
    if not ids:
        return
    result = await db.execute(
        select(FactoryOrder)
        .options(selectinload(FactoryOrder.items))
        .where(
            FactoryOrder.id.in_(ids),
            FactoryOrder.project_id == project_id,
            FactoryOrder.is_deleted == False,  # noqa: E712
        )
        .limit(200)
    )
    changed = False
    for order in result.scalars().all():
        old_status = order.status
        await _maybe_update_status(db, order, changed_by=changed_by)
        if order.status != old_status:
            changed = True
    if changed:
        await db.commit()


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
    """List all factory orders with items and supplier, filtered by project_id + is_deleted."""
    result = await db.execute(
        select(FactoryOrder)
        .options(selectinload(FactoryOrder.items), selectinload(FactoryOrder.supplier))
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
    """Get a single factory order with items and supplier."""
    result = await db.execute(
        select(FactoryOrder)
        .options(selectinload(FactoryOrder.items), selectinload(FactoryOrder.supplier))
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
    # Validate supplier belongs to same project
    if data.supplier_id is not None:
        supplier_result = await db.execute(
            select(Supplier).where(
                Supplier.id == data.supplier_id,
                Supplier.project_id == project_id,
                Supplier.is_deleted == False,  # noqa: E712
            )
        )
        if not supplier_result.scalar_one_or_none():
            raise ValueError(f"Supplier {data.supplier_id} not found in project {project_id}")

    order = FactoryOrder(
        project_id=project_id,
        order_number=data.order_number,
        factory_name=data.factory_name,
        supplier_id=data.supplier_id,
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
    await _invalidate_supplier_catalog(project_id)
    # Re-fetch with eager-loaded items + supplier (avoids lazy-load MissingGreenlet in async)
    created = await get_factory_order(db, project_id, order.id)
    if created is None:
        # Unreachable in practice — row was just committed above — but keeps mypy happy
        msg = f"Failed to reload factory order {order.id} after commit"
        raise RuntimeError(msg)
    return created


async def update_factory_order(
    db: AsyncSession, project_id: int, order_id: int, data: FactoryOrderUpdate
) -> FactoryOrder | None:
    """Update factory order fields (not items)."""
    order = await get_factory_order(db, project_id, order_id)
    if not order:
        return None

    update_data = data.model_dump(exclude_unset=True)

    # Validate supplier belongs to same project when updating supplier_id
    if "supplier_id" in update_data and update_data["supplier_id"] is not None:
        supplier_result = await db.execute(
            select(Supplier).where(
                Supplier.id == update_data["supplier_id"],
                Supplier.project_id == project_id,
                Supplier.is_deleted == False,  # noqa: E712
            )
        )
        if not supplier_result.scalar_one_or_none():
            raise ValueError(f"Supplier {update_data['supplier_id']} not found in project {project_id}")

    for field, value in update_data.items():
        setattr(order, field, value)

    await db.commit()
    await _invalidate_supplier_catalog(project_id)
    # Re-fetch with eager-loaded items + supplier
    return await get_factory_order(db, project_id, order_id)


async def delete_factory_order(db: AsyncSession, project_id: int, order_id: int) -> bool:
    """Soft delete a factory order."""
    order = await get_factory_order(db, project_id, order_id)
    if not order:
        return False

    order.soft_delete()
    await db.commit()
    await _invalidate_supplier_catalog(project_id)
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

    # Build barcode → existing item map for merge
    existing_map: dict[str, FactoryOrderItem] = {}
    for ei in order.items:
        if not ei.is_deleted:
            existing_map[ei.barcode] = ei

    created = []
    merged = 0
    total_qty = 0
    for item_data in items:
        existing = existing_map.get(item_data.barcode)
        if existing:
            # Merge: increase qty, update price/specs if provided
            existing.qty += item_data.qty
            if item_data.price_cny:
                existing.price_cny = item_data.price_cny
            if item_data.box_size:
                existing.box_size = item_data.box_size
            if item_data.pcs_per_box:
                existing.pcs_per_box = item_data.pcs_per_box
            if item_data.weight_kg:
                existing.weight_kg = item_data.weight_kg
            if item_data.subject:
                existing.subject = item_data.subject
            if item_data.article_seller:
                existing.article_seller = item_data.article_seller
            created.append(existing)
            merged += 1
        else:
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
            existing_map[item_data.barcode] = item
        total_qty += item_data.qty

    new_count = len(items) - merged
    details_parts = []
    if new_count > 0:
        details_parts.append(f"добавлено {new_count} новых")
    if merged > 0:
        details_parts.append(f"обновлено {merged} существующих")
    details_parts.append(f"({total_qty} шт.)")

    await _add_history(
        db,
        project_id,
        order.id,
        "items_added",
        details=f"Позиции: {', '.join(details_parts)}",
        changed_by=user_name,
    )

    await db.commit()
    for item in created:
        await db.refresh(item)
    await _invalidate_supplier_catalog(project_id)
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

    # Build nomenclature lookup for area_m2 / weight_kg
    nom_result = await db.execute(select(Nomenclature).where(Nomenclature.project_id == project_id))
    nom_map = {n.barcode: n for n in nom_result.scalars().all()}

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
        nom = nom_map.get(fo_item.barcode)
        cost_item = CostOrderItem(
            project_id=project_id,
            order_no=vehicle.order_no,
            barcode=fo_item.barcode,
            subject=fo_item.subject,
            article_seller=fo_item.article_seller,
            qty=assignment.qty,
            price_cny=fo_item.price_cny,
            weight_kg=fo_item.weight_kg,
            area_m2=nom.area_m2 if nom else None,
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

    # Auto-recalculate costs (duty, vat, delivery) for affected vehicles
    from backend.services.cost.items import recalculate_order_items

    for v_order_no in vehicles_used:
        await recalculate_order_items(db, project_id, v_order_no)

    await _invalidate_supplier_catalog(project_id)
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

    # Validate box_detail: each value > 0, sum == qty
    if "box_detail" in update_data and update_data["box_detail"] is not None:
        bd = update_data["box_detail"]
        if not isinstance(bd, list) or not bd:
            raise ValueError("box_detail должен быть непустым списком")
        if any(not isinstance(v, int) or v <= 0 for v in bd):
            raise ValueError("Каждая коробка должна содержать > 0 штук")
        effective_qty = update_data.get("qty", item.qty)
        if sum(bd) != effective_qty:
            raise ValueError(f"Сумма коробок ({sum(bd)}) ≠ кол-ву ({effective_qty})")

    for field, value in update_data.items():
        setattr(item, field, value)

    await db.commit()
    await db.refresh(item)
    await _invalidate_supplier_catalog(project_id)
    return item


async def bulk_update_item_prices(
    db: AsyncSession,
    project_id: int,
    updates: list[dict],
    user_name: str | None = None,
) -> dict:
    """Bulk-update FactoryOrderItem.price_cny across (possibly multiple) orders.

    updates: list of {"factory_order_item_id": int, "new_price_cny": Decimal}.
    Only items belonging to the project are updated; unknown foi_ids are returned
    in `not_found`. Each price change is logged to FactoryOrderHistory per order.
    """
    if not updates:
        return {"updated": 0, "not_found": []}

    foi_ids = [u["factory_order_item_id"] for u in updates]
    result = await db.execute(
        select(FactoryOrderItem).where(
            FactoryOrderItem.id.in_(foi_ids),
            FactoryOrderItem.project_id == project_id,
            FactoryOrderItem.is_deleted == False,  # noqa: E712
        )
    )
    foi_map = {fi.id: fi for fi in result.scalars().all()}

    not_found: list[int] = []
    # Group price change details per factory_order for concise history entries
    per_order_changes: dict[int, list[str]] = {}
    updated_count = 0

    for upd in updates:
        foi_id = upd["factory_order_item_id"]
        new_price = upd["new_price_cny"]
        fi = foi_map.get(foi_id)
        if not fi:
            not_found.append(foi_id)
            continue
        if fi.price_cny == new_price:
            continue  # no-op
        old_price = fi.price_cny
        fi.price_cny = new_price
        per_order_changes.setdefault(fi.factory_order_id, []).append(f"{fi.barcode}: {old_price} → {new_price} ¥")
        updated_count += 1

    if updated_count == 0:
        return {"updated": 0, "not_found": not_found}

    for fo_id, changes in per_order_changes.items():
        await _add_history(
            db,
            project_id,
            fo_id,
            "price_updated",
            details="Цены обновлены: " + "; ".join(changes),
            changed_by=user_name,
        )

    await db.commit()
    await _invalidate_supplier_catalog(project_id)
    return {"updated": updated_count, "not_found": not_found}


async def bulk_update_items_specs(
    db: AsyncSession,
    project_id: int,
    order_id: int,
    updates: list[dict],
) -> dict:
    """Bulk update box_size/pcs_per_box/weight_kg for items matched by barcode.

    Returns {ok, updated, not_found}.
    """
    order = await get_factory_order(db, project_id, order_id)
    if not order:
        raise ValueError("Factory order not found")

    # Build barcode → item map (active items only)
    barcode_map: dict[str, list[FactoryOrderItem]] = {}
    for item in order.items:
        if item.is_deleted:
            continue
        barcode_map.setdefault(item.barcode, []).append(item)

    updated = 0
    not_found: list[str] = []

    for upd in updates:
        barcode = upd.get("barcode", "").strip()
        if not barcode:
            continue

        items = barcode_map.get(barcode)
        if not items:
            not_found.append(barcode)
            continue

        for item in items:
            changed = False
            if upd.get("box_size") is not None:
                item.box_size = upd["box_size"]
                changed = True
            if upd.get("pcs_per_box") is not None:
                item.pcs_per_box = upd["pcs_per_box"]
                changed = True
            if upd.get("weight_kg") is not None:
                item.weight_kg = upd["weight_kg"]
                changed = True
            if changed:
                updated += 1

    if updated > 0:
        await db.commit()
        await _invalidate_supplier_catalog(project_id)

    return {"ok": True, "updated": updated, "not_found": not_found}


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

    if item.mix_group_id is not None:
        raise ValueError("Нельзя удалить: позиция входит в микс-группу. Сначала разбейте микс.")

    if item.assigned_qty > 0:
        raise ValueError(f"Нельзя удалить: позиция распределена по машинам " f"({item.assigned_qty} шт.)")

    item.soft_delete()
    await db.commit()
    await _invalidate_supplier_catalog(project_id)
    return True


# ─── Mix Groups ──────────────────────────────────────────────────────────


async def set_mix_group(
    db: AsyncSession,
    project_id: int,
    order_id: int,
    items: list,
    box_size: str,
) -> tuple[str, list[int]]:
    """Group items into a mix box (shared packaging).

    Returns (mix_group_id, matched_item_ids).
    Validates all items belong to the same order and project.
    `items` is a list of objects with `.id` and `.pcs_per_box` attributes.
    """
    if len(items) < 2:
        raise ValueError("Микс-группа должна содержать минимум 2 позиции")

    if not box_size:
        raise ValueError("Размер коробки не может быть пустым")

    order = await get_factory_order(db, project_id, order_id)
    if not order:
        raise ValueError("Factory order not found")

    items_by_id = {i.id: i for i in order.items}
    matched: list[tuple[FactoryOrderItem, int]] = []
    for input_item in items:
        if input_item.pcs_per_box <= 0:
            raise ValueError(f"pcs_per_box для позиции {input_item.id} должен быть больше 0")
        item = items_by_id.get(input_item.id)
        if not item:
            raise ValueError(f"Позиция {input_item.id} не найдена в заказе {order_id}")
        if item.assigned_qty != 0:
            raise ValueError(f"Позиция {input_item.id} уже распределена по машинам")
        if item.mix_group_id is not None:
            raise ValueError(f"Позиция {input_item.id} уже входит в микс-группу")
        matched.append((item, input_item.pcs_per_box))

    mix_id = str(uuid.uuid4())
    matched_ids = []
    for item, pcs_per_box in matched:
        item.mix_group_id = mix_id
        item.mix_box_size = box_size
        item.mix_pcs_per_box = pcs_per_box
        matched_ids.append(item.id)

    await db.commit()
    return mix_id, matched_ids


async def remove_mix_group(
    db: AsyncSession,
    project_id: int,
    order_id: int,
    mix_group_id: str,
) -> int:
    """Remove a mix group — set mix_group_id=None on all items in the group.

    Returns count of affected items.
    """
    order = await get_factory_order(db, project_id, order_id)
    if not order:
        raise ValueError("Factory order not found")

    count = 0
    for item in order.items:
        if item.mix_group_id == mix_group_id:
            item.mix_group_id = None
            item.mix_box_size = None
            item.mix_pcs_per_box = None
            count += 1

    if count == 0:
        raise ValueError(f"Микс-группа {mix_group_id} не найдена в заказе {order_id}")

    await db.commit()
    return count


# Statuses that count as "shipped or above" for auto-close logic
_SHIPPED_OR_ABOVE = {
    VehicleStatus.SHIPPED,
    VehicleStatus.CUSTOMS,
    VehicleStatus.DISPATCHED,
    VehicleStatus.DELIVERED,
}


async def check_and_close_order(
    db: AsyncSession,
    project_id: int,
    factory_order_id: int,
) -> bool:
    """
    Auto-close a factory order when all its vehicles are >= SHIPPED.

    Checks all CostOrderItems linked via factory_order_item_id to items of
    this factory order, collects unique CostOrder.order_no values, and if
    every one of them is in SHIPPED_OR_ABOVE status AND the factory order
    is in DISTRIBUTED status → transitions it to CLOSED.

    Returns True if order was closed, False otherwise.
    """
    order = await get_factory_order(db, project_id, factory_order_id)
    if not order:
        return False

    # Only auto-close DISTRIBUTED orders
    if order.status != FactoryOrderStatus.DISTRIBUTED.value:
        return False

    # Collect factory_order_item IDs for this order
    fo_item_ids = [item.id for item in order.items if not item.is_deleted]
    if not fo_item_ids:
        return False

    # Find all unique CostOrder.order_no linked to these items
    cost_items_result = await db.execute(
        select(CostOrderItem.order_no)
        .where(
            CostOrderItem.factory_order_item_id.in_(fo_item_ids),
            CostOrderItem.project_id == project_id,
            CostOrderItem.is_deleted == False,  # noqa: E712
        )
        .distinct()
        .limit(200)
    )
    vehicle_order_nos = [row[0] for row in cost_items_result.all()]

    if not vehicle_order_nos:
        return False

    # Fetch statuses of all those vehicles
    vehicles_result = await db.execute(
        select(CostOrder.order_no, CostOrder.status).where(
            CostOrder.order_no.in_(vehicle_order_nos),
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712
        )
    )
    vehicle_rows = vehicles_result.all()

    if not vehicle_rows:
        return False

    # All vehicles must be >= SHIPPED
    all_shipped = all(row[1] in _SHIPPED_OR_ABOVE for row in vehicle_rows)
    if not all_shipped:
        return False

    # Transition to CLOSED
    old_status = order.status
    order.status = FactoryOrderStatus.CLOSED.value
    await _add_history(
        db,
        project_id,
        order.id,
        "status_change",
        old_status=old_status,
        new_status=FactoryOrderStatus.CLOSED.value,
        details="Автозакрытие: все машины >= SHIPPED",
    )
    await db.commit()
    logger.info("Factory order %d auto-closed (all vehicles shipped)", factory_order_id)
    return True
