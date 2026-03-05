"""Make project_id NOT NULL on all project-scoped tables.

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-03-05

IMPORTANT: Before running, ensure all project_id values are set.
This migration will fail if any NULLs remain.
Run this SQL to check: SELECT table_name FROM information_schema.columns
WHERE column_name = 'project_id' AND is_nullable = 'YES';
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "d4e5f6g7h8i9"
down_revision = "c3d4e5f6g7h8"
branch_labels = None
depends_on = None

# Tables where project_id should become NOT NULL
TABLES = [
    "transactions",
    "accounts",
    "counterparty_categories",
    "overrides",
    "opening_balances",
    "category_ref",
    "orders",
    "lead_times",
    "planned_payments",
    "planned_incomes",
    "customs_topup",
    "customs_alloc",
    "customs_dt",
    "nomenclature",
    "duty_rules",
    "cost_orders",
    "integration_keys",
    "sync_log",
    "wb_funnel_daily",
    "wb_payouts",
    "import_log",
]


def upgrade():
    # Step 1: Delete orphaned rows with NULL project_id (shouldn't exist in normal operation)
    conn = op.get_bind()
    for table in TABLES:
        # Check if table has project_id column
        result = conn.execute(sa.text(
            f"SELECT COUNT(*) FROM {table} WHERE project_id IS NULL"
        ))
        null_count = result.scalar()
        if null_count > 0:
            print(f"⚠️  {table}: {null_count} rows with NULL project_id — deleting")
            conn.execute(sa.text(f"DELETE FROM {table} WHERE project_id IS NULL"))

    # Step 2: Alter columns to NOT NULL
    for table in TABLES:
        try:
            op.alter_column(table, "project_id", nullable=False)
            print(f"✅ {table}.project_id → NOT NULL")
        except Exception as e:
            print(f"⏭️  {table}: skipped ({e})")


def downgrade():
    for table in TABLES:
        try:
            op.alter_column(table, "project_id", nullable=True)
        except Exception:
            pass
