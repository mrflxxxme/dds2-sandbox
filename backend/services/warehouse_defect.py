"""
Warehouse defect management — mark, receive, writeoff, recover defective goods.
"""

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.warehouse import MovementType, WarehouseStock
from backend.services.warehouse_crud import get_warehouse
from backend.services.warehouse_stock_engine import _resolve_barcode, _update_stock

logger = logging.getLogger(__name__)


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
