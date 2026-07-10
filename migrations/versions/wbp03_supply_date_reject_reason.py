"""assembly_wb_supply: дата брони слота WB + текст кабинетных ошибок поставки

Revision ID: wbp03_supply_date_reject
Revises: wbp02_wb_supply_state
Create Date: 2026-07-10

Две колонки из карточки поставки кабинета (supplyDetails):
- supply_date   — дата забронированного слота сдачи (supplyDate). Показываем
                  колонкой «Дата брони WB» в списке сборок и в шапке заявки.
- reject_reason — текст ошибок поставки (rejectReason): «Не заполнены ШК коробов
                  в разделе Упаковка…», «Не заполнен пропуск…». Показываем
                  баннером в панели «Поставка WB», как в кабинете.
"""

from alembic import op
import sqlalchemy as sa

revision = "wbp03_supply_date_reject"
down_revision = "wbp02_wb_supply_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assembly_wb_supply",
        sa.Column("supply_date", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "assembly_wb_supply",
        sa.Column("reject_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assembly_wb_supply", "reject_reason")
    op.drop_column("assembly_wb_supply", "supply_date")
