# ruff: noqa: RUF002 — русские комментарии
"""wb_returns: archived_at — ручная архивация «забрали без оформления»

Добавляет колонку `archived_at` (nullable timestamp). Выставляется пользователем,
чтобы спрятать безнадёжно забранные без оформления возвраты из вкладок «На ПВЗ» /
«В пути на склад» и показывать их только во вкладке «История».

Не пересекается с `is_deleted` (SoftDeleteMixin): WB-sync при каждом upsert
восстанавливает soft-deleted записи, а `archived_at` — локальное решение
бухгалтера/менеджера и sync его не трогает.

Revision ID: wh07_wb_returns_archived_at
Revises: wh06_wb_returns_hardening
Create Date: 2026-04-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "wh07_wb_returns_archived_at"
down_revision: str | None = "wh06_wb_returns_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wb_goods_returns",
        sa.Column("archived_at", sa.DateTime(), nullable=True),
    )
    # Partial index — 99% записей archived_at IS NULL, нужен только чтобы быстро
    # отфильтровать «живые» строки во вкладках pvz / in_transit.
    op.create_index(
        "ix_wb_goods_returns_archived_null",
        "wb_goods_returns",
        ["project_id"],
        unique=False,
        postgresql_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_wb_goods_returns_archived_null", table_name="wb_goods_returns")
    op.drop_column("wb_goods_returns", "archived_at")
