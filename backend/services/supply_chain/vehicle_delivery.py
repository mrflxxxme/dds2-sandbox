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
from backend.services.supply_chain.factory_orders import refresh_factory_order_statuses
from backend.services.supply_chain.supplier_catalog import invalidate_supplier_catalog as _invalidate_supplier_catalog
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
    VehicleStatus.CUSTOMS: [VehicleStatus.DISPATCHED],
    # DISPATCHED → DELIVERED happens automatically when InboundReceipt is accepted
}

# Statuses that qualify as "shipped or above" for factory order auto-close
_SHIPPED_OR_ABOVE = {
    VehicleStatus.SHIPPED,
    VehicleStatus.CUSTOMS,
    VehicleStatus.DISPATCHED,
    VehicleStatus.DELIVERED,
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
            CostOrder.is_deleted == False,
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
            CostOrder.is_deleted == False,
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
    """Create a new vehicle (CostOrder) for forming.

    For Russian vehicles (country=RUSSIA), we force a simple flat-RUB cost model:
    no FX rates, no CNY/USD delivery, no invoice/DT/payment_ref, container_type=gazelle.
    """
    from backend.services.warehouse_crud import get_or_create_transit_warehouse

    is_russia = data.country == "RUSSIA"

    if is_russia:
        container_type = "gazelle"
        rate_cny = Decimal("1")
        rate_usd = Decimal("1")
        rate_eur = Decimal("1")
        delivery_cost_cny = Decimal("0")
        delivery_cost_usd = Decimal("0")
        delivery_cost_rub = Decimal(str(data.delivery_cost_rub or 0))
        invoice_no = None
        payment_ref = None
    else:
        container_type = data.container_type
        rate_cny = data.rate_cny
        rate_usd = data.rate_usd
        rate_eur = data.rate_eur
        delivery_cost_cny = data.delivery_cost_cny
        delivery_cost_usd = data.delivery_cost_usd
        delivery_cost_rub = Decimal(str(data.delivery_cost_rub or 0))
        invoice_no = data.invoice_no
        payment_ref = data.payment_ref

    transport_type = CONTAINER_TRANSPORT_MAP.get(container_type, "AUTO")

    target_wh_id = data.target_warehouse_id
    if not target_wh_id:
        transit = await get_or_create_transit_warehouse(db, project_id)
        target_wh_id = transit.id

    vehicle = CostOrder(
        project_id=project_id,
        order_no=data.order_no,
        container_type=container_type,
        transport_type=transport_type,
        country=data.country,
        delivery_cost_cny=delivery_cost_cny,
        delivery_cost_usd=delivery_cost_usd,
        delivery_cost_rub=delivery_cost_rub,
        rate_cny=rate_cny,
        rate_usd=rate_usd,
        rate_eur=rate_eur,
        ship_date=data.ship_date,
        invoice_no=invoice_no,
        payment_ref=payment_ref,
        target_warehouse_id=target_wh_id,
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
            CostOrder.is_deleted == False,
        )
    )
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        return None

    always_editable = {
        "invoice_no",
        "dt_number",
        "note",
        "actual_ship_date",
        "delivery_cost_cny",
        "delivery_cost_usd",
        "delivery_cost_rub",
        "ship_date",
        "estimated_arrival_date",
        "target_warehouse_id",
        "rate_cny",
        "rate_usd",
        "rate_eur",
        "payment_ref",
        "country",
    }
    update_data = data.model_dump(exclude_unset=True)

    if vehicle.status != VehicleStatus.FORMING:
        restricted = set(update_data.keys()) - always_editable
        if restricted:
            raise ValueError("Редактирование возможно только в статусе ФОРМИРОВАНИЕ")

    # If country is being changed to RUSSIA, force RU defaults on the payload.
    # If changed to CHINA, we do NOT touch any numeric field — user enters them.
    new_country = update_data.get("country")
    if new_country == "RUSSIA":
        update_data["container_type"] = "gazelle"
        update_data["rate_cny"] = Decimal("1")
        update_data["rate_usd"] = Decimal("1")
        update_data["rate_eur"] = Decimal("1")
        update_data["delivery_cost_cny"] = Decimal("0")
        update_data["delivery_cost_usd"] = Decimal("0")
        update_data["invoice_no"] = None
        update_data["dt_number"] = None
        update_data["payment_ref"] = None

    cost_fields = {
        "rate_cny",
        "rate_usd",
        "rate_eur",
        "delivery_cost_cny",
        "delivery_cost_usd",
        "delivery_cost_rub",
        "country",
    }
    needs_recalc = bool(cost_fields & set(update_data.keys()))
    has_items = bool(vehicle.items)

    for field, value in update_data.items():
        if field == "container_type" and value:
            vehicle.container_type = value
            vehicle.transport_type = CONTAINER_TRANSPORT_MAP.get(value, "AUTO")
        else:
            setattr(vehicle, field, value)

    await db.commit()

    if needs_recalc and has_items:
        from backend.services.cost.items import recalculate_order_items

        await recalculate_order_items(db, project_id, order_no)

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
            CostOrder.is_deleted == False,
        )
    )
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise ValueError(f"Vehicle {order_no} not found")

    added = 0
    affected_fo_ids: set[int] = set()
    for item_req in data.items:
        # Get factory order item
        fo_item_result = await db.execute(
            select(FactoryOrderItem)
            .join(FactoryOrder)
            .where(
                FactoryOrderItem.id == item_req.factory_order_item_id,
                FactoryOrderItem.project_id == project_id,
                FactoryOrderItem.is_deleted == False,
                FactoryOrder.project_id == project_id,
                FactoryOrder.is_deleted == False,
            )
        )
        fo_item = fo_item_result.scalar_one_or_none()
        if not fo_item:
            raise ValueError(f"FactoryOrderItem {item_req.factory_order_item_id} not found")

        remaining = fo_item.qty - fo_item.assigned_qty
        if item_req.qty > remaining:
            raise ValueError(f"Cannot assign {item_req.qty} of {fo_item.barcode}: " f"only {remaining} remaining")

        affected_fo_ids.add(fo_item.factory_order_id)

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

        # Calculate volume_m3 per unit from box dimensions
        from backend.services.cost.helpers import parse_box_volume_m3

        bs = fo_item.mix_box_size if fo_item.mix_group_id and fo_item.mix_box_size else fo_item.box_size
        ppb = fo_item.mix_pcs_per_box if fo_item.mix_group_id and fo_item.mix_pcs_per_box else fo_item.pcs_per_box
        vol_m3 = parse_box_volume_m3(bs, ppb)

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
            volume_m3=vol_m3 if vol_m3 > 0 else None,
            factory_order_item_id=fo_item.id,
        )
        db.add(cost_item)
        fo_item.assigned_qty += item_req.qty
        added += 1

    await db.commit()

    # Auto-recalculate costs (duty, vat, delivery) for the vehicle
    from backend.services.cost.items import recalculate_order_items

    await recalculate_order_items(db, project_id, order_no)
    await refresh_factory_order_statuses(db, project_id, affected_fo_ids)
    await _invalidate_supplier_catalog(project_id)

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
            CostOrderItem.is_deleted == False,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,
        )
    )
    cost_item = result.scalar_one_or_none()
    if not cost_item:
        raise ValueError(f"Item {item_id} not found in vehicle {order_no}")

    # Check if the linked FactoryOrderItem belongs to a mix group
    mix_group_id = None
    if cost_item.factory_order_item_id:
        fo_item_result = await db.execute(
            select(FactoryOrderItem).where(
                FactoryOrderItem.id == cost_item.factory_order_item_id,
                FactoryOrderItem.project_id == project_id,
                FactoryOrderItem.is_deleted == False,
            )
        )
        fo_item = fo_item_result.scalar_one_or_none()
        if fo_item:
            mix_group_id = fo_item.mix_group_id

    if mix_group_id is not None:
        # Remove all mix group members from this vehicle
        mix_fo_items_result = await db.execute(
            select(FactoryOrderItem.id).where(
                FactoryOrderItem.mix_group_id == mix_group_id,
                FactoryOrderItem.project_id == project_id,
                FactoryOrderItem.is_deleted == False,
            )
        )
        mix_fo_item_ids = {row[0] for row in mix_fo_items_result}

        mix_cost_items_result = await db.execute(
            select(CostOrderItem)
            .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
            .where(
                CostOrderItem.order_no == order_no,
                CostOrderItem.factory_order_item_id.in_(mix_fo_item_ids),
                CostOrderItem.is_deleted == False,
                CostOrder.project_id == project_id,
                CostOrder.is_deleted == False,
            )
        )
        mix_cost_items = mix_cost_items_result.scalars().all()

        # Restore assigned_qty for each and soft-delete
        fo_restore_result = await db.execute(
            select(FactoryOrderItem).where(
                FactoryOrderItem.id.in_(mix_fo_item_ids),
                FactoryOrderItem.project_id == project_id,
                FactoryOrderItem.is_deleted == False,
            )
        )
        fo_items_map = {fi.id: fi for fi in fo_restore_result.scalars().all()}

        removed = 0
        affected_fo_ids: set[int] = set()
        for mix_ci in mix_cost_items:
            if mix_ci.factory_order_item_id and mix_ci.factory_order_item_id in fo_items_map:
                fi = fo_items_map[mix_ci.factory_order_item_id]
                fi.assigned_qty = max(0, fi.assigned_qty - mix_ci.qty)
                affected_fo_ids.add(fi.factory_order_id)
            mix_ci.soft_delete()
            removed += 1

        await db.commit()

        from backend.services.cost.items import recalculate_order_items

        await recalculate_order_items(db, project_id, order_no)
        await refresh_factory_order_statuses(db, project_id, affected_fo_ids)
        await _invalidate_supplier_catalog(project_id)

        return {"ok": True, "removed": removed}

    # Single-item removal (not in a mix group)
    affected_fo_ids_single: set[int] = set()
    if cost_item.factory_order_item_id:
        fo_item_result2 = await db.execute(
            select(FactoryOrderItem).where(
                FactoryOrderItem.id == cost_item.factory_order_item_id,
                FactoryOrderItem.project_id == project_id,
                FactoryOrderItem.is_deleted == False,
            )
        )
        fo_item2 = fo_item_result2.scalar_one_or_none()
        if fo_item2:
            fo_item2.assigned_qty = max(0, fo_item2.assigned_qty - cost_item.qty)
            affected_fo_ids_single.add(fo_item2.factory_order_id)

    cost_item.soft_delete()
    await db.commit()

    # Recalculate remaining items (delivery allocation changes)
    from backend.services.cost.items import recalculate_order_items

    await recalculate_order_items(db, project_id, order_no)
    await refresh_factory_order_statuses(db, project_id, affected_fo_ids_single)
    await _invalidate_supplier_catalog(project_id)

    return {"ok": True}


