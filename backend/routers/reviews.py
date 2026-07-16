# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Router: /reviews — WB customer feedbacks (отзывы покупателей).

Thin HTTP layer поверх `backend.services.reviews_service`. Список и сводка
читаются из зеркала БД (wb_feedbacks); POST /reviews/sync запускает on-demand
подтяжку из WB (кнопка «Обновить»).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.database import get_db
from backend.integrations.resilience import CircuitOpenError, RateLimitError
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.reviews import ReviewsListResponse, ReviewsSummaryResponse
from backend.services import reviews_service
from backend.services.wb_reviews_sync import sync_project_feedbacks
from backend.utils.rate_limit import rate_limit_write

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
    """Список отзывов покупателей WB для текущего проекта (из зеркала БД)."""
    return await reviews_service.list_reviews(
        db, project.id, is_answered=is_answered, take=take, skip=skip
    )


@router.get("/summary", response_model=ReviewsSummaryResponse)
async def reviews_summary(
    tag: str | None = Query(None, description="Фильтр по ярлыку товара (имя ProductTag)"),
    period: str = Query(
        "1y",
        pattern="^(2w|1m|3m|6m|1y|all)$",
        description="Диапазон: 2w/1m/3m/6m/1y/all (дефолт — год)",
    ),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> ReviewsSummaryResponse:
    """Сводная аналитика отзывов проекта за период (KPI, ряды, категории/бренды). Опц. фильтр по ярлыку."""
    return await reviews_service.get_reviews_summary(db, project.id, tag=tag, period=period)


@router.post("/sync", response_model=ReviewsSummaryResponse)
async def sync_reviews(
    tag: str | None = Query(None, description="Фильтр по ярлыку для возвращаемой сводки"),
    period: str = Query(
        "1y",
        pattern="^(2w|1m|3m|6m|1y|all)$",
        description="Диапазон возвращаемой сводки: 2w/1m/3m/6m/1y/all",
    ),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> ReviewsSummaryResponse:
    """
    On-demand подтяжка отзывов из WB в зеркало БД (кнопка «Обновить»).
    Инкрементально (активные отзывы), затем возвращает свежую сводку.
    """
    api_key = await reviews_service.resolve_wb_key(db, project.id)
    if not api_key:
        # Нет ключа — не ошибка: отдаём пустую сводку с has_key=False (фронт покажет подсказку)
        return ReviewsSummaryResponse(has_key=False, period=period)

    # Пустое зеркало → первый синк тянет и архив (полная история), как ночной джоб;
    # далее инкрементально (только активные) — иначе архив гоняли бы каждый раз.
    first_sync = not await reviews_service.has_any_feedback(db, project.id)
    try:
        await sync_project_feedbacks(db, project.id, api_key, full_backfill=first_sync)
    except RateLimitError as e:
        # WB Feedbacks API часто отдаёт 429 — отдаём 429 + Retry-After, не сырой 500
        raise HTTPException(
            status_code=429,
            detail="WB временно ограничил частоту запросов, попробуйте позже",
            headers={"Retry-After": str(e.retry_after)},
        )
    except CircuitOpenError:
        # брейкер открыт после серии сбоев WB — 503, не 500
        raise HTTPException(status_code=503, detail="WB API временно недоступен, попробуйте позже")
    except ValueError as e:
        # неверный ключ / ошибка WB API → 502 с человекочитаемым текстом
        raise HTTPException(status_code=502, detail=str(e))

    await invalidate_cache(f"reviews:summary:project_id={project.id}")
    return await reviews_service.get_reviews_summary(db, project.id, tag=tag, period=period)
