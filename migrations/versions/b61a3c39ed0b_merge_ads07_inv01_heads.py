"""merge ads07 + inv01 heads

Revision ID: b61a3c39ed0b
Revises: ads07_search_positions, inv01_invite_expires_at
Create Date: 2026-07-13 07:29:27.974818

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b61a3c39ed0b'
down_revision: Union[str, None] = ('ads07_search_positions', 'inv01_invite_expires_at')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
