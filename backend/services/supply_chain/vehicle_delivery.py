"""
Supply Chain — Vehicle (CostOrder) CRUD, status management and overview.
"""

import logging
from datetime import timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.cost import CostOrder, CostOrderItem
from backend.models.enums import VehicleStatus
from backend.models.planning import LeadTime
from backend.models.supply_chain import FactoryOrder, FactoryOrderHistory, FactoryOrderItem
from backend.models.warehouse import (
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
)
from backend.schemas.supply_chain import (
    CONTAINER_TRANSPORT_MAP,
    AddItemsToVehicleRequest,
    PostShipmentItemsRequest,
    VehicleCostSummary,
    VehicleCreate,
    VehicleItemSchema,
    VehiclePriceResyncItem,
    VehiclePriceResyncPreview,
    VehicleSchema,
    VehicleStatusUpdate,
    VehicleUpdate,
)
from backend.services.supply_chain.factory_orders import (
    FactoryQtyExceeded,
    _adjust_assigned_qty,
    _resolve_existing_drift,
    refresh_factory_order_statuses,
)
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


async def _generate_vehicle_order_no(db: AsyncSession, project_id: int) -> str:
    """Generate next sequential V-NNNN order_no for this project."""
    result = await db.execute(
        select(CostOrder.order_no).where(
            CostOrder.project_id == project_id,
            CostOrder.order_no.like("V-%"),
        )
    )
    max_num = 0
    for (order_no,) in result:
        parts = order_no.split("-", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        num = int(parts[1])
        if num > max_num:
            max_num = num
    return f"V-{max_num + 1:04d}"


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

    order_no = (data.order_no or "").strip() or await _generate_vehicle_order_no(db, project_id)

    vehicle = CostOrder(
        project_id=project_id,
        order_no=order_no,
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
        vehicle_name=data.vehicle_name,
        plate_number=data.plate_number,
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
        "vehicle_name",
        "plate_number",
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

    new_target_wh = update_data.get("target_warehouse_id")
    if new_target_wh is not None and vehicle.inbound_receipt_id and new_target_wh != vehicle.target_warehouse_id:
        receipt_result = await db.execute(
            select(InboundReceipt).where(
                InboundReceipt.id == vehicle.inbound_receipt_id,
                InboundReceipt.project_id == project_id,
                InboundReceipt.is_deleted == False,
            )
        )
        linked_receipt = receipt_result.scalar_one_or_none()
        if linked_receipt and linked_receipt.status == InboundStatus.ACCEPTED:
            raise ValueError(
                f"Нельзя сменить склад: приёмка {linked_receipt.number} уже принята. "
                f"Создайте корректировочное движение или новую машину."
            )
        if linked_receipt and linked_receipt.warehouse_id != new_target_wh:
            linked_receipt.warehouse_id = new_target_wh

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

        # Use shared utility — strict mode for add (route still maps to 400)
        try:
            await _adjust_assigned_qty(
                db,
                fo_item,
                delta=item_req.qty,
                mode="strict",
                user_name=None,
                cost_order_no=order_no,
            )
        except FactoryQtyExceeded as e:
            available = e.detail.get("available", 0)
            raise ValueError(f"Cannot assign {item_req.qty} of {fo_item.barcode}: only {available} remaining") from e

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

        # Calculate volume_m3 per unit from box dimensions (use override if provided)
        from backend.services.cost.helpers import parse_box_volume_m3

        fo_bs = fo_item.mix_box_size if fo_item.mix_group_id and fo_item.mix_box_size else fo_item.box_size
        fo_ppb = fo_item.mix_pcs_per_box if fo_item.mix_group_id and fo_item.mix_pcs_per_box else fo_item.pcs_per_box
        effective_bs = item_req.box_size_override or fo_bs
        effective_ppb = item_req.pcs_per_box_override or fo_ppb
        vol_m3 = parse_box_volume_m3(effective_bs, effective_ppb)

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
            box_size_override=item_req.box_size_override,
            pcs_per_box_override=item_req.pcs_per_box_override,
        )
        db.add(cost_item)
        added += 1

    await db.commit()

    # Auto-recalculate costs (duty, vat, delivery) for the vehicle
    from backend.services.cost.items import recalculate_order_items

    await recalculate_order_items(db, project_id, order_no)
    await refresh_factory_order_statuses(db, project_id, affected_fo_ids)
    await _invalidate_supplier_catalog(project_id)

    return {"ok": True, "added": added}


_POST_SHIPMENT_ALLOWED = {
    VehicleStatus.SHIPPED,
    VehicleStatus.CUSTOMS,
    VehicleStatus.DISPATCHED,
    VehicleStatus.DELIVERED,
}


async def add_items_post_shipment(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    data: PostShipmentItemsRequest,
    user_name: str | None = None,
) -> dict:
    """sc18: добавить позиции в машину после отгрузки (status >= SHIPPED).

    - Создаёт CostOrderItem с `added_after_ship=True`.
    - Sync FactoryOrderItem.assigned_qty через `_adjust_assigned_qty` (mode из payload).
    - Recalc стоимости (duty/vat/delivery) для машины.
    - DISPATCHED: добавляет item в существующий InboundReceipt (EXPECTED).
    - DELIVERED: создаёт отдельный InboundReceipt (DRAFT) для ручной приёмки.
    - History: per-FO событие `item_added_post_shipment`.

    raise FactoryQtyExceeded если strict + delta > available — router транслирует в 422.
    raise ValueError на статус-нарушение / ненайденный FOI.
    """
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

    if vehicle.status not in _POST_SHIPMENT_ALLOWED:
        raise ValueError(
            f"Post-shipment add allowed only for status >= SHIPPED (current: {vehicle.status}). "
            f"Use regular add_items_to_vehicle for FORMING."
        )

    added = 0
    affected_fo_ids: set[int] = set()
    new_cost_items: list[CostOrderItem] = []

    for item_req in data.items:
        fo_result = await db.execute(
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
        fo_item = fo_result.scalar_one_or_none()
        if not fo_item:
            raise ValueError(f"FactoryOrderItem {item_req.factory_order_item_id} not found")

        await _adjust_assigned_qty(
            db,
            fo_item,
            delta=item_req.qty,
            mode=item_req.mode,
            user_name=user_name,
            cost_order_no=order_no,
        )
        affected_fo_ids.add(fo_item.factory_order_id)

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

        from backend.services.cost.helpers import parse_box_volume_m3

        fo_bs = fo_item.mix_box_size if fo_item.mix_group_id and fo_item.mix_box_size else fo_item.box_size
        fo_ppb = fo_item.mix_pcs_per_box if fo_item.mix_group_id and fo_item.mix_pcs_per_box else fo_item.pcs_per_box
        effective_bs = item_req.box_size_override or fo_bs
        effective_ppb = item_req.pcs_per_box_override or fo_ppb
        vol_m3 = parse_box_volume_m3(effective_bs, effective_ppb)

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
            box_size_override=item_req.box_size_override,
            pcs_per_box_override=item_req.pcs_per_box_override,
            added_after_ship=True,
        )
        db.add(cost_item)
        new_cost_items.append(cost_item)

        history = FactoryOrderHistory(
            project_id=project_id,
            factory_order_id=fo_item.factory_order_id,
            event_type="item_added_post_shipment",
            details=f"+{item_req.qty} шт. {fo_item.barcode} в машину {order_no} (статус {vehicle.status})",
            changed_at=utcnow(),
            changed_by=user_name,
        )
        db.add(history)
        added += 1

    await db.flush()

    receipts_updated = 0
    new_receipt_id: int | None = None

    if vehicle.status == VehicleStatus.DISPATCHED:
        existing_receipt_result = await db.execute(
            select(InboundReceipt).where(
                InboundReceipt.cost_order_id == vehicle.id,
                InboundReceipt.project_id == project_id,
                InboundReceipt.status == InboundStatus.EXPECTED,
            )
        )
        existing_receipt = existing_receipt_result.scalar_one_or_none()
        if existing_receipt:
            for cost_item in new_cost_items:
                try:
                    nom = await _resolve_barcode(db, project_id, cost_item.barcode)
                    receipt_item = InboundReceiptItem(
                        project_id=project_id,
                        receipt_id=existing_receipt.id,
                        nomenclature_id=nom.id,
                        barcode=cost_item.barcode,
                        expected_qty=cost_item.qty,
                        actual_qty=0,
                    )
                    db.add(receipt_item)
                    receipts_updated += 1
                except ValueError:
                    logger.warning(
                        "Barcode %s not found in nomenclature for project %d, "
                        "skipping receipt item add (vehicle %s, post-shipment)",
                        cost_item.barcode,
                        project_id,
                        vehicle.order_no,
                    )

    elif vehicle.status == VehicleStatus.DELIVERED:
        new_receipt_number = await _next_number(db, project_id, "IN", InboundReceipt)
        new_receipt = InboundReceipt(
            project_id=project_id,
            warehouse_id=vehicle.target_warehouse_id,
            number=new_receipt_number,
            status=InboundStatus.DRAFT,
            planned_date=utcnow().date(),
            comment=f"Доп. приёмка после отгрузки машины {vehicle.order_no}",
            cost_order_id=vehicle.id,
        )
        db.add(new_receipt)
        await db.flush()
        new_receipt_id = new_receipt.id

        for cost_item in new_cost_items:
            try:
                nom = await _resolve_barcode(db, project_id, cost_item.barcode)
                receipt_item = InboundReceiptItem(
                    project_id=project_id,
                    receipt_id=new_receipt.id,
                    nomenclature_id=nom.id,
                    barcode=cost_item.barcode,
                    expected_qty=cost_item.qty,
                    actual_qty=0,
                )
                db.add(receipt_item)
            except ValueError:
                logger.warning(
                    "Barcode %s not found in nomenclature for project %d, "
                    "skipping receipt item add (vehicle %s, post-shipment delivered)",
                    cost_item.barcode,
                    project_id,
                    vehicle.order_no,
                )

    # Rebuild the linked EXPECTED receipt from the vehicle's active items so a
    # post-shipment add can't leave duplicate receipt lines (one per barcode).
    await reconcile_receipt_from_vehicle(db, project_id, vehicle.id)

    await db.commit()

    from backend.services.cost.items import recalculate_order_items

    await recalculate_order_items(db, project_id, order_no)
    await refresh_factory_order_statuses(db, project_id, affected_fo_ids)
    await _invalidate_supplier_catalog(project_id)

    return {
        "ok": True,
        "added": added,
        "receipt_items_added": receipts_updated,
        "new_receipt_id": new_receipt_id,
        "vehicle_status": vehicle.status,
    }


async def reconcile_receipt_from_vehicle(db: AsyncSession, project_id: int, vehicle_id: int) -> int:
    """Rebuild the linked EXPECTED receipt's lines from the vehicle's CURRENT
    active cost items (aggregated by barcode), enforcing one line per barcode.

    Idempotent reconciliation — closes the gap where editing vehicle items
    (soft-delete + re-add, often the same factory_order_item) left stale
    duplicate receipt lines that doubled the stock on accept. Only touches
    EXPECTED receipts: ACCEPTED stock has already been applied and is corrected
    via a separate writeoff/return flow. Returns the number of lines changed.
    """
    receipt = (
        await db.execute(
            select(InboundReceipt).where(
                InboundReceipt.cost_order_id == vehicle_id,
                InboundReceipt.project_id == project_id,
                InboundReceipt.status == InboundStatus.EXPECTED,
                InboundReceipt.is_deleted == False,
            )
        )
    ).scalar_one_or_none()
    if receipt is None:
        return 0

    # Target expected_qty per barcode from ACTIVE cost items of this vehicle.
    target_rows = (
        await db.execute(
            select(CostOrderItem.barcode, func.sum(CostOrderItem.qty))
            .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
            .where(
                CostOrder.id == vehicle_id,
                CostOrder.project_id == project_id,
                CostOrderItem.project_id == project_id,
                CostOrderItem.is_deleted == False,
            )
            .group_by(CostOrderItem.barcode)
        )
    ).all()
    target: dict[str, int] = {bc: int(qty or 0) for bc, qty in target_rows}

    existing = (
        (
            await db.execute(
                select(InboundReceiptItem).where(
                    InboundReceiptItem.receipt_id == receipt.id,
                    InboundReceiptItem.project_id == project_id,
                )
            )
        )
        .scalars()
        .all()
    )

    changed = 0
    by_barcode: dict[str, InboundReceiptItem] = {}
    for item in existing:
        if item.barcode in by_barcode:
            # Duplicate line for the same barcode — the bug we're fixing.
            await db.delete(item)  # no-soft-delete-check: InboundReceiptItem has no SoftDeleteMixin
            changed += 1
            continue
        by_barcode[item.barcode] = item

    for barcode, qty in target.items():
        existing_item = by_barcode.pop(barcode) if barcode in by_barcode else None
        if existing_item is None:
            try:
                nom = await _resolve_barcode(db, project_id, barcode)
            except ValueError:
                logger.warning(
                    "reconcile: barcode %s not in nomenclature (project %d, vehicle %d), skipping",
                    barcode,
                    project_id,
                    vehicle_id,
                )
                continue
            db.add(
                InboundReceiptItem(
                    project_id=project_id,
                    receipt_id=receipt.id,
                    nomenclature_id=nom.id,
                    barcode=barcode,
                    expected_qty=qty,
                    actual_qty=0,
                )
            )
            changed += 1
        elif existing_item.expected_qty != qty:
            existing_item.expected_qty = qty
            changed += 1

    # Barcodes no longer present on the vehicle → drop their receipt lines.
    for item in by_barcode.values():
        await db.delete(item)  # no-soft-delete-check: InboundReceiptItem has no SoftDeleteMixin
        changed += 1

    return changed


_REMOVE_ALLOWED = {
    VehicleStatus.FORMING,
    VehicleStatus.SHIPPED,
    VehicleStatus.CUSTOMS,
    VehicleStatus.DISPATCHED,
}


async def remove_item_from_vehicle(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    item_id: int,
    user_name: str | None = None,
) -> dict:
    """Remove a CostOrderItem from vehicle and restore assigned_qty.

    sc19: разрешено для FORMING/SHIPPED/CUSTOMS/DISPATCHED. DELIVERED — блокируется
    (товар уже на складе, требуется отдельный writeoff/return workflow).
    На DISPATCHED дополнительно: уменьшается `expected_qty` в существующем InboundReceiptItem.
    """
    vehicle_result = await db.execute(
        select(CostOrder).where(
            CostOrder.order_no == order_no,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,
        )
    )
    vehicle = vehicle_result.scalar_one_or_none()
    if not vehicle:
        raise ValueError(f"Vehicle {order_no} not found")
    if vehicle.status not in _REMOVE_ALLOWED:
        raise ValueError(
            f"Удаление позиции запрещено для статуса {vehicle.status}. "
            f"DELIVERED — товар уже на складе, требуется списание/возврат."
        )

    is_post_shipment = vehicle.status != VehicleStatus.FORMING

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
        barcodes_to_qty: dict[str, int] = {}
        for mix_ci in mix_cost_items:
            if mix_ci.factory_order_item_id and mix_ci.factory_order_item_id in fo_items_map:
                fi = fo_items_map[mix_ci.factory_order_item_id]
                await _adjust_assigned_qty(db, fi, delta=-mix_ci.qty, mode="strict", user_name=None)
                affected_fo_ids.add(fi.factory_order_id)
                if is_post_shipment:
                    history = FactoryOrderHistory(
                        project_id=project_id,
                        factory_order_id=fi.factory_order_id,
                        event_type="item_removed_post_shipment",
                        details=f"-{mix_ci.qty} шт. {mix_ci.barcode} из машины {order_no} (статус {vehicle.status})",
                        changed_at=utcnow(),
                        changed_by=user_name,
                    )
                    db.add(history)
            barcodes_to_qty[mix_ci.barcode] = barcodes_to_qty.get(mix_ci.barcode, 0) + mix_ci.qty
            mix_ci.soft_delete()
            removed += 1

        # Rebuild the linked EXPECTED receipt from the vehicle's active items —
        # supersedes the incremental cleanup and guarantees one line per barcode.
        receipt_items_affected = await reconcile_receipt_from_vehicle(db, project_id, vehicle.id)

        await db.commit()

        from backend.services.cost.items import recalculate_order_items

        await recalculate_order_items(db, project_id, order_no)
        await refresh_factory_order_statuses(db, project_id, affected_fo_ids)
        await _invalidate_supplier_catalog(project_id)

        return {"ok": True, "removed": removed, "receipt_items_affected": receipt_items_affected}

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
            await _adjust_assigned_qty(db, fo_item2, delta=-cost_item.qty, mode="strict", user_name=None)
            affected_fo_ids_single.add(fo_item2.factory_order_id)
            if is_post_shipment:
                history = FactoryOrderHistory(
                    project_id=project_id,
                    factory_order_id=fo_item2.factory_order_id,
                    event_type="item_removed_post_shipment",
                    details=f"-{cost_item.qty} шт. {cost_item.barcode} из машины {order_no} (статус {vehicle.status})",
                    changed_at=utcnow(),
                    changed_by=user_name,
                )
                db.add(history)

    cost_item.soft_delete()
    # Rebuild the linked EXPECTED receipt from the vehicle's active items (after
    # the soft-delete) — supersedes incremental cleanup, one line per barcode.
    receipt_items_affected_single = await reconcile_receipt_from_vehicle(db, project_id, vehicle.id)
    await db.commit()

    # Recalculate remaining items (delivery allocation changes)
    from backend.services.cost.items import recalculate_order_items

    await recalculate_order_items(db, project_id, order_no)
    await refresh_factory_order_statuses(db, project_id, affected_fo_ids_single)
    await _invalidate_supplier_catalog(project_id)

    return {"ok": True, "receipt_items_affected": receipt_items_affected_single}


async def update_vehicle_item(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    item_id: int,
    new_qty: int | None = None,
    box_size_override: str | None = None,
    pcs_per_box_override: int | None = None,
    box_detail_override: list[int] | None = None,
    set_box_size_override: bool = False,
    set_pcs_per_box_override: bool = False,
    set_box_detail_override: bool = False,
    mode: Literal["strict", "extend_plan"] = "strict",
    user_name: str | None = None,
) -> dict:
    """Update a CostOrderItem (per-vehicle) and sync FactoryOrderItem.assigned_qty.

    sc17: when qty changes and the cost item is linked to a FactoryOrderItem,
    we synchronize assigned_qty via `_adjust_assigned_qty`. Two modes:

    - mode="strict" (default): if delta exceeds available, raise FactoryQtyExceeded
      (router maps to 422 with structured detail for inline drift confirm in UI).
    - mode="extend_plan": if delta exceeds available, bump FactoryOrderItem.qty by
      the overflow and write a `qty_extended_from_vehicle` history entry.

    box_* overrides do not touch FactoryOrderItem — they record actual packing
    that differs from the factory order plan.
    """
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

    qty_changed = new_qty is not None and new_qty != cost_item.qty
    any_override_set = set_box_size_override or set_pcs_per_box_override or set_box_detail_override

    # sc17 resolve-existing-drift: mode=extend_plan без смены qty на позиции с
    # фабричным заказом — синхронизируем assigned/plan с фактическим sum cost_items.
    resolve_drift = mode == "extend_plan" and not qty_changed and cost_item.factory_order_item_id is not None

    # Early return: nothing to change at all
    if not qty_changed and not any_override_set and not resolve_drift:
        return {"ok": True, "item_id": item_id, "noop": True}

    affected_fo_ids: set[int] = set()
    extended_by = 0

    # Sync FactoryOrderItem.assigned_qty if qty changed and item is linked
    if qty_changed and cost_item.factory_order_item_id:
        fo_result = await db.execute(
            select(FactoryOrderItem).where(
                FactoryOrderItem.id == cost_item.factory_order_item_id,
                FactoryOrderItem.project_id == project_id,
                FactoryOrderItem.is_deleted == False,
            )
        )
        fo_item = fo_result.scalar_one_or_none()
        if fo_item:
            delta = new_qty - cost_item.qty  # type: ignore[operator]
            # raises FactoryQtyExceeded if strict and delta > available
            adjust_result = await _adjust_assigned_qty(
                db,
                fo_item,
                delta=delta,
                mode=mode,
                user_name=user_name,
                cost_order_no=order_no,
            )
            extended_by = adjust_result.extended_by
            affected_fo_ids.add(fo_item.factory_order_id)
    elif resolve_drift:
        fo_result = await db.execute(
            select(FactoryOrderItem).where(
                FactoryOrderItem.id == cost_item.factory_order_item_id,
                FactoryOrderItem.project_id == project_id,
                FactoryOrderItem.is_deleted == False,
            )
        )
        fo_item = fo_result.scalar_one_or_none()
        if fo_item:
            adjust_result = await _resolve_existing_drift(
                db,
                fo_item,
                cost_order_no=order_no,
                user_name=user_name,
            )
            extended_by = adjust_result.extended_by
            affected_fo_ids.add(fo_item.factory_order_id)

    if new_qty is not None:
        cost_item.qty = new_qty
    if set_box_size_override:
        cost_item.box_size_override = box_size_override
    if set_pcs_per_box_override:
        cost_item.pcs_per_box_override = pcs_per_box_override
    if set_box_detail_override:
        cost_item.box_detail_override = box_detail_override

    # Recompute volume_m3 (per unit) when box dimensions change — delivery_rub
    # in recalculate_order_items is allocated proportionally to volume_m3.
    if set_box_size_override or set_pcs_per_box_override:
        from backend.services.cost.helpers import parse_box_volume_m3

        fo_bs = None
        fo_ppb = None
        if cost_item.factory_order_item_id:
            fo_result2 = await db.execute(
                select(FactoryOrderItem).where(
                    FactoryOrderItem.id == cost_item.factory_order_item_id,
                    FactoryOrderItem.project_id == project_id,
                    FactoryOrderItem.is_deleted == False,
                )
            )
            fo_item2 = fo_result2.scalar_one_or_none()
            if fo_item2:
                fo_bs = fo_item2.mix_box_size if fo_item2.mix_group_id and fo_item2.mix_box_size else fo_item2.box_size
                fo_ppb = (
                    fo_item2.mix_pcs_per_box
                    if fo_item2.mix_group_id and fo_item2.mix_pcs_per_box
                    else fo_item2.pcs_per_box
                )
        effective_bs = cost_item.box_size_override or fo_bs
        effective_ppb = cost_item.pcs_per_box_override or fo_ppb
        vol_m3 = parse_box_volume_m3(effective_bs, effective_ppb)
        cost_item.volume_m3 = vol_m3 if vol_m3 > 0 else None

    await db.commit()

    if qty_changed or set_box_size_override or set_pcs_per_box_override:
        from backend.services.cost.items import recalculate_order_items

        await recalculate_order_items(db, project_id, order_no)

    if affected_fo_ids:
        await refresh_factory_order_statuses(db, project_id, affected_fo_ids)

    await _invalidate_supplier_catalog(project_id)

    return {"ok": True, "item_id": item_id, "extended_by": extended_by}


# Backward-compatible alias (kept for any existing callers/tests)
async def update_vehicle_item_qty(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    item_id: int,
    new_qty: int,
) -> dict:
    return await update_vehicle_item(db, project_id, order_no, item_id, new_qty=new_qty)


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
            await _adjust_assigned_qty(db, fo_item, delta=-cost_item.qty, mode="strict", user_name=None)
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

    # Only process active (non-deleted) items — items removed earlier via
    # remove_items_from_vehicle already had their assigned_qty restored.
    active_items = [item for item in vehicle.items if not item.is_deleted]

    # Restore assigned_qty on linked factory order items
    affected_fo_ids: set[int] = set()
    fo_item_ids = [item.factory_order_item_id for item in active_items if item.factory_order_item_id]
    if fo_item_ids:
        fo_result = await db.execute(
            select(FactoryOrderItem).where(
                FactoryOrderItem.id.in_(fo_item_ids),
                FactoryOrderItem.project_id == project_id,
                FactoryOrderItem.is_deleted == False,
            )
        )
        fo_items_map = {fi.id: fi for fi in fo_result.scalars().all()}
        for cost_item in active_items:
            if cost_item.factory_order_item_id and cost_item.factory_order_item_id in fo_items_map:
                fo_item = fo_items_map[cost_item.factory_order_item_id]
                await _adjust_assigned_qty(db, fo_item, delta=-cost_item.qty, mode="strict", user_name=None)
                affected_fo_ids.add(fo_item.factory_order_id)

    # Soft-delete CostOrderItems when removing vehicle
    for cost_item in active_items:
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
        .options(selectinload(FactoryOrder.items.and_(FactoryOrderItem.is_deleted == False)))
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
            if item.is_deleted:  # safety net — selectinload+identity map can return stale objects
                continue
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

    # selectinload(CostOrder.items) doesn't filter is_deleted — drop them here.
    active_items = [ci for ci in vehicle.items if not ci.is_deleted]

    # Batch-load FactoryOrderItem + FactoryOrder data to avoid N+1.
    # sc17: also fetch FactoryOrderItem.qty + assigned_qty for drift detection.
    fo_item_ids = [ci.factory_order_item_id for ci in active_items if ci.factory_order_item_id is not None]
    fo_item_map: dict[int, tuple] = {}
    sum_by_foi: dict[int, int] = {}
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
                FactoryOrderItem.qty,
                FactoryOrderItem.assigned_qty,
            )
            .join(FactoryOrder, FactoryOrderItem.factory_order_id == FactoryOrder.id)
            .where(
                FactoryOrderItem.id.in_(fo_item_ids),
                FactoryOrderItem.is_deleted == False,
                FactoryOrder.project_id == project_id,
            )
        )
        for row in fo_batch_result:
            fo_item_map[row[0]] = (row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10])

        # sc17: batch sum of active CostOrderItem.qty per foi — single query, O(1)
        sum_by_foi_result = await db.execute(
            select(
                CostOrderItem.factory_order_item_id,
                func.coalesce(func.sum(CostOrderItem.qty), 0),
            )
            .where(
                CostOrderItem.factory_order_item_id.in_(fo_item_ids),
                CostOrderItem.project_id == project_id,
                CostOrderItem.is_deleted == False,
            )
            .group_by(CostOrderItem.factory_order_item_id)
        )
        sum_by_foi = {row[0]: row[1] for row in sum_by_foi_result}

    for cost_item in active_items:
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
        fo_qty: int | None = None
        fo_assigned: int | None = None
        qty_drift: int | None = None

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
                    fo_qty,
                    fo_assigned,
                ) = fo_data

                # sc17: compute qty_drift on this row.
                # other_vehicles_qty = sum across foi MINUS this row.
                # room_for_this = max(0, fo_qty - other_vehicles_qty)
                # qty_drift = max(0, cost_item.qty - room_for_this)
                if fo_qty is not None:
                    sum_total = int(sum_by_foi.get(cost_item.factory_order_item_id, 0))
                    other_vehicles_qty = sum_total - cost_item.qty
                    room_for_this = max(0, fo_qty - other_vehicles_qty)
                    qty_drift = max(0, cost_item.qty - room_for_this)

        # Apply per-vehicle overrides if present
        if cost_item.box_size_override:
            box_size = cost_item.box_size_override
        if cost_item.pcs_per_box_override:
            pcs_per_box = cost_item.pcs_per_box_override
        if cost_item.box_detail_override:
            box_detail = cost_item.box_detail_override

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
                qty_drift=qty_drift,
                fo_qty=fo_qty,
                fo_assigned=fo_assigned,
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
        vehicle_name=vehicle.vehicle_name,
        plate_number=vehicle.plate_number,
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


