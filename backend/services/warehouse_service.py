"""
Warehouse service — CRUD warehouses, receipts, shipments, transfers, adjustments, stock engine.
"""

import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.cost import Nomenclature
from backend.models.warehouse import (
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    MovementType,
    OutboundShipment,
    OutboundShipmentItem,
    OutboundStatus,
    StockAdjustment,
    StockMovement,
    StockTransfer,
    StockTransferItem,
    TransferStatus,
    Warehouse,
    WarehouseStock,
    WarehouseType,
)
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


# ─── Warehouses CRUD ────────────────────────────────────────────────────────


async def list_warehouses(db: AsyncSession, project_id: int) -> list:
    """List all active warehouses for a project, ordered by sort_order."""
    result = await db.execute(
        select(Warehouse)
        .where(
            Warehouse.project_id == project_id,
            Warehouse.is_deleted == False,  # noqa: E712
        )
        .order_by(Warehouse.sort_order, Warehouse.id)
    )
    return result.scalars().all()


async def get_warehouse(db: AsyncSession, project_id: int, warehouse_id: int) -> Warehouse | None:
    """Get a single warehouse by id."""
    result = await db.execute(
        select(Warehouse).where(
            Warehouse.id == warehouse_id,
            Warehouse.project_id == project_id,
            Warehouse.is_deleted == False,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def create_warehouse(db: AsyncSession, project_id: int, payload: dict) -> Warehouse:
    """Create a new warehouse."""
    wh = Warehouse(
        project_id=project_id,
        name=payload["name"],
        warehouse_type=payload["warehouse_type"],
        country=payload.get("country"),
        address=payload.get("address"),
        assembly_days=payload.get("assembly_days"),
        sort_order=payload.get("sort_order", 0),
    )
    db.add(wh)
    await db.commit()
    await db.refresh(wh)
    return wh


async def update_warehouse(db: AsyncSession, project_id: int, warehouse_id: int, payload: dict) -> Warehouse | None:
    """Update an existing warehouse."""
    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        return None

    for key, value in payload.items():
        if hasattr(wh, key) and key not in ("id", "project_id"):
            setattr(wh, key, value)

    await db.commit()
    await db.refresh(wh)
    return wh


async def delete_warehouse(db: AsyncSession, project_id: int, warehouse_id: int) -> bool:
    """Soft-delete a warehouse."""
    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        return False

    wh.soft_delete()
    await db.commit()
    return True


async def reorder_warehouses(db: AsyncSession, project_id: int, items: list[dict]) -> bool:
    """Update sort_order for multiple warehouses."""
    for item in items:
        wh_id = item.get("id")
        sort_order = item.get("sort_order", 0)
        if wh_id is None:
            continue
        result = await db.execute(
            select(Warehouse).where(
                Warehouse.id == wh_id,
                Warehouse.project_id == project_id,
                Warehouse.is_deleted == False,  # noqa: E712
            )
        )
        wh = result.scalar_one_or_none()
        if wh:
            wh.sort_order = sort_order

    await db.commit()
    return True


# ─── Next number generator ─────────────────────────────────────────────────


async def _next_number(db: AsyncSession, project_id: int, prefix: str, table) -> str:
    """Generate next sequential number for a document (IN-1, OUT-1, TR-1).
    Counts ALL records (including soft-deleted) to avoid number collisions."""
    result = await db.execute(
        select(func.count(table.id)).where(
            table.project_id == project_id,
        )
    )
    count = result.scalar() or 0
    return f"{prefix}-{count + 1}"


# ─── Barcode → Nomenclature lookup ─────────────────────────────────────────


async def _resolve_barcode(db: AsyncSession, project_id: int, barcode: str) -> Nomenclature:
    """Find Nomenclature by barcode. Raises ValueError if not found."""
    result = await db.execute(
        select(Nomenclature).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode == barcode,
        )
    )
    nom = result.scalar_one_or_none()
    if not nom:
        raise ValueError(f"Barcode not found: {barcode}")
    return nom


# ─── Stock Engine ──────────────────────────────────────────────────────────


async def _update_stock(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    nomenclature_id: int,
    barcode: str,
    delta: int,
    movement_type: MovementType,
    reference_type: str,
    reference_id: int | None = None,
    comment: str | None = None,
) -> None:
    """
    Update warehouse_stock balance and create stock_movement in one go.

    delta: positive = inbound, negative = outbound.
    Raises ValueError if resulting quantity < 0.
    """
    # 1. Get or create WarehouseStock row
    result = await db.execute(
        select(WarehouseStock).where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id == warehouse_id,
            WarehouseStock.nomenclature_id == nomenclature_id,
        )
    )
    stock = result.scalar_one_or_none()

    if stock is None:
        stock = WarehouseStock(
            project_id=project_id,
            warehouse_id=warehouse_id,
            nomenclature_id=nomenclature_id,
            barcode=barcode,
            quantity=0,
            in_transit=0,
        )
        db.add(stock)
        await db.flush()

    # 2. Check non-negative constraint
    new_qty = stock.quantity + delta
    if new_qty < 0:
        raise ValueError(f"Insufficient stock for barcode {barcode}: " f"have {stock.quantity}, need {abs(delta)}")

    stock.quantity = new_qty
    stock.updated_at = utcnow()

    # 3. Create movement record (audit log)
    movement = StockMovement(
        project_id=project_id,
        warehouse_id=warehouse_id,
        nomenclature_id=nomenclature_id,
        barcode=barcode,
        movement_type=movement_type,
        quantity=delta,
        reference_type=reference_type,
        reference_id=reference_id,
        comment=comment,
        created_at=utcnow(),
    )
    db.add(movement)


