"""fix campaign_type column type to String

Revision ID: fix_campaign_type_str
Revises: 3ec456194b77
Create Date: 2026-03-26
"""

import sqlalchemy as sa
from alembic import op

revision = "fix_campaign_type_str"
down_revision = "3ec456194b77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "wb_ad_campaigns",
        "campaign_type",
        existing_type=sa.Integer(),
        type_=sa.String(20),
        existing_nullable=True,
        postgresql_using="campaign_type::varchar",
    )


def downgrade() -> None:
    op.alter_column(
        "wb_ad_campaigns",
        "campaign_type",
        existing_type=sa.String(20),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="campaign_type::integer",
    )
