"""size overrides + aliases + product subcategories

Revision ID: sz01_size_subcats
Revises: ff02_ff_review_proposal
Create Date: 2026-06-28

Редактор размеров на воронке: ручной перенос товара в размер (size_overrides),
переименование/объединение размеров (size_aliases) и под-категории внутри размера
(product_subcategories + single-select product_subcategory_map, одна на товар).
"""

import sqlalchemy as sa
from alembic import op

revision = "sz01_size_subcats"
down_revision = "ff02_ff_review_proposal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "size_aliases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("raw_size", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "raw_size", name="uq_size_alias"),
    )
    op.create_index("ix_size_aliases_project_id", "size_aliases", ["project_id"])

    op.create_table(
        "size_overrides",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("nm_id", sa.Integer(), nullable=False),
        sa.Column("size_value", sa.String(length=50), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "nm_id", name="uq_size_override_nm"),
    )
    op.create_index("ix_size_overrides_project_nm_id", "size_overrides", ["project_id", "nm_id"])

    op.create_table(
        "product_subcategories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("color", sa.String(length=7), nullable=False, server_default="#8B5CF6"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "name", name="uq_product_subcategory_name"),
    )
    op.create_index("ix_product_subcategories_project_id", "product_subcategories", ["project_id"])

    op.create_table(
        "product_subcategory_map",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "subcategory_id",
            sa.Integer(),
            sa.ForeignKey("product_subcategories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nm_id", sa.Integer(), nullable=False),
        sa.UniqueConstraint("project_id", "nm_id", name="uq_product_subcategory_map_nm"),
    )
    op.create_index(
        "ix_product_subcategory_map_project_nm_id", "product_subcategory_map", ["project_id", "nm_id"]
    )
    op.create_index(
        "ix_product_subcategory_map_subcategory_id", "product_subcategory_map", ["subcategory_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_product_subcategory_map_subcategory_id", table_name="product_subcategory_map")
    op.drop_index("ix_product_subcategory_map_project_nm_id", table_name="product_subcategory_map")
    op.drop_table("product_subcategory_map")
    op.drop_index("ix_product_subcategories_project_id", table_name="product_subcategories")
    op.drop_table("product_subcategories")
    op.drop_index("ix_size_overrides_project_nm_id", table_name="size_overrides")
    op.drop_table("size_overrides")
    op.drop_index("ix_size_aliases_project_id", table_name="size_aliases")
    op.drop_table("size_aliases")
