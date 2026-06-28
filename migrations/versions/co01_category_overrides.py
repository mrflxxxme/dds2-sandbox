"""category overrides (ручной перенос товара в категорию)

Revision ID: co01_category_overrides
Revises: sz01_size_subcats
Create Date: 2026-06-28

Редактор «Ярлыки товаров»: ручное переназначение категории товара
(category_overrides, one per nm_id) — оверрайдит предмет WB (subject) в
группировке воронки «Категория → Размер → SKU». Зеркало size_overrides.
"""

import sqlalchemy as sa
from alembic import op

revision = "co01_category_overrides"
down_revision = "sz01_size_subcats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "category_overrides",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("nm_id", sa.Integer(), nullable=False),
        sa.Column("category_value", sa.String(length=100), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "nm_id", name="uq_category_override_nm"),
    )
    op.create_index(
        "ix_category_overrides_project_nm_id", "category_overrides", ["project_id", "nm_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_category_overrides_project_nm_id", table_name="category_overrides")
    op.drop_table("category_overrides")
