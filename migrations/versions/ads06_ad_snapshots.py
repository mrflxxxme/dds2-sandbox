"""wb_ad_campaign_snapshots: снимки накопительных внутридневных счётчиков кампании

WB не отдаёт внутридневную разбивку показы/клики (мин. ось — сутки). Копим сами: job
каждые ~30 мин снимает накопительный «сегодняшний» счётчик из кабинетного campaigns-stats,
дельта между снимками одного stat_date = показы/клики/расход за интервал. Нужно для
интрадей-графика «место принятия решения» (стиль mkeeper).

Revision ID: ads06_ad_snapshots
Revises: ads05_ad_search_daily
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ads06_ad_snapshots"
down_revision: str | None = "ads05_ad_search_daily"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_ad_campaign_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("views_cum", sa.Integer(), nullable=True),
        sa.Column("clicks_cum", sa.Integer(), nullable=True),
        sa.Column("spend_cum", sa.Numeric(18, 2), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ad_snapshot_campaign_captured",
        "wb_ad_campaign_snapshots",
        ["project_id", "campaign_id", "stat_date", "captured_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ad_snapshot_campaign_captured", table_name="wb_ad_campaign_snapshots")
    op.drop_table("wb_ad_campaign_snapshots")