async def _load_resync_candidates(
    db: AsyncSession,
    project_id: int,
    order_no: str,
) -> tuple[CostOrder, list[CostOrderItem], dict[int, Decimal], int]:
    """Load vehicle + linked cost items + current FactoryOrderItem prices.

    Returns (vehicle, linked_cost_items, fo_price_map, unlinked_count).
    Raises ValueError if vehicle not found.
    """
    veh_result = await db.execute(
        select(CostOrder).where(
            CostOrder.order_no == order_no,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,
        )
    )
    vehicle = veh_result.scalar_one_or_none()
    if not vehicle:
        raise ValueError(f"Vehicle {order_no} not found")

    items_result = await db.execute(
        select(CostOrderItem).where(
            CostOrderItem.order_no == order_no,
            CostOrderItem.project_id == project_id,
            CostOrderItem.is_deleted == False,
        )
    )
    all_items = list(items_result.scalars().all())
    linked = [i for i in all_items if i.factory_order_item_id]
    unlinked = len(all_items) - len(linked)

    fo_price_map: dict[int, Decimal] = {}
    if linked:
        fo_ids = [i.factory_order_item_id for i in linked if i.factory_order_item_id]
        fo_result = await db.execute(
            select(FactoryOrderItem.id, FactoryOrderItem.price_cny).where(
                FactoryOrderItem.id.in_(fo_ids),
                FactoryOrderItem.project_id == project_id,
                FactoryOrderItem.is_deleted == False,
            )
        )
        for row in fo_result:
            fo_price_map[row[0]] = row[1]

    return vehicle, linked, fo_price_map, unlinked


