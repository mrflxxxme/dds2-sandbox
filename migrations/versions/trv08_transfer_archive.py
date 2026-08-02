# ruff: noqa: RUF002, RUF003
"""Локальный архив переезда: ручное «убрать с глаз»

Зеркало `fulfillment_requests.local_archived`. НЕ soft-delete: архивный переезд
остаётся в отчётах и в остатках (его сток реально уехал), прячется только из
рабочих списков.

Отличается от вида «Архив» в UI, который вычисляется по статусу
(DELIVERED/CLOSED/CANCELLED + брак): там решает система, здесь — человек.

Индекс частичный — архивных всегда меньшинство, а спрашивают их отдельным
видом; полный индекс по boolean на этой таблице бесполезен.

Revision ID: trv08_transfer_archive
Revises: trv07_transfer_pickup_attempt
Create Date: 2026-08-02 19:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "trv08_transfer_archive"
down_revision: str | None = "trv07_transfer_pickup_attempt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stock_transfers",
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("stock_transfers", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_stock_transfers_archived",
        "stock_transfers",
        ["project_id"],
        postgresql_where=sa.text("archived = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_stock_transfers_archived", table_name="stock_transfers")
    op.drop_column("stock_transfers", "archived_at")
    op.drop_column("stock_transfers", "archived")
