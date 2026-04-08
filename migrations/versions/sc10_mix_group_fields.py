"""Add mix_box_size and mix_pcs_per_box to factory_order_items.

Stores physical mix box dimensions and per-SKU quantity within the mix box,
separate from original box_size/pcs_per_box so originals are preserved.

Revision ID: sc10_mix_group_fields
Revises: sc09_add_mix_group
Create Date: 2026-04-08
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "sc10_mix_group_fields"
down_revision: str | None = "sc09_add_mix_group"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("factory_order_items", sa.Column("mix_box_size", sa.String(50), nullable=True))
    op.add_column("factory_order_items", sa.Column("mix_pcs_per_box", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("factory_order_items", "mix_pcs_per_box")
    op.drop_column("factory_order_items", "mix_box_size")
