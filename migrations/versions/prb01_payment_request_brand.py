"""payment_request.brand — бренд-атрибуция заявки на оплату

Revision ID: prb01_payment_request_brand
Revises: pay08_payment_categories
Create Date: 2026-06-29

Бренд (атрибут товара из wb_finance_rows.brand_name) на заявке на оплату.
NULL = «Все бренды» — общий расход по проекту. Колонка nullable (без
server_default): старые заявки остаются «Все бренды». Тег для фильтра списка.
"""

from alembic import op
import sqlalchemy as sa

revision = "prb01_payment_request_brand"
down_revision = "pay08_payment_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_request", sa.Column("brand", sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_request", "brand")
