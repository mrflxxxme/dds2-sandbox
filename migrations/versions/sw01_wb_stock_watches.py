"""wb_stock_watches: слежение за поступлением товара + is_stock_reply в wb_feedback_replies

Revision ID: sw01_wb_stock_watches
Revises: pc01_wb_product_cards
Create Date: 2026-07-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "sw01_wb_stock_watches"
down_revision: str | None = "pc01_wb_product_cards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_stock_watches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("question_wb_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="watching"),
        sa.Column("reply_id", sa.BigInteger(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["reply_id"], ["wb_feedback_replies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "question_wb_id", name="uq_wb_stock_watches_project_question"),
    )
    op.create_index(
        "ix_wb_stock_watches_project_status", "wb_stock_watches", ["project_id", "status"]
    )
    op.create_index("ix_wb_stock_watches_nm_id", "wb_stock_watches", ["nm_id"])

    # признак stock-черновика (бейдж «поступление» в очереди автоответов)
    op.add_column(
        "wb_feedback_replies",
        sa.Column("is_stock_reply", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("wb_feedback_replies", "is_stock_reply")
    op.drop_index("ix_wb_stock_watches_nm_id", table_name="wb_stock_watches")
    op.drop_index("ix_wb_stock_watches_project_status", table_name="wb_stock_watches")
    op.drop_table("wb_stock_watches")
