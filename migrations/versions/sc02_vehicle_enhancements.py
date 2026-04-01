"""vehicle enhancements: container_type, actual_ship_date, SHIPPED status

Revision ID: sc02_vehicle_enh
Revises: sc01_supply_chain
Create Date: 2026-04-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "sc02_vehicle_enh"
down_revision: str | None = "sc01_supply_chain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add new columns to cost_orders
    op.add_column("cost_orders", sa.Column("container_type", sa.String(20), nullable=True))
    op.add_column("cost_orders", sa.Column("actual_ship_date", sa.Date(), nullable=True))

    # 2. Rename enum value IN_TRANSIT → SHIPPED (data updated automatically by PG)
    op.execute("ALTER TYPE vehiclestatus RENAME VALUE 'IN_TRANSIT' TO 'SHIPPED'")


def downgrade() -> None:
    # Revert enum value
    op.execute("ALTER TYPE vehiclestatus RENAME VALUE 'SHIPPED' TO 'IN_TRANSIT'")

    # Drop columns
    op.drop_column("cost_orders", "actual_ship_date")
    op.drop_column("cost_orders", "container_type")
