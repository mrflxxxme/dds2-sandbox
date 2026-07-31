"""
Warehouse CRUD — warehouses, delivery times.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.cost import CostOrder
from backend.models.counterparty import Counterparty
from backend.models.enums import VehicleStatus
from backend.models.warehouse import (
    InboundReceipt,
    InboundStatus,
    Warehouse,
    WarehouseCounterparty,
    WarehouseDeliveryTime,
    WarehouseStock,
)
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


# ─── Warehouses CRUD ────────────────────────────────────────────────────────


async def _load_extra_counterparties(
    db: AsyncSession, project_id: int, warehouse_ids: list[int]
) -> dict[int, list[dict]]:
    """Дополнительные контрагенты складов → {warehouse_id: [{id, inn, name}, ...]}.

    Один запрос на все склады (без N+1). Только активные контрагенты.
    """
    if not warehouse_ids:
        return {}
    result = await db.execute(
        select(
            WarehouseCounterparty.warehouse_id,
            Counterparty.id,
            Counterparty.inn,
            Counterparty.name,
        )
        .join(Counterparty, Counterparty.id == WarehouseCounterparty.counterparty_id)
        .where(
            WarehouseCounterparty.project_id == project_id,
            WarehouseCounterparty.warehouse_id.in_(warehouse_ids),
            Counterparty.is_deleted == False,  # noqa: E712
        )
        .order_by(WarehouseCounterparty.warehouse_id, WarehouseCounterparty.id)
    )
    out: dict[int, list[dict]] = {}
    for wh_id, cp_id, cp_inn, cp_name in result.all():
        out.setdefault(wh_id, []).append({"id": cp_id, "inn": cp_inn, "name": cp_name})
    return out


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

    # Count vehicles in transit (SHIPPED or CUSTOMS) targeting each warehouse
    vehicles_sub = (
        select(
            CostOrder.target_warehouse_id,
            func.count().label("vehicles_in_transit"),
        )
        .where(
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712
            CostOrder.status.in_([VehicleStatus.SHIPPED, VehicleStatus.CUSTOMS, VehicleStatus.DISPATCHED]),
            CostOrder.target_warehouse_id.isnot(None),
        )
        .group_by(CostOrder.target_warehouse_id)
        .subquery()
    )

    result = await db.execute(
        select(
            Warehouse,
            stock_sub.c.total_stock,
            vehicles_sub.c.vehicles_in_transit,
            Counterparty.inn.label("cp_inn"),
            Counterparty.name.label("cp_name"),
        )
        .outerjoin(stock_sub, Warehouse.id == stock_sub.c.warehouse_id)
        .outerjoin(vehicles_sub, Warehouse.id == vehicles_sub.c.target_warehouse_id)
        .outerjoin(Counterparty, Counterparty.id == Warehouse.counterparty_id)
        .where(
            Warehouse.project_id == project_id,
            Warehouse.is_deleted.is_(False),
        )
        .order_by(Warehouse.sort_order, Warehouse.id)
    )
    rows = result.all()
    warehouses = []
    for wh, total_stock, vehicles_in_transit, cp_inn, cp_name in rows:
        wh.total_stock = int(total_stock or 0)
        wh.vehicles_in_transit = int(vehicles_in_transit or 0)
        wh.counterparty_inn = cp_inn
        wh.counterparty_name = cp_name
        warehouses.append(wh)
    extra_map = await _load_extra_counterparties(db, project_id, [wh.id for wh in warehouses])
    for wh in warehouses:
        wh.extra_counterparties = extra_map.get(wh.id, [])
    return warehouses


async def get_or_create_transit_warehouse(db: AsyncSession, project_id: int) -> Warehouse:
    """Get or create a default 'Транзит' warehouse for vehicles without a target."""
    result = await db.execute(
        select(Warehouse).where(
            Warehouse.project_id == project_id,
            Warehouse.name == "Транзит",
            Warehouse.is_deleted == False,  # noqa: E712
        )
    )
    wh = result.scalar_one_or_none()
    if wh:
        return wh

    wh = Warehouse(
        project_id=project_id,
        name="Транзит",
        warehouse_type="EXTERNAL",
        sort_order=999,
    )
    db.add(wh)
    await db.flush()
    await db.refresh(wh)
    logger.info("Auto-created transit warehouse id=%s for project=%s", wh.id, project_id)
    return wh


async def get_warehouse(db: AsyncSession, project_id: int, warehouse_id: int) -> Warehouse | None:
    """Get a single warehouse by id. Populates counterparty_inn/name if linked."""
    result = await db.execute(
        select(Warehouse, Counterparty.inn.label("cp_inn"), Counterparty.name.label("cp_name"))
        .outerjoin(Counterparty, Counterparty.id == Warehouse.counterparty_id)
        .where(
            Warehouse.id == warehouse_id,
            Warehouse.project_id == project_id,
            Warehouse.is_deleted == False,  # noqa: E712
        )
    )
    row = result.first()
    if row is None:
        return None
    wh, cp_inn, cp_name = row
    wh.counterparty_inn = cp_inn
    wh.counterparty_name = cp_name
    extra_map = await _load_extra_counterparties(db, project_id, [wh.id])
    wh.extra_counterparties = extra_map.get(wh.id, [])
    assert isinstance(wh, Warehouse)
    return wh


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


async def link_counterparty(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    *,
    inn: str | None,
    name: str | None,
) -> Warehouse | None:
    """Set/update warehouse's counterparty (legal entity) by INN.

    Upserts a Counterparty by (project_id, inn). If the CP is new or has
    primary_type='OTHER' — bumps it to FULFILLMENT. Never overwrites other types.
    If inn is None — unlinks the counterparty from the warehouse.
    """
    from sqlalchemy import text as sa_text

    from backend.cache import invalidate_project_reports
    from backend.models.counterparty import Counterparty

    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        return None

    if not inn:
        wh.counterparty_id = None
        await db.commit()
        await db.refresh(wh)
        return wh

    clean_inn = inn.strip()
    cp_name = (name or "").strip() or clean_inn

    existing = await db.execute(
        select(Counterparty).where(
            Counterparty.project_id == project_id,
            Counterparty.inn == clean_inn,
            Counterparty.is_deleted == False,  # noqa: E712
        )
    )
    cp = existing.scalar_one_or_none()
    if cp is None:
        cp = Counterparty(
            project_id=project_id,
            inn=clean_inn,
            name=cp_name,
            primary_type="FULFILLMENT",
            created_by_import=False,
        )
        db.add(cp)
        await db.flush()
    else:
        # Update name only if longer; bump primary_type only if OTHER
        if cp_name and len(cp_name) > len(cp.name or ""):
            cp.name = cp_name
        if cp.primary_type == "OTHER":
            cp.primary_type = "FULFILLMENT"

    wh.counterparty_id = cp.id
    await db.commit()
    await db.refresh(wh)
    await invalidate_project_reports(project_id)
    # Mute unused import warnings when this helper isn't wired elsewhere yet
    _ = sa_text
    return wh


async def _upsert_fulfillment_counterparty(db: AsyncSession, project_id: int, inn: str, name: str | None) -> Counterparty:
    """Upsert a Counterparty by (project_id, inn), bumping primary_type OTHER→FULFILLMENT.

    Shared by link_counterparty (primary) and add_extra_counterparty (additional).
    Never overwrites a non-OTHER primary_type; updates name only if longer.
    """
    clean_inn = inn.strip()
    cp_name = (name or "").strip() or clean_inn
    existing = await db.execute(
        select(Counterparty).where(
            Counterparty.project_id == project_id,
            Counterparty.inn == clean_inn,
            Counterparty.is_deleted == False,  # noqa: E712
        )
    )
    cp = existing.scalar_one_or_none()
    if cp is None:
        cp = Counterparty(
            project_id=project_id,
            inn=clean_inn,
            name=cp_name,
            primary_type="FULFILLMENT",
            created_by_import=False,
        )
        db.add(cp)
        await db.flush()
    else:
        if cp_name and len(cp_name) > len(cp.name or ""):
            cp.name = cp_name
        if cp.primary_type == "OTHER":
            cp.primary_type = "FULFILLMENT"
    return cp


async def add_extra_counterparty(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    *,
    inn: str,
    name: str | None,
) -> Warehouse | None:
    """Привязать ДОПОЛНИТЕЛЬНОГО контрагента к складу (поверх основного).

    Upsert контрагента как FULFILLMENT + идемпотентная вставка в link-таблицу.
    Если контрагент уже привязан к складу (основным или доп.) — no-op.
    """
    from backend.cache import invalidate_project_reports

    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        return None

    clean_inn = (inn or "").strip()
    if not clean_inn:
        return wh

    cp = await _upsert_fulfillment_counterparty(db, project_id, clean_inn, name)

    # Идемпотентно: не дублировать, если уже привязан этой же связью.
    existing_link = await db.execute(
        select(WarehouseCounterparty.id).where(
            WarehouseCounterparty.project_id == project_id,
            WarehouseCounterparty.warehouse_id == warehouse_id,
            WarehouseCounterparty.counterparty_id == cp.id,
        )
    )
    if existing_link.scalar_one_or_none() is None:
        db.add(
            WarehouseCounterparty(
                project_id=project_id,
                warehouse_id=warehouse_id,
                counterparty_id=cp.id,
            )
        )

    await db.commit()
    await db.refresh(wh)
    await invalidate_project_reports(project_id)
    return await get_warehouse(db, project_id, warehouse_id)


async def remove_extra_counterparty(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    counterparty_id: int,
) -> Warehouse | None:
    """Отвязать доп. контрагента от склада (hard-delete строки link-таблицы).

    Контрагента (Counterparty) не трогаем — только связь. Основной контрагент
    (`Warehouse.counterparty_id`) отвязывается отдельным эндпоинтом.
    """
    from sqlalchemy import delete as sa_delete

    from backend.cache import invalidate_project_reports

    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        return None

    await db.execute(
        sa_delete(WarehouseCounterparty).where(
            WarehouseCounterparty.project_id == project_id,
            WarehouseCounterparty.warehouse_id == warehouse_id,
            WarehouseCounterparty.counterparty_id == counterparty_id,
        )
    )
    await db.commit()
    await db.refresh(wh)
    await invalidate_project_reports(project_id)
    return await get_warehouse(db, project_id, warehouse_id)


async def get_expected_vehicles(db: AsyncSession, project_id: int, warehouse_id: int) -> list:
    """Get vehicles in transit (SHIPPED/CUSTOMS) targeting this warehouse."""
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(CostOrder)
        .options(selectinload(CostOrder.items))
        .where(
            CostOrder.project_id == project_id,
            CostOrder.target_warehouse_id == warehouse_id,
            CostOrder.is_deleted == False,  # noqa: E712
            CostOrder.status.in_([VehicleStatus.SHIPPED, VehicleStatus.CUSTOMS, VehicleStatus.DISPATCHED]),
        )
        .order_by(CostOrder.ship_date.desc().nulls_last())
    )
    return list(result.scalars().all())


async def get_vehicle_receipt_map(
    db: AsyncSession, project_id: int, vehicle_ids: list[int]
) -> dict[int, int]:
    """Наша приёмка каждой машины: {cost_order_id: inbound_receipt_id}.

    Приёмку создаёт отгрузка машины (InboundReceipt.cost_order_id = vehicle.id,
    status=EXPECTED); пост-отгрузочные доборы могут добавить DRAFT-дубль.
    Предпочитаем EXPECTED, иначе — свежайшую по id.
    """
    if not vehicle_ids:
        return {}
    result = await db.execute(
        select(InboundReceipt.id, InboundReceipt.cost_order_id, InboundReceipt.status)
        .where(
            InboundReceipt.project_id == project_id,
            InboundReceipt.cost_order_id.in_(vehicle_ids),
            InboundReceipt.is_deleted == False,  # noqa: E712
        )
        .order_by(InboundReceipt.id)
        .limit(1000)
    )
    mapping: dict[int, int] = {}
    frozen: set[int] = set()  # машины, у которых уже найдена EXPECTED-приёмка
    for receipt_id, cost_order_id, status in result.all():
        if cost_order_id in frozen:
            continue
        mapping[cost_order_id] = receipt_id  # среди прочих — последняя (макс. id)
        if status == InboundStatus.EXPECTED.value:
            frozen.add(cost_order_id)
    return mapping


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
