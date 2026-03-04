"""enable_row_level_security

Adds PostgreSQL Row-Level Security (RLS) policies to all tenant-scoped tables.
This ensures data isolation at the database level, independent of application logic.

How it works:
1. Each table gets RLS enabled
2. Policy: rows visible only when project_id = current_setting('app.project_id')
3. Superuser/owner bypass RLS (so migrations and seeds still work)
4. The FastAPI middleware sets `SET LOCAL app.project_id = :pid` per request

Revision ID: 1a1681117e29
Create Date: 2026-03-04
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '1a1681117e29'
down_revision = 'c3d4e5f6g7h8'
branch_labels = None
depends_on = None

# All tables with project_id column (verified from DB)
RLS_TABLES = [
    "transactions",
    "accounts",
    "counterparty_categories",
    "overrides",
    "opening_balances",
    "category_ref",
    "import_log",
    "customs_topup",
    "customs_alloc",
    "customs_dt",
    "orders",
    "lead_time",
    "planned_payments",
    "planned_incomes",
    "wb_payouts",
    "integration_keys",
    "wb_funnel_daily",
    "wb_cost_override",
    "nomenclature",
    "duty_rules",
    "cost_orders",
    "project_invites",
    "project_members",
]


def upgrade() -> None:
    for table in RLS_TABLES:
        # Enable RLS on table
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")

        # Policy: allow access only to rows matching app.project_id session var
        # USING = SELECT filter, WITH CHECK = INSERT/UPDATE filter
        policy_name = f"rls_{table}_project_isolation"
        op.execute(f"""
            CREATE POLICY {policy_name} ON {table}
            FOR ALL
            USING (
                project_id = NULLIF(current_setting('app.project_id', true), '')::int
            )
            WITH CHECK (
                project_id = NULLIF(current_setting('app.project_id', true), '')::int
            )
        """)

    # Also set FORCE RLS on the application role (if using a non-owner role)
    # For now we rely on the middleware to SET LOCAL app.project_id
    # Owner/superuser bypasses RLS by default


def downgrade() -> None:
    for table in RLS_TABLES:
        policy_name = f"rls_{table}_project_isolation"
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
