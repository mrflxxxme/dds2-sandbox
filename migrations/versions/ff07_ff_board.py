"""add ff_board fields to telegram_chat_bindings

Pinned auto-board of fulfillment requests: per-chat opt-in (ff_board_enabled)
plus the id of the pinned message (ff_board_message_id), which is edited in
place on every FF sync instead of sending a new one. Default false / NULL —
the board is off until the chat enables it via /board on.

Revision ID: ff07_ff_board
Revises: de05_ff_notify_enabled
Create Date: 2026-06-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff07_ff_board"
down_revision: str | None = "de05_ff_notify_enabled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_chat_bindings",
        sa.Column("ff_board_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "telegram_chat_bindings",
        sa.Column("ff_board_message_id", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("telegram_chat_bindings", "ff_board_message_id")
    op.drop_column("telegram_chat_bindings", "ff_board_enabled")