async def clear_all_vehicle_items(
    db: AsyncSession,
    project_id: int,
    order_no: str,
) -> dict:
    """Remove ALL items from a FORMING vehicle and restore assigned_qty."""
    result = await db.execute(
        select(CostOrder)
        .options(selectinload(CostOrder.items))
        .where(
            CostOrder.order_no == order_no,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,
        )
    )
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise ValueError(f"Vehicle {order_no} not found")

    if vehicle.status != VehicleStatus.FORMING:
        raise ValueError("Clear items allowed only for FORMING vehicles")

    active_items = [item for item in vehicle.items if not item.is_deleted]
    if not active_items:
        return {"ok": True, "removed": 0}

    # Restore assigned_qty on linked factory order items
    fo_item_ids = [item.factory_order_item_id for item in active_items if item.factory_order_item_id]
    fo_items_map: dict[int, FactoryOrderItem] = {}
    if fo_item_ids:
        fo_result = await db.execute(
            select(FactoryOrderItem).where(
                FactoryOrderItem.id.in_(fo_item_ids),
                FactoryOrderItem.project_id == project_id,
                FactoryOrderItem.is_deleted == False,
            )
        )
        fo_items_map = {fi.id: fi for fi in fo_result.scalars().all()}

    removed = 0
    affected_fo_ids: set[int] = set()
    for cost_item in active_items:
        if cost_item.factory_order_item_id and cost_item.factory_order_item_id in fo_items_map:
            fo_item = fo_items_map[cost_item.factory_order_item_id]
            fo_item.assigned_qty = max(0, fo_item.assigned_qty - cost_item.qty)
            affected_fo_ids.add(fo_item.factory_order_id)
        cost_item.soft_delete()
        removed += 1

    await db.commit()

    from backend.services.cost.items import recalculate_order_items

    await recalculate_order_items(db, project_id, order_no)
    await refresh_factory_order_statuses(db, project_id, affected_fo_ids)
    await _invalidate_supplier_catalog(project_id)

    return {"ok": True, "removed": removed}


