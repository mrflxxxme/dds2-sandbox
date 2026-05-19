"""add box_qty_per_order table

Revision ID: box_qty_per_order
Revises: 9e4a5b921752
Create Date: 2026-05-18 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "box_qty_per_order"
down_revision: str | None = "9e4a5b921752"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "box_qty_per_order",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "factory_order_item_id",
            sa.Integer(),
            sa.ForeignKey("factory_order_items.id"),
            nullable=False,
        ),
        sa.Column("box_qty", sa.Integer(), nullable=True),
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
            "factory_order_item_id",
            name="uq_box_qty_po_project_foi",
        ),
    )


def downgrade() -> None:
    op.drop_table("box_qty_per_order")
