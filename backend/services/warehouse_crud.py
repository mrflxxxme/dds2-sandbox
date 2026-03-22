"""
Warehouse CRUD — warehouses, delivery times.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.warehouse import (
    Warehouse,
    WarehouseDeliveryTime,
    WarehouseStock,
)
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


# ─── Warehouses CRUD ────────────────────────────────────────────────────────


async def list_warehouses(db: AsyncSession, project_id: int) -> list:
    """List all active warehouses for a project, ordered by sort_order."""
    stock_sub = (
        select(
            WarehouseStock.warehouse_id,
            func.coalesce(func.sum(WarehouseStock.quantity), 0).label("total_stock"),
        )
        .where(
            WarehouseStock.project_id == project_id,
        )
        .group_by(WarehouseStock.warehouse_id)
        .subquery()
    )

    result = await db.execute(
        select(Warehouse, stock_sub.c.total_stock)
        .outerjoin(stock_sub, Warehouse.id == stock_sub.c.warehouse_id)
        .where(
            Warehouse.project_id == project_id,
            Warehouse.is_deleted.is_(False),
        )
        .order_by(Warehouse.sort_order, Warehouse.id)
    )
    rows = result.all()
    warehouses = []
    for wh, total_stock in rows:
        wh.total_stock = int(total_stock or 0)
        warehouses.append(wh)
    return warehouses


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


# ─── Delivery Times (Время доставки до WB) ─────────────────────────────────


async def get_delivery_times(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
) -> dict | None:
    """Get delivery time settings for a warehouse to all WB warehouses."""
    from backend.models import WbStockSnapshot

    # Check warehouse exists and belongs to project
    wh_result = await db.execute(
        select(Warehouse).where(
            Warehouse.id == warehouse_id,
            Warehouse.project_id == project_id,
            Warehouse.is_deleted.is_(False),
        )
    )
    warehouse = wh_result.scalar_one_or_none()
    if not warehouse:
        return None

    assembly_days = warehouse.assembly_days or 0
    wb_acceptance_days = warehouse.wb_acceptance_days or 2

    # Get unique WB warehouse names from snapshots
    wb_wh_result = await db.execute(
        select(func.distinct(WbStockSnapshot.warehouse_name))
        .where(
            WbStockSnapshot.project_id == project_id,
        )
        .order_by(WbStockSnapshot.warehouse_name)
    )
    wb_names = [r[0] for r in wb_wh_result]

    # Get saved delivery times
    dt_result = await db.execute(
        select(WarehouseDeliveryTime).where(
            WarehouseDeliveryTime.project_id == project_id,
            WarehouseDeliveryTime.warehouse_id == warehouse_id,
        )
    )
    saved_map = {r.wb_warehouse_name: r.delivery_days for r in dt_result.scalars()}

    rows = []
    for name in wb_names:
        delivery = saved_map.get(name, 3)  # default 3 days
        rows.append(
            {
                "wb_warehouse_name": name,
                "delivery_days": delivery,
                "total_days": assembly_days + delivery + wb_acceptance_days,
            }
        )

    return {
        "warehouse_id": warehouse_id,
        "warehouse_name": warehouse.name,
        "assembly_days": assembly_days,
        "wb_acceptance_days": wb_acceptance_days,
        "wb_warehouses": rows,
    }


async def update_delivery_times(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    assembly_days: int | None,
    wb_acceptance_days: int | None,
    items: list[dict],
) -> dict | None:
    """Update delivery time settings for a warehouse."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    # Check warehouse exists
    wh_result = await db.execute(
        select(Warehouse).where(
            Warehouse.id == warehouse_id,
            Warehouse.project_id == project_id,
            Warehouse.is_deleted.is_(False),
        )
    )
    warehouse = wh_result.scalar_one_or_none()
    if not warehouse:
        return None

    # Update warehouse fields
    if assembly_days is not None:
        warehouse.assembly_days = assembly_days
    if wb_acceptance_days is not None:
        warehouse.wb_acceptance_days = wb_acceptance_days

    # Upsert delivery times
    if items:
        for item in items:
            stmt = pg_insert(WarehouseDeliveryTime).values(
                project_id=project_id,
                warehouse_id=warehouse_id,
                wb_warehouse_name=item["wb_warehouse_name"],
                delivery_days=item["delivery_days"],
            )
            stmt = stmt.on_conflict_do_update(
                constraint="uq_wh_delivery_time",
                set_={"delivery_days": stmt.excluded.delivery_days, "updated_at": utcnow()},
            )
            await db.execute(stmt)

    await db.commit()
    return await get_delivery_times(db, project_id, warehouse_id)
