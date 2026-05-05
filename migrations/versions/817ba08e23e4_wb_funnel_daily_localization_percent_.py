"""wb_funnel_daily: localization_percent + time_to_ready_minutes

Revision ID: 817ba08e23e4
Revises: 93f1cbd9fd52
Create Date: 2026-05-05 10:59:57.639132

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "817ba08e23e4"
down_revision: str | None = "93f1cbd9fd52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wb_funnel_daily",
        sa.Column("localization_percent", sa.Numeric(5, 2), nullable=True),
    )
    op.add_column(
        "wb_funnel_daily",
        sa.Column("time_to_ready_minutes", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wb_funnel_daily", "time_to_ready_minutes")
    op.drop_column("wb_funnel_daily", "localization_percent")
