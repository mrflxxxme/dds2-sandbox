# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Sync: зеркалим отзывы покупателей WB (feedbacks) в таблицу wb_feedbacks.

- Инкрементальный прогон тянет активные отзывы (isAnswered false+true).
- full_backfill=True дополнительно проходит архив WB (первичное наполнение
  истории — глубина ограничена тем, что WB ещё отдаёт).
Upsert по (project_id, wb_id): is_answered/text/answer могут меняться, поэтому
существующие строки обновляются. Ключи дедуплицируются ДО executemany
(CardinalityViolation при повторе wb_id между страницами/активом и архивом).
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WBFeedback
from backend.utils.time import utcnow

logger = logging.getLogger("dds.reviews.sync")

# WB feedbacks: take ≤ 5000 за запрос. Кап на прогон — защита от runaway-пагинации.
_PAGE = 5000
_MAX_PAGES = 40  # до 200k отзывов на прогон
_UPSERT_BATCH = 1000


def _parse_dt(raw: str | None) -> datetime | None:
    """WB createdDate (ISO, часто с 'Z') → naive UTC datetime."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(tz=None).replace(tzinfo=None)
    return dt


def _row_from_feedback(project_id: int, fb: dict, now: datetime) -> dict | None:
    """WB feedback dict → строка wb_feedbacks (или None, если нет id)."""
    wb_id = str(fb.get("id") or "").strip()
    if not wb_id:
        return None
    pd = fb.get("productDetails") or {}
    text = (fb.get("text") or "").strip() or None
    pros = (fb.get("pros") or "").strip() or None
    cons = (fb.get("cons") or "").strip() or None
    return {
        "project_id": project_id,
        "wb_id": wb_id,
        "nm_id": pd.get("nmId"),
        "rating": int(fb.get("productValuation") or 0),
        "text": text,
        "pros": pros,
        "cons": cons,
        "has_text": bool(text or pros or cons),
        "created_date": _parse_dt(fb.get("createdDate")),
        "user_name": (fb.get("userName") or "").strip() or None,
        "is_answered": bool(fb.get("answer")),
        "brand": (pd.get("brandName") or "").strip() or None,
        "product_name": (pd.get("productName") or "").strip() or None,
        "article": (pd.get("supplierArticle") or "").strip() or None,
        "synced_at": now,
    }


async def _upsert_rows(db: AsyncSession, rows: list[dict]) -> int:
    """Upsert строк по (project_id, wb_id). Возвращает число обработанных строк."""
    if not rows:
        return 0
    for i in range(0, len(rows), _UPSERT_BATCH):
        batch = rows[i : i + _UPSERT_BATCH]
        stmt = pg_insert(WBFeedback).values(batch)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_wb_feedbacks_project_wb_id",
            set_={
                "nm_id": stmt.excluded.nm_id,
                "rating": stmt.excluded.rating,
                "text": stmt.excluded.text,
                "pros": stmt.excluded.pros,
                "cons": stmt.excluded.cons,
                "has_text": stmt.excluded.has_text,
                "created_date": stmt.excluded.created_date,
                "user_name": stmt.excluded.user_name,
                "is_answered": stmt.excluded.is_answered,
                "brand": stmt.excluded.brand,
                "product_name": stmt.excluded.product_name,
                "article": stmt.excluded.article,
                "synced_at": stmt.excluded.synced_at,
                "updated_at": utcnow(),
            },
        )
        await db.execute(stmt)
    return len(rows)


async def sync_project_feedbacks(
    db: AsyncSession,
    project_id: int,
    api_key: str,
    full_backfill: bool = False,
) -> dict:
    """
    Синхронизировать отзывы проекта из WB в wb_feedbacks.

    full_backfill=True — дополнительно пройти архив (первичное наполнение).
    Возвращает {"rows_fetched": int, "rows_upserted": int}.
    """
    from backend.integrations.wb_api import WBApiClient

    client = WBApiClient(api_key, project_id=project_id)
    now = utcnow()

    # wb_id → row (dedup last-wins: архив/актив могут пересекаться)
    collected: dict[str, dict] = {}

    async def _drain_active(is_answered: bool) -> None:
        for page in range(_MAX_PAGES):
            data = await client.get_feedbacks(is_answered=is_answered, take=_PAGE, skip=page * _PAGE)
            feedbacks = data.get("feedbacks") or []
            for fb in feedbacks:
                if isinstance(fb, dict):
                    row = _row_from_feedback(project_id, fb, now)
                    if row:
                        collected[row["wb_id"]] = row
            if len(feedbacks) < _PAGE:
                break
        else:
            # Упёрлись в кап пагинации — часть отзывов не подтянута (не тихо!)
            logger.warning(
                "WB feedbacks sync: project %d — active(is_answered=%s) hit page cap "
                "(%d×%d), история усечена",
                project_id,
                is_answered,
                _MAX_PAGES,
                _PAGE,
            )

    await _drain_active(is_answered=False)
    await _drain_active(is_answered=True)

    if full_backfill:
        for page in range(_MAX_PAGES):
            data = await client.get_feedbacks_archive(take=_PAGE, skip=page * _PAGE)
            feedbacks = data.get("feedbacks") or []
            for fb in feedbacks:
                if isinstance(fb, dict):
                    row = _row_from_feedback(project_id, fb, now)
                    if row:
                        collected[row["wb_id"]] = row
            if len(feedbacks) < _PAGE:
                break
        else:
            logger.warning(
                "WB feedbacks sync: project %d — archive backfill hit page cap (%d×%d), "
                "история архива усечена",
                project_id,
                _MAX_PAGES,
                _PAGE,
            )

    rows = list(collected.values())
    upserted = await _upsert_rows(db, rows)
    await db.commit()

    logger.info(
        "WB feedbacks sync: project %d — fetched=%d, upserted=%d (backfill=%s)",
        project_id,
        len(rows),
        upserted,
        full_backfill,
    )
    return {"rows_fetched": len(rows), "rows_upserted": upserted}
