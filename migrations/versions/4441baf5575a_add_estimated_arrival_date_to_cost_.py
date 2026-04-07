"""add estimated_arrival_date to cost_orders

Revision ID: 4441baf5575a
Revises: ab60160de0fc
Create Date: 2026-04-07 08:22:07.319232

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4441baf5575a"
down_revision: str | None = "ab60160de0fc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cost_orders", sa.Column("estimated_arrival_date", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("cost_orders", "estimated_arrival_date")
