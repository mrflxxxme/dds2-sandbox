# ruff: noqa: RUF002 — русские комментарии и docstring
"""Schemas: /reviews — WB customer feedbacks (отзывы покупателей)."""

from __future__ import annotations

from pydantic import BaseModel


class ReviewItem(BaseModel):
    """Один отзыв покупателя WB."""

    id: str
    text: str
    rating: int  # productValuation, 1..5 (0 если WB не прислал)
    created_date: str | None = None
    user_name: str | None = None
    pros: str | None = None
    cons: str | None = None
    nm_id: int | None = None
    product_name: str | None = None
    article: str | None = None
    brand: str | None = None
    is_answered: bool = False


class ReviewsListResponse(BaseModel):
    """Ответ списка отзывов + агрегаты."""

    items: list[ReviewItem]
    # Всего отзывов в текущем срезе (по фильтру is_answered) — для пагинации «показано N из M»
    total: int = 0
    count_unanswered: int = 0
    count_archive: int = 0
    average_rating: float | None = None
    # False → у проекта не настроен активный WB-ключ (фронт покажет подсказку)
    has_key: bool = True


# ─── Сводная аналитика отзывов (из зеркала БД) ──────────────────────────────


class ReviewsSummary(BaseModel):
    """Верхние KPI сводки отзывов."""

    average_rating: float | None = None  # средняя оценка (1..5), None если нет оценок
    total: int = 0  # всего отзывов
    count_no_text: int = 0  # только оценка, без текста/плюсов/минусов
    count_with_text: int = 0  # с текстом
    count_unanswered: int = 0  # без ответа продавца
    count_positive: int = 0  # оценка 4..5
    count_negative: int = 0  # оценка 1..2


class MonthlyRatingPoint(BaseModel):
    """Точка графика «средний рейтинг по бакету» (месяц или день — см. granularity)."""

    month: str  # YYYY-MM (granularity=month) либо YYYY-MM-DD (granularity=day)
    avg_rating: float | None = None
    count: int = 0


class MonthlyVolumePoint(BaseModel):
    """Точка графика «объём отзывов за бакет с разбивкой по оценкам»."""

    month: str  # YYYY-MM либо YYYY-MM-DD (см. granularity)
    r1: int = 0
    r2: int = 0
    r3: int = 0
    r4: int = 0
    r5: int = 0


class GroupRating(BaseModel):
    """Карточка сводного рейтинга по категории / бренду: средняя + распределение 1..5."""

    name: str
    avg_rating: float | None = None
    count: int = 0
    r1: int = 0
    r2: int = 0
    r3: int = 0
    r4: int = 0
    r5: int = 0


class NewcomerReview(BaseModel):
    """Проблемная новинка: товар недавно на продаже, но рейтинг ниже порога."""

    nm_id: int
    name: str  # название товара (снапшот WB) либо «nmID …»
    brand: str
    subject: str  # предмет/категория
    first_date: str  # YYYY-MM-DD — эффективная дата старта (продажа или первый отзыв)
    days_on_sale: int  # сколько дней «на продаже» по first_date
    avg_rating: float | None = None
    count: int = 0  # всего отзывов
    count_unanswered: int = 0
    r1: int = 0
    r2: int = 0
    r3: int = 0
    r4: int = 0
    r5: int = 0


class NewcomersResponse(BaseModel):
    """Раздел «Проблемные новинки»: новинки с рейтингом ниже порога."""

    items: list[NewcomerReview] = []
    days: int = 30  # окно «новинки» (дней на продаже)
    max_rating: float = 4.6  # порог «плохого» рейтинга
    # False → у проекта не настроен активный WB-ключ (фронт покажет подсказку)
    has_key: bool = True


class ReviewsSummaryResponse(BaseModel):
    """Полная сводка отзывов проекта (все блоки сводной страницы)."""

    summary: ReviewsSummary = ReviewsSummary()
    monthly_rating: list[MonthlyRatingPoint] = []
    monthly_volume: list[MonthlyVolumePoint] = []
    by_category: list[GroupRating] = []
    by_brand: list[GroupRating] = []
    # Гранулярность временных рядов: "day" (короткие периоды) либо "month"
    granularity: str = "month"
    # Применённый период выборки (2w/1m/3m/6m/1y/all) — эхо запроса для фронта
    period: str = "1y"
    # False → у проекта не настроен активный WB-ключ (фронт покажет подсказку)
    has_key: bool = True
