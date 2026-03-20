"""add brand_plans table

Revision ID: e1a3b5c7d9f0
Revises: d95b7aabca5d
Create Date: 2026-03-20 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1a3b5c7d9f0"
down_revision: str = "d95b7aabca5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brand_plans",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("brand", sa.String(200), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("plan_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "brand", "year", "month", name="uq_brand_plan_project_brand_ym"),
    )
    op.create_index("ix_brand_plans_project_id", "brand_plans", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_brand_plans_project_id", table_name="brand_plans")
    op.drop_table("brand_plans")
