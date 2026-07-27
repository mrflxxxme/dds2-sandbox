"""wb_questions: зеркало вопросов покупателей WB

Revision ID: wq01_wb_questions
Revises: ca01_complaint_agents
Create Date: 2026-07-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "wq01_wb_questions"
down_revision: str | None = "ca01_complaint_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_questions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("wb_id", sa.String(length=64), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("is_answered", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_date", sa.DateTime(), nullable=True),
        sa.Column("user_name", sa.String(length=200), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("product_name", sa.String(length=500), nullable=True),
        sa.Column("article", sa.String(length=200), nullable=True),
        sa.Column("brand", sa.String(length=200), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "wb_id", name="uq_wb_questions_project_wb_id"),
    )
    op.create_index("ix_wb_questions_project_created", "wb_questions", ["project_id", "created_date"])
    op.create_index("ix_wb_questions_project_nm_id", "wb_questions", ["project_id", "nm_id"])
    op.create_index("ix_wb_questions_project_answered", "wb_questions", ["project_id", "is_answered"])


def downgrade() -> None:
    op.drop_index("ix_wb_questions_project_answered", table_name="wb_questions")
    op.drop_index("ix_wb_questions_project_nm_id", table_name="wb_questions")
    op.drop_index("ix_wb_questions_project_created", table_name="wb_questions")
    op.drop_table("wb_questions")
