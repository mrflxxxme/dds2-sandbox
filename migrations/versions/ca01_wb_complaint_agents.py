"""wb_complaint_agents: ИИ-агенты подготовки жалоб на отзывы по правилам

Revision ID: ca01_complaint_agents
Revises: vibe01_vibecoding_stats
Create Date: 2026-07-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ca01_complaint_agents"
down_revision: str | None = "vibe01_vibecoding_stats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_complaint_agents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("brand", sa.String(length=200), nullable=True),
        sa.Column("nm_ids", sa.Text(), nullable=True),
        sa.Column("star_levels", sa.String(length=16), nullable=False, server_default="1,2,3"),
        sa.Column("rules", sa.Text(), nullable=False),
        sa.Column("examples", sa.Text(), nullable=True),
        sa.Column("llm_provider", sa.String(length=32), nullable=False, server_default="openai_compatible"),
        sa.Column("llm_model", sa.String(length=64), nullable=False, server_default="deepseek-chat"),
        sa.Column("llm_base_url", sa.String(length=200), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wb_complaint_agents_project_id", "wb_complaint_agents", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_wb_complaint_agents_project_id", table_name="wb_complaint_agents")
    op.drop_table("wb_complaint_agents")
