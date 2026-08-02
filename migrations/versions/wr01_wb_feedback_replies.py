"""wb_reply_agents + wb_feedback_replies: ИИ-автоответы на отзывы и вопросы

Revision ID: wr01_wb_feedback_replies
Revises: wq01_wb_questions
Create Date: 2026-07-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "wr01_wb_feedback_replies"
down_revision: str | None = "wq01_wb_questions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_reply_agents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("target", sa.String(length=16), nullable=False, server_default="both"),
        sa.Column("star_levels", sa.String(length=16), nullable=False, server_default="1,2,3,4,5"),
        sa.Column("nm_ids", sa.Text(), nullable=True),
        sa.Column("auto_send", sa.Boolean(), nullable=False, server_default=sa.text("false")),
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
    op.create_index("ix_wb_reply_agents_project_id", "wb_reply_agents", ["project_id"])

    op.create_table(
        "wb_feedback_replies",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_wb_id", sa.String(length=64), nullable=False),
        sa.Column("draft_text", sa.Text(), nullable=False),
        sa.Column("final_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("agent_id", sa.BigInteger(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["agent_id"], ["wb_reply_agents.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_wb_feedback_replies_project_status", "wb_feedback_replies", ["project_id", "status"]
    )
    op.create_index(
        "ix_wb_feedback_replies_project_target",
        "wb_feedback_replies",
        ["project_id", "target_type", "target_wb_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_wb_feedback_replies_project_target", table_name="wb_feedback_replies")
    op.drop_index("ix_wb_feedback_replies_project_status", table_name="wb_feedback_replies")
    op.drop_table("wb_feedback_replies")
    op.drop_index("ix_wb_reply_agents_project_id", table_name="wb_reply_agents")
    op.drop_table("wb_reply_agents")
