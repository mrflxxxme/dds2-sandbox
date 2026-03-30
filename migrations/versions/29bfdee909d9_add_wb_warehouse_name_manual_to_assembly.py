"""add_wb_warehouse_name_manual_to_assembly

Revision ID: 29bfdee909d9
Revises: be4b097b260d
Create Date: 2026-03-30 07:25:06.819225

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "29bfdee909d9"
down_revision: str | None = "be4b097b260d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "assembly_requests",
        sa.Column("wb_warehouse_name_manual", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assembly_requests", "wb_warehouse_name_manual")
