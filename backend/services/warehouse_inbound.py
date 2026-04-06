"""
Warehouse inbound — receipts (priemka).
"""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.warehouse import (
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    MovementType,
)
from backend.services.warehouse_crud import get_warehouse
from backend.services.warehouse_stock_engine import (
    _next_number,
    _resolve_barcodes_batch,
    _update_stock,
)

logger = logging.getLogger(__name__)


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
        .limit(500)
    )
    return list(result.scalars().all())


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

    # Add items — resolve barcodes in one batch query
    items_data = payload.get("items", [])
    barcode_map = await _resolve_barcodes_batch(db, project_id, [d["barcode"] for d in items_data])
    for item_data in items_data:
        nom = barcode_map[item_data["barcode"]]
        item = InboundReceiptItem(
            project_id=project_id,
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

            barcode_map = await _resolve_barcodes_batch(db, project_id, [d["barcode"] for d in payload["items"]])
            for item_data in payload["items"]:
                nom = barcode_map[item_data["barcode"]]
                item = InboundReceiptItem(
                    project_id=project_id,
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

    # Auto-fill actual_qty from expected_qty if not set
    for item in receipt.items:
        if item.actual_qty <= 0 and item.expected_qty > 0:
            item.actual_qty = item.expected_qty

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
