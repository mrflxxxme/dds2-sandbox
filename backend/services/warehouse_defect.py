"""
Warehouse defect management — mark, receive, writeoff, recover defective goods.

mark_defect_bulk → creates DefectMarkOperation(status=ACCEPTED) document (DM-N).
receive_defect_bulk → creates InboundReceipt(is_defect=true, status=ACCEPTED) document.
writeoff_defect_bulk → creates OutboundShipment(is_defect=true, status=SHIPPED) document.
recover_defect_bulk → internal stock rebalance (no document).
"""

import logging
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.warehouse import (
    DefectMarkOperation,
    DefectMarkOperationItem,
    DefectMarkStatus,
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    MovementType,
    OutboundShipment,
    OutboundShipmentItem,
    OutboundStatus,
    StockMovement,
    WarehouseStock,
)
from backend.services.warehouse_crud import get_warehouse
from backend.services.warehouse_stock_engine import (
    _next_number,
    _resolve_barcode,
    _resolve_barcodes_batch,
    _update_stock,
)

logger = logging.getLogger(__name__)


# ─── Bulk defect operation config (mark / recover — no document) ──────────

_BULK_CONFIG: dict[str, tuple[MovementType, int, int]] = {
    # operation -> (movement_type, delta_sign, defect_delta_sign)
    "recover": (MovementType.DEFECT_RECOVER, 1, -1),
}

_DEFECT_MOVEMENT_TYPES = {
    MovementType.DEFECT_MARK.value,
    MovementType.DEFECT_RECEIVE.value,
    MovementType.DEFECT_WRITEOFF.value,
    MovementType.DEFECT_RECOVER.value,
}


async def _bulk_defect_op(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
    operation: str,
) -> dict:
    """
    Shared bulk executor for mark/recover (no document created).

    Each item runs in a nested savepoint — a single failure does not abort the batch.
    Returns: {status, processed, failed, errors}.
    """
    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")

    movement_type, delta_sign, defect_sign = _BULK_CONFIG[operation]
    reason = payload.get("reason")
    items = payload.get("items") or []

    processed = 0
    errors: list[dict] = []

    for item in items:
        if isinstance(item, dict):
            barcode = item.get("barcode")
            qty = item.get("quantity")
        else:
            barcode = getattr(item, "barcode", None)
            qty = getattr(item, "quantity", None)

        if not barcode or not qty or qty <= 0:
            errors.append({"barcode": barcode or "", "error": "Invalid barcode or quantity"})
            continue

        try:
            async with db.begin_nested():
                nom = await _resolve_barcode(db, project_id, barcode)
                await _update_stock(
                    db,
                    project_id=project_id,
                    warehouse_id=warehouse_id,
                    nomenclature_id=nom.id,
                    barcode=barcode,
                    delta=delta_sign * qty,
                    defect_delta=defect_sign * qty,
                    movement_type=movement_type,
                    reference_type="DEFECT",
                    comment=reason,
                )
            processed += 1
        except ValueError as e:
            errors.append({"barcode": barcode, "error": str(e)})
        except Exception as e:
            logger.exception("bulk defect %s failed for barcode %s", operation, barcode)
            errors.append({"barcode": barcode, "error": str(e)})

    await db.commit()

    failed = len(errors)
    if processed and not failed:
        status = "ok"
    elif processed and failed:
        status = "partial"
    else:
        status = "error"

    return {
        "status": status,
        "processed": processed,
        "failed": failed,
        "errors": errors,
    }


