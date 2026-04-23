"""
FBO Supply returns — handle the unaccepted qty delta for partial acceptance.

When WB accepts only N of M items, the remaining (M-N) qty must be accounted for:
  - GOODS    → physically returned to source warehouse as sellable stock
  - DEFECT   → returned as defect (is_defect receipt)
  - UTILIZED → WB utilized the goods, nothing came back — write-off adjustment

Implementation: creates InboundReceipt (GOODS/DEFECT) or StockMovement
(UTILIZED) and flags WbFboSupply.return_processed_at so it drops out of
the partial-acceptance summary/filter.
"""

from __future__ import annotations

import enum
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.warehouse import (
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    MovementType,
    OutboundShipment,
    OutboundShipmentItem,
    OutboundStatus,
    Warehouse,
)
from backend.models.wb_fbo import WbFboSupply
from backend.services.warehouse_stock_engine import (
    _next_number,
    _resolve_barcodes_batch,
    _update_stock,
)
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


class FboReturnType(str, enum.Enum):
    GOODS = "GOODS"  # back to source warehouse as sellable
    DEFECT = "DEFECT"  # back to source warehouse as defect
    UTILIZED = "UTILIZED"  # WB utilized, no physical return — write-off


async def process_fbo_return(
    db: AsyncSession,
    project_id: int,
    supply_id: int,
    items: list[dict[str, Any]],
    warehouse_id: int | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    """
    Handle unaccepted qty for a WbFboSupply — mixed mode (per-item return_type).

    items: [{barcode, quantity, return_type: GOODS|DEFECT|UTILIZED}, ...]
    Rows with GOODS/DEFECT are grouped into a single InboundReceipt on
    `warehouse_id`. UTILIZED rows are logged-only (no stock change).

    Returns: {receipt_id, receipt_number, supply_id}. receipt_* is None if
    there are no GOODS/DEFECT rows (pure utilization case).
    """
    supply = await _load_supply(db, project_id, supply_id)
    if supply is None:
        raise ValueError("FBO Supply not found")

    if supply.return_processed_at is not None:
        raise ValueError("Return already processed for this supply")

    _validate_items_against_delta(supply, items)

    # Partition items by disposition.
    stock_items = [i for i in items if i.get("return_type") in ("GOODS", "DEFECT")]
    utilize_items = [i for i in items if i.get("return_type") == "UTILIZED"]

    unknown_types = [i.get("return_type") for i in items if i.get("return_type") not in ("GOODS", "DEFECT", "UTILIZED")]
    if unknown_types:
        raise ValueError(f"Unknown return_type: {unknown_types[0]}")

    result: dict[str, Any] = {"receipt_id": None, "receipt_number": None}

    if stock_items:
        if warehouse_id is None:
            raise ValueError("warehouse_id is required when items include GOODS/DEFECT")
        wh = (
            await db.execute(
                select(Warehouse).where(
                    Warehouse.id == warehouse_id,
                    Warehouse.project_id == project_id,
                    Warehouse.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if wh is None:
            raise ValueError("Warehouse not found")

        receipt = await _create_return_receipt(
            db,
            project_id=project_id,
            warehouse_id=warehouse_id,
            supply=supply,
            items=stock_items,
            comment=comment,
        )
        result = {"receipt_id": receipt.id, "receipt_number": receipt.number}

    if utilize_items:
        await _log_utilization(db, project_id, supply, utilize_items, comment)

    # supply.return_type: GOODS / DEFECT / UTILIZED for homogeneous returns,
    # 'MIXED' when the user split the delta across stock + utilization.
    has_stock = bool(stock_items)
    has_utilize = bool(utilize_items)
    if has_stock and has_utilize:
        supply.return_type = "MIXED"
    elif has_utilize:
        supply.return_type = "UTILIZED"
    elif stock_items:
        # All GOODS, all DEFECT, or mixed GOODS+DEFECT — prefer DEFECT flag
        # only when ALL stock rows are defect.
        all_defect = all(i.get("return_type") == "DEFECT" for i in stock_items)
        supply.return_type = "DEFECT" if all_defect else "GOODS"

    supply.return_processed_at = utcnow()
    supply.return_qty = sum(int(r.get("quantity") or 0) for r in items)
    await db.commit()
    result["supply_id"] = supply.id
    return result


async def _load_supply(db: AsyncSession, project_id: int, supply_id: int) -> WbFboSupply | None:
    result = await db.execute(
        select(WbFboSupply)
        .where(
            WbFboSupply.id == supply_id,
            WbFboSupply.project_id == project_id,
        )
        .options(selectinload(WbFboSupply.items))
    )
    return result.scalar_one_or_none()


def _validate_items_against_delta(supply: WbFboSupply, items: list[dict[str, Any]]) -> None:
    """Ensure requested qty per barcode does not exceed (quantity - accepted_qty)."""
    if not items:
        raise ValueError("items must not be empty")

    # sum per-item delta from supply items (what WB rejected per SKU)
    delta_by_barcode: dict[str, int] = {
        it.barcode: max(0, (it.quantity or 0) - (it.accepted_qty or 0)) for it in supply.items
    }

    for row in items:
        bc = row.get("barcode")
        qty = int(row.get("quantity", 0) or 0)
        if not bc or qty <= 0:
            raise ValueError("items[*].barcode and items[*].quantity > 0 are required")
        available = delta_by_barcode.get(bc, 0)
        if qty > available:
            raise ValueError(f"qty {qty} > unaccepted delta {available} for barcode {bc}")


async def _create_return_receipt(
    db: AsyncSession,
    *,
    project_id: int,
    warehouse_id: int,
    supply: WbFboSupply,
    items: list[dict[str, Any]],
    comment: str | None,
) -> InboundReceipt:
    """Create and immediately accept an InboundReceipt on the source warehouse.

    Items may mix GOODS and DEFECT — decide per row how to apply to stock.
    Receipt header is_defect/defect_reason reflect the mix: True only when
    every row is DEFECT (keeps single-type receipts unchanged).
    """
    all_defect = all(row.get("return_type") == "DEFECT" for row in items)
    number = await _next_number(db, project_id, "IN", InboundReceipt)
    defect_reason = "Недоприёмка WB (брак)" if all_defect else None
    auto_comment = f"Возврат по поставке FBW-{supply.wb_supply_id}"
    if comment:
        auto_comment = f"{auto_comment}. {comment}"

    receipt = InboundReceipt(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=number,
        status=InboundStatus.ACCEPTED,
        planned_date=utcnow().date(),
        actual_date=utcnow().date(),
        comment=auto_comment,
        is_defect=all_defect,
        defect_reason=defect_reason,
    )
    db.add(receipt)
    await db.flush()

    barcodes = [row["barcode"] for row in items]
    barcode_map = await _resolve_barcodes_batch(db, project_id, barcodes)

    for row in items:
        bc = row["barcode"]
        qty = int(row["quantity"])
        is_defect = row.get("return_type") == "DEFECT"
        nom = barcode_map.get(bc)
        if nom is None:
            raise ValueError(f"Nomenclature not found for barcode {bc}")
        db.add(
            InboundReceiptItem(
                project_id=project_id,
                receipt_id=receipt.id,
                nomenclature_id=nom.id,
                barcode=bc,
                expected_qty=qty,
                actual_qty=qty,
            )
        )
        if is_defect:
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=warehouse_id,
                nomenclature_id=nom.id,
                barcode=bc,
                delta=0,
                defect_delta=qty,
                movement_type=MovementType.DEFECT_RECEIVE,
                reference_type="RECEIPT",
                reference_id=receipt.id,
                comment=f"Возврат-брак FBW-{supply.wb_supply_id}",
            )
        else:
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=warehouse_id,
                nomenclature_id=nom.id,
                barcode=bc,
                delta=qty,
                movement_type=MovementType.INBOUND,
                reference_type="RECEIPT",
                reference_id=receipt.id,
                comment=f"Возврат годных FBW-{supply.wb_supply_id}",
            )

    await db.flush()
    return receipt


async def process_fbo_excess(
    db: AsyncSession,
    project_id: int,
    supply_id: int,
    items: list[dict[str, Any]],
    warehouse_id: int,
    comment: str | None = None,
) -> dict[str, Any]:
    """
    Handle excess (overshoot) qty for a WbFboSupply: WB accepted MORE than
    we shipped per barcode. User chooses a warehouse to write off the delta
    from. Creates an OutboundShipment + items + stock adjustment, then
    flags supply.excess_processed_at.
    """
    supply = await _load_supply(db, project_id, supply_id)
    if supply is None:
        raise ValueError("FBO Supply not found")
    if supply.excess_processed_at is not None:
        raise ValueError("Excess already processed for this supply")

    _validate_items_against_excess(supply, items)

    wh = (
        await db.execute(
            select(Warehouse).where(
                Warehouse.id == warehouse_id,
                Warehouse.project_id == project_id,
                Warehouse.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if wh is None:
        raise ValueError("Warehouse not found")

    barcodes = [row["barcode"] for row in items]
    barcode_map = await _resolve_barcodes_batch(db, project_id, barcodes)

    number = await _next_number(db, project_id, "OUT", OutboundShipment)
    auto_comment = f"Излишек по поставке FBW-{supply.wb_supply_id} (WB принял больше отправленного)"
    if comment:
        auto_comment = f"{auto_comment}. {comment}"

    shipment = OutboundShipment(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=number,
        status=OutboundStatus.SHIPPED,
        shipped_date=utcnow().date(),
        comment=auto_comment,
    )
    db.add(shipment)
    await db.flush()

    total_qty = 0
    for row in items:
        bc = row["barcode"]
        qty = int(row["quantity"])
        nom = barcode_map.get(bc)
        if nom is None:
            raise ValueError(f"Nomenclature not found for barcode {bc}")
        db.add(
            OutboundShipmentItem(
                project_id=project_id,
                shipment_id=shipment.id,
                nomenclature_id=nom.id,
                barcode=bc,
                quantity=qty,
            )
        )
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=warehouse_id,
            nomenclature_id=nom.id,
            barcode=bc,
            delta=-qty,
            movement_type=MovementType.OUTBOUND,
            reference_type="SHIPMENT",
            reference_id=shipment.id,
            comment=f"Излишек FBW-{supply.wb_supply_id}",
        )
        total_qty += qty

    supply.excess_processed_at = utcnow()
    supply.excess_qty = total_qty
    await db.commit()

    return {
        "supply_id": supply.id,
        "shipment_id": shipment.id,
        "shipment_number": shipment.number,
        "total_qty": total_qty,
    }


def _validate_items_against_excess(supply: WbFboSupply, items: list[dict[str, Any]]) -> None:
    """Excess delta = max(0, accepted - quantity) per barcode."""
    if not items:
        raise ValueError("items must not be empty")
    excess_by_barcode: dict[str, int] = {
        it.barcode: max(0, (it.accepted_qty or 0) - (it.quantity or 0)) for it in supply.items
    }
    for row in items:
        bc = row.get("barcode")
        qty = int(row.get("quantity", 0) or 0)
        if not bc or qty <= 0:
            raise ValueError("items[*].barcode and items[*].quantity > 0 are required")
        available = excess_by_barcode.get(bc, 0)
        if qty > available:
            raise ValueError(f"qty {qty} > excess delta {available} for barcode {bc}")


async def _log_utilization(
    db: AsyncSession,
    project_id: int,
    supply: WbFboSupply,
    items: list[dict[str, Any]],
    comment: str | None,
) -> None:
    """UTILIZED: no stock change, just mark supply.return_processed_at and log."""
    total_qty = sum(int(r["quantity"]) for r in items)
    logger.info(
        "fbo_return.utilized",
        extra={
            "project_id": project_id,
            "supply_id": supply.id,
            "wb_supply_id": supply.wb_supply_id,
            "total_qty": total_qty,
            "comment": comment,
        },
    )
