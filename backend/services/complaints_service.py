# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Service: жалобы на отзывы (для удаления) — `wb_feedback_complaints`.

Инструмент готовит текст жалобы и ФИКСИРУЕТ факт подачи + исход. Кандидаты —
низкооценённые отзывы (1–3★) из зеркала `wb_feedbacks`. Автоотправки в WB нет:
статус (удалено/не удалено) продавец проставляет вручную по итогу рассмотрения.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WBFeedback, WBFeedbackComplaint
from backend.models.wb_feedback_complaints import COMPLAINT_REASONS, COMPLAINT_STATUSES
from backend.schemas.reviews import (
    ComplaintBulkResult,
    ComplaintCandidate,
    ComplaintCandidatesResponse,
    ComplaintItem,
    ComplaintsResponse,
    ComplaintStats,
)
from backend.services.reviews_service import _has_wb_key, has_any_feedback
from backend.utils.time import utcnow

_CANDIDATE_MAX = 500
_BULK_MAX = 500  # кап массовой подачи за один прогон
_TERMINAL = {"removed", "rejected"}


async def list_candidates(
    db: AsyncSession,
    project_id: int,
    max_rating: int = 3,
    take: int = 100,
    only_open: bool = True,
) -> ComplaintCandidatesResponse:
    """Низкооценённые отзывы (1..max_rating) с текущим статусом жалобы."""
    max_rating = max(1, min(max_rating, 3))
    take = max(1, min(take, _CANDIDATE_MAX))

    complaint = (
        select(WBFeedbackComplaint.wb_feedback_id, WBFeedbackComplaint.status)
        .where(WBFeedbackComplaint.project_id == project_id)
        .subquery()
    )
    stmt = (
        select(WBFeedback, complaint.c.status)
        .outerjoin(complaint, complaint.c.wb_feedback_id == WBFeedback.wb_id)
        .where(
            WBFeedback.project_id == project_id,
            WBFeedback.rating >= 1,
            WBFeedback.rating <= max_rating,
        )
    )
    if only_open:
        stmt = stmt.where(complaint.c.status.is_(None))
    stmt = stmt.order_by(
        WBFeedback.has_text.desc(),
        WBFeedback.rating.asc(),
        WBFeedback.created_date.desc().nullslast(),
    ).limit(take)

    rows = (await db.execute(stmt)).all()
    items = [
        ComplaintCandidate(
            wb_feedback_id=f.wb_id,
            nm_id=f.nm_id,
            rating=f.rating,
            text=f.text or "",
            cons=f.cons,
            created_date=f.created_date.isoformat() if f.created_date else None,
            user_name=f.user_name,
            product_name=f.product_name,
            brand=f.brand,
            complaint_status=status,
        )
        for f, status in rows
    ]
    # всего накопившихся кандидатов без жалобы — для кнопки массовой подачи
    already = select(WBFeedbackComplaint.wb_feedback_id).where(
        WBFeedbackComplaint.project_id == project_id
    )
    total_open = await db.scalar(
        select(func.count(WBFeedback.id)).where(
            WBFeedback.project_id == project_id,
            WBFeedback.rating >= 1,
            WBFeedback.rating <= max_rating,
            WBFeedback.wb_id.notin_(already),
        )
    )

    has_key = bool(items) or await has_any_feedback(db, project_id) or await _has_wb_key(db, project_id)
    return ComplaintCandidatesResponse(items=items, total_open=int(total_open or 0), has_key=has_key)


async def _stats(db: AsyncSession, project_id: int) -> ComplaintStats:
    rows = (
        await db.execute(
            select(WBFeedbackComplaint.status, func.count(WBFeedbackComplaint.id))
            .where(WBFeedbackComplaint.project_id == project_id)
            .group_by(WBFeedbackComplaint.status)
        )
    ).all()
    by = {s: int(c) for s, c in rows}
    removed = by.get("removed", 0)
    rejected = by.get("rejected", 0)
    pending = by.get("pending", 0)
    filed = removed + rejected + pending
    closed = removed + rejected
    rate = round(removed / closed * 100, 1) if closed else None
    return ComplaintStats(filed=filed, removed=removed, rejected=rejected, pending=pending, removal_rate=rate)


def _to_item(c: WBFeedbackComplaint, review_text: str | None, product_name: str | None) -> ComplaintItem:
    return ComplaintItem(
        id=c.id,
        wb_feedback_id=c.wb_feedback_id,
        nm_id=c.nm_id,
        rating=c.rating,
        reason=c.reason,
        status=c.status,
        text=c.text,
        note=c.note,
        created_at=c.created_at.isoformat() if c.created_at else None,
        resolved_at=c.resolved_at.isoformat() if c.resolved_at else None,
        product_name=product_name,
        review_text=review_text,
    )


