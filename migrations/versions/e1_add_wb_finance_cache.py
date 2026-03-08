"""add wb_finance_rows and wb_finance_sync_log tables

Revision ID: e1a1b1c1d1e1
Revises: 23dff53314f5
Create Date: 2026-03-08 05:15:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e1a1b1c1d1e1'
down_revision: Union[str, None] = '23dff53314f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'wb_finance_rows',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('realizationreport_id', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('rrd_id', sa.BigInteger(), nullable=False),
        sa.Column('date_from', sa.Date(), nullable=False),
        sa.Column('date_to', sa.Date(), nullable=False),
        sa.Column('sa_name', sa.String(200), nullable=True),
        sa.Column('nm_id', sa.Integer(), server_default='0'),
        sa.Column('brand_name', sa.String(200), nullable=True),
        sa.Column('subject_name', sa.String(200), nullable=True),
        sa.Column('doc_type_name', sa.String(100), nullable=True),
        sa.Column('supplier_oper_name', sa.String(200), nullable=True),
        sa.Column('quantity', sa.Integer(), server_default='0'),
        sa.Column('retail_price_withdisc_rub', sa.Numeric(18, 2), server_default='0'),
        sa.Column('retail_amount', sa.Numeric(18, 2), server_default='0'),
        sa.Column('ppvz_for_pay', sa.Numeric(18, 2), server_default='0'),
        sa.Column('ppvz_sales_commission', sa.Numeric(18, 2), server_default='0'),
        sa.Column('delivery_rub', sa.Numeric(18, 2), server_default='0'),
        sa.Column('penalty', sa.Numeric(18, 2), server_default='0'),
        sa.Column('additional_payment', sa.Numeric(18, 2), server_default='0'),
        sa.Column('storage_fee', sa.Numeric(18, 2), server_default='0'),
        sa.Column('acceptance', sa.Numeric(18, 2), server_default='0'),
        sa.Column('deduction', sa.Numeric(18, 2), server_default='0'),
        sa.Column('ppvz_reward', sa.Numeric(18, 2), server_default='0'),
        sa.Column('rebill_logistic_cost', sa.Numeric(18, 2), server_default='0'),
        sa.Column('ppvz_vw', sa.Numeric(18, 2), server_default='0'),
        sa.Column('ppvz_vw_nds', sa.Numeric(18, 2), server_default='0'),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('now()')),
    )
    op.create_unique_constraint('uq_wb_finance_rrd', 'wb_finance_rows', ['project_id', 'rrd_id'])
    op.create_index('ix_wb_finance_project_dates', 'wb_finance_rows', ['project_id', 'date_from', 'date_to'])
    op.create_index('ix_wb_finance_project_sa', 'wb_finance_rows', ['project_id', 'sa_name'])

    op.create_table(
        'wb_finance_sync_log',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id'), nullable=False),
        sa.Column('date_from', sa.Date(), nullable=False),
        sa.Column('date_to', sa.Date(), nullable=False),
        sa.Column('rows_synced', sa.Integer(), server_default='0'),
        sa.Column('status', sa.String(20), server_default="'OK'"),
        sa.Column('error_msg', sa.String(500), nullable=True),
        sa.Column('synced_at', sa.DateTime(), server_default=sa.text('now()')),
    )
    op.create_index('ix_wb_finance_sync_project', 'wb_finance_sync_log', ['project_id', 'synced_at'])


def downgrade() -> None:
    op.drop_index('ix_wb_finance_sync_project', table_name='wb_finance_sync_log')
    op.drop_table('wb_finance_sync_log')
    op.drop_index('ix_wb_finance_project_sa', table_name='wb_finance_rows')
    op.drop_index('ix_wb_finance_project_dates', table_name='wb_finance_rows')
    op.drop_constraint('uq_wb_finance_rrd', 'wb_finance_rows', type_='unique')
    op.drop_table('wb_finance_rows')
