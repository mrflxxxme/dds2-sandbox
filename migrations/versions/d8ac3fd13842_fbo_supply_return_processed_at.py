"""fbo_supply_return_processed_at

Revision ID: d8ac3fd13842
Revises: cp01_counterparties_loans
Create Date: 2026-04-21 07:59:56.488214

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8ac3fd13842"
down_revision: str | None = "cp01_counterparties_loans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wb_fbo_supplies",
        sa.Column("return_processed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wb_fbo_supplies", "return_processed_at")
