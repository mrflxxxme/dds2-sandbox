"""add vat_rate to projects

Revision ID: d1_add_vat_rate
Revises: 1a1681117e29
Create Date: 2026-03-05 13:45:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd1_add_vat_rate'
down_revision: Union[str, None] = '1a1681117e29'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('vat_rate', sa.Numeric(5, 2), nullable=True, server_default='22'))


def downgrade() -> None:
    op.drop_column('projects', 'vat_rate')
