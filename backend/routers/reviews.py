# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Router: /reviews — WB customer feedbacks (отзывы покупателей).

Thin HTTP layer поверх `backend.services.reviews_service`. Список и сводка
читаются из зеркала БД (wb_feedbacks); POST /reviews/sync запускает on-demand
подтяжку из WB (кнопка «Обновить»).
"""

import logging
from datetime import date, datetime, time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.database import get_db
from backend.integrations.resilience import CircuitOpenError, RateLimitError
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.common import DeleteResponse
from backend.schemas.reviews import (
    CardItem,
    CardSyncResult,
    ComplaintAgentItem,
    ComplaintAgentRunResult,
    ComplaintAgentSave,
    ComplaintBulkCreate,
    ComplaintBulkResult,
    ComplaintCandidatesResponse,
    ComplaintCreate,
    ComplaintItem,
    ComplaintsResponse,
    ComplaintStatusUpdate,
    KbImportResult,
    KbItem,
    KbListResponse,
    KbProductsResponse,
    KbSave,
    NewcomersResponse,
    QuestionItem,
    QuestionsListResponse,
    QuestionsSyncResult,
    RepliesListResponse,
    ReplyAgentItem,
    ReplyAgentRunResult,
    ReplyAgentSave,
    ReplyCreate,
    ReplyItem,
    ReplySendResult,
    ReplyUpdate,
    ReviewBreakdownResponse,
    ReviewsListResponse,
    ReviewsSummaryResponse,
    StockWatchItem,
    StockWatchListResponse,
    StockWatchScanResult,
)
from backend.services import complaint_agents_service, complaints_service, reply_service, reviews_service, stock_watch_service
from backend.services import wb_cards_service
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


# ─── ИИ-агенты подготовки жалоб ──────────────────────────────────────────────


@router.get("/complaint-agents", response_model=list[ComplaintAgentItem])
async def list_complaint_agents(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> list[ComplaintAgentItem]:
    """Список ИИ-агентов подготовки жалоб проекта."""
    rows = await complaint_agents_service.list_agents(db, project.id)
    return [ComplaintAgentItem(**r) for r in rows]


@router.post("/complaint-agents", response_model=ComplaintAgentItem)
async def create_complaint_agent(
    body: ComplaintAgentSave,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> ComplaintAgentItem:
    """Создать ИИ-агента подготовки жалоб."""
    return ComplaintAgentItem(**await complaint_agents_service.create_agent(db, project.id, body.model_dump(exclude_none=True)))


@router.patch("/complaint-agents/{agent_id}", response_model=ComplaintAgentItem)
async def update_complaint_agent(
    agent_id: int,
    body: ComplaintAgentSave,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> ComplaintAgentItem:
    """Изменить агента."""
    res = await complaint_agents_service.update_agent(db, project.id, agent_id, body.model_dump(exclude_none=True))
    if res is None:
        raise HTTPException(404, "Агент не найден")
    return ComplaintAgentItem(**res)


@router.delete("/complaint-agents/{agent_id}", response_model=DeleteResponse)
async def delete_complaint_agent(
    agent_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> DeleteResponse:
    """Удалить агента."""
    ok = await complaint_agents_service.delete_agent(db, project.id, agent_id)
    if not ok:
        raise HTTPException(404, "Агент не найден")
    return DeleteResponse(deleted=True, id=agent_id)


@router.post("/complaint-agents/{agent_id}/run", response_model=ComplaintAgentRunResult)
async def run_complaint_agent(
    agent_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> ComplaintAgentRunResult:
    """Прогнать агента: LLM оценивает отзывы по правилам → готовит жалобы на подходящие."""
    try:
        res = await complaint_agents_service.run_agent(db, project.id, agent_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return ComplaintAgentRunResult(**res)


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


@router.post("/complaints/bulk", response_model=ComplaintBulkResult)
async def create_complaints_bulk(
    body: ComplaintBulkCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> ComplaintBulkResult:
    """Зафиксировать жалобы на ВСЕ накопившиеся отзывы 1–3★ одним действием."""
    try:
        return await complaints_service.bulk_create_complaints(
            db, project.id, body.reason, body.text, max_rating=body.max_rating
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
    data = await reviews_service.get_reviews_breakdown(
        db, project.id, group_by=group_by,
        date_from=dt_from, date_to=dt_to,
        subject=subject, brand=brand, nm_id=nm_id,
    )
    return ReviewBreakdownResponse(**data)


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

    # синк обновил зеркало → сбрасываем весь кэш раздела отзывов
    for prefix in ("reviews:summary", "reviews:newcomers", "reviews:breakdown"):
        await invalidate_cache(f"{prefix}:project_id={project.id}")
    return await reviews_service.get_reviews_summary(db, project.id, tag=tag, period=period)


# ─── Вопросы покупателей (зеркало wb_questions) ──────────────────────────────


@router.get("/questions", response_model=QuestionsListResponse)
async def list_questions(
    is_answered: bool = Query(False, description="Только отвеченные (true) / неотвеченные (false)"),
    take: int = Query(100, ge=1, le=5000),
    skip: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> QuestionsListResponse:
    """Список вопросов покупателей WB для текущего проекта (из зеркала БД)."""
    data = await reply_service.list_questions(
        db, project.id, is_answered=is_answered, take=take, skip=skip
    )
    return QuestionsListResponse(**data)


@router.post("/questions/sync", response_model=QuestionsSyncResult)
async def sync_questions(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> QuestionsSyncResult:
    """On-demand подтяжка вопросов из WB в зеркало БД (кнопка «Обновить»)."""
    api_key = await reviews_service.resolve_wb_key(db, project.id)
    if not api_key:
        return QuestionsSyncResult(has_key=False)
    first_sync = not await reply_service.has_any_question(db, project.id)
    try:
        result = await reply_service.sync_project_questions(
            db, project.id, api_key, full_backfill=first_sync
        )
    except RateLimitError as e:
        raise HTTPException(
            status_code=429,
            detail="WB временно ограничил частоту запросов, попробуйте позже",
            headers={"Retry-After": str(e.retry_after)},
        )
    except CircuitOpenError:
        raise HTTPException(status_code=503, detail="WB API временно недоступен, попробуйте позже")
    except httpx.HTTPError:
        # сетевая ошибка до WB (недоступен хост/обрыв TLS) — 503, не сырой 500
        raise HTTPException(status_code=503, detail="Не удалось связаться с WB API, попробуйте позже")
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return QuestionsSyncResult(**result)


# ─── ИИ-агенты автоответов ───────────────────────────────────────────────────


@router.get("/reply-agents", response_model=list[ReplyAgentItem])
async def list_reply_agents(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> list[ReplyAgentItem]:
    """Список ИИ-агентов автоответов проекта."""
    rows = await reply_service.list_agents(db, project.id)
    return [ReplyAgentItem(**r) for r in rows]


@router.post("/reply-agents", response_model=ReplyAgentItem)
async def create_reply_agent(
    body: ReplyAgentSave,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> ReplyAgentItem:
    """Создать ИИ-агента автоответов."""
    try:
        return ReplyAgentItem(
            **await reply_service.create_agent(db, project.id, body.model_dump(exclude_none=True))
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.patch("/reply-agents/{agent_id}", response_model=ReplyAgentItem)
async def update_reply_agent(
    agent_id: int,
    body: ReplyAgentSave,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> ReplyAgentItem:
    """Изменить агента автоответов."""
    try:
        res = await reply_service.update_agent(
            db, project.id, agent_id, body.model_dump(exclude_none=True)
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if res is None:
        raise HTTPException(404, "Агент не найден")
    return ReplyAgentItem(**res)


@router.delete("/reply-agents/{agent_id}", response_model=DeleteResponse)
async def delete_reply_agent(
    agent_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> DeleteResponse:
    """Удалить агента автоответов."""
    ok = await reply_service.delete_agent(db, project.id, agent_id)
    if not ok:
        raise HTTPException(404, "Агент не найден")
    return DeleteResponse(deleted=True, id=agent_id)


@router.post("/reply-agents/{agent_id}/run", response_model=ReplyAgentRunResult)
async def run_reply_agent(
    agent_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> ReplyAgentRunResult:
    """Прогнать агента: LLM генерирует черновики ответов на неотвеченные отзывы/вопросы."""
    try:
        res = await reply_service.run_reply_agent(db, project.id, agent_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return ReplyAgentRunResult(**res)


# ─── База знаний товаров (wb_product_kb) ─────────────────────────────────────


@router.get("/kb/products", response_model=KbProductsResponse)
async def list_kb_products(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> KbProductsResponse:
    """Товары проекта с числом записей базы знаний (+ имя/артикул из зеркала)."""
    return KbProductsResponse(**await reply_service.list_kb_products(db, project.id))


@router.get("/kb", response_model=KbListResponse)
async def list_kb(
    nm_id: int | None = Query(None, description="Фильтр по товару (nm_id)"),
    enabled: bool | None = Query(None, description="Фильтр по enabled (None — все)"),
    take: int = Query(200, ge=1, le=1000),
    skip: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> KbListResponse:
    """Записи базы знаний проекта (фильтры: товар, enabled)."""
    return KbListResponse(
        **await reply_service.list_kb(db, project.id, nm_id=nm_id, enabled=enabled, take=take, skip=skip)
    )


@router.post("/kb", response_model=KbItem)
async def create_kb(
    body: KbSave,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> KbItem:
    """Создать запись базы знаний вручную (source=manual)."""
    try:
        return KbItem(**await reply_service.create_kb(db, project.id, body.model_dump(exclude_none=True)))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.patch("/kb/{kb_id}", response_model=KbItem)
async def update_kb(
    kb_id: int,
    body: KbSave,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> KbItem:
    """Изменить запись КБ (enabled=false — мягкое отключение без удаления)."""
    try:
        res = await reply_service.update_kb(db, project.id, kb_id, body.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if res is None:
        raise HTTPException(404, "Запись базы знаний не найдена")
    return KbItem(**res)


@router.delete("/kb/{kb_id}", response_model=DeleteResponse)
async def delete_kb(
    kb_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> DeleteResponse:
    """Удалить запись КБ (мягкая альтернатива — PATCH enabled=false)."""
    ok = await reply_service.delete_kb(db, project.id, kb_id)
    if not ok:
        raise HTTPException(404, "Запись базы знаний не найдена")
    return DeleteResponse(deleted=True, id=kb_id)


@router.post("/kb/import", response_model=KbImportResult)
async def import_kb(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> KbImportResult:
    """Импорт базы знаний из архива отвеченных вопросов (wb_questions, дедуп по md5)."""
    return KbImportResult(**await reply_service.import_kb_from_answered_questions(db, project.id))


# ─── Зеркало карточек WB (wb_product_cards) ──────────────────────────────────


@router.post("/cards/sync", response_model=CardSyncResult)
async def sync_cards(
    nm_ids: list[int] | None = Query(
        None, description="Конкретные nm_id (None — все из КБ/зеркал проекта)"
    ),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> CardSyncResult:
    """On-demand синк карточек WB (публичный basket-API) в зеркало wb_product_cards."""
    try:
        res = await wb_cards_service.sync_project_cards(db, project.id, nm_ids)
    except (CircuitOpenError, RateLimitError) as e:
        raise HTTPException(429, str(e)) from None
    except httpx.HTTPError as e:
        raise HTTPException(502, f"WB недоступен: {e}") from None
    return CardSyncResult(**res)


@router.get("/cards/{nm_id}", response_model=CardItem)
async def get_card(
    nm_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> CardItem:
    """Карточка товара из зеркала (описание, характеристики, URL фото для UI)."""
    card = await wb_cards_service.get_card(db, project.id, nm_id)
    if card is None:
        raise HTTPException(404, "Карточка не синкнута (POST /cards/sync)")
    return CardItem(**card)


# ─── Слежение за поступлением товара (wb_stock_watches) ──────────────────────


@router.get("/stock-watches", response_model=StockWatchListResponse)
async def list_stock_watches(
    status: str | None = Query(None, description="Фильтр: watching|drafted|dismissed"),
    take: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> StockWatchListResponse:
    """Список слежений «вопрос → ждём поступление» (+ текст вопроса, счётчики статусов)."""
    return StockWatchListResponse(
        **await stock_watch_service.list_stock_watches(db, project.id, status=status, take=take, skip=skip)
    )


@router.post("/stock-watches/scan", response_model=StockWatchScanResult)
async def scan_stock_watches(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> StockWatchScanResult:
    """On-demand перескан неотвеченных вопросов о наличии → watches (идемпотентно)."""
    return StockWatchScanResult(**await stock_watch_service.scan_stock_questions(db, project.id))


# ─── Ответы на отзывы/вопросы (черновики → отправка) ─────────────────────────


@router.get("/replies", response_model=RepliesListResponse)
async def list_replies(
    status: str | None = Query(None, description="Фильтр: draft|approved|sent|error|rejected"),
    take: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> RepliesListResponse:
    """Список ответов/черновиков проекта с данными цели (текст отзыва/вопроса, рейтинг, товар)."""
    data = await reply_service.list_replies(db, project.id, status=status, take=take, skip=skip)
    return RepliesListResponse(**data)


@router.post("/replies", response_model=ReplyItem)
async def create_reply(
    body: ReplyCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> ReplyItem:
    """Создать ручной черновик ответа на отзыв/вопрос (source=manual)."""
    try:
        return ReplyItem(**await reply_service.create_draft(db, project.id, body.model_dump()))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.patch("/replies/{reply_id}", response_model=ReplyItem)
async def update_reply(
    reply_id: int,
    body: ReplyUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> ReplyItem:
    """Редактировать черновик (text) и/или сменить статус (action=approve|reject|reopen)."""
    try:
        res = await reply_service.update_draft(
            db, project.id, reply_id, body.model_dump(exclude_none=True)
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if res is None:
        raise HTTPException(404, "Ответ не найден")
    return ReplyItem(**res)


@router.post("/replies/send", response_model=ReplySendResult, status_code=202)
async def send_replies(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
) -> ReplySendResult:
    """
    Отправить все approved-ответы проекта в WB.

    Отправка идёт фоном (лимит WB 1 rps — до ~50 ответов за прогон ≈ до минуты),
    эндпоинт сразу возвращает 202 + сколько approved стоит в очереди. Итог —
    в GET /replies (status sent/error).
    """
    import asyncio

    from sqlalchemy import func, select

    from backend.database import AsyncSessionLocal
    from backend.models import WBFeedbackReply

    project_id = project.id
    pending = await db.scalar(
        select(func.count(WBFeedbackReply.id)).where(
            WBFeedbackReply.project_id == project_id,
            WBFeedbackReply.status == "approved",
        )
    )
    pending = int(pending or 0)
    if pending:

        async def _bg_send() -> None:
            try:
                async with AsyncSessionLocal() as bg_db:
                    await reply_service.send_pending_replies(bg_db, project_id)
            except Exception as e:  # noqa: BLE001 — фоновая отправка не должна падать молча
                logger.error("replies send bg: project %d failed — %s", project_id, e, exc_info=True)

        asyncio.create_task(_bg_send())  # noqa: RUF006
    return ReplySendResult(sent=0, errors=0, pending=pending)
