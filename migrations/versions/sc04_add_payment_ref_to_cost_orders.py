"""add payment_ref to cost_orders

Revision ID: sc04_add_payment_ref
Revises: sc03_fix_money_precision
Create Date: 2026-04-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "sc04_add_payment_ref"
down_revision: str | None = "sc03_fix_money_precision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cost_orders",
        sa.Column("payment_ref", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cost_orders", "payment_ref")
