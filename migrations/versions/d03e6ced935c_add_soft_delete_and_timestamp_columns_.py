"""add soft_delete and timestamp columns to 12 models

Revision ID: d03e6ced935c
Revises: k1_add_order_city_map
Create Date: 2026-03-13 09:13:46.884316

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd03e6ced935c'
down_revision: Union[str, None] = 'k1_add_order_city_map'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables that need is_deleted + deleted_at (SoftDeleteMixin)
SOFT_DELETE_TABLES = [
    'accounts',
    'counterparty_categories',
    'overrides',
    'category_ref',
    'integration_keys',
    'duty_rules',
    'customs_topup',
    'customs_dt',
    'planned_incomes',
    'wb_payouts',
    'payment_fact_links',
]

# Tables that need updated_at (TimestampMixin addition to order_city_map)
TIMESTAMP_TABLES = [
    'order_city_map',
]


def upgrade() -> None:
    # Add is_deleted + deleted_at to all tables needing SoftDeleteMixin
    for table in SOFT_DELETE_TABLES:
        op.add_column(table, sa.Column('is_deleted', sa.Boolean(),
                      nullable=False, server_default=sa.text('false')))
        op.add_column(table, sa.Column('deleted_at', sa.DateTime(), nullable=True))

    # Add updated_at to order_city_map (TimestampMixin — created_at already exists)
    for table in TIMESTAMP_TABLES:
        op.add_column(table, sa.Column('updated_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    for table in TIMESTAMP_TABLES:
        op.drop_column(table, 'updated_at')

    for table in SOFT_DELETE_TABLES:
        op.drop_column(table, 'deleted_at')
        op.drop_column(table, 'is_deleted')
