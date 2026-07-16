"""telegram_chat_bindings: supply_notify_enabled (opt-in под алерты расхождений поставок ФФ)

Revision ID: tg01_supply_notify_enabled
Revises: ads08_wb_ad_campaign_events
Create Date: 2026-07-15

Отдельный per-chat флаг под уведомления «Расхождение поставок ФФ» (дата/паллеты/
пропуск, рассылка из планировщика раз в 2ч). Независим от ff_notify_enabled — чат
под эти алерты выбирается отдельно. Дефолт false: включается вручную в настройках.
"""

import sqlalchemy as sa
from alembic import op

revision = "tg01_supply_notify_enabled"
down_revision = "ads08_wb_ad_campaign_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telegram_chat_bindings",
        sa.Column("supply_notify_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("telegram_chat_bindings", "supply_notify_enabled")
