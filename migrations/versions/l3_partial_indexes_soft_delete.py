"""Add partial indexes WHERE is_deleted = false for hot soft-delete tables.

Targets tables without partial indexes yet: projects (60k), factory_orders (5k),
factory_order_items (5k), cost_orders (5k), cost_order_items (4k).

Partial indexes are smaller than full ones and query planner prefers them for
`WHERE is_deleted = false` filters. CONCURRENTLY does not lock tables — safe
for production. Other tables (warehouse_stock, transactions, stock_movements,
audit_log, ...) already have partial indexes via bugfix01 / l1_indexes.

Revision ID: l3_partial_idx_sd
Revises: sc17_box_detail_override
Create Date: 2026-04-20
"""

from alembic import op

revision: str = "l3_partial_idx_sd"
down_revision: str | None = "sc17_box_detail_override"
branch_labels: str | None = None
depends_on: str | None = None


INDEXES: list[tuple[str, str, str]] = [
    # (index_name, table, column_list)
    ("ix_projects_active_owner", "projects", "owner_id"),
    ("ix_factory_orders_active_project", "factory_orders", "project_id"),
    ("ix_factory_order_items_active_order", "factory_order_items", "factory_order_id"),
    ("ix_cost_orders_active_project", "cost_orders", "project_id"),
    ("ix_cost_order_items_active_project", "cost_order_items", "project_id"),
]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for idx_name, table, cols in INDEXES:
            op.execute(
                f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {idx_name} " f"ON {table} ({cols}) WHERE is_deleted = false"
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for idx_name, _table, _cols in INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {idx_name}")
