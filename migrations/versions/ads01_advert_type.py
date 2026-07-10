"""add advert_type to wb_ad_campaigns (WB numeric type: 8=auto/recommend, 9=auction)

Revision ID: ads01_advert_type
Revises: 7883be1967e3
Create Date: 2026-07-09

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ads01_advert_type"
down_revision: str | None = "7883be1967e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("wb_ad_campaigns", sa.Column("advert_type", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("wb_ad_campaigns", "advert_type")
