"""add reassigned_to_supply_id to wb_fbo_supplies

Revision ID: a27da659cd77
Revises: 4c7491a7ec5b
Create Date: 2026-04-23 07:49:21.360591

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a27da659cd77"
down_revision: str | None = "4c7491a7ec5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "wb_fbo_supplies",
        sa.Column("reassigned_to_supply_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_wb_fbo_supplies_reassigned_to",
        "wb_fbo_supplies",
        ["reassigned_to_supply_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_wb_fbo_supplies_reassigned_to",
        "wb_fbo_supplies",
        "wb_fbo_supplies",
        ["reassigned_to_supply_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_wb_fbo_supplies_reassigned_to", "wb_fbo_supplies", type_="foreignkey")
    op.drop_index("ix_wb_fbo_supplies_reassigned_to", table_name="wb_fbo_supplies")
    op.drop_column("wb_fbo_supplies", "reassigned_to_supply_id")
