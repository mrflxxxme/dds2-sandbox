"""add is_archived to wb_fbo_supplies

Revision ID: 1bb8199bd70c
Revises: 16112b2c4038
Create Date: 2026-04-23 06:36:30.078853

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1bb8199bd70c"
down_revision: str | None = "16112b2c4038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wb_fbo_supplies",
        sa.Column("is_archived", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wb_fbo_supplies", "is_archived")
