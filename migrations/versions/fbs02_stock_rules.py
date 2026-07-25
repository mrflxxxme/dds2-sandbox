"""WB FBS: ручные правила трансляции остатка (товар / под-категория / предмет / бренд)

Revision ID: fbs02_stock_rules
Revises: fbs01_wb_fbs_domain
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision: str = "fbs02_stock_rules"
down_revision: str | None = "fbs01_wb_fbs_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wb_fbs_stock_rules",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("wb_warehouse_id", sa.BigInteger(), nullable=True),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("scope_key", sa.String(length=200), nullable=False),
        sa.Column("nomenclature_id", sa.Integer(), nullable=True),
        sa.Column("subcategory_id", sa.Integer(), nullable=True),
        sa.Column("brand", sa.String(length=100), nullable=True),
        sa.Column("subject", sa.String(length=100), nullable=True),
        sa.Column("mode", sa.String(length=10), server_default="cap", nullable=False),
        sa.Column("manual_qty", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["nomenclature_id"], ["nomenclature.id"]),
        sa.ForeignKeyConstraint(["subcategory_id"], ["product_subcategories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wb_fbs_rules_project_id", "wb_fbs_stock_rules", ["project_id"])
    op.create_index("ix_wb_fbs_rules_nomenclature", "wb_fbs_stock_rules", ["nomenclature_id"])
    op.create_index("ix_wb_fbs_rules_subcategory", "wb_fbs_stock_rules", ["subcategory_id"])
    # Уникальность только среди живых строк: SoftDelete иначе занимает слот навсегда.
    op.create_index(
        "uq_wb_fbs_rule_scope",
        "wb_fbs_stock_rules",
        ["project_id", "wb_warehouse_id", "scope_key"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )
    # NULL != NULL в partial unique — глобальным правилам нужен свой слот.
    op.create_index(
        "uq_wb_fbs_rule_scope_global",
        "wb_fbs_stock_rules",
        ["project_id", "scope_key"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false AND wb_warehouse_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_wb_fbs_rule_scope_global", table_name="wb_fbs_stock_rules")
    op.drop_index("uq_wb_fbs_rule_scope", table_name="wb_fbs_stock_rules")
    op.drop_index("ix_wb_fbs_rules_subcategory", table_name="wb_fbs_stock_rules")
    op.drop_index("ix_wb_fbs_rules_nomenclature", table_name="wb_fbs_stock_rules")
    op.drop_index("ix_wb_fbs_rules_project_id", table_name="wb_fbs_stock_rules")
    op.drop_table("wb_fbs_stock_rules")