async def delete_vehicle(
    db: AsyncSession,
    project_id: int,
    order_no: str,
) -> dict:
    """Soft-delete a FORMING vehicle, restore assigned_qty on factory items."""
    result = await db.execute(
        select(CostOrder)
        .options(selectinload(CostOrder.items))
        .where(
            CostOrder.order_no == order_no,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,
        )
    )
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise ValueError(f"Vehicle {order_no} not found")

    if vehicle.status != VehicleStatus.FORMING:
        raise ValueError("Удалить можно только машину в статусе FORMING")

    # Restore assigned_qty on linked factory order items
    affected_fo_ids: set[int] = set()
    fo_item_ids = [item.factory_order_item_id for item in vehicle.items if item.factory_order_item_id]
    if fo_item_ids:
        fo_result = await db.execute(
            select(FactoryOrderItem).where(
                FactoryOrderItem.id.in_(fo_item_ids),
                FactoryOrderItem.project_id == project_id,
                FactoryOrderItem.is_deleted == False,
            )
        )
        fo_items_map = {fi.id: fi for fi in fo_result.scalars().all()}
        for cost_item in vehicle.items:
            if cost_item.factory_order_item_id and cost_item.factory_order_item_id in fo_items_map:
                fo_item = fo_items_map[cost_item.factory_order_item_id]
                fo_item.assigned_qty = max(0, fo_item.assigned_qty - cost_item.qty)
                affected_fo_ids.add(fo_item.factory_order_id)

    # Soft-delete CostOrderItems when removing vehicle
    for cost_item in list(vehicle.items):
        cost_item.soft_delete()  # no-soft-delete-check

    # Soft-delete the vehicle
    vehicle.soft_delete()
    await db.commit()
    await refresh_factory_order_statuses(db, project_id, affected_fo_ids)
    await _invalidate_supplier_catalog(project_id)
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
            FactoryOrder.is_deleted == False,
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
                        "box_detail": item.box_detail,
                        "mix_group_id": item.mix_group_id,
                        "mix_box_size": item.mix_box_size,
                        "mix_pcs_per_box": item.mix_pcs_per_box,
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
                FactoryOrderItem.box_detail,
                FactoryOrderItem.factory_order_id,
                FactoryOrder.order_number,
                FactoryOrderItem.mix_group_id,
                FactoryOrderItem.mix_box_size,
                FactoryOrderItem.mix_pcs_per_box,
            )
            .join(FactoryOrder, FactoryOrderItem.factory_order_id == FactoryOrder.id)
            .where(
                FactoryOrderItem.id.in_(fo_item_ids),
                FactoryOrderItem.is_deleted == False,
                FactoryOrder.project_id == project_id,
            )
        )
        for row in fo_batch_result:
            fo_item_map[row[0]] = (row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8])

    for cost_item in vehicle.items:
        total_qty += cost_item.qty
        total_cny += cost_item.price_cny * cost_item.qty

        # Enrich with factory order item data (from pre-loaded batch)
        box_size = None
        pcs_per_box = None
        box_detail = None
        fo_order_id = None
        fo_order_number = None
        mix_group_id = None
        mix_box_size = None
        mix_pcs_per_box = None

        if cost_item.factory_order_item_id:
            fo_data = fo_item_map.get(cost_item.factory_order_item_id)
            if fo_data:
                (
                    box_size,
                    pcs_per_box,
                    box_detail,
                    fo_order_id,
                    fo_order_number,
                    mix_group_id,
                    mix_box_size,
                    mix_pcs_per_box,
                ) = fo_data

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
                box_detail=box_detail,
                mix_group_id=mix_group_id,
                mix_box_size=mix_box_size,
                mix_pcs_per_box=mix_pcs_per_box,
                factory_order_id=fo_order_id,
                factory_order_number=fo_order_number,
            )
        )

    # Use manually set estimated_arrival_date, or calculate from lead time
    if vehicle.estimated_arrival_date:
        estimated_arrival = vehicle.estimated_arrival_date
    else:
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
        payment_ref=vehicle.payment_ref,
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
    user_name: str | None = None,
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
            CostOrder.is_deleted == False,
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

    # Capture old status BEFORE change for history
    old_status = current

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

    # CUSTOMS → DISPATCHED: validate + create InboundReceipt (ожидается на складе)
    if data.status == VehicleStatus.DISPATCHED:
        errors = []
        if not vehicle.invoice_no:
            errors.append("номер инвойса")
        if not vehicle.dt_number:
            errors.append("номер ДТ")
        if not vehicle.target_warehouse_id:
            errors.append("склад назначения")
        if errors:
            raise ValueError(f"Укажите: {', '.join(errors)}")
        receipt = await _create_inbound_from_vehicle(db, project_id, vehicle)
        vehicle.inbound_receipt_id = receipt.id

    vehicle.status = data.status

    # Record status history
    from backend.models.supply_chain import VehicleStatusHistory

    history = VehicleStatusHistory(
        project_id=project_id,
        order_no=order_no,
        old_status=old_status,
        new_status=data.status,
        changed_at=utcnow(),
        changed_by=user_name,
        comment=None,
    )
    db.add(history)

    await db.commit()

    if receipt:
        result_data["inbound_receipt_id"] = receipt.id
        result_data["inbound_receipt_number"] = receipt.number

    # Auto-close factory orders if vehicle reached SHIPPED or above
    if data.status in _SHIPPED_OR_ABOVE:
        await _check_and_close_factory_orders_for_vehicle(db, project_id, vehicle)

    # Invalidate supplier catalog cache — delivered_qty depends on vehicle status
    await _invalidate_supplier_catalog(project_id)

    return result_data


