"""Add bonus_type_name to wb_finance_rows.

Revision ID: g1_add_bonus_type_name
Revises: f1_add_rr_dt
Create Date: 2026-03-08
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'g1_add_bonus_type_name'
down_revision = 'f1_add_rr_dt'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('wb_finance_rows', sa.Column('bonus_type_name', sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column('wb_finance_rows', 'bonus_type_name')