# ─── Inbound Receipts (Приёмка) ────────────────────────────────────────────


async def list_receipts(db: AsyncSession, project_id: int, warehouse_id: int) -> list:
    """List receipts for a warehouse."""
    result = await db.execute(
        select(InboundReceipt)
        .options(selectinload(InboundReceipt.items))
        .where(
            InboundReceipt.project_id == project_id,
            InboundReceipt.warehouse_id == warehouse_id,
            InboundReceipt.is_deleted == False,  # noqa: E712
        )
        .order_by(InboundReceipt.id.desc())
    )
    return result.scalars().all()


async def get_receipt(db: AsyncSession, project_id: int, receipt_id: int) -> InboundReceipt | None:
    """Get receipt with items."""
    result = await db.execute(
        select(InboundReceipt).where(
            InboundReceipt.id == receipt_id,
            InboundReceipt.project_id == project_id,
            InboundReceipt.is_deleted == False,  # noqa: E712
        )
    )
    receipt = result.scalar_one_or_none()
    if receipt:
        # Eagerly load items
        await db.refresh(receipt, ["items"])
    return receipt


async def create_receipt(db: AsyncSession, project_id: int, warehouse_id: int, payload: dict) -> InboundReceipt:
    """Create a new inbound receipt with items (barcode lookup)."""
    # Verify warehouse exists
    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")

    number = await _next_number(db, project_id, "IN", InboundReceipt)

    receipt = InboundReceipt(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=number,
        status=InboundStatus.EXPECTED,
        planned_date=payload.get("planned_date"),
        comment=payload.get("comment"),
        tags=payload.get("tags"),
    )
    db.add(receipt)
    await db.flush()  # get receipt.id

    # Add items — resolve barcodes
    for item_data in payload.get("items", []):
        nom = await _resolve_barcode(db, project_id, item_data["barcode"])
        item = InboundReceiptItem(
            receipt_id=receipt.id,
            nomenclature_id=nom.id,
            barcode=item_data["barcode"],
            expected_qty=item_data["expected_qty"],
            actual_qty=item_data.get("actual_qty", 0),
        )
        db.add(item)

    await db.commit()
    await db.refresh(receipt, ["items"])
    return receipt


