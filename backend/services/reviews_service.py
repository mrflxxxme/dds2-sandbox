# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Service: WB customer feedbacks (отзывы).

Live-фетч через WB-ключ проекта (без зеркала в БД) — отзыв это «живые» данные
провайдера. Нет активного WB-ключа → возвращаем пустой ответ с has_key=False,
фронт показывает подсказку вместо ошибки.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.schemas.reviews import ReviewItem, ReviewsListResponse

logger = logging.getLogger("dds.reviews")


def _map_feedback(fb: dict) -> ReviewItem:
    """WB feedback dict → ReviewItem."""
    pd = fb.get("productDetails") or {}
    return ReviewItem(
        id=str(fb.get("id", "")),
        text=(fb.get("text") or "").strip(),
        rating=int(fb.get("productValuation") or 0),
        created_date=fb.get("createdDate"),
        user_name=(fb.get("userName") or "").strip() or None,
        pros=(fb.get("pros") or "").strip() or None,
        cons=(fb.get("cons") or "").strip() or None,
        nm_id=pd.get("nmId"),
        product_name=(pd.get("productName") or "").strip() or None,
        article=(pd.get("supplierArticle") or "").strip() or None,
        brand=(pd.get("brandName") or "").strip() or None,
        is_answered=bool(fb.get("answer")),
    )


async def list_reviews(
    db: AsyncSession,
    project_id: int,
    is_answered: bool = False,
    take: int = 100,
    skip: int = 0,
) -> ReviewsListResponse:
    """Список отзывов покупателей WB для проекта (live-фетч)."""
    from backend.services.funnel.wb_api_client import get_wb_key

    # Каскад ключей: специализированный feedbacks → аналитика → общий wb
    api_key = (
        await get_wb_key(db, project_id, "wb_feedbacks")
        or await get_wb_key(db, project_id, "wb_analytics")
        or await get_wb_key(db, project_id, "wb")
    )
    if not api_key:
        return ReviewsListResponse(items=[], has_key=False)

    from backend.integrations.wb_api import WBApiClient

    client = WBApiClient(api_key, project_id=project_id)
    data = await client.get_feedbacks(is_answered=is_answered, take=take, skip=skip)

    feedbacks = data.get("feedbacks") or []
    items = [_map_feedback(fb) for fb in feedbacks if isinstance(fb, dict)]

    ratings = [i.rating for i in items if i.rating > 0]
    avg = round(sum(ratings) / len(ratings), 2) if ratings else None

    return ReviewsListResponse(
        items=items,
        count_unanswered=int(data.get("countUnanswered") or 0),
        count_archive=int(data.get("countArchive") or 0),
        average_rating=avg,
        has_key=True,
    )
