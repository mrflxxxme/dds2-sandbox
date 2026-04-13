"""
Supply Chain — Supplier CRUD service.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.models.supply_chain import Supplier
from backend.schemas.supply_chain import SupplierCreate, SupplierUpdate

logger = logging.getLogger(__name__)


async def list_suppliers(db: AsyncSession, project_id: int) -> list[Supplier]:
    """List all suppliers for a project, excluding soft-deleted."""
    result = await db.execute(
        select(Supplier)
        .where(
            Supplier.project_id == project_id,
            Supplier.is_deleted == False,  # noqa: E712
        )
        .order_by(Supplier.name)
        .limit(500)
    )
    return list(result.scalars().all())


async def get_supplier(db: AsyncSession, project_id: int, supplier_id: int) -> Supplier | None:
    """Get a single supplier by id, scoped to project."""
    result = await db.execute(
        select(Supplier).where(
            Supplier.id == supplier_id,
            Supplier.project_id == project_id,
            Supplier.is_deleted == False,  # noqa: E712
        )
    )
    return result.scalar_one_or_none()


async def _invalidate_supplier_caches(project_id: int) -> None:
    """Invalidate both supplier list and catalog caches for a project."""
    await invalidate_cache(f"supply_chain:suppliers:{project_id}")
    await invalidate_cache(f"supply_chain:supplier_catalog:project_id={project_id}")


async def create_supplier(db: AsyncSession, project_id: int, data: SupplierCreate) -> Supplier:
    """Create a new supplier."""
    supplier = Supplier(
        project_id=project_id,
        name=data.name,
        country=data.country,
        currency=data.currency,
        delivery_days_min=data.delivery_days_min,
        delivery_days_max=data.delivery_days_max,
        note=data.note,
    )
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)
    await _invalidate_supplier_caches(project_id)
    return supplier


async def update_supplier(db: AsyncSession, project_id: int, supplier_id: int, data: SupplierUpdate) -> Supplier | None:
    """Update supplier fields (partial update)."""
    supplier = await get_supplier(db, project_id, supplier_id)
    if not supplier:
        return None

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(supplier, field, value)

    await db.commit()
    await db.refresh(supplier)
    await _invalidate_supplier_caches(project_id)
    return supplier


async def delete_supplier(db: AsyncSession, project_id: int, supplier_id: int) -> bool:
    """Soft-delete a supplier."""
    supplier = await get_supplier(db, project_id, supplier_id)
    if not supplier:
        return False

    supplier.soft_delete()
    await db.commit()
    await _invalidate_supplier_caches(project_id)
    return True