async def _check_and_close_factory_orders_for_vehicle(
    db: AsyncSession,
    project_id: int,
    vehicle: CostOrder,
) -> None:
    """
    After a vehicle status update, check if any linked factory orders
    can be auto-closed (all their vehicles >= SHIPPED).
    """
    from backend.services.supply_chain.factory_orders import check_and_close_order

    # Find factory orders linked to items in this vehicle
    fo_item_ids = [item.factory_order_item_id for item in vehicle.items if item.factory_order_item_id is not None]
    if not fo_item_ids:
        return

    fo_result = await db.execute(
        select(FactoryOrderItem.factory_order_id)
        .where(
            FactoryOrderItem.id.in_(fo_item_ids),
            FactoryOrderItem.project_id == project_id,
            FactoryOrderItem.is_deleted == False,
        )
        .distinct()
        .limit(100)
    )
    factory_order_ids = [row[0] for row in fo_result.all()]

    for fo_id in factory_order_ids:
        try:
            await check_and_close_order(db, project_id, fo_id)
        except Exception:
            logger.exception("Failed to auto-close factory order %d", fo_id)


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
    items_result = await db.execute(
        select(CostOrderItem)
        .where(
            CostOrderItem.order_no == order_no,
            CostOrderItem.project_id == project_id,
            CostOrderItem.is_deleted == False,
        )
        .limit(2000)
    )
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


