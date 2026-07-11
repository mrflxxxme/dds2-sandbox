# ruff: noqa: RUF002 — русские комментарии
"""project_invites: expires_at — срок годности приглашения

Добавляет колонку `expires_at` (nullable timestamp). Ссылка-приглашение перестаёт
работать после этого момента (по умолчанию 7 дней от создания). Проверяется в
`register` (регистрация по инвайту) и `accept_invite` (присоединение существующего
юзера). NULL = legacy-инвайт без срока (созданный до этой колонки).

Revision ID: inv01_invite_expires_at
Revises: ads03_ad_nm_daily
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "inv01_invite_expires_at"
down_revision: str | None = "ads03_ad_nm_daily"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_invites",
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("project_invites", "expires_at")
