"""Add box_size_override and pcs_per_box_override to cost_order_items.

Per-vehicle override of box specs so that if actual arrival differs from the
factory order plan (different packing), users can record it without rewriting
the order plan.

Revision ID: sc16_cost_item_box_override
Revises: sc15_vehicle_name_plate
Create Date: 2026-04-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "sc16_cost_item_box_override"
down_revision: str | None = "sc15_vehicle_name_plate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cost_order_items", sa.Column("box_size_override", sa.String(length=50), nullable=True))
    op.add_column("cost_order_items", sa.Column("pcs_per_box_override", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("cost_order_items", "pcs_per_box_override")
    op.drop_column("cost_order_items", "box_size_override")
