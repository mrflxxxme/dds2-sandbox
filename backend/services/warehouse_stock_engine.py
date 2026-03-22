"""
Warehouse stock engine — stock balance updates, movements, adjustments, summaries.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.cost import Nomenclature
from backend.models.warehouse import (
    MovementType,
    StockAdjustment,
    StockMovement,
    WarehouseStock,
)
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


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


# ─── Stock Adjustments (Корректировка) ─────────────────────────────────────


async def create_adjustment(db: AsyncSession, project_id: int, warehouse_id: int, payload: dict) -> StockAdjustment:
    """
    Create stock adjustment (inventory correction).
    delta > 0 = surplus, delta < 0 = shortage.
    """
    from backend.services.warehouse_crud import get_warehouse

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


# ─── Summary Stock (Сводные остатки) ───────────────────────────────────────


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
