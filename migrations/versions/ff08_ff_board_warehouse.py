"""bind ff_board to a single warehouse (telegram_chat_bindings.ff_board_warehouse_id)

Per-warehouse pinned FF-board: a chat can be scoped to one fulfillment warehouse
(e.g. the Газпром chat) so its board shows only that warehouse's active assembly
requests. NULL = the all-warehouses board (current behaviour) — the column is
nullable and defaults to NULL, so existing boards are unaffected. ON DELETE
SET NULL falls a scoped board back to the global view if its warehouse is removed.

Revision ID: ff08_ff_board_warehouse
Revises: ff07_ff_board
Create Date: 2026-06-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff08_ff_board_warehouse"
down_revision: str | None = "ff07_ff_board"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_chat_bindings",
        sa.Column("ff_board_warehouse_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_telegram_chat_bindings_ff_board_warehouse_id",
        "telegram_chat_bindings",
        ["ff_board_warehouse_id"],
    )
    op.create_foreign_key(
        "fk_telegram_chat_bindings_ff_board_warehouse_id",
        "telegram_chat_bindings",
        "warehouses",
        ["ff_board_warehouse_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_telegram_chat_bindings_ff_board_warehouse_id",
        "telegram_chat_bindings",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_telegram_chat_bindings_ff_board_warehouse_id",
        table_name="telegram_chat_bindings",
    )
    op.drop_column("telegram_chat_bindings", "ff_board_warehouse_id")
