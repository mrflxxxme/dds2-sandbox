"""wb_stock_watches: колонка last_qty (остаток при последней проверке тика)

Revision ID: sw02_wb_stock_watches_last_qty
Revises: sw01_wb_stock_watches
Create Date: 2026-07-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "sw02_wb_stock_watches_last_qty"
down_revision: str | None = "sw01_wb_stock_watches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wb_stock_watches",
        sa.Column("last_qty", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wb_stock_watches", "last_qty")
