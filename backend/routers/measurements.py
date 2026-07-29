"""
Router: /measurements — замеры складов WB + удержания за занижение габаритов.
Thin HTTP layer — логика в services/measurements_service.py и wb_measurements_sync.py.
"""

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.measurements import (
    MeasurementFiltersResponse,
    MeasurementPenaltyListResponse,
    MeasurementSyncResult,
    PenaltyArticleSummaryResponse,
    WarehouseMeasurementListResponse,
)
from backend.services import measurements_service, wb_measurements_sync
from backend.utils.rate_limit import rate_limit_write

logger = logging.getLogger("dds.measurements")

router = APIRouter(prefix="/measurements")


@router.get("/filters", response_model=MeasurementFiltersResponse)
async def get_measurement_filters(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Списки брендов и предметов для выпадающих фильтров."""
    return await measurements_service.get_filters(db, project.id)


@router.get("/warehouse", response_model=WarehouseMeasurementListResponse)
async def list_warehouse_measurements(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    nm_id: int | None = Query(None),
    subject: str | None = Query(None),
    brand: str | None = Query(None),
    search: str | None = Query(None, description="Поиск по артикулу или номеру замера"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Контрольные замеры товаров на складах WB за период."""
    items, total = await measurements_service.list_warehouse_measurements(
        db, project.id, date_from, date_to, nm_id, subject, brand, search, limit, offset
    )
    return {"items": items, "total": total}


@router.get("/penalties", response_model=MeasurementPenaltyListResponse)
async def list_measurement_penalties(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    nm_id: int | None = Query(None),
    subject: str | None = Query(None),
    brand: str | None = Query(None),
    search: str | None = Query(None, description="Поиск по артикулу или номеру замера"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Удержания за занижение габаритов (по результатам замеров) за период."""
    items, total, total_penalty, total_reversal = await measurements_service.list_finance_penalties(
        db, project.id, date_from, date_to, subject, brand, search, limit, offset
    )
    return {
        "items": items,
        "total": total,
        "total_penalty": total_penalty,
        "total_reversal": total_reversal,
    }


@router.get("/penalties/summary", response_model=PenaltyArticleSummaryResponse)
async def penalties_summary_by_article(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    subject: str | None = Query(None),
    brand: str | None = Query(None),
    search: str | None = Query(None, description="Поиск по артикулу или номеру замера"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сводка удержаний по артикулам: суммы удержаний/сторно, нетто, кол-во."""
    items, totals = await measurements_service.summarize_finance_penalties(
        db, project.id, date_from, date_to, subject, brand, search
    )
    return {"items": items, **totals}


@router.post("/sync", response_model=MeasurementSyncResult, dependencies=[Depends(rate_limit_write)])
async def sync_measurements(
    days: int = Query(90, ge=1, le=1200, description="За сколько дней назад тянуть"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Ручной синк замеров и удержаний из WB за последние `days` дней."""
    date_to = date.today()
    date_from = date_to - timedelta(days=days)
    try:
        result = await wb_measurements_sync.sync_all_measurements(db, project.id, date_from, date_to)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return result
