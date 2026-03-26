"""add_wb_ad_campaigns_table

Revision ID: b841d6548410
Revises: m10_coalesce_date_index
Create Date: 2026-03-25 10:14:44.008606

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b841d6548410"
down_revision: str | None = "m10_coalesce_date_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_ad_campaigns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=True),
        sa.Column("campaign_type", sa.String(length=20), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("budget", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("nm_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "campaign_id", name="uq_ad_campaign_project"),
    )
    op.create_index("ix_ad_campaign_project", "wb_ad_campaigns", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ad_campaign_project", table_name="wb_ad_campaigns")
    op.drop_table("wb_ad_campaigns")
