# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Router: /reviews — WB customer feedbacks (отзывы покупателей).

Thin HTTP layer поверх `backend.services.reviews_service`. Список и сводка
читаются из зеркала БД (wb_feedbacks); POST /reviews/sync запускает on-demand
подтяжку из WB (кнопка «Обновить»).
"""

import logging
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.database import get_db
from backend.integrations.resilience import CircuitOpenError, RateLimitError
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.reviews import (
    ComplaintCandidatesResponse,
    ComplaintCreate,
    ComplaintItem,
    ComplaintsResponse,
    ComplaintStatusUpdate,
    NewcomersResponse,
    ReviewBreakdownResponse,
    ReviewsListResponse,
    ReviewsSummaryResponse,
)
from backend.services import complaints_service, reviews_service
from backend.services.wb_reviews_sync import sync_project_feedbacks
from backend.utils.rate_limit import rate_limit_write

logger = logging.getLogger("dds.routers.reviews")

router = APIRouter(prefix="/reviews", tags=["Reviews"])


@router.get("", response_model=ReviewsListResponse)
async def list_reviews(
    is_answered: bool = Query(False, description="Только отвеченные (true) / неотвеченные (false)"),
    take: int = Query(100, ge=1, le=5000),
    skip: int = Query(0, ge=0),
    nm_id: int | None = Query(None, description="Все отзывы конкретного товара (игнорирует is_answered)"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> ReviewsListResponse:
    """Список отзывов покупателей WB для текущего проекта (из зеркала БД). Опц. по товару (nm_id)."""
    return await reviews_service.list_reviews(
        db, project.id, is_answered=is_answered, take=take, skip=skip, nm_id=nm_id
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


@router.get("/newcomers", response_model=NewcomersResponse)
async def reviews_newcomers(
    days: int = Query(30, ge=1, le=365, description="Окно «новинки» — дней на продаже"),
    max_rating: float = Query(4.6, gt=0, le=5, description="Порог «плохого» рейтинга"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> NewcomersResponse:
    """Проблемные новинки: товары на продаже < `days` дней со средним рейтингом < `max_rating`."""
    data = await reviews_service.get_new_low_rated(db, project.id, days=days, max_rating=max_rating)
    return NewcomersResponse(**data)


# ─── Жалобы на отзывы (для удаления) ─────────────────────────────────────────


@router.get("/complaints/candidates", response_model=ComplaintCandidatesResponse)
async def complaint_candidates(
    max_rating: int = Query(3, ge=1, le=3, description="Верхняя оценка кандидатов (1..3)"),
    take: int = Query(100, ge=1, le=500),
    only_open: bool = Query(True, description="Только без поданной жалобы"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> ComplaintCandidatesResponse:
    """Низкооценённые отзывы (1–3★) — кандидаты на жалобу, с текущим статусом."""
    return await complaints_service.list_candidates(
        db, project.id, max_rating=max_rating, take=take, only_open=only_open
    )


@router.get("/complaints", response_model=ComplaintsResponse)
async def list_complaints(
    status: str | None = Query(None, description="Фильтр по статусу"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> ComplaintsResponse:
    """Поданные жалобы на отзывы + KPI (подано/удалено/не удалено/в ожидании)."""
    return await complaints_service.get_complaints(db, project.id, status=status)


@router.post("/complaints", response_model=ComplaintItem)
async def create_complaint(
    body: ComplaintCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> ComplaintItem:
    """Зафиксировать подачу жалобы на отзыв."""
    try:
        return await complaints_service.create_complaint(
            db, project.id, body.wb_feedback_id, body.reason, body.text
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.patch("/complaints/{complaint_id}", response_model=ComplaintItem)
async def update_complaint(
    complaint_id: int,
    body: ComplaintStatusUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> ComplaintItem:
    """Проставить исход жалобы (удалено/не удалено/в ожидании)."""
    try:
        res = await complaints_service.update_status(db, project.id, complaint_id, body.status, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if res is None:
        raise HTTPException(404, "Жалоба не найдена")
    return res


@router.get("/breakdown", response_model=ReviewBreakdownResponse)
async def reviews_breakdown(
    group_by: str = Query("month", pattern="^(day|week|month|subject|brand|nm_id)$"),
    date_from: date | None = Query(None, description="Начало периода (вкл.)"),
    date_to: date | None = Query(None, description="Конец периода (вкл.)"),
    subject: str | None = Query(None, description="Фильтр по предмету"),
    brand: str | None = Query(None, description="Фильтр по бренду"),
    nm_id: int | None = Query(None, description="Фильтр по артикулу (nm_id)"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> ReviewBreakdownResponse:
    """Детальная таблица отзывов с группировкой (день/неделя/месяц/предмет/бренд/артикул)."""
    dt_from = datetime.combine(date_from, time.min) if date_from else None
    dt_to = datetime.combine(date_to, time.min) if date_to else None
    return await reviews_service.get_reviews_breakdown(
        db, project.id, group_by=group_by,
        date_from=dt_from, date_to=dt_to,
        subject=subject, brand=brand, nm_id=nm_id,
    )


@router.get("/complaint-reviews", response_model=ReviewsListResponse)
async def complaint_reviews(
    term: str = Query(..., min_length=1, max_length=100, description="Слово из темы жалоб"),
    days: int = Query(30, ge=1, le=365),
    max_rating: float = Query(4.6, gt=0, le=5),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> ReviewsListResponse:
    """Негативные отзывы проблемных новинок, содержащие слово `term` (клик по теме жалоб)."""
    return await reviews_service.get_complaint_reviews(
        db, project.id, term, days=days, max_rating=max_rating
    )


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
