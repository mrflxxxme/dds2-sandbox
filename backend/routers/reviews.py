# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Router: /reviews — WB customer feedbacks (отзывы покупателей).

Thin HTTP layer поверх `backend.services.reviews_service`.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.reviews import ReviewsListResponse
from backend.services import reviews_service

logger = logging.getLogger("dds.routers.reviews")

router = APIRouter(prefix="/reviews", tags=["Reviews"])


@router.get("", response_model=ReviewsListResponse)
async def list_reviews(
    is_answered: bool = Query(False, description="Только отвеченные (true) / неотвеченные (false)"),
    take: int = Query(100, ge=1, le=5000),
    skip: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> ReviewsListResponse:
    """Список отзывов покупателей WB для текущего проекта (live-фетч через WB-ключ)."""
    try:
        return await reviews_service.list_reviews(
            db, project.id, is_answered=is_answered, take=take, skip=skip
        )
    except ValueError as e:
        # неверный ключ / ошибка WB API → 502 с человекочитаемым текстом
        raise HTTPException(status_code=502, detail=str(e))
