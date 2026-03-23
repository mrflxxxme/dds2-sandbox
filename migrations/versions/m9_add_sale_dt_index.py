"""Add index on sale_dt for wb_finance_rows.

BDR and OPIU reports now filter by COALESCE(sale_dt, rr_dt) instead of rr_dt alone.
Adding an index on sale_dt improves query performance for the new filter.

Revision ID: m9_add_sale_dt_index
Revises: m8_wb_order_cancel_daily
"""

from alembic import op

revision = "m9_add_sale_dt_index"
down_revision = "m8_wb_order_cancel_daily"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_wb_finance_project_sale_dt",
        "wb_finance_rows",
        ["project_id", "sale_dt"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_wb_finance_project_sale_dt", table_name="wb_finance_rows")
