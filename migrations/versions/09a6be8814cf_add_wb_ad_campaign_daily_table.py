"""add wb_ad_campaign_daily table

Revision ID: 09a6be8814cf
Revises: b841d6548410
Create Date: 2026-03-25 13:30:17.858245

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "09a6be8814cf"
down_revision: str | None = "b841d6548410"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_ad_campaign_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("views", sa.Integer(), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.Column("spend", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "campaign_id", "date", name="uq_ad_campaign_daily"),
    )
    op.create_index(
        "ix_ad_campaign_daily_campaign", "wb_ad_campaign_daily", ["project_id", "campaign_id"], unique=False
    )
    op.create_index("ix_ad_campaign_daily_project_date", "wb_ad_campaign_daily", ["project_id", "date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ad_campaign_daily_project_date", table_name="wb_ad_campaign_daily")
    op.drop_index("ix_ad_campaign_daily_campaign", table_name="wb_ad_campaign_daily")
    op.drop_table("wb_ad_campaign_daily")
