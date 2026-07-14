# ruff: noqa: RUF002, RUF003
"""ads: таблица wb_ad_campaign_events (история изменений кампании: бюджет + статус)

История значимых изменений рекламной кампании — пишется в sync_ad_campaigns и
refresh_one_campaign (event_type: budget_change / status_change). Модель
WbAdCampaignEvent появилась в #600, но миграция не была включена → таблицы нет,
padает refresh_one_campaign и тест test_refresh_updates_mirror_and_daily.

Additive, новая таблица, без backfill.

Revision ID: ads08_wb_ad_campaign_events
Revises: b61a3c39ed0b
Create Date: 2026-07-14 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ads08_wb_ad_campaign_events"
down_revision: str | None = "b61a3c39ed0b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_ad_campaign_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("old_value", sa.String(length=50), nullable=True),
        sa.Column("new_value", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ad_event_project_campaign", "wb_ad_campaign_events", ["project_id", "campaign_id"]
    )
    op.create_index("ix_ad_event_created", "wb_ad_campaign_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ad_event_created", table_name="wb_ad_campaign_events")
    op.drop_index("ix_ad_event_project_campaign", table_name="wb_ad_campaign_events")
    op.drop_table("wb_ad_campaign_events")