async def get_complaints(db: AsyncSession, project_id: int, status: str | None = None) -> ComplaintsResponse:
    """Поданные жалобы (+ снапшот отзыва) и KPI."""
    stmt = (
        select(WBFeedbackComplaint, WBFeedback.text, WBFeedback.product_name)
        .outerjoin(
            WBFeedback,
            (WBFeedback.project_id == project_id) & (WBFeedback.wb_id == WBFeedbackComplaint.wb_feedback_id),
        )
        .where(WBFeedbackComplaint.project_id == project_id)
        .order_by(WBFeedbackComplaint.created_at.desc())
        .limit(_CANDIDATE_MAX)
    )
    if status in COMPLAINT_STATUSES:
        stmt = stmt.where(WBFeedbackComplaint.status == status)
    rows = (await db.execute(stmt)).all()
    items = [_to_item(c, txt, pname) for c, txt, pname in rows]
    stats = await _stats(db, project_id)
    has_key = bool(stats.filed) or await has_any_feedback(db, project_id) or await _has_wb_key(db, project_id)
    return ComplaintsResponse(items=items, stats=stats, has_key=has_key)


async def create_complaint(
    db: AsyncSession, project_id: int, wb_feedback_id: str, reason: str, text: str
) -> ComplaintItem:
    """Зафиксировать подачу жалобы на отзыв (idempotent по отзыву)."""
    wb_feedback_id = (wb_feedback_id or "").strip()
    text = (text or "").strip()
    if not wb_feedback_id or not text:
        raise ValueError("Нужны id отзыва и текст жалобы")
    if reason not in COMPLAINT_REASONS:
        reason = "not_related"

    fb = (
        await db.execute(
            select(WBFeedback).where(
                WBFeedback.project_id == project_id, WBFeedback.wb_id == wb_feedback_id
            )
        )
    ).scalar_one_or_none()
    if fb is None:
        raise ValueError("Отзыв не найден в зеркале")

    existing = (
        await db.execute(
            select(WBFeedbackComplaint).where(
                WBFeedbackComplaint.project_id == project_id,
                WBFeedbackComplaint.wb_feedback_id == wb_feedback_id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        # повторная подача по тому же отзыву — обновляем причину/текст, статус сохраняем
        existing.reason = reason
        existing.text = text
        c = existing
    else:
        c = WBFeedbackComplaint(
            project_id=project_id,
            wb_feedback_id=wb_feedback_id,
            nm_id=fb.nm_id,
            rating=fb.rating,
            reason=reason,
            text=text,
            status="pending",
        )
        db.add(c)
    await db.commit()
    await db.refresh(c)
    return _to_item(c, fb.text, fb.product_name)


async def bulk_create_complaints(
    db: AsyncSession, project_id: int, reason: str, text: str, max_rating: int = 3
) -> ComplaintBulkResult:
    """
    Зафиксировать жалобы на ВСЕ накопившиеся отзывы 1..max_rating без жалобы.

    Текст один на все (шаблон не привязан к конкретному отзыву). За прогон — не
    больше `_BULK_MAX` (остаток берётся повторным нажатием, `truncated=True`).
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Нужен текст жалобы")
    if reason not in COMPLAINT_REASONS:
        reason = "not_related"
    max_rating = max(1, min(max_rating, 3))

    already = select(WBFeedbackComplaint.wb_feedback_id).where(
        WBFeedbackComplaint.project_id == project_id
    )
    rows = (
        await db.execute(
            select(WBFeedback)
            .where(
                WBFeedback.project_id == project_id,
                WBFeedback.rating >= 1,
                WBFeedback.rating <= max_rating,
                WBFeedback.wb_id.notin_(already),
            )
            .order_by(WBFeedback.rating.asc(), WBFeedback.created_date.desc().nullslast())
            .limit(_BULK_MAX + 1)
        )
    ).scalars().all()

    truncated = len(rows) > _BULK_MAX
    for f in rows[:_BULK_MAX]:
        db.add(
            WBFeedbackComplaint(
                project_id=project_id,
                wb_feedback_id=f.wb_id,
                nm_id=f.nm_id,
                rating=f.rating,
                reason=reason,
                text=text,
                status="pending",
            )
        )
    await db.commit()
    return ComplaintBulkResult(created=min(len(rows), _BULK_MAX), truncated=truncated)


async def update_status(
    db: AsyncSession, project_id: int, complaint_id: int, status: str, note: str | None
) -> ComplaintItem | None:
    """Проставить исход жалобы (удалено/не удалено/в ожидании)."""
    if status not in COMPLAINT_STATUSES:
        raise ValueError("Недопустимый статус")
    c = (
        await db.execute(
            select(WBFeedbackComplaint).where(
                WBFeedbackComplaint.project_id == project_id,
                WBFeedbackComplaint.id == complaint_id,
            )
        )
    ).scalar_one_or_none()
    if c is None:
        return None
    c.status = status
    c.note = note
    c.resolved_at = utcnow() if status in _TERMINAL else None
    await db.commit()
    await db.refresh(c)
    return _to_item(c, None, None)
