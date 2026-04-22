"""fbo_supply_return_type_and_qty

Revision ID: 1800fcf4d87e
Revises: d8ac3fd13842
Create Date: 2026-04-22 10:39:47.351231

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1800fcf4d87e"
down_revision: str | None = "d8ac3fd13842"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wb_fbo_supplies",
        sa.Column("return_type", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "wb_fbo_supplies",
        sa.Column("return_qty", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wb_fbo_supplies", "return_qty")
    op.drop_column("wb_fbo_supplies", "return_type")
