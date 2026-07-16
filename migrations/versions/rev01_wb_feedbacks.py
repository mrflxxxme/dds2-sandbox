"""wb_feedbacks: create wb_feedbacks table (зеркало отзывов покупателей WB)

Mirror WB Feedbacks API (+архив). Сводная аналитика отзывов строится из этой
таблицы: рейтинг по месяцам, объём по оценкам, разбивки по категории/бренду/ярлыку.

Revision ID: rev01_wb_feedbacks
Revises: abt01_ab_photo_tests
Create Date: 2026-07-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "rev01_wb_feedbacks"
down_revision: str | None = "abt01_ab_photo_tests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_feedbacks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        # WB API fields
        sa.Column("wb_id", sa.String(length=64), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("pros", sa.Text(), nullable=True),
        sa.Column("cons", sa.Text(), nullable=True),
        sa.Column("has_text", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_date", sa.DateTime(), nullable=True),
        sa.Column("user_name", sa.String(length=200), nullable=True),
        sa.Column("is_answered", sa.Boolean(), nullable=False, server_default=sa.false()),
        # Снапшот из WB productDetails (фолбэк для товаров не из Nomenclature)
        sa.Column("brand", sa.String(length=200), nullable=True),
        sa.Column("product_name", sa.String(length=500), nullable=True),
        sa.Column("article", sa.String(length=200), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        # TimestampMixin
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "wb_id", name="uq_wb_feedbacks_project_wb_id"),
    )

    op.create_index(
        "ix_wb_feedbacks_project_created",
        "wb_feedbacks",
        ["project_id", "created_date"],
    )
    op.create_index(
        "ix_wb_feedbacks_project_nm_id",
        "wb_feedbacks",
        ["project_id", "nm_id"],
    )
    op.create_index(
        "ix_wb_feedbacks_project_rating",
        "wb_feedbacks",
        ["project_id", "rating"],
    )


def downgrade() -> None:
    op.drop_index("ix_wb_feedbacks_project_rating", table_name="wb_feedbacks")
    op.drop_index("ix_wb_feedbacks_project_nm_id", table_name="wb_feedbacks")
    op.drop_index("ix_wb_feedbacks_project_created", table_name="wb_feedbacks")
    op.drop_table("wb_feedbacks")