async def update_receipt(db: AsyncSession, project_id: int, receipt_id: int, payload: dict) -> InboundReceipt | None:
    """Update receipt. DRAFT/EXPECTED: all fields. ACCEPTED: actual_qty + metadata only (with stock delta)."""
    receipt = await get_receipt(db, project_id, receipt_id)
    if not receipt:
        return None

    if receipt.status == InboundStatus.ACCEPTED:
        # ── ACCEPTED branch: only actual_qty changes + metadata ──
        for field in ("comment", "tags"):
            if field in payload:
                setattr(receipt, field, payload[field])

        if "items" in payload and payload["items"] is not None:
            existing_by_barcode = {item.barcode: item for item in receipt.items}
            for item_data in payload["items"]:
                barcode = item_data["barcode"]
                existing_item = existing_by_barcode.get(barcode)
                if not existing_item:
                    raise ValueError(f"Cannot add new items to ACCEPTED receipt. Barcode: {barcode}")

                new_actual = item_data.get("actual_qty", existing_item.actual_qty)
                old_actual = existing_item.actual_qty
                delta = new_actual - old_actual

                if delta != 0:
                    await _update_stock(
                        db,
                        project_id=project_id,
                        warehouse_id=receipt.warehouse_id,
                        nomenclature_id=existing_item.nomenclature_id,
                        barcode=barcode,
                        delta=delta,
                        movement_type=MovementType.INBOUND_EDIT,
                        reference_type="RECEIPT",
                        reference_id=receipt.id,
                        comment=f"Edit receipt {receipt.number}: {old_actual} → {new_actual}",
                    )
                    existing_item.actual_qty = new_actual

        await db.commit()
        await db.refresh(receipt, ["items"])
        return receipt

    elif receipt.status in (InboundStatus.DRAFT, InboundStatus.EXPECTED):
        # ── DRAFT/EXPECTED branch: full edit ──
        for field in ("planned_date", "comment", "tags"):
            if field in payload:
                setattr(receipt, field, payload[field])

        # Update status DRAFT → EXPECTED
        if (
            "status" in payload
            and payload["status"] == InboundStatus.EXPECTED
            and receipt.status == InboundStatus.DRAFT
        ):
            receipt.status = InboundStatus.EXPECTED

        # Replace items if provided
        if "items" in payload and payload["items"] is not None:
            for old_item in list(receipt.items):
                await db.delete(old_item)
            await db.flush()

            for item_data in payload["items"]:
                nom = await _resolve_barcode(db, project_id, item_data["barcode"])
                item = InboundReceiptItem(
                    receipt_id=receipt.id,
                    nomenclature_id=nom.id,
                    barcode=item_data["barcode"],
                    expected_qty=item_data["expected_qty"],
                    actual_qty=item_data.get("actual_qty", 0),
                )
                db.add(item)

        await db.commit()
        await db.refresh(receipt, ["items"])
        return receipt

    else:
        raise ValueError(f"Cannot edit receipt in status {receipt.status}")


async def accept_receipt(db: AsyncSession, project_id: int, receipt_id: int) -> InboundReceipt:
    """
    Accept receipt: DRAFT/EXPECTED → ACCEPTED.
    Adds actual_qty to warehouse stock for each item.
    """
    receipt = await get_receipt(db, project_id, receipt_id)
    if not receipt:
        raise ValueError("Receipt not found")

    if receipt.status not in (InboundStatus.DRAFT, InboundStatus.EXPECTED):
        raise ValueError(f"Cannot accept receipt in status {receipt.status}")

    if not receipt.items:
        raise ValueError("Cannot accept receipt with no items")

    # Update stock for each item
    for item in receipt.items:
        if item.actual_qty <= 0:
            continue
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=receipt.warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=item.actual_qty,
            movement_type=MovementType.INBOUND,
            reference_type="RECEIPT",
            reference_id=receipt.id,
        )

    receipt.status = InboundStatus.ACCEPTED
    receipt.actual_date = date.today()

    await db.commit()
    await db.refresh(receipt, ["items"])
    return receipt


