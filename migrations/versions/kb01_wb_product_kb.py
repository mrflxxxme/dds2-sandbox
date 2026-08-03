"""wb_product_kb: база знаний товаров + needs_info/generation в wb_feedback_replies

Revision ID: kb01_wb_product_kb
Revises: wr01_wb_feedback_replies
Create Date: 2026-07-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "kb01_wb_product_kb"
down_revision: str | None = "wr01_wb_feedback_replies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_product_kb",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("topic", sa.String(length=100), nullable=False),
        sa.Column("question_example", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("question_hash", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_wb_product_kb_project_nm_enabled", "wb_product_kb", ["project_id", "nm_id", "enabled"]
    )
    op.create_index("ix_wb_product_kb_nm_id", "wb_product_kb", ["nm_id"])
    # дедуп-гард импорта из архива отвеченных вопросов
    op.create_index(
        "uq_wb_product_kb_project_nm_qhash",
        "wb_product_kb",
        ["project_id", "nm_id", "question_hash"],
        unique=True,
        postgresql_where=sa.text("question_hash IS NOT NULL"),
    )

    # защита от выдумок: черновик без фактов КБ + происхождение текста
    op.add_column(
        "wb_feedback_replies",
        sa.Column("needs_info", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "wb_feedback_replies",
        sa.Column("generation", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wb_feedback_replies", "generation")
    op.drop_column("wb_feedback_replies", "needs_info")
    op.drop_index("uq_wb_product_kb_project_nm_qhash", table_name="wb_product_kb")
    op.drop_index("ix_wb_product_kb_nm_id", table_name="wb_product_kb")
    op.drop_index("ix_wb_product_kb_project_nm_enabled", table_name="wb_product_kb")
    op.drop_table("wb_product_kb")
