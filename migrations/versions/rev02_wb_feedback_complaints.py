"""wb_feedback_complaints: учёт жалоб на отзывы для их удаления

Фиксация факта подачи жалобы на отзыв + исход (подано/удалено/не удалено).

Revision ID: rev02_feedback_complaints
Revises: rev01_wb_feedbacks
Create Date: 2026-07-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "rev02_feedback_complaints"
down_revision: str | None = "rev01_wb_feedbacks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_feedback_complaints",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("wb_feedback_id", sa.String(length=64), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reason", sa.String(length=32), nullable=False, server_default="not_related"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "wb_feedback_id", name="uq_feedback_complaint_project_feedback"),
    )
    op.create_index("ix_feedback_complaints_project_status", "wb_feedback_complaints", ["project_id", "status"])
    op.create_index("ix_wb_feedback_complaints_project_id", "wb_feedback_complaints", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_wb_feedback_complaints_project_id", table_name="wb_feedback_complaints")
    op.drop_index("ix_feedback_complaints_project_status", table_name="wb_feedback_complaints")
    op.drop_table("wb_feedback_complaints")