async def cancel_receipt(db: AsyncSession, project_id: int, receipt_id: int) -> InboundReceipt:
    """
    Cancel accepted receipt: ACCEPTED → CANCELLED.
    Rolls back actual_qty from warehouse stock.
    """
    receipt = await get_receipt(db, project_id, receipt_id)
    if not receipt:
        raise ValueError("Receipt not found")

    if receipt.status != InboundStatus.ACCEPTED:
        raise ValueError(f"Can only cancel ACCEPTED receipts, got {receipt.status}")

    # Reverse stock for each item
    for item in receipt.items:
        if item.actual_qty <= 0:
            continue
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=receipt.warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=-item.actual_qty,
            movement_type=MovementType.INBOUND_CANCEL,
            reference_type="RECEIPT",
            reference_id=receipt.id,
            comment=f"Cancel receipt {receipt.number}",
        )

    receipt.status = InboundStatus.CANCELLED

    await db.commit()
    await db.refresh(receipt, ["items"])
    return receipt


# ─── Warehouse Stock queries ──────────────────────────────────────────────


async def _get_reserved_map(db: AsyncSession, project_id: int, warehouse_id: int) -> dict[int, int]:
    """Get reserved qty per nomenclature_id from active assembly requests."""
    from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus

    result = await db.execute(
        select(
            AssemblyRequestItem.nomenclature_id,
            func.sum(AssemblyRequestItem.quantity).label("reserved"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.warehouse_id == warehouse_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_(
                [
                    AssemblyStatus.PENDING,
                    AssemblyStatus.IN_PROGRESS,
                    AssemblyStatus.READY,
                    AssemblyStatus.VEHICLE_ASSIGNED,
                ]
            ),
        )
        .group_by(AssemblyRequestItem.nomenclature_id)
    )
    return {row.nomenclature_id: row.reserved for row in result.all()}


async def get_warehouse_stock(db: AsyncSession, project_id: int, warehouse_id: int) -> list[dict]:
    """Get current stock for a warehouse, enriched with reserved/available."""
    result = await db.execute(
        select(WarehouseStock)
        .where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id == warehouse_id,
            WarehouseStock.quantity > 0,
        )
        .order_by(WarehouseStock.barcode)
    )
    rows = result.scalars().all()

    reserved_map = await _get_reserved_map(db, project_id, warehouse_id)

    enriched = []
    for row in rows:
        reserved = reserved_map.get(row.nomenclature_id, 0)
        available = max(0, row.quantity - reserved)
        enriched.append(
            {
                "id": row.id,
                "project_id": row.project_id,
                "warehouse_id": row.warehouse_id,
                "nomenclature_id": row.nomenclature_id,
                "barcode": row.barcode,
                "quantity": row.quantity,
                "in_transit": row.in_transit,
                "cost_price": row.cost_price,
                "updated_at": row.updated_at,
                "reserved": reserved,
                "available": available,
            }
        )
    return enriched


