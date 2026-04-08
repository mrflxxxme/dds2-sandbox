"""Add box_detail JSONB column to factory_order_items.

Stores per-box qty breakdown: [28, 28, 28, 24] or null (auto-calc).

Revision ID: sc08_add_box_detail
Revises: sc07_cleanup_status
Create Date: 2026-04-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "sc08_add_box_detail"
down_revision: str | None = "sc07_cleanup_status"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("factory_order_items", sa.Column("box_detail", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("factory_order_items", "box_detail")
