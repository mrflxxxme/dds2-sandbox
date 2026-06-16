"""add ff_notify_enabled flag to telegram_chat_bindings

Per-chat opt-in for fulfillment status-change Telegram notifications
(assembly READY + inbound acceptance), independent from the daily-digest
`notify_enabled` flag. Default false so no chat is notified until opted in.

Revision ID: de05_ff_notify_enabled
Revises: ff06_box_overrides
Create Date: 2026-06-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "de05_ff_notify_enabled"
down_revision: str | None = "ff06_box_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_chat_bindings",
        sa.Column("ff_notify_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("telegram_chat_bindings", "ff_notify_enabled")
