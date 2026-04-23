"""add accounting_started_at to projects

Revision ID: 16112b2c4038
Revises: 1800fcf4d87e
Create Date: 2026-04-23 05:32:35.714125

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "16112b2c4038"
down_revision: str | None = "1800fcf4d87e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("accounting_started_at", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "accounting_started_at")
