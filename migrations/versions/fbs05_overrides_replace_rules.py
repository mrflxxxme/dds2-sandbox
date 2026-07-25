"""WB FBS: заменить систему правил простой потоварной заменой количества

Решение владельца 2026-07-24: четырёхуровневые правила (товар → под-категория →
предмет → бренд) с приоритетами и отдельной вкладкой оказались переусложнением.
Нужно ровно одно: у товара на складе можно задать количество, 0 = не отдавать.

Данных не теряем: на момент миграции правил заведено не было (счётчик
«С правилом: 0» в интерфейсе), а те, что были бы, — потоварные — переносим.

Revision ID: fbs05_overrides_replace_rules
Revises: fbs04_observe_mode_fbo_gate
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision: str = "fbs05_overrides_replace_rules"
down_revision: str | None = "fbs04_observe_mode_fbo_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wb_fbs_stock_overrides",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("wb_warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("nomenclature_id", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["nomenclature_id"], ["nomenclature.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "wb_warehouse_id", "nomenclature_id", name="uq_wb_fbs_override"),
    )
    op.create_index("ix_wb_fbs_overrides_project_wh", "wb_fbs_stock_overrides", ["project_id", "wb_warehouse_id"])
    op.create_index("ix_wb_fbs_overrides_nomenclature", "wb_fbs_stock_overrides", ["nomenclature_id"])

    # Перенос потоварных правил, у которых был конкретный склад: cap → потолок,
    # exclude → 0. Категорийные/брендовые уровни переносить некуда — их не стало.
    op.execute(
        """
        INSERT INTO wb_fbs_stock_overrides
            (project_id, wb_warehouse_id, nomenclature_id, qty, created_at, updated_at)
        SELECT r.project_id, r.wb_warehouse_id, r.nomenclature_id,
               CASE WHEN r.mode = 'exclude' THEN 0 ELSE COALESCE(r.manual_qty, 0) END,
               r.created_at, r.updated_at
          FROM wb_fbs_stock_rules r
         WHERE r.is_deleted = false
           AND r.is_active = true
           AND r.scope_type = 'nomenclature'
           AND r.nomenclature_id IS NOT NULL
           AND r.wb_warehouse_id IS NOT NULL
           AND (r.mode = 'exclude' OR r.manual_qty IS NOT NULL)
        ON CONFLICT ON CONSTRAINT uq_wb_fbs_override DO NOTHING
        """
    )

    op.drop_index("uq_wb_fbs_rule_scope_global", table_name="wb_fbs_stock_rules")
    op.drop_index("uq_wb_fbs_rule_scope", table_name="wb_fbs_stock_rules")
    op.drop_index("ix_wb_fbs_rules_subcategory", table_name="wb_fbs_stock_rules")
    op.drop_index("ix_wb_fbs_rules_nomenclature", table_name="wb_fbs_stock_rules")
    op.drop_index("ix_wb_fbs_rules_project_id", table_name="wb_fbs_stock_rules")
    op.drop_table("wb_fbs_stock_rules")


def downgrade() -> None:
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
    op.create_index(
        "uq_wb_fbs_rule_scope",
        "wb_fbs_stock_rules",
        ["project_id", "wb_warehouse_id", "scope_key"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_wb_fbs_rule_scope_global",
        "wb_fbs_stock_rules",
        ["project_id", "scope_key"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false AND wb_warehouse_id IS NULL"),
    )
    op.execute(
        """
        INSERT INTO wb_fbs_stock_rules
            (project_id, wb_warehouse_id, scope_type, scope_key, nomenclature_id,
             mode, manual_qty, is_active, created_at, updated_at, is_deleted)
        SELECT o.project_id, o.wb_warehouse_id, 'nomenclature',
               'nomenclature:' || o.nomenclature_id, o.nomenclature_id,
               CASE WHEN o.qty = 0 THEN 'exclude' ELSE 'cap' END,
               NULLIF(o.qty, 0), true, o.created_at, o.updated_at, false
          FROM wb_fbs_stock_overrides o
        """
    )

    op.drop_index("ix_wb_fbs_overrides_nomenclature", table_name="wb_fbs_stock_overrides")
    op.drop_index("ix_wb_fbs_overrides_project_wh", table_name="wb_fbs_stock_overrides")
    op.drop_table("wb_fbs_stock_overrides")
