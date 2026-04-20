"""Add box_detail_override to cost_order_items.

Allows recording actual box breakdown per vehicle item (different packing
than factory order plan) without modifying FactoryOrderItem.box_detail.

Revision ID: sc17_cost_item_box_detail_override
Revises: sc16_cost_item_box_override
Create Date: 2026-04-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "sc17_box_detail_override"
down_revision: str | None = "sc16_cost_item_box_override"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cost_order_items", sa.Column("box_detail_override", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("cost_order_items", "box_detail_override")