async def preview_price_resync(
    db: AsyncSession,
    project_id: int,
    order_no: str,
) -> VehiclePriceResyncPreview:
    """Preview items whose price_cny differs from the linked FactoryOrderItem."""
    vehicle, linked, fo_price_map, unlinked = await _load_resync_candidates(db, project_id, order_no)

    changed: list[VehiclePriceResyncItem] = []
    sum_delta = Decimal("0")
    for ci in linked:
        foi_price = fo_price_map.get(ci.factory_order_item_id or 0)
        if foi_price is None:
            continue
        if ci.price_cny == foi_price:
            continue
        delta = (foi_price - ci.price_cny) * Decimal(str(ci.qty))
        sum_delta += delta
        changed.append(
            VehiclePriceResyncItem(
                cost_item_id=ci.id,
                barcode=ci.barcode,
                article_seller=ci.article_seller,
                subject=ci.subject,
                qty=ci.qty,
                old_price_cny=ci.price_cny,
                new_price_cny=foi_price,
                delta_sum_cny=delta,
            )
        )

    return VehiclePriceResyncPreview(
        vehicle_status=vehicle.status,
        total_items=len(linked),
        unlinked_items=unlinked,
        changed_items=len(changed),
        sum_delta_cny=sum_delta,
        items=changed,
    )


