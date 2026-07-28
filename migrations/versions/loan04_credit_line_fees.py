"""loan: комиссия за неиспользованный лимит и день платежа

Revision ID: loan04_line_fees
Revises: loan03_credit_line
Create Date: 2026-07-27

У ВКЛ ВБ Банка три статьи расходов, а не одна:
- проценты на выбранное тело;
- комиссия за НЕиспользованный лимит (2 % годовых на разницу «лимит − выбрано»);
- разовая комиссия за установление лимита (0.25 %, разово при открытии).

Плюс платят там 5-го числа следующего месяца, а не 25-го, как по частным займам,
поэтому день платежа тоже переезжает на уровень займа.
"""

import sqlalchemy as sa
from alembic import op

revision = "loan04_line_fees"
down_revision = "loan03_credit_line"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("loan", sa.Column("unused_limit_rate", sa.Numeric(6, 4), nullable=True))
    op.add_column("loan", sa.Column("payment_day", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("loan", "payment_day")
    op.drop_column("loan", "unused_limit_rate")
