"""add_soft_delete_columns

Revision ID: 69703897c946
Revises: 7b78c9a66753
Create Date: 2026-03-02 06:07:40.190035

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '69703897c946'
down_revision: Union[str, None] = '7b78c9a66753'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Soft delete columns for orders
    op.add_column('orders', sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('orders', sa.Column('deleted_at', sa.DateTime(), nullable=True))

    # Soft delete columns for planned_payments
    op.add_column('planned_payments', sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('planned_payments', sa.Column('deleted_at', sa.DateTime(), nullable=True))

    # Soft delete columns for cost_orders
    op.add_column('cost_orders', sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('cost_orders', sa.Column('deleted_at', sa.DateTime(), nullable=True))

    # Soft delete columns for transactions
    op.add_column('transactions', sa.Column('is_deleted', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('transactions', sa.Column('deleted_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('transactions', 'deleted_at')
    op.drop_column('transactions', 'is_deleted')
    op.drop_column('cost_orders', 'deleted_at')
    op.drop_column('cost_orders', 'is_deleted')
    op.drop_column('planned_payments', 'deleted_at')
    op.drop_column('planned_payments', 'is_deleted')
    op.drop_column('orders', 'deleted_at')
    op.drop_column('orders', 'is_deleted')
