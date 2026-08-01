# ruff: noqa: RUF001, RUF002, RUF003
"""Ценообразование: наценка по артикулам (текущая цена ВБ + себестоимость + расходы ВБ)."""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.pricing import SppMapResponse
from backend.services.pricing import ai_advisor
from backend.services.pricing import markup as markup_service
from backend.services.pricing import spp_map as spp_map_service
from backend.services.pricing import spp_points as spp_points_service
from backend.services.pricing.sync import sync_card_spp, sync_wb_prices
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
    only_in_stock: bool = Query(False),
    anomaly_only: bool = Query(False),
    group_by: str = Query("category"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Наценка по артикулам. group_by: category | sku | size | imt (склейка) | anomaly."""
    gb = group_by if group_by in ("sku", "anomaly", "category", "size", "imt") else "category"
    return await markup_service.get_markup_analytics(
        db,
        project.id,
        date_from=date_from,
        date_to=date_to,
        brand=brand,
        category=category,
        search=search,
        min_orders=min_orders,
        only_in_stock=only_in_stock,
        anomaly_only=anomaly_only,
        group_by=gb,
    )


@router.post("/ai-recommendations")
async def ai_recommendations(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    only_in_stock: bool = Query(True),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit_write),
):
    """AI-рекомендации по ценам/рекламе/остаткам (Claude по сводке метрик)."""
    return await ai_advisor.get_ai_recommendations(
        db, project.id, date_from=date_from, date_to=date_to, only_in_stock=only_in_stock
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


@router.get("/spp-map", response_model=SppMapResponse)
async def spp_map(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    step: int = Query(100, ge=10, le=1000),
    source: str = Query("card"),
    category: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Карта «категория × цена → СПП»: лесенка уровней и обрывы, где СПП падает."""
    src = source if source in ("card", "orders") else "card"
    return await spp_map_service.get_spp_map(
        db, project.id, date_from=date_from, date_to=date_to, step=step, source=src, category=category
    )


@router.get("/spp-history")
async def spp_history(
    nm_ids: str = Query(..., description="nm_id через запятую"),
    days: int = Query(30, ge=1, le=180),
    source: str = Query("card"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """История «цена → СПП» по дням для набора артикулов."""
    ids = [int(x) for x in nm_ids.split(",") if x.strip().isdigit()][:500]
    src = source if source in ("card", "orders") else "card"
    return {
        "rows": await spp_map_service.get_level_history(db, project.id, ids, days=days, source=src)
    }


@router.post("/spp-observe")
async def spp_observe(
    backfill_days: int = Query(0, ge=0, le=180),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit_write),
):
    """Снять точку СПП сейчас (card-API); `backfill_days>0` — ещё и ретро из заказов."""
    snap = await spp_points_service.snapshot_from_card(db, project.id)
    back = (
        await spp_points_service.backfill_from_orders(db, project.id, days=backfill_days)
        if backfill_days
        else {"written": 0, "days": 0}
    )
    return {"snapshot": snap, "backfill": back}


@router.post("/sync-spp")
async def sync_spp(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit_write),
):
    """Синк реальной цены покупателя с СПП из публичного card-API (без ключа)."""
    res = await sync_card_spp(db, project.id)
    await invalidate_cache(f"reports:pricing_markup:project_id={project.id}")
    return res
