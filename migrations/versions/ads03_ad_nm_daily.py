"""wb_ad_nm_daily: посуточная РК-статистика в разбивке кампания × товар (nmId)

Данные приходят в том же ответе WB /adv/v3/fullstats (days → apps → nms), который уже
используется для итогов кампании — раньше nm-разбивка схлопывалась и терялась.

Revision ID: ads03_ad_nm_daily
Revises: ads02_campaign_created_at
Create Date: 2026-07-10

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ads03_ad_nm_daily"
down_revision: str | None = "ads02_campaign_created_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_ad_nm_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("spend", sa.Numeric(18, 2), nullable=True, server_default="0"),
        sa.Column("atbs", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("orders", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("shks", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("orders_sum", sa.Numeric(18, 2), nullable=True, server_default="0"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "campaign_id", "nm_id", "date", name="uq_ad_nm_daily"),
    )
    # FK project_id покрыт составным индексом ниже (project_id — ведущая колонка)
    op.create_index("ix_ad_nm_daily_campaign_date", "wb_ad_nm_daily", ["project_id", "campaign_id", "date"])
    op.create_index("ix_ad_nm_daily_nm_date", "wb_ad_nm_daily", ["project_id", "nm_id", "date"])


def downgrade() -> None:
    op.drop_index("ix_ad_nm_daily_nm_date", table_name="wb_ad_nm_daily")
    op.drop_index("ix_ad_nm_daily_campaign_date", table_name="wb_ad_nm_daily")
    op.drop_table("wb_ad_nm_daily")
