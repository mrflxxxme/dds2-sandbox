"""wb_ad_campaigns.bid_mode (режим ставки CPM: единая/ручная)

Revision ID: ads01_ad_campaign_bid_mode
Revises: pr01_wb_prices
Create Date: 2026-07-08

Добавляет колонку bid_mode в wb_ad_campaigns. Значения из WB
/api/advert/v2/adverts поле bid_type: 'unified' (единая ставка) / 'manual'
(ручная). Синк заполняет её через _parse_advert_item; фронт «Управление
рекламой» показывает фильтр «единая/ручная» под CPM-кампаниями.
Nullable, без server_default — старые строки остаются NULL до следующего синка.
"""

import sqlalchemy as sa
from alembic import op

revision = "ads01_ad_campaign_bid_mode"
down_revision = "dh01_assembly_draft_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("wb_ad_campaigns", sa.Column("bid_mode", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("wb_ad_campaigns", "bid_mode")
