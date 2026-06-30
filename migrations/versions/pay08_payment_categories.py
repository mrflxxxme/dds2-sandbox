# ruff: noqa: RUF001, RUF002, RUF003
"""payment_categories — редактируемый справочник «Назначение оплаты»

Revision ID: pay08_payment_categories
Revises: cogs01_category_ref_is_cogs
Create Date: 2026-06-28

Категории заявок на оплату («Назначение») перестают быть хардкод-енумом — теперь
редактируемый справочник (добавить/переименовать/порядок/удалить). Колонка
payment_requests.category остаётся String (код хранится как есть — без миграции данных).
Сидируем 7 текущих как is_system (переименовать можно, удалить нельзя; LOGISTICS/OTHER —
дефолты сервиса). project_id NULL = глобальная (общая для всех брендов).

Partial-unique по (COALESCE(project_id,0), code) WHERE NOT is_deleted: NULL-project_id
в обычном UNIQUE считались бы различными → COALESCE сводит глобальные к «проекту 0».
"""

from alembic import op
import sqlalchemy as sa

revision = "pay08_payment_categories"
down_revision = "cogs01_category_ref_is_cogs"
branch_labels = None
depends_on = None


# Сид: код (стабильный, хранится в payment_requests.category) → лейбл + порядок.
_SEED = [
    ("PHOTO_CONTENT", "Фотоконтент", 10),
    ("DESIGN", "Дизайн", 20),
    ("FULFILLMENT", "Фулфилмент", 30),
    ("CUSTOMS", "Таможенное оформление", 40),
    ("LOGISTICS", "Логистика", 50),
    ("HOUSEHOLD", "Хозрасходы", 60),
    ("OTHER", "Другое", 70),
]


def upgrade() -> None:
    op.create_table(
        "payment_categories",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_payment_categories_project_id", "payment_categories", ["project_id"])

    # Сид 7 системных категорий (глобальные: project_id NULL).
    cats = sa.table(
        "payment_categories",
        sa.column("project_id", sa.Integer),
        sa.column("code", sa.String),
        sa.column("label", sa.String),
        sa.column("sort_order", sa.Integer),
        sa.column("is_system", sa.Boolean),
        sa.column("is_deleted", sa.Boolean),
    )
    op.bulk_insert(
        cats,
        [
            {"project_id": None, "code": c, "label": lbl, "sort_order": so, "is_system": True, "is_deleted": False}
            for c, lbl, so in _SEED
        ],
    )

    # Partial-unique по коду (NULL-project_id → 0): повторный INSERT после soft-delete не падает.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_payment_category_code "
            "ON payment_categories (COALESCE(project_id, 0), code) WHERE is_deleted = false"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_payment_category_code")
    op.drop_index("ix_payment_categories_project_id", table_name="payment_categories")
    op.drop_table("payment_categories")
