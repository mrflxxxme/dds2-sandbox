"""Add vehicle detail fields to assembly_requests.

Revision ID: m6_assembly_vehicle_fields
Revises: m5_assembly_status_history
"""

import sqlalchemy as sa
from alembic import op

revision = "m6_assembly_vehicle_fields"
down_revision = "m5_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assembly_requests", sa.Column("vehicle_brand", sa.String(100), nullable=True))
    op.add_column("assembly_requests", sa.Column("driver_phone", sa.String(30), nullable=True))
    op.add_column("assembly_requests", sa.Column("pickup_date", sa.Date, nullable=True))
    op.add_column("assembly_requests", sa.Column("pickup_time_slot", sa.String(20), nullable=True))
    op.add_column("assembly_requests", sa.Column("pickup_cost", sa.Numeric(10, 2), nullable=True))
    op.add_column("assembly_requests", sa.Column("delivery_date", sa.Date, nullable=True))


def downgrade() -> None:
    op.drop_column("assembly_requests", "delivery_date")
    op.drop_column("assembly_requests", "pickup_cost")
    op.drop_column("assembly_requests", "pickup_time_slot")
    op.drop_column("assembly_requests", "pickup_date")
    op.drop_column("assembly_requests", "driver_phone")
    op.drop_column("assembly_requests", "vehicle_brand")
