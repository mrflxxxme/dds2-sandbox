"""Add country and delivery_cost_rub to cost_orders for Russian suppliers.

Russian vehicles use 'gazelle' container, RUB-only delivery (may be free),
no customs/duty/VAT calculation. Existing vehicles default to CHINA.

Revision ID: sc12_vehicle_country_rub
Revises: 984626694440
Create Date: 2026-04-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "sc12_vehicle_country_rub"
down_revision: str | None = "984626694440"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cost_orders",
        sa.Column("country", sa.String(10), nullable=False, server_default="CHINA"),
    )
    op.add_column(
        "cost_orders",
        sa.Column(
            "delivery_cost_rub",
            sa.Numeric(14, 2),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index("ix_cost_orders_country", "cost_orders", ["country"])


def downgrade() -> None:
    op.drop_index("ix_cost_orders_country", table_name="cost_orders")
    op.drop_column("cost_orders", "delivery_cost_rub")
    op.drop_column("cost_orders", "country")
