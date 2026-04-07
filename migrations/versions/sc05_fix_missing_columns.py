"""fix missing columns: factory_order_items.project_id, vehicle_status_history.changed_by/changed_at

Revision ID: sc05_fix_missing_columns
Revises: sc04_add_payment_ref
Create Date: 2026-04-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "sc05_fix_missing_columns"
down_revision: str | None = "sc04_add_payment_ref"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. factory_order_items: add missing project_id column + FK + index
    op.add_column(
        "factory_order_items",
        sa.Column("project_id", sa.Integer(), nullable=True),
    )
    # Backfill project_id from parent factory_orders
    op.execute(
        """
        UPDATE factory_order_items foi
        SET project_id = fo.project_id
        FROM factory_orders fo
        WHERE foi.factory_order_id = fo.id
          AND foi.project_id IS NULL
        """
    )
    # Now make it NOT NULL
    op.alter_column("factory_order_items", "project_id", nullable=False)
    op.create_foreign_key(
        "fk_factory_order_items_project",
        "factory_order_items",
        "projects",
        ["project_id"],
        ["id"],
    )
    op.create_index("ix_factory_order_items_project_id", "factory_order_items", ["project_id"])

    # 2. vehicle_status_history: add missing changed_by column
    op.add_column(
        "vehicle_status_history",
        sa.Column("changed_by", sa.String(100), nullable=True),
    )

    # 3. vehicle_status_history: fix changed_at timezone (DateTime → DateTime(timezone=True))
    op.alter_column(
        "vehicle_status_history",
        "changed_at",
        type_=sa.DateTime(timezone=True),
        existing_type=sa.DateTime(),
        existing_nullable=False,
    )


def downgrade() -> None:
    # 3. Revert changed_at timezone
    op.alter_column(
        "vehicle_status_history",
        "changed_at",
        type_=sa.DateTime(),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )

    # 2. Remove changed_by
    op.drop_column("vehicle_status_history", "changed_by")

    # 1. Remove project_id from factory_order_items
    op.drop_index("ix_factory_order_items_project_id", table_name="factory_order_items")
    op.drop_constraint("fk_factory_order_items_project", "factory_order_items", type_="foreignkey")
    op.drop_column("factory_order_items", "project_id")
