"""add telegram_username to users

Revision ID: add_user_telegram_username
Revises: add_imt_aliases
Create Date: 2026-03-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "add_user_telegram_username"
down_revision: str | None = "add_imt_aliases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("telegram_username", sa.String(100), nullable=True))
    op.create_unique_constraint("uq_user_telegram_username", "users", ["telegram_username"])


def downgrade() -> None:
    op.drop_constraint("uq_user_telegram_username", "users", type_="unique")
    op.drop_column("users", "telegram_username")