async def get_stock_movements(db: AsyncSession, project_id: int, warehouse_id: int, limit: int = 200) -> list:
    """Get movement history for a warehouse."""
    result = await db.execute(
        select(StockMovement)
        .where(
            StockMovement.project_id == project_id,
            StockMovement.warehouse_id == warehouse_id,
        )
        .order_by(StockMovement.created_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


# ─── Outbound Shipments (Отгрузка) — Phase 3 ──────────────────────────────


async def list_shipments(db: AsyncSession, project_id: int, warehouse_id: int) -> list:
    """List shipments for a warehouse."""
    result = await db.execute(
        select(OutboundShipment)
        .options(selectinload(OutboundShipment.items))
        .where(
            OutboundShipment.project_id == project_id,
            OutboundShipment.warehouse_id == warehouse_id,
            OutboundShipment.is_deleted == False,  # noqa: E712
        )
        .order_by(OutboundShipment.id.desc())
    )
    return result.scalars().all()


async def get_shipment(db: AsyncSession, project_id: int, shipment_id: int) -> OutboundShipment | None:
    """Get shipment with items."""
    result = await db.execute(
        select(OutboundShipment).where(
            OutboundShipment.id == shipment_id,
            OutboundShipment.project_id == project_id,
            OutboundShipment.is_deleted == False,  # noqa: E712
        )
    )
    shipment = result.scalar_one_or_none()
    if shipment:
        await db.refresh(shipment, ["items"])
    return shipment


async def create_shipment(db: AsyncSession, project_id: int, warehouse_id: int, payload: dict) -> OutboundShipment:
    """Create outbound shipment. Only from FULFILLMENT warehouses."""
    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")
    if wh.warehouse_type != WarehouseType.FULFILLMENT:
        raise ValueError("Shipments can only be created from FULFILLMENT warehouses")

    number = await _next_number(db, project_id, "OUT", OutboundShipment)

    shipment = OutboundShipment(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=number,
        status=OutboundStatus.DRAFT,
        destination=payload.get("destination"),
        comment=payload.get("comment"),
    )
    db.add(shipment)
    await db.flush()

    for item_data in payload.get("items", []):
        nom = await _resolve_barcode(db, project_id, item_data["barcode"])
        item = OutboundShipmentItem(
            shipment_id=shipment.id,
            nomenclature_id=nom.id,
            barcode=item_data["barcode"],
            quantity=item_data["quantity"],
        )
        db.add(item)

    await db.commit()
    await db.refresh(shipment, ["items"])
    return shipment


async def ship_shipment(db: AsyncSession, project_id: int, shipment_id: int) -> OutboundShipment:
    """
    Ship: DRAFT → SHIPPED.
    Checks stock >= qty, then stock -= qty for each item.
    """
    shipment = await get_shipment(db, project_id, shipment_id)
    if not shipment:
        raise ValueError("Shipment not found")

    if shipment.status != OutboundStatus.DRAFT:
        raise ValueError(f"Cannot ship in status {shipment.status}")

    if not shipment.items:
        raise ValueError("Cannot ship with no items")

    for item in shipment.items:
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=shipment.warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=-item.quantity,
            movement_type=MovementType.OUTBOUND,
            reference_type="SHIPMENT",
            reference_id=shipment.id,
        )

    shipment.status = OutboundStatus.SHIPPED
    shipment.shipped_date = date.today()

    await db.commit()
    await db.refresh(shipment, ["items"])
    return shipment


async def deliver_shipment(db: AsyncSession, project_id: int, shipment_id: int) -> OutboundShipment:
    """Mark shipment as delivered: SHIPPED → DELIVERED."""
    shipment = await get_shipment(db, project_id, shipment_id)
    if not shipment:
        raise ValueError("Shipment not found")

    if shipment.status != OutboundStatus.SHIPPED:
        raise ValueError(f"Cannot deliver in status {shipment.status}")

    shipment.status = OutboundStatus.DELIVERED

    await db.commit()
    await db.refresh(shipment, ["items"])
    return shipment


async def cancel_shipment(db: AsyncSession, project_id: int, shipment_id: int) -> OutboundShipment:
    """
    Cancel shipped shipment: SHIPPED → CANCELLED.
    Returns stock for each item.
    """
    shipment = await get_shipment(db, project_id, shipment_id)
    if not shipment:
        raise ValueError("Shipment not found")

    if shipment.status != OutboundStatus.SHIPPED:
        raise ValueError(f"Can only cancel SHIPPED shipments, got {shipment.status}")

    for item in shipment.items:
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=shipment.warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=item.quantity,
            movement_type=MovementType.OUTBOUND_CANCEL,
            reference_type="SHIPMENT",
            reference_id=shipment.id,
            comment=f"Cancel shipment {shipment.number}",
        )

    shipment.status = OutboundStatus.CANCELLED

    await db.commit()
    await db.refresh(shipment, ["items"])
    return shipment


# ─── Stock Transfers (Перемещение) — Phase 4 ──────────────────────────────


async def list_transfers(db: AsyncSession, project_id: int, in_transit_only: bool = False) -> list:
    """List transfers. Optionally filter only IN_TRANSIT."""
    query = (
        select(StockTransfer)
        .options(selectinload(StockTransfer.items))
        .where(
            StockTransfer.project_id == project_id,
            StockTransfer.is_deleted == False,  # noqa: E712
        )
    )
    if in_transit_only:
        query = query.where(StockTransfer.status == TransferStatus.IN_TRANSIT)
    query = query.order_by(StockTransfer.id.desc())
    result = await db.execute(query)
    return result.scalars().all()


