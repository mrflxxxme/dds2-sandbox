"""Add mix_group_id to factory_order_items.

Allows grouping multiple items into a single "mix box" for delivery.
Items with the same mix_group_id are packed together as one box.

Revision ID: sc09_add_mix_group
Revises: sc08_add_box_detail
Create Date: 2026-04-08
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "sc09_add_mix_group"
down_revision: str | None = "sc08_add_box_detail"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("factory_order_items", sa.Column("mix_group_id", sa.String(36), nullable=True))
    op.create_index(
        "ix_factory_order_items_mix_group",
        "factory_order_items",
        ["factory_order_id", "mix_group_id"],
        postgresql_where=sa.text("mix_group_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_factory_order_items_mix_group", table_name="factory_order_items")
    op.drop_column("factory_order_items", "mix_group_id")
