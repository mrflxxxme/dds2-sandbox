"""Add is_deleted and deleted_at to factory_order_items and cost_order_items.

BUG-9: factory_order_items — deleted parent orders leave orphaned items visible.
BUG-10: cost_order_items — same issue.

Adds SoftDeleteMixin columns so child items can be soft-deleted together
with their parents.

Revision ID: bugfix02_soft_del_items
Revises: bugfix01_add_project_id_indexes
Create Date: 2026-04-09
"""

import sqlalchemy as sa
from alembic import op

revision: str = "bugfix02_soft_del_items"
down_revision: str | None = "bugfix01_add_project_id_indexes"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # factory_order_items
    op.add_column(
        "factory_order_items",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "factory_order_items",
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )

    # cost_order_items
    op.add_column(
        "cost_order_items",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "cost_order_items",
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cost_order_items", "deleted_at")
    op.drop_column("cost_order_items", "is_deleted")
    op.drop_column("factory_order_items", "deleted_at")
    op.drop_column("factory_order_items", "is_deleted")
