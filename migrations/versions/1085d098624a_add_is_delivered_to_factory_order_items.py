"""add_is_delivered_to_factory_order_items

Revision ID: 1085d098624a
Revises: sc11_add_suppliers
Create Date: 2026-04-13 06:56:38.641211

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1085d098624a"
down_revision: str | None = "sc11_add_suppliers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "factory_order_items",
        sa.Column("is_delivered", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("factory_order_items", "is_delivered")
