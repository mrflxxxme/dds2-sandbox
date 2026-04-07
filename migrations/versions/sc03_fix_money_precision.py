"""fix_money_precision: Numeric(14,2) → Numeric(18,2) for factory_orders.total_cny

Revision ID: sc03_fix_money_precision
Revises: c0813b34e34b
Create Date: 2026-04-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "sc03_fix_money_precision"
down_revision: str | None = "c0813b34e34b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "factory_orders",
        "total_cny",
        type_=sa.Numeric(18, 2),
        existing_type=sa.Numeric(14, 2),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "factory_orders",
        "total_cny",
        type_=sa.Numeric(14, 2),
        existing_type=sa.Numeric(18, 2),
        existing_nullable=True,
    )