async def apply_price_resync(
    db: AsyncSession,
    project_id: int,
    order_no: str,
) -> dict:
    """Apply resync: copy FactoryOrderItem.price_cny into linked CostOrderItem and recalc."""
    _vehicle, linked, fo_price_map, _unlinked = await _load_resync_candidates(db, project_id, order_no)

    applied = 0
    for ci in linked:
        foi_price = fo_price_map.get(ci.factory_order_item_id or 0)
        if foi_price is None or ci.price_cny == foi_price:
            continue
        ci.price_cny = foi_price
        applied += 1

    if applied == 0:
        return {"applied": 0}

    await db.commit()

    from backend.cache import invalidate_project_reports
    from backend.services.cost.items import recalculate_order_items

    await recalculate_order_items(db, project_id, order_no)
    await _invalidate_supplier_catalog(project_id)
    await invalidate_project_reports(project_id)

    return {"applied": applied}


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

    # Aggregate ACTIVE cost items by barcode → one receipt line per barcode.
    # vehicle.items is unfiltered (includes soft-deleted), and the same barcode
    # can arrive as separate cost items from 2+ factory orders — either would
    # create duplicate receipt lines that double the stock on accept.
    qty_by_barcode: dict[str, int] = {}
    for cost_item in vehicle.items:
        if cost_item.is_deleted:
            continue
        qty_by_barcode[cost_item.barcode] = qty_by_barcode.get(cost_item.barcode, 0) + (cost_item.qty or 0)

    for barcode, qty in qty_by_barcode.items():
        try:
            nom = await _resolve_barcode(db, project_id, barcode)
            item = InboundReceiptItem(
                project_id=project_id,
                receipt_id=receipt.id,
                nomenclature_id=nom.id,
                barcode=barcode,
                expected_qty=qty,
                actual_qty=0,
            )
            db.add(item)
        except ValueError:
            logger.warning(
                "Barcode %s not found in nomenclature for project %d, " "skipping inbound receipt item (vehicle %s)",
                barcode,
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
