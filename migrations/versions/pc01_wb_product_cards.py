"""wb_product_cards: зеркало публичных карточек WB (basket-API)

Revision ID: pc01_wb_product_cards
Revises: kb01_wb_product_kb
Create Date: 2026-07-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "pc01_wb_product_cards"
down_revision: str | None = "kb01_wb_product_kb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_product_cards",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("contents", sa.Text(), nullable=True),
        sa.Column("characteristics", postgresql.JSONB(), nullable=True),
        sa.Column("photo_urls", postgresql.JSONB(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_wb_product_cards_project_nm", "wb_product_cards", ["project_id", "nm_id"], unique=True
    )
    op.create_index("ix_wb_product_cards_nm_id", "wb_product_cards", ["nm_id"])


def downgrade() -> None:
    op.drop_index("ix_wb_product_cards_nm_id", table_name="wb_product_cards")
    op.drop_index("uq_wb_product_cards_project_nm", table_name="wb_product_cards")
    op.drop_table("wb_product_cards")
