"""wb_ad_search_daily: посуточная статистика зоны «Поиск» (сумма поисковых кластеров)

Источник — WB /adv/v1/normquery/stats (кластеры с детализацией по дням). Нужна для
разбивки метрик кампании по зонам показов на графике и в таблице «Метрики по дням».

Revision ID: ads05_ad_search_daily
Revises: ads04_ad_finance
Create Date: 2026-07-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ads05_ad_search_daily"
down_revision: str | None = "ads04_ad_finance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_ad_search_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=True),
        sa.Column("clicks", sa.Integer(), nullable=True),
        sa.Column("spend", sa.Numeric(18, 2), nullable=True),
        sa.Column("atbs", sa.Integer(), nullable=True),
        sa.Column("orders", sa.Integer(), nullable=True),
        sa.Column("shks", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "campaign_id", "nm_id", "date", name="uq_ad_search_daily"),
    )
    op.create_index("ix_ad_search_daily_campaign_date", "wb_ad_search_daily", ["project_id", "campaign_id", "date"])


def downgrade() -> None:
    op.drop_index("ix_ad_search_daily_campaign_date", table_name="wb_ad_search_daily")
    op.drop_table("wb_ad_search_daily")
