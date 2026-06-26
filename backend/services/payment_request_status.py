# ruff: noqa: RUF002, RUF003
"""
Payment-request status machine — переходы + аудит.

Хелперы session-agnostic: db.add() синхронен и в AsyncSession, и в Session, поэтому
их зовёт и async-сервис (submit / create-draft), и sync-матчер выписки
(etl/sync_payment_requests). Чистая логика перехода — без I/O.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.models.payment_request import (
    PAYMENT_REQUEST_TRANSITIONS,
    PaymentRequest,
    PaymentRequestEvent,
    PaymentRequestStatus,
)
from backend.utils.time import utcnow


def check_transition(current: PaymentRequestStatus, target: PaymentRequestStatus) -> None:
    """Validate a status transition. Raises ValueError if not allowed."""
    allowed = PAYMENT_REQUEST_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(
            f"Недопустимый переход {current.value} → {target.value}. Разрешено: "
            f"{', '.join(s.value for s in allowed) or 'нет'}"
        )


def log_event(
    db: AsyncSession | Session,
    request_id: int,
    old_status: str | None,
    new_status: str,
    changed_by: str = "user",
    comment: str | None = None,
) -> None:
    """Append a PaymentRequestEvent audit row (db.add is sync on both session types)."""
    db.add(
        PaymentRequestEvent(
            payment_request_id=request_id,
            old_status=old_status,
            new_status=new_status,
            changed_at=utcnow(),
            changed_by=changed_by,
            comment=comment,
        )
    )


def apply_transition(
    db: AsyncSession | Session,
    pr: PaymentRequest,
    target: PaymentRequestStatus,
    changed_by: str = "user",
    comment: str | None = None,
) -> None:
    """Validate + apply a transition on a persisted PaymentRequest and log the event."""
    old = pr.status
    check_transition(PaymentRequestStatus(old), target)
    pr.status = target.value
    log_event(db, pr.id, old, target.value, changed_by=changed_by, comment=comment)
