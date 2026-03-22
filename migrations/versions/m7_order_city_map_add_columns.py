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
    # Use IF NOT EXISTS for idempotency (columns may already exist on production)
    conn = op.get_bind()

    # 1. order_city_map: add okrug + order_date
    conn.execute(sa.text("ALTER TABLE order_city_map ADD COLUMN IF NOT EXISTS okrug VARCHAR(100)"))
    conn.execute(sa.text("ALTER TABLE order_city_map ADD COLUMN IF NOT EXISTS order_date DATE"))

    # 2. warehouses: add wb_acceptance_days
    conn.execute(sa.text("ALTER TABLE warehouses ADD COLUMN IF NOT EXISTS wb_acceptance_days INTEGER DEFAULT 2"))

    # 3. warehouse_delivery_times table
    conn.execute(
        sa.text("""
        CREATE TABLE IF NOT EXISTS warehouse_delivery_times (
            id SERIAL PRIMARY KEY,
            project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            warehouse_id INTEGER NOT NULL REFERENCES warehouses(id) ON DELETE CASCADE,
            wb_warehouse_name VARCHAR(255) NOT NULL,
            delivery_days INTEGER NOT NULL DEFAULT 3,
            created_at TIMESTAMP DEFAULT now(),
            updated_at TIMESTAMP DEFAULT now(),
            CONSTRAINT uq_wh_delivery_time UNIQUE (project_id, warehouse_id, wb_warehouse_name)
        )
    """)
    )
    conn.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_warehouse_delivery_times_project_id ON warehouse_delivery_times (project_id)"
        )
    )
    conn.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_warehouse_delivery_times_warehouse_id ON warehouse_delivery_times (warehouse_id)"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_warehouse_delivery_times_warehouse_id", table_name="warehouse_delivery_times")
    op.drop_index("ix_warehouse_delivery_times_project_id", table_name="warehouse_delivery_times")
    op.drop_table("warehouse_delivery_times")
    op.drop_column("warehouses", "wb_acceptance_days")
    op.drop_column("order_city_map", "order_date")
    op.drop_column("order_city_map", "okrug")
