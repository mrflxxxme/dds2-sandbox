"""add measurements_notify_enabled to telegram_chat_bindings

Per-chat opt-in для ежедневной сводки замеров WB (джоб 09:00 MSK).

Revision ID: 7883be1967e3
Revises: e0551748b04f
Create Date: 2026-07-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7883be1967e3'
down_revision: Union[str, None] = 'e0551748b04f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'telegram_chat_bindings',
        sa.Column(
            'measurements_notify_enabled',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column('telegram_chat_bindings', 'measurements_notify_enabled')
