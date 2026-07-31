"""wb_finance_rows.acquiring_fee/percent — эквайринг из отчёта реализации WB

WB отдаёт эквайринг в reportDetailByPeriod (`acquiring_fee` — сумма, `acquiring_percent`
— ставка банка), но синк эти поля не сохранял, и в воронке колонки «Эквайринг» просто
не из чего было построить. Само число уже сидит внутри `ppvz_for_pay` (а значит, и в
«Расходе WB»), поэтому колонка информационная: она разбирает расход на составляющие,
а не добавляет новый.

Исторические строки останутся с нулями, пока финансовый отчёт не перезальют.

Revision ID: fin01_acquiring_fee
Revises: fbsasm03_order_history
"""

import sqlalchemy as sa
from alembic import op

revision = "fin01_acquiring_fee"
down_revision = "fbsasm03_order_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wb_finance_rows",
        sa.Column("acquiring_fee", sa.Numeric(18, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "wb_finance_rows",
        sa.Column("acquiring_percent", sa.Numeric(8, 4), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("wb_finance_rows", "acquiring_percent")
    op.drop_column("wb_finance_rows", "acquiring_fee")
