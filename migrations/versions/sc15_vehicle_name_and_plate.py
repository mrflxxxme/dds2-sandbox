"""Add vehicle_name and plate_number to cost_orders (vehicles).

- `vehicle_name` — user-editable display label (order_no stays as immutable ID)
- `plate_number` — truck license plate, optional

Revision ID: sc15_vehicle_name_plate
Revises: wh04_mark_ops
Create Date: 2026-04-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "sc15_vehicle_name_plate"
down_revision: str | None = "wh04_mark_ops"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cost_orders", sa.Column("vehicle_name", sa.String(length=200), nullable=True))
    op.add_column("cost_orders", sa.Column("plate_number", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("cost_orders", "plate_number")
    op.drop_column("cost_orders", "vehicle_name")
