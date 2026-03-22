# ruff: noqa: RUF001
"""add okrug, order_date, updated_at to order_city_map

Revision ID: m7_order_city_map_add_columns
Revises: m6_assembly_vehicle_fields
Create Date: 2026-03-22 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "m7_order_city_map_add_columns"
down_revision = "m6_assembly_vehicle_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "order_city_map",
        sa.Column("okrug", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "order_city_map",
        sa.Column("order_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "order_city_map",
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("order_city_map", "updated_at")
    op.drop_column("order_city_map", "order_date")
    op.drop_column("order_city_map", "okrug")
