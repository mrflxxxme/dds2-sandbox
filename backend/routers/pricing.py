# ruff: noqa: RUF001, RUF002, RUF003
"""Ценообразование: наценка по артикулам (текущая цена ВБ + себестоимость + расходы ВБ)."""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.services.pricing import markup as markup_service
from backend.services.pricing.sync import sync_wb_prices
from backend.utils.rate_limit import rate_limit_write

logger = logging.getLogger("dds.pricing")

router = APIRouter(prefix="/pricing")


@router.get("/markup")
async def get_markup(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    brand: str | None = Query(None),
    category: str | None = Query(None),
    search: str | None = Query(None),
    min_orders: int = Query(0, ge=0),
    group_by: str = Query("category"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Наценка по артикулам. group_by: category (дерево) | sku (плоский список)."""
    return await markup_service.get_markup_analytics(
        db,
        project.id,
        date_from=date_from,
        date_to=date_to,
        brand=brand,
        category=category,
        search=search,
        min_orders=min_orders,
        group_by="sku" if group_by == "sku" else "category",
    )


@router.post("/sync")
async def sync_prices(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit_write),
):
    """Ручной синк текущих цен витрины ВБ (API «Цены и скидки»)."""
    sync_log = await sync_wb_prices(db, project.id)
    await invalidate_cache(f"reports:pricing_markup:project_id={project.id}")
    return {
        "status": sync_log.status,
        "rows": sync_log.rows_inserted,
        "synced_at": sync_log.finished_at.isoformat() if sync_log.finished_at else None,
        "message": sync_log.error_msg,
    }
