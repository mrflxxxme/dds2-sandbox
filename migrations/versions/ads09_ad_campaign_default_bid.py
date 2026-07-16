"""wb_ad_campaigns.default_bid (ставка кампании ₽ по активной зоне)

Revision ID: ads09_ad_campaign_default_bid
Revises: ads08_wb_ad_campaign_events
Create Date: 2026-07-15

Добавляет колонку default_bid в wb_ad_campaigns — ставка кампании (₽) по
активной зоне показов (поиск приоритетнее). Синк заполняет её через
_parse_advert_item из nm_settings[].bids_kopecks (WB /api/advert/v2/adverts).
Нужна для инлайн-правки ставки прямо в списке «Управление рекламой» —
рендер из зеркала без лишнего вызова WB.
Nullable, без server_default — старые строки остаются NULL до следующего синка.
"""

import sqlalchemy as sa
from alembic import op

revision = "ads09_ad_campaign_default_bid"
down_revision = "ads08_wb_ad_campaign_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wb_ad_campaigns",
        sa.Column("default_bid", sa.Numeric(18, 2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wb_ad_campaigns", "default_bid")
