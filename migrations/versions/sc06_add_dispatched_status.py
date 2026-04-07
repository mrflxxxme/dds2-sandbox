"""add DISPATCHED value to vehiclestatus enum

Revision ID: sc06_add_dispatched
Revises: sc05_fix_missing_columns
Create Date: 2026-04-07

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "sc06_add_dispatched"
down_revision: str | None = "sc05_fix_missing_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE vehiclestatus ADD VALUE IF NOT EXISTS 'DISPATCHED' AFTER 'CUSTOMS'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; no-op
    pass
