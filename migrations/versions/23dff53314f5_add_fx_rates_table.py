"""add fx_rates table

Revision ID: 23dff53314f5
Revises: d1_add_vat_rate
Create Date: 2026-03-07 19:12:41.729117

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '23dff53314f5'
down_revision: Union[str, None] = 'd1_add_vat_rate'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('fx_rates',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('date', sa.Date(), nullable=False),
        sa.Column('pair', sa.String(length=10), nullable=False),
        sa.Column('rate', sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column('source', sa.String(length=50), nullable=True),
        sa.Column('txn_id', sa.String(length=300), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'date', 'pair', 'txn_id', name='uq_fx_rate_txn'),
    )
    op.create_index('ix_fx_rate_pair', 'fx_rates', ['pair'], unique=False)
    op.create_index('ix_fx_rate_project_date', 'fx_rates', ['project_id', 'date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_fx_rate_project_date', table_name='fx_rates')
    op.drop_index('ix_fx_rate_pair', table_name='fx_rates')
    op.drop_table('fx_rates')
