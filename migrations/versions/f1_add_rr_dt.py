"""Add rr_dt, sale_dt, order_dt to wb_finance_rows.

Revision ID: f1_add_rr_dt
Revises: e1_add_wb_finance_cache
Create Date: 2026-03-08
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'f1_add_rr_dt'
down_revision = 'e1_add_wb_finance_cache'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('wb_finance_rows', sa.Column('rr_dt', sa.Date(), nullable=True))
    op.add_column('wb_finance_rows', sa.Column('sale_dt', sa.Date(), nullable=True))
    op.add_column('wb_finance_rows', sa.Column('order_dt', sa.Date(), nullable=True))
    op.create_index('ix_wb_finance_project_rr_dt', 'wb_finance_rows', ['project_id', 'rr_dt'])


def downgrade() -> None:
    op.drop_index('ix_wb_finance_project_rr_dt', table_name='wb_finance_rows')
    op.drop_column('wb_finance_rows', 'order_dt')
    op.drop_column('wb_finance_rows', 'sale_dt')
    op.drop_column('wb_finance_rows', 'rr_dt')
