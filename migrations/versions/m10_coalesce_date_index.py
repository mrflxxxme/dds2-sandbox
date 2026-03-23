"""Add functional index on COALESCE(sale_dt, rr_dt) for wb_finance_rows.

BDR and OPIU queries filter by COALESCE(sale_dt, rr_dt) BETWEEN date_from AND date_to.
The existing index on (project_id, sale_dt) doesn't help because of the COALESCE.
A functional index on (project_id, COALESCE(sale_dt, rr_dt)) covers the actual filter.

Revision ID: m10_coalesce_date_index
Revises: m9_add_sale_dt_index
"""

from alembic import op

revision = "m10_coalesce_date_index"
down_revision = "m9_add_sale_dt_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
        "ix_wb_finance_project_coalesce_dt "
        "ON wb_finance_rows (project_id, COALESCE(sale_dt, rr_dt))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_wb_finance_project_coalesce_dt")
