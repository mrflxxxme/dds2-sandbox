"""merge spp03 + sw02 heads

Revision ID: mrg01_spp03_sw02
Revises: spp03_price_probes, sw02_wb_stock_watches_last_qty
Create Date: 2026-08-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'mrg01_spp03_sw02'
down_revision: Union[str, None] = ('spp03_price_probes', 'sw02_wb_stock_watches_last_qty')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
