"""
Supply Chain — SupplyProject CRUD.

SupplyProject is a user-defined grouping ("campaign" / "batch") for factory
orders within a tenant project. 1 factory order → 0..1 supply project.
"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.supply_chain import FactoryOrder, SupplyProject
from backend.schemas.supply_chain import SupplyProjectCreate, SupplyProjectUpdate


async def list_supply_projects(db: AsyncSession, project_id: int) -> list[SupplyProject]:
    """List supply projects for a tenant project, each annotated with orders_count.

    orders_count = number of non-archived, non-deleted factory orders bound to it.
    """
    result = await db.execute(
        select(SupplyProject)
        .where(
            SupplyProject.project_id == project_id,
            SupplyProject.is_deleted == False,  # noqa: E712
        )
        .order_by(SupplyProject.name)
        .limit(500)
    )
    projects = list(result.scalars().all())
    if not projects:
        return projects

    counts_result = await db.execute(
        select(FactoryOrder.supply_project_id, func.count())
        .where(
            FactoryOrder.project_id == project_id,
            FactoryOrder.is_deleted == False,  # noqa: E712
            FactoryOrder.is_archived == False,  # noqa: E712
            FactoryOrder.supply_project_id.isnot(None),
        )
        .group_by(FactoryOrder.supply_project_id)
    )
    counts = {row[0]: row[1] for row in counts_result.all()}
    for p in projects:
        # Transient attribute consumed by SupplyProjectSchema (from_attributes).
        p.orders_count = counts.get(p.id, 0)  # type: ignore[attr-defined]
    return projects


async def get_supply_project(db: AsyncSession, project_id: int, sp_id: int) -> SupplyProject | None:
    result = await db.execute(
        select(SupplyProject).where(
            SupplyProject.id == sp_id,
            SupplyProject.project_id == project_id,
            SupplyProject.is_deleted == False,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def create_supply_project(db: AsyncSession, project_id: int, data: SupplyProjectCreate) -> SupplyProject:
    sp = SupplyProject(
        project_id=project_id,
        name=data.name,
        color=data.color,
        note=data.note,
    )
    db.add(sp)
    await db.commit()
    await db.refresh(sp)
    return sp


async def update_supply_project(
    db: AsyncSession, project_id: int, sp_id: int, data: SupplyProjectUpdate
) -> SupplyProject | None:
    sp = await get_supply_project(db, project_id, sp_id)
    if not sp:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(sp, field, value)
    await db.commit()
    await db.refresh(sp)
    return sp


async def delete_supply_project(db: AsyncSession, project_id: int, sp_id: int) -> bool:
    """Soft-delete a supply project and unbind any orders pointing to it."""
    sp = await get_supply_project(db, project_id, sp_id)
    if not sp:
        return False
    # Detach orders so they don't reference a soft-deleted project.
    await db.execute(
        update(FactoryOrder)
        .where(
            FactoryOrder.supply_project_id == sp_id,
            FactoryOrder.project_id == project_id,
        )
        .values(supply_project_id=None)
    )
    sp.soft_delete()
    await db.commit()
    return True
