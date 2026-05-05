# ruff: noqa: RUF002
"""Router: /localization — отчёт «Индекс локализации» (ИЛ + ИРП).

См. backend/services/localization_index_service.py для расчётной логики
и backend/services/localization_tariff.py для таблиц КТР/КРП.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.localization import (
    LocalizationByPeriod,
    LocalizationSkuRow,
    LocalizationSummary,
)
from backend.services import localization_index_service

router = APIRouter()


@router.get("/localization/summary", response_model=LocalizationSummary)
async def get_localization_summary(
    date_from: date = Query(...),
    date_to: date = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Top-block отчёта локализации: ИЛ, ИРП, заказы, артикулы."""
    return await localization_index_service.get_summary(
        db,
        project.id,
        date_from.isoformat(),
        date_to.isoformat(),
    )


@router.get("/localization/skus", response_model=list[LocalizationSkuRow])
async def get_localization_by_sku(
    date_from: date = Query(...),
    date_to: date = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Таблица per-SKU: nm_id, total/local/non_local, КТР/КРП, статус."""
    return await localization_index_service.get_by_sku(
        db,
        project.id,
        date_from.isoformat(),
        date_to.isoformat(),
    )


@router.get("/localization", response_model=LocalizationByPeriod)
async def get_localization_full(
    date_from: date = Query(...),
    date_to: date = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Полный ответ: top-block + таблица per-SKU одним запросом."""
    summary = await localization_index_service.get_summary(
        db,
        project.id,
        date_from.isoformat(),
        date_to.isoformat(),
    )
    rows = await localization_index_service.get_by_sku(
        db,
        project.id,
        date_from.isoformat(),
        date_to.isoformat(),
    )
    return {"summary": summary, "rows": rows}
