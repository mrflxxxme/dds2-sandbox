# ruff: noqa: RUF001
"""add okrug + order_date to order_city_map, wb_acceptance_days to warehouses, warehouse_delivery_times table

Revision ID: m7_order_city_map_add_columns
Revises: 4b113f1a37a6
Create Date: 2026-03-22 10:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "m7_order_city_map_add_columns"
down_revision = "4b113f1a37a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. order_city_map: add okrug + order_date
    op.add_column(
        "order_city_map",
        sa.Column("okrug", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "order_city_map",
        sa.Column("order_date", sa.Date(), nullable=True),
    )

    # 2. warehouses: add wb_acceptance_days
    op.add_column(
        "warehouses",
        sa.Column("wb_acceptance_days", sa.Integer(), server_default="2", nullable=True),
    )

    # 3. warehouse_delivery_times table
    op.create_table(
        "warehouse_delivery_times",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), sa.ForeignKey("warehouses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("wb_warehouse_name", sa.String(length=255), nullable=False),
        sa.Column("delivery_days", sa.Integer(), server_default="3", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "warehouse_id", "wb_warehouse_name", name="uq_wh_delivery_time"),
    )
    op.create_index("ix_warehouse_delivery_times_project_id", "warehouse_delivery_times", ["project_id"])
    op.create_index("ix_warehouse_delivery_times_warehouse_id", "warehouse_delivery_times", ["warehouse_id"])


def downgrade() -> None:
    op.drop_index("ix_warehouse_delivery_times_warehouse_id", table_name="warehouse_delivery_times")
    op.drop_index("ix_warehouse_delivery_times_project_id", table_name="warehouse_delivery_times")
    op.drop_table("warehouse_delivery_times")
    op.drop_column("warehouses", "wb_acceptance_days")
    op.drop_column("order_city_map", "order_date")
    op.drop_column("order_city_map", "okrug")
