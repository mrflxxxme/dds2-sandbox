"""
Router: /raw-data — какие сырые данные мы тянем из внешних API и копим у себя,
плюс принудительная дозагрузка источника.
Тонкий HTTP-слой — логика в services/raw_data_service.py.
"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.services import raw_data_service
from backend.utils.rate_limit import rate_limit_write

logger = logging.getLogger("dds.raw_data")

router = APIRouter(prefix="/raw-data")


@router.get("/sources")
async def list_sources(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Источники сырых данных: что копим, сколько накоплено, за какой период."""
    return await raw_data_service.get_sources_overview(db, project.id)


@router.get("/sources/{key}/rows")
async def source_rows(
    key: str,
    limit: int = Query(50, ge=1, le=raw_data_service.MAX_ROWS_PER_PAGE),
    offset: int = Query(0, ge=0),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    sort_by: str | None = Query(None),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Содержимое таблицы источника: страница строк за период."""
    try:
        return await raw_data_service.get_source_rows(
            db, project.id, key, limit=limit, offset=offset,
            date_from=date_from, date_to=date_to, sort_by=sort_by, sort_dir=sort_dir,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/refresh-progress")
async def refresh_progress(project: Project = Depends(get_current_project)):
    """Прогресс запущенных дозагрузок этого проекта."""
    return {"progress": raw_data_service.get_refresh_progress(project.id)}


class RefreshRequest(BaseModel):
    date_from: date | None = None
    date_to: date | None = None


@router.post("/sources/{key}/refresh", dependencies=[Depends(rate_limit_write)])
async def refresh_source(
    key: str,
    body: RefreshRequest | None = None,
    project: Project = Depends(get_current_project),
):
    """Принудительно дозагрузить источник из внешнего API (в фоне)."""
    body = body or RefreshRequest()
    result = await raw_data_service.start_refresh(project.id, key, body.date_from, body.date_to)
    if result["status"] == "unsupported":
        raise HTTPException(400, result.get("error") or "Дозагрузка недоступна")
    return result
