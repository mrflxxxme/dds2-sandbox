"""WB FBS: убрать вычет черновиков сборки из формулы остатка

Решение владельца 2026-07-24: черновик распределения — это намерение логиста,
а не обязательство. Он может висеть часами, всё это время придерживая товар
от продажи по FBS. Обязательством считается только заявка на сборку — она
по-прежнему вычитается (`warehouse_stock_engine._get_reserved_map_batch`).

Revision ID: fbs03_drop_draft_reserve
Revises: fbs02_stock_rules
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision: str = "fbs03_drop_draft_reserve"
down_revision: str | None = "fbs02_stock_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("wb_fbs_warehouses", "subtract_draft_reserve")


def downgrade() -> None:
    op.add_column(
        "wb_fbs_warehouses",
        sa.Column("subtract_draft_reserve", sa.Boolean(), server_default="true", nullable=False),
    )