async def get_transfer(db: AsyncSession, project_id: int, transfer_id: int) -> StockTransfer | None:
    """Get transfer with items."""
    result = await db.execute(
        select(StockTransfer).where(
            StockTransfer.id == transfer_id,
            StockTransfer.project_id == project_id,
            StockTransfer.is_deleted == False,  # noqa: E712
        )
    )
    transfer = result.scalar_one_or_none()
    if transfer:
        await db.refresh(transfer, ["items"])
    return transfer


async def create_transfer(db: AsyncSession, project_id: int, payload: dict) -> StockTransfer:
    """Create stock transfer between two warehouses."""
    from_wh = await get_warehouse(db, project_id, payload["from_warehouse_id"])
    to_wh = await get_warehouse(db, project_id, payload["to_warehouse_id"])
    if not from_wh:
        raise ValueError("Source warehouse not found")
    if not to_wh:
        raise ValueError("Destination warehouse not found")
    if from_wh.id == to_wh.id:
        raise ValueError("Cannot transfer to the same warehouse")

    number = await _next_number(db, project_id, "TR", StockTransfer)

    transfer = StockTransfer(
        project_id=project_id,
        from_warehouse_id=from_wh.id,
        to_warehouse_id=to_wh.id,
        number=number,
        status=TransferStatus.DRAFT,
        comment=payload.get("comment"),
    )
    db.add(transfer)
    await db.flush()

    for item_data in payload.get("items", []):
        nom = await _resolve_barcode(db, project_id, item_data["barcode"])
        item = StockTransferItem(
            transfer_id=transfer.id,
            nomenclature_id=nom.id,
            barcode=item_data["barcode"],
            quantity=item_data["quantity"],
        )
        db.add(item)

    await db.commit()
    await db.refresh(transfer, ["items"])
    return transfer


async def send_transfer(db: AsyncSession, project_id: int, transfer_id: int) -> StockTransfer:
    """
    Send transfer: DRAFT → IN_TRANSIT.
    source.stock -= qty, target.in_transit += qty.
    """
    transfer = await get_transfer(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Transfer not found")

    if transfer.status != TransferStatus.DRAFT:
        raise ValueError(f"Cannot send in status {transfer.status}")

    if not transfer.items:
        raise ValueError("Cannot send transfer with no items")

    for item in transfer.items:
        # Deduct from source warehouse
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=transfer.from_warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=-item.quantity,
            movement_type=MovementType.TRANSFER_OUT,
            reference_type="TRANSFER",
            reference_id=transfer.id,
        )

        # Mark as in_transit on destination
        result = await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id == transfer.to_warehouse_id,
                WarehouseStock.nomenclature_id == item.nomenclature_id,
            )
        )
        target_stock = result.scalar_one_or_none()
        if target_stock is None:
            target_stock = WarehouseStock(
                project_id=project_id,
                warehouse_id=transfer.to_warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                quantity=0,
                in_transit=0,
            )
            db.add(target_stock)
            await db.flush()

        target_stock.in_transit += item.quantity
        target_stock.updated_at = utcnow()

    transfer.status = TransferStatus.IN_TRANSIT

    await db.commit()
    await db.refresh(transfer, ["items"])
    return transfer


async def complete_transfer(db: AsyncSession, project_id: int, transfer_id: int) -> StockTransfer:
    """
    Complete transfer: IN_TRANSIT → COMPLETED.
    target.stock += qty, target.in_transit -= qty.
    """
    transfer = await get_transfer(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Transfer not found")

    if transfer.status != TransferStatus.IN_TRANSIT:
        raise ValueError(f"Cannot complete in status {transfer.status}")

    for item in transfer.items:
        # Add to destination stock
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=transfer.to_warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=item.quantity,
            movement_type=MovementType.TRANSFER_IN,
            reference_type="TRANSFER",
            reference_id=transfer.id,
        )

        # Decrease in_transit
        result = await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id == transfer.to_warehouse_id,
                WarehouseStock.nomenclature_id == item.nomenclature_id,
            )
        )
        target_stock = result.scalar_one_or_none()
        if target_stock:
            target_stock.in_transit = max(0, target_stock.in_transit - item.quantity)
            target_stock.updated_at = utcnow()

    transfer.status = TransferStatus.COMPLETED

    await db.commit()
    await db.refresh(transfer, ["items"])
    return transfer


