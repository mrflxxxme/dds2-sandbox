# ruff: noqa: RUF002, RUF003
"""add box_size to box_qty_per_warehouse

Revision ID: box_qty_pw_size
Revises: box_qty_per_order
Create Date: 2026-05-19 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "box_qty_pw_size"
down_revision: str | None = "box_qty_per_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Ручной per-ФФ размер коробки — рядом с per-ФФ кратностью box_qty.
    op.add_column(
        "box_qty_per_warehouse",
        sa.Column("box_size", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("box_qty_per_warehouse", "box_size")
