# ruff: noqa: RUF001, RUF002
"""smd01: assembly_stock_mismatch_daily — суточный снимок расхождения наш склад vs ФФ

FulfillmentStock — только текущий снимок (перезатир синком), динамику расхождения
задним числом не восстановить. Таблица копит её вперёд: одна строка = день × склад ×
эффективный ШК, только со строкой расхождения (diff != 0). Наличие/отсутствие строки
за день кодирует «появился/сошёлся» для журнала изменений вкладки «Динамика
расхождения». Пишется scheduler-джобой stock_mismatch_snapshot.

Revision ID: smd01_stock_mismatch_daily
Revises: asm785_pickup_cost_history
Create Date: 2026-07-23 13:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = "smd01_stock_mismatch_daily"
down_revision = "asm785_pickup_cost_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assembly_stock_mismatch_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("barcode", sa.String(length=64), nullable=False),
        sa.Column("nomenclature_id", sa.Integer(), nullable=True),
        sa.Column("article_seller", sa.String(length=255), nullable=True),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("ff_good", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ff_logistics", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("our_quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("our_defect", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("diff", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "snapshot_date",
            "warehouse_id",
            "barcode",
            name="uq_asm_stock_mismatch_daily",
        ),
    )
    # Таймлайн одного SKU по складу (drill журнала изменений).
    op.create_index(
        "ix_asm_stock_mismatch_daily_sku",
        "assembly_stock_mismatch_daily",
        ["project_id", "warehouse_id", "barcode", "snapshot_date"],
    )
    # Выборка истории за окно дней (график) + FK warehouse_id прикрыт префиксом sku-индекса.
    op.create_index(
        "ix_asm_stock_mismatch_daily_project_date",
        "assembly_stock_mismatch_daily",
        ["project_id", "snapshot_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_asm_stock_mismatch_daily_project_date", table_name="assembly_stock_mismatch_daily")
    op.drop_index("ix_asm_stock_mismatch_daily_sku", table_name="assembly_stock_mismatch_daily")
    op.drop_table("assembly_stock_mismatch_daily")
