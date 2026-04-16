"""
Warehouse defect management — mark, receive, writeoff, recover defective goods.
"""

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.warehouse import MovementType, StockMovement, WarehouseStock
from backend.services.warehouse_crud import get_warehouse
from backend.services.warehouse_stock_engine import _resolve_barcode, _update_stock

logger = logging.getLogger(__name__)


# ─── Bulk defect operation config ────────────────────────────────────────

_BULK_CONFIG: dict[str, tuple[MovementType, int, int]] = {
    # operation -> (movement_type, delta_sign, defect_delta_sign)
    # final delta = sign * quantity
    "mark": (MovementType.DEFECT_MARK, -1, 1),
    "receive": (MovementType.DEFECT_RECEIVE, 0, 1),
    "writeoff": (MovementType.DEFECT_WRITEOFF, 0, -1),
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
    Shared bulk-defect executor.

    - Validates warehouse once (not per-item).
    - Iterates items; each item runs inside a nested savepoint so a single
      failure (bad barcode, insufficient stock, etc.) does not abort the
      whole batch.
    - Commits the outer transaction once at the end.

    Returns: {status, processed, failed, errors}
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
        except Exception as e:  # — bulk op: collect and continue
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


async def mark_defect_bulk(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """Bulk: mark existing good stock as defective."""
    return await _bulk_defect_op(db, project_id, warehouse_id, payload, "mark")


async def receive_defect_bulk(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """Bulk: receive defective goods from outside (WB returns, etc.)."""
    return await _bulk_defect_op(db, project_id, warehouse_id, payload, "receive")


async def writeoff_defect_bulk(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """Bulk: write off (destroy) defective goods."""
    return await _bulk_defect_op(db, project_id, warehouse_id, payload, "writeoff")


async def recover_defect_bulk(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """Bulk: restore defective goods to good stock (repaired)."""
    return await _bulk_defect_op(db, project_id, warehouse_id, payload, "recover")


async def delete_defect_movement(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    movement_id: int,
) -> dict:
    """
    Hard-delete a defect stock movement and revert its stock effect.

    Only DEFECT_* movements are deletable here (transit/receipt/shipment audit
    records stay untouched). StockMovement has no SoftDeleteMixin, so hard
    delete is intentional.
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

    # Inverse adjustment — same engine path so balance checks apply.
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

    await db.delete(movement)
    await db.commit()

    return {"status": "ok", "reverted": reverted_type, "quantity": qty}


async def mark_defect(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """
    Mark existing good stock as defective.
    quantity -= qty, defect_quantity += qty.
    """
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
        delta=-payload["quantity"],
        defect_delta=payload["quantity"],
        movement_type=MovementType.DEFECT_MARK,
        reference_type="DEFECT",
        comment=payload.get("reason"),
    )

    await db.commit()
    return {"status": "ok", "operation": "mark_defect", "barcode": payload["barcode"], "quantity": payload["quantity"]}


async def receive_defect(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """
    Receive defective goods from outside (WB returns, etc.).
    defect_quantity += qty. quantity unchanged.
    """
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
        delta=0,
        defect_delta=payload["quantity"],
        movement_type=MovementType.DEFECT_RECEIVE,
        reference_type="DEFECT",
        comment=payload.get("reason"),
    )

    await db.commit()
    return {
        "status": "ok",
        "operation": "receive_defect",
        "barcode": payload["barcode"],
        "quantity": payload["quantity"],
    }


async def writeoff_defect(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """
    Write off (destroy) defective goods.
    defect_quantity -= qty.
    """
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
        delta=0,
        defect_delta=-payload["quantity"],
        movement_type=MovementType.DEFECT_WRITEOFF,
        reference_type="DEFECT",
        comment=payload.get("reason"),
    )

    await db.commit()
    return {
        "status": "ok",
        "operation": "writeoff_defect",
        "barcode": payload["barcode"],
        "quantity": payload["quantity"],
    }


async def recover_defect(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    payload: dict,
) -> dict:
    """
    Restore defective goods to good stock (repaired).
    defect_quantity -= qty, quantity += qty.
    """
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
