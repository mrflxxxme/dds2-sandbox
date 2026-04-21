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
    return_type: FboReturnType,
    warehouse_id: int | None,
    items: list[dict[str, Any]],
    comment: str | None = None,
) -> dict[str, Any]:
    """
    Handle unaccepted qty for a WbFboSupply.

    items: [{barcode: str, quantity: int}, ...] — qty to return per SKU.
    warehouse_id: required for GOODS/DEFECT (source warehouse). Ignored for UTILIZED.

    Returns: {receipt_id, receipt_number, supply_id} (receipt_* = None for UTILIZED).
    """
    supply = await _load_supply(db, project_id, supply_id)
    if supply is None:
        raise ValueError("FBO Supply not found")

    _validate_items_against_delta(supply, items)

    if return_type in (FboReturnType.GOODS, FboReturnType.DEFECT):
        if warehouse_id is None:
            raise ValueError("warehouse_id is required for GOODS/DEFECT return")
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
            items=items,
            is_defect=return_type == FboReturnType.DEFECT,
            comment=comment,
        )
        result = {"receipt_id": receipt.id, "receipt_number": receipt.number}
    elif return_type == FboReturnType.UTILIZED:
        await _log_utilization(db, project_id, supply, items, comment)
        result = {"receipt_id": None, "receipt_number": None}
    else:
        raise ValueError(f"Unknown return_type: {return_type}")

    supply.return_processed_at = utcnow()
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
    is_defect: bool,
    comment: str | None,
) -> InboundReceipt:
    """Create and immediately accept an InboundReceipt on the source warehouse."""
    number = await _next_number(db, project_id, "IN", InboundReceipt)
    defect_reason = "Недоприёмка WB (брак)" if is_defect else None
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
        is_defect=is_defect,
        defect_reason=defect_reason,
    )
    db.add(receipt)
    await db.flush()

    barcodes = [row["barcode"] for row in items]
    barcode_map = await _resolve_barcodes_batch(db, project_id, barcodes)

    for row in items:
        bc = row["barcode"]
        qty = int(row["quantity"])
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
        # Immediately apply to stock (defect_delta for defect returns,
        # regular quantity delta for goods returns).
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
