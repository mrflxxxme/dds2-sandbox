"""add box_qty_per_warehouse table

Revision ID: 9e4a5b921752
Revises: 571a514c279d
Create Date: 2026-05-10 17:43:57.077449

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9e4a5b921752"
down_revision: str | None = "571a514c279d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "box_qty_per_warehouse",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("barcode", sa.String(length=50), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("box_qty", sa.Integer(), nullable=True),
        sa.Column(
            "use_box_multiplicity",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "project_id",
            "barcode",
            "warehouse_id",
            name="uq_box_qty_pw_project_bc_wh",
        ),
    )
    op.create_index(
        "ix_box_qty_pw_project_bc",
        "box_qty_per_warehouse",
        ["project_id", "barcode"],
    )


def downgrade() -> None:
    op.drop_index("ix_box_qty_pw_project_bc", table_name="box_qty_per_warehouse")
    op.drop_table("box_qty_per_warehouse")