# ─── Stock Adjustments (Корректировка) — Phase 5 ──────────────────────────


async def create_adjustment(db: AsyncSession, project_id: int, warehouse_id: int, payload: dict) -> StockAdjustment:
    """
    Create stock adjustment (inventory correction).
    delta > 0 = surplus, delta < 0 = shortage.
    """
    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")

    nom = await _resolve_barcode(db, project_id, payload["barcode"])

    adjustment = StockAdjustment(
        project_id=project_id,
        warehouse_id=warehouse_id,
        nomenclature_id=nom.id,
        barcode=payload["barcode"],
        delta=payload["delta"],
        reason=payload["reason"],
    )
    db.add(adjustment)

    await _update_stock(
        db,
        project_id=project_id,
        warehouse_id=warehouse_id,
        nomenclature_id=nom.id,
        barcode=payload["barcode"],
        delta=payload["delta"],
        movement_type=MovementType.ADJUSTMENT,
        reference_type="ADJUSTMENT",
        reference_id=None,  # will be set after flush
        comment=payload["reason"],
    )

    await db.commit()
    await db.refresh(adjustment)
    return adjustment


# ─── Summary Stock (Сводные остатки) — Phase 6 ────────────────────────────


async def get_stock_summary(db: AsyncSession, project_id: int) -> list[dict]:
    """
    Summary stock across all warehouses.
    Returns: [{barcode, nomenclature_id, warehouses: {wh_id: qty}, in_transit: {wh_id: qty},
               reserved: {wh_id: qty}, total, total_in_transit, total_reserved, total_available}]
    """
    result = await db.execute(
        select(WarehouseStock)
        .where(
            WarehouseStock.project_id == project_id,
        )
        .order_by(WarehouseStock.barcode)
    )
    rows = result.scalars().all()

    # Collect unique warehouse_ids to get reserved maps
    wh_ids = {row.warehouse_id for row in rows}
    all_reserved: dict[int, dict[int, int]] = {}
    for wh_id in wh_ids:
        all_reserved[wh_id] = await _get_reserved_map(db, project_id, wh_id)

    # Group by nomenclature_id
    summary: dict[int, dict] = {}
    for row in rows:
        key = row.nomenclature_id
        if key not in summary:
            summary[key] = {
                "nomenclature_id": row.nomenclature_id,
                "barcode": row.barcode,
                "warehouses": {},
                "in_transit": {},
                "reserved": {},
                "total": 0,
                "total_in_transit": 0,
                "total_reserved": 0,
                "total_available": 0,
            }
        entry = summary[key]
        if row.quantity > 0:
            entry["warehouses"][row.warehouse_id] = row.quantity
            entry["total"] += row.quantity
        if row.in_transit > 0:
            entry["in_transit"][row.warehouse_id] = row.in_transit
            entry["total_in_transit"] += row.in_transit
        # Reserved from assembly requests
        res = all_reserved.get(row.warehouse_id, {}).get(row.nomenclature_id, 0)
        if res > 0:
            entry["reserved"][row.warehouse_id] = res
            entry["total_reserved"] += res

    # Compute available
    for entry in summary.values():
        entry["total_available"] = max(0, entry["total"] - entry["total_reserved"])

    return [v for v in summary.values() if v["total"] > 0 or v["total_in_transit"] > 0]


async def update_cost_price(db: AsyncSession, project_id: int, stock_id: int, cost_price) -> WarehouseStock | None:
    """Set manual cost_price on a warehouse_stock row."""
    result = await db.execute(
        select(WarehouseStock).where(
            WarehouseStock.id == stock_id,
            WarehouseStock.project_id == project_id,
        )
    )
    stock = result.scalar_one_or_none()
    if not stock:
        return None

    stock.cost_price = cost_price
    stock.updated_at = utcnow()
    await db.commit()
    await db.refresh(stock)
    return stock
