"""factory_order_status_and_history

Revision ID: c0813b34e34b
Revises: 4441baf5575a
Create Date: 2026-04-07 10:11:48.261549

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c0813b34e34b"
down_revision: str | None = "4441baf5575a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) Add status column to factory_orders
    op.add_column("factory_orders", sa.Column("status", sa.String(length=20), nullable=False, server_default="FORMING"))

    # 2) Create factory_order_history table
    op.create_table(
        "factory_order_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("factory_order_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("old_status", sa.String(length=20), nullable=True),
        sa.Column("new_status", sa.String(length=20), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changed_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["factory_order_id"], ["factory_orders.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_factory_order_history_project_id", "factory_order_history", ["project_id"], unique=False)
    op.create_index("ix_factory_order_history_order_id", "factory_order_history", ["factory_order_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_factory_order_history_order_id", table_name="factory_order_history")
    op.drop_index("ix_factory_order_history_project_id", table_name="factory_order_history")
    op.drop_table("factory_order_history")
    op.drop_column("factory_orders", "status")
