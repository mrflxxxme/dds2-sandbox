"""
Warehouse outbound — shipments and stock transfers.
"""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.warehouse import (
    MovementType,
    OutboundShipment,
    OutboundShipmentItem,
    OutboundStatus,
    StockTransfer,
    StockTransferItem,
    TransferStatus,
    WarehouseStock,
    WarehouseType,
)
from backend.services.warehouse_crud import get_warehouse
from backend.services.warehouse_stock_engine import (
    _next_number,
    _resolve_barcode,
    _update_stock,
)
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


# ─── Outbound Shipments (Отгрузка) ────────────────────────────────────────


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
            project_id=project_id,
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


# ─── Stock Transfers (Перемещение) ─────────────────────────────────────────


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
            project_id=project_id,
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
