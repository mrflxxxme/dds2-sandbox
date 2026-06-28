"""wb_prices (текущая цена витрины ВБ по nm_id)

Revision ID: pr01_wb_prices
Revises: pay07_payment_request_general
Create Date: 2026-06-28

Синк-зеркало цен из WB API «Цены и скидки» (discounts-prices, /api/v2/list/goods/filter):
один UPSERT-ряд на (project_id, nm_id). Питает страницу «Ценообразование»
(коэффициент наценки = текущая цена / себестоимость, с учётом расходов ВБ из воронки).
СПП тут НЕ хранится — этот эндпоинт его не отдаёт (берётся из воронки).
"""

import sqlalchemy as sa
from alembic import op

revision = "pr01_wb_prices"
down_revision = "pay07_payment_request_general"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wb_prices",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("nm_id", sa.Integer(), nullable=False),
        sa.Column("vendor_code", sa.String(length=100), nullable=True),
        sa.Column("base_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("price", sa.Numeric(18, 2), nullable=True),
        sa.Column("discount", sa.Numeric(5, 2), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="RUB"),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "nm_id", name="uq_wb_price_nm"),
    )
    op.create_index("ix_wb_prices_project", "wb_prices", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_wb_prices_project", table_name="wb_prices")
    op.drop_table("wb_prices")
