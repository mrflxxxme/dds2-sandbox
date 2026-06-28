# ruff: noqa: RUF002, RUF003
"""category_ref: флаг is_cogs («себестоимость товара»)

Расходные категории, помеченные `is_cogs`, исключаются из расходных отчётов
(дашборд, ДДС) — такие выплаты (таможня/ФТС, поставщики, межд. логистика)
капитализируются в стоимость товара, а не считаются операционным расходом.

Revision ID: cogs01_category_ref_is_cogs
Revises: pay07_payment_request_general
Create Date: 2026-06-28 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "cogs01_category_ref_is_cogs"
down_revision: str | None = "pay07_payment_request_general"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "category_ref",
        sa.Column("is_cogs", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("category_ref", "is_cogs")