async def recover_defect_bulk(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """Bulk: restore defective goods to good stock (repaired). No document (per-item audit only)."""
    return await _bulk_defect_op(db, project_id, warehouse_id, payload, "recover")


async def mark_defect_bulk(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """
    Mark good stock as defective → creates DefectMarkOperation(status=ACCEPTED) document (DM-N).

    Each item in a nested savepoint — a single failure does not abort the batch.
    If nothing succeeds, no document is created.
    Returns: {status, processed, failed, errors, operation_id?, number?}.
    """
    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")

    reason = (payload.get("reason") or "").strip() or None
    valid_items, errors = _prepare_valid_items(payload.get("items") or [])
    if not valid_items:
        return {
            "status": "error",
            "processed": 0,
            "failed": len(errors),
            "errors": errors,
            "operation_id": None,
        }

    number = await _next_number(db, project_id, "DM", DefectMarkOperation)
    operation = DefectMarkOperation(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=number,
        status=DefectMarkStatus.ACCEPTED,
        actual_date=date.today(),
        reason=reason,
    )
    db.add(operation)
    await db.flush()

    processed_items: list[dict] = []
    for data in valid_items:
        barcode = data["barcode"]
        qty = data["quantity"]
        try:
            async with db.begin_nested():
                nom = await _resolve_barcode(db, project_id, barcode)
                await _update_stock(
                    db,
                    project_id=project_id,
                    warehouse_id=warehouse_id,
                    nomenclature_id=nom.id,
                    barcode=barcode,
                    delta=-qty,
                    defect_delta=qty,
                    movement_type=MovementType.DEFECT_MARK,
                    reference_type="MARK_OP",
                    reference_id=operation.id,
                    comment=reason,
                )
                db.add(
                    DefectMarkOperationItem(
                        project_id=project_id,
                        operation_id=operation.id,
                        nomenclature_id=nom.id,
                        barcode=barcode,
                        quantity=qty,
                    )
                )
            processed_items.append({"barcode": barcode, "quantity": qty})
        except ValueError as e:
            errors.append({"barcode": barcode, "error": str(e)})
        except Exception as e:
            logger.exception("bulk mark defect failed for barcode %s", barcode)
            errors.append({"barcode": barcode, "error": str(e)})

    if not processed_items:
        # Nothing applied — soft-delete the empty document (DefectMarkOperation has SoftDeleteMixin)
        operation.soft_delete()
        await db.commit()
        return {
            "status": "error",
            "processed": 0,
            "failed": len(errors),
            "errors": errors,
            "operation_id": None,
        }

    await db.commit()
    await db.refresh(operation)

    failed = len(errors)
    status = "ok" if failed == 0 else "partial"

    return {
        "status": status,
        "processed": len(processed_items),
        "failed": failed,
        "errors": errors,
        "operation_id": operation.id,
        "number": operation.number,
    }


async def list_mark_operations(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
) -> list[DefectMarkOperation]:
    """List defect-mark operations (DM-N) for a warehouse."""
    result = await db.execute(
        select(DefectMarkOperation)
        .options(selectinload(DefectMarkOperation.items))
        .where(
            DefectMarkOperation.project_id == project_id,
            DefectMarkOperation.warehouse_id == warehouse_id,
            DefectMarkOperation.is_deleted == False,  # noqa: E712
        )
        .order_by(DefectMarkOperation.id.desc())
        .limit(500)
    )
    return list(result.scalars().all())


async def get_mark_operation(
    db: AsyncSession,
    project_id: int,
    operation_id: int,
) -> DefectMarkOperation | None:
    """Fetch a single DefectMarkOperation by id."""
    result = await db.execute(
        select(DefectMarkOperation)
        .options(selectinload(DefectMarkOperation.items))
        .where(
            DefectMarkOperation.id == operation_id,
            DefectMarkOperation.project_id == project_id,
            DefectMarkOperation.is_deleted == False,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def cancel_mark_operation(
    db: AsyncSession,
    project_id: int,
    operation_id: int,
) -> dict:
    """
    Cancel a DefectMarkOperation: revert each item (defect → back to good).
    Status ACCEPTED → CANCELLED. Idempotent guard: cannot cancel CANCELLED.
    """
    result = await db.execute(
        select(DefectMarkOperation)
        .options(selectinload(DefectMarkOperation.items))
        .where(
            DefectMarkOperation.id == operation_id,
            DefectMarkOperation.project_id == project_id,
            DefectMarkOperation.is_deleted == False,  # noqa: E712
        )
    )
    operation = result.scalar_one_or_none()
    if operation is None:
        raise ValueError("Operation not found")

    if operation.status != DefectMarkStatus.ACCEPTED:
        raise ValueError(f"Cannot cancel operation in status {operation.status}")

    for item in operation.items:
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=operation.warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=item.quantity,
            defect_delta=-item.quantity,
            movement_type=MovementType.DEFECT_RECOVER,
            reference_type="MARK_OP_CANCEL",
            reference_id=operation.id,
            comment=f"Отмена {operation.number}",
        )

    operation.status = DefectMarkStatus.CANCELLED
    await db.commit()

    return {
        "status": "ok",
        "operation_id": operation.id,
        "number": operation.number,
        "reverted_items": len(operation.items),
    }


# ─── Document-creating operations (receive / writeoff) ────────────────────


def _prepare_valid_items(items: list) -> tuple[list[dict], list[dict]]:
    """Normalize items, separate valid from invalid. Returns (valid, errors)."""
    valid: list[dict] = []
    errors: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            barcode = item.get("barcode")
            qty = item.get("quantity")
        else:
            barcode = getattr(item, "barcode", None)
            qty = getattr(item, "quantity", None)
        if not barcode or not qty or qty <= 0:
            errors.append({"barcode": barcode or "", "error": "Invalid barcode or quantity"})
            continue
        valid.append({"barcode": barcode, "quantity": int(qty)})
    return valid, errors


async def receive_defect_bulk(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """
    Receive defective goods → InboundReceipt(is_defect=true, status=ACCEPTED).
    All-or-nothing: any barcode/stock failure rolls back the whole document.
    """
    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")

    reason = (payload.get("reason") or "").strip()
    if not reason:
        raise ValueError("Reason is required")

    valid_items, errors = _prepare_valid_items(payload.get("items") or [])
    if not valid_items:
        return {
            "status": "error",
            "processed": 0,
            "failed": len(errors),
            "errors": errors,
            "receipt_id": None,
        }

    barcode_map = await _resolve_barcodes_batch(db, project_id, [i["barcode"] for i in valid_items])

    number = await _next_number(db, project_id, "IN", InboundReceipt)
    receipt = InboundReceipt(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=number,
        status=InboundStatus.ACCEPTED,
        actual_date=date.today(),
        is_defect=True,
        defect_reason=reason,
    )
    db.add(receipt)
    await db.flush()

    for data in valid_items:
        nom = barcode_map[data["barcode"]]
        qty = data["quantity"]
        db.add(
            InboundReceiptItem(
                project_id=project_id,
                receipt_id=receipt.id,
                nomenclature_id=nom.id,
                barcode=data["barcode"],
                expected_qty=qty,
                actual_qty=qty,
            )
        )
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=warehouse_id,
            nomenclature_id=nom.id,
            barcode=data["barcode"],
            delta=0,
            defect_delta=qty,
            movement_type=MovementType.DEFECT_RECEIVE,
            reference_type="RECEIPT",
            reference_id=receipt.id,
            comment=reason,
        )

    await db.commit()

    return {
        "status": "ok" if not errors else "partial",
        "processed": len(valid_items),
        "failed": len(errors),
        "errors": errors,
        "receipt_id": receipt.id,
        "number": receipt.number,
    }


async def writeoff_defect_bulk(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """
    Write off defective goods → OutboundShipment(is_defect=true, status=SHIPPED).
    Deducts defect_quantity for each item. All-or-nothing.
    """
    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")

    reason = (payload.get("reason") or "").strip()
    if not reason:
        raise ValueError("Reason is required")

    valid_items, errors = _prepare_valid_items(payload.get("items") or [])
    if not valid_items:
        return {
            "status": "error",
            "processed": 0,
            "failed": len(errors),
            "errors": errors,
            "shipment_id": None,
        }

    barcode_map = await _resolve_barcodes_batch(db, project_id, [i["barcode"] for i in valid_items])

    number = await _next_number(db, project_id, "OUT", OutboundShipment)
    shipment = OutboundShipment(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=number,
        status=OutboundStatus.SHIPPED,
        shipped_date=date.today(),
        is_defect=True,
        defect_reason=reason,
    )
    db.add(shipment)
    await db.flush()

    for data in valid_items:
        nom = barcode_map[data["barcode"]]
        qty = data["quantity"]
        db.add(
            OutboundShipmentItem(
                project_id=project_id,
                shipment_id=shipment.id,
                nomenclature_id=nom.id,
                barcode=data["barcode"],
                quantity=qty,
            )
        )
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=warehouse_id,
            nomenclature_id=nom.id,
            barcode=data["barcode"],
            delta=0,
            defect_delta=-qty,
            movement_type=MovementType.DEFECT_WRITEOFF,
            reference_type="SHIPMENT",
            reference_id=shipment.id,
            comment=reason,
        )

    await db.commit()

    return {
        "status": "ok" if not errors else "partial",
        "processed": len(valid_items),
        "failed": len(errors),
        "errors": errors,
        "shipment_id": shipment.id,
        "number": shipment.number,
    }


# ─── Legacy per-item movement deletion (for old DEFECT_* without document) ─


async def delete_defect_movement(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    movement_id: int,
) -> dict:
    """
    Hard-delete a defect stock movement and revert its stock effect.
    Used for legacy movements that weren't created as part of a receipt/shipment document.
    """
    result = await db.execute(
        select(StockMovement).where(
            StockMovement.id == movement_id,
            StockMovement.project_id == project_id,
            StockMovement.warehouse_id == warehouse_id,
        )
    )
    movement = result.scalar_one_or_none()
    if movement is None:
        raise ValueError("Movement not found")

    if movement.movement_type not in _DEFECT_MOVEMENT_TYPES:
        raise ValueError(f"Cannot delete movement of type {movement.movement_type}")

    await _update_stock(
        db,
        project_id=project_id,
        warehouse_id=warehouse_id,
        nomenclature_id=movement.nomenclature_id,
        barcode=movement.barcode,
        delta=-movement.quantity,
        defect_delta=-movement.defect_delta,
        movement_type=MovementType(movement.movement_type),
        reference_type="DEFECT_REVERT",
        reference_id=movement.id,
        comment=f"Revert movement #{movement.id}",
    )

    reverted_type = movement.movement_type
    qty = abs(movement.defect_delta) or abs(movement.quantity)

    await db.delete(movement)  # no-soft-delete-check: StockMovement has no SoftDeleteMixin (audit log pattern)
    await db.commit()

    return {"status": "ok", "reverted": reverted_type, "quantity": qty}


# ─── Single-item endpoints (backward compat) ──────────────────────────────


async def mark_defect(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """Single-item mark: creates a 1-item DefectMarkOperation document."""
    return await mark_defect_bulk(
        db,
        project_id,
        warehouse_id,
        {
            "reason": payload.get("reason") or "—",
            "items": [{"barcode": payload["barcode"], "quantity": payload["quantity"]}],
        },
    )


async def receive_defect(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """Single-item receive: creates a 1-item InboundReceipt document."""
    return await receive_defect_bulk(
        db,
        project_id,
        warehouse_id,
        {
            "reason": payload.get("reason") or "—",
            "items": [{"barcode": payload["barcode"], "quantity": payload["quantity"]}],
        },
    )


async def writeoff_defect(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """Single-item writeoff: creates a 1-item OutboundShipment document."""
    return await writeoff_defect_bulk(
        db,
        project_id,
        warehouse_id,
        {
            "reason": payload.get("reason") or "—",
            "items": [{"barcode": payload["barcode"], "quantity": payload["quantity"]}],
        },
    )


async def recover_defect(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")

    nom = await _resolve_barcode(db, project_id, payload["barcode"])
    await _update_stock(
        db,
        project_id=project_id,
        warehouse_id=warehouse_id,
        nomenclature_id=nom.id,
        barcode=payload["barcode"],
        delta=payload["quantity"],
        defect_delta=-payload["quantity"],
        movement_type=MovementType.DEFECT_RECOVER,
        reference_type="DEFECT",
        comment=payload.get("reason"),
    )
    await db.commit()
    return {
        "status": "ok",
        "operation": "recover_defect",
        "barcode": payload["barcode"],
        "quantity": payload["quantity"],
    }


# ─── Queries ──────────────────────────────────────────────────────────────


async def get_defect_stock(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
) -> list[dict]:
    """Get defective stock for a warehouse (defect_quantity > 0)."""
    result = await db.execute(
        select(WarehouseStock)
        .where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id == warehouse_id,
            WarehouseStock.defect_quantity > 0,
        )
        .order_by(WarehouseStock.barcode)
        .limit(500)
    )
    rows = result.scalars().all()

    return [
        {
            "id": row.id,
            "project_id": row.project_id,
            "warehouse_id": row.warehouse_id,
            "nomenclature_id": row.nomenclature_id,
            "barcode": row.barcode,
            "defect_quantity": row.defect_quantity,
            "defect_in_transit": row.defect_in_transit,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


async def get_defect_receipts(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
) -> list[InboundReceipt]:
    """List defect receipts for a warehouse."""
    result = await db.execute(
        select(InboundReceipt)
        .options(selectinload(InboundReceipt.items))
        .where(
            InboundReceipt.project_id == project_id,
            InboundReceipt.warehouse_id == warehouse_id,
            InboundReceipt.is_defect.is_(True),
            InboundReceipt.is_deleted == False,  # noqa: E712
        )
        .order_by(InboundReceipt.id.desc())
        .limit(500)
    )
    return list(result.scalars().all())


async def get_defect_shipments(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
) -> list[OutboundShipment]:
    """List defect writeoff shipments for a warehouse."""
    result = await db.execute(
        select(OutboundShipment)
        .options(selectinload(OutboundShipment.items))
        .where(
            OutboundShipment.project_id == project_id,
            OutboundShipment.warehouse_id == warehouse_id,
            OutboundShipment.is_defect.is_(True),
            OutboundShipment.is_deleted == False,  # noqa: E712
        )
        .order_by(OutboundShipment.id.desc())
        .limit(500)
    )
    return list(result.scalars().all())


async def get_defect_summary(
    db: AsyncSession,
    project_id: int,
) -> list[dict]:
    """Summary defective stock across all warehouses."""
    result = await db.execute(
        select(WarehouseStock)
        .where(
            WarehouseStock.project_id == project_id,
            or_(WarehouseStock.defect_quantity > 0, WarehouseStock.defect_in_transit > 0),
        )
        .order_by(WarehouseStock.barcode)
        .limit(500)
    )
    rows = result.scalars().all()

    summary: dict[int, dict] = {}
    for row in rows:
        key = row.nomenclature_id
        if key not in summary:
            summary[key] = {
                "nomenclature_id": row.nomenclature_id,
                "barcode": row.barcode,
                "warehouses": {},
                "total_defect": 0,
                "total_defect_in_transit": 0,
            }
        entry = summary[key]
        if row.defect_quantity > 0:
            entry["warehouses"][row.warehouse_id] = row.defect_quantity
            entry["total_defect"] += row.defect_quantity
        if row.defect_in_transit > 0:
            entry["total_defect_in_transit"] += row.defect_in_transit

    return list(summary.values())
