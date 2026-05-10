"""add box_qty_override to nomenclature

Revision ID: 7884bb220c30
Revises: b3a7d24f8c10
Create Date: 2026-05-10 08:59:40.412349

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7884bb220c30"
down_revision: str | None = "b3a7d24f8c10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "nomenclature",
        sa.Column("box_qty_override", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("nomenclature", "box_qty_override")
