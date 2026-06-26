# ruff: noqa: RUF002, RUF003
"""Убрать статус «Черновик»: существующие DRAFT-заявки → PENDING_REVIEW

Новый flow создаёт заявку сразу «На проверке» (PENDING_REVIEW). Старые заявки,
застрявшие в DRAFT (модалка закрыта до передачи в оплату), переводим в PENDING_REVIEW.
Чистая data-миграция, без изменения схемы; статус хранится как String(20).
downgrade необратим (какие именно заявки были DRAFT — не восстановить), поэтому no-op.

Revision ID: pay05_drop_draft_status
Revises: pay04_shipment_multilink
Create Date: 2026-06-26 12:40:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "pay05_drop_draft_status"
down_revision: str | None = "pay04_shipment_multilink"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE payment_request SET status = 'PENDING_REVIEW' "
        "WHERE status = 'DRAFT' AND is_deleted = false"
    )


def downgrade() -> None:
    # Необратимо: какие заявки были DRAFT — не знаем. No-op.
    pass
