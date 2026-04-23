"""add excess fields to wb_fbo_supplies

Revision ID: 4c7491a7ec5b
Revises: 1bb8199bd70c
Create Date: 2026-04-23 06:57:30.827475

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c7491a7ec5b"
down_revision: str | None = "1bb8199bd70c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wb_fbo_supplies",
        sa.Column("excess_processed_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "wb_fbo_supplies",
        sa.Column("excess_qty", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wb_fbo_supplies", "excess_qty")
    op.drop_column("wb_fbo_supplies", "excess_processed_at")
