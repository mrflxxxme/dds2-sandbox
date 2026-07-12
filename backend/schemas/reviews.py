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
    count_unanswered: int = 0
    count_archive: int = 0
    average_rating: float | None = None
    # False → у проекта не настроен активный WB-ключ (фронт покажет подсказку)
    has_key: bool = True
