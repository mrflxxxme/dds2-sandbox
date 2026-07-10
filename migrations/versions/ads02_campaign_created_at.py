"""add created_at to wb_ad_campaigns (WB createTime) + backfill from earliest daily stat

Revision ID: ads02_campaign_created_at
Revises: ads01_advert_type
Create Date: 2026-07-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ads02_campaign_created_at"
down_revision: str | None = "ads01_advert_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("wb_ad_campaigns", sa.Column("created_at", sa.DateTime(), nullable=True))
    # Бэкфилл: дата добавления ≈ самый ранний день дневной статистики кампании,
    # иначе — последняя синхронизация (updated_at). На следующем синке заменится
    # реальным WB createTime.
    op.execute(
        """
        UPDATE wb_ad_campaigns c
        SET created_at = COALESCE(
            (
                SELECT MIN(d.date)::timestamp
                FROM wb_ad_campaign_daily d
                WHERE d.project_id = c.project_id AND d.campaign_id = c.campaign_id
            ),
            c.updated_at
        )
        WHERE c.created_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("wb_ad_campaigns", "created_at")