async def recalculate_all_vehicles(
    db: AsyncSession,
    project_id: int,
) -> dict:
    """Recalculate costs for ALL vehicles in the project."""
    from backend.services.cost.items import recalculate_order_items

    result = await db.execute(
        select(CostOrder.order_no)
        .where(
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,
            CostOrder.status.isnot(None),
        )
        .limit(500)
    )
    order_nos = [row[0] for row in result.all()]

    total_updated = 0
    for order_no in order_nos:
        updated, _ = await recalculate_order_items(db, project_id, order_no)
        total_updated += updated or 0

    return {"ok": True, "vehicles": len(order_nos), "items_updated": total_updated}


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
    - total_factory_orders: count of active factory orders
    - total_vehicles: count of active vehicles (CostOrder with status)
    - vehicles_by_status: breakdown by VehicleStatus
    - total_items: total item qty across all active vehicles
    - total_amount_cny: sum of total_cny across all active factory orders
    """
    from backend.models.supply_chain import FactoryOrder

    # Count factory orders
    fo_result = await db.execute(
        select(func.count(FactoryOrder.id)).where(
            FactoryOrder.project_id == project_id,
            FactoryOrder.is_deleted == False,
        )
    )
    total_factory_orders = fo_result.scalar() or 0

    # Total amount CNY across all active factory orders
    amount_result = await db.execute(
        select(func.coalesce(func.sum(FactoryOrder.total_cny), 0)).where(
            FactoryOrder.project_id == project_id,
            FactoryOrder.is_deleted == False,
        )
    )
    total_amount_cny = float(amount_result.scalar() or 0)

    # Count vehicles by status
    status_result = await db.execute(
        select(CostOrder.status, func.count(CostOrder.id))
        .where(
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,
            CostOrder.status.isnot(None),
        )
        .group_by(CostOrder.status)
    )
    vehicles_by_status = {row[0]: row[1] for row in status_result}

    # Total vehicles count
    total_vehicles = sum(vehicles_by_status.values())

    # Total items in active (non-delivered) vehicles.
    # FORMING is forming (not yet shipped), not "в пути".
    # SHIPPED/CUSTOMS/DISPATCHED are in transit.
    active_statuses = [VehicleStatus.FORMING, VehicleStatus.SHIPPED, VehicleStatus.CUSTOMS, VehicleStatus.DISPATCHED]
    items_result = await db.execute(
        select(func.coalesce(func.sum(CostOrderItem.qty), 0))
        .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
        .where(
            CostOrderItem.project_id == project_id,
            CostOrderItem.is_deleted == False,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,
            CostOrder.status.in_(active_statuses),
        )
    )
    total_items = items_result.scalar() or 0

    return {
        "total_factory_orders": total_factory_orders,
        "total_vehicles": total_vehicles,
        "vehicles_by_status": vehicles_by_status,
        "total_items": total_items,
        "total_amount_cny": total_amount_cny,
    }


async def get_vehicle_history(
    db: AsyncSession,
    project_id: int,
    order_no: str,
) -> list:
    """Get status history for a vehicle, ordered by changed_at."""
    from backend.models.supply_chain import VehicleStatusHistory

    result = await db.execute(
        select(VehicleStatusHistory)
        .where(
            VehicleStatusHistory.project_id == project_id,
            VehicleStatusHistory.order_no == order_no,
        )
        .order_by(VehicleStatusHistory.changed_at.desc())
        .limit(200)
    )
    return list(result.scalars().all())
