"""merge wbp01(WB-поставка)+ads01(реклама) heads

Revision ID: mrg01_wbp_ads
Revises: wbp01_assembly_wb_supply, ads01_ad_campaign_bid_mode
Create Date: 2026-07-08 14:06:00.037446

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'mrg01_wbp_ads'
down_revision: Union[str, None] = ('wbp01_assembly_wb_supply', 'ads01_ad_campaign_bid_mode')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
