# ruff: noqa: RUF002, RUF003
"""telegram_chat_bindings: design_notify_enabled (opt-in под утреннюю сводку дизайна)

Revision ID: dsn02_design_notify_flag
Revises: dsn01_design_tasks
Create Date: 2026-08-02

Отдельный per-chat флаг под утреннюю сводку задач модуля «Дизайн карточек»
(джоба Ф4, дефолт 09:00 МСК). Зеркало supply_notify_enabled (tg01): независим
от прочих notify-флагов — чат под эту сводку выбирается отдельно. Дефолт false:
включается вручную в настройках.
"""

import sqlalchemy as sa
from alembic import op

revision = "dsn02_design_notify_flag"
down_revision = "dsn01_design_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telegram_chat_bindings",
        sa.Column("design_notify_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("telegram_chat_bindings", "design_notify_enabled")
