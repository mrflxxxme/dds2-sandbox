"""loan_schedule_entry.is_debt_plan — план погашения долга vs график начисления

Строки графика с процентами ОТМЕНЯЮТ формулу начисления: движок берёт проценты
из графика (у аннуитета банк считает своё округление, формулой его не повторить).
План погашения накопленного долга так вести нельзя — проценты продолжают капать
по ставке, а строки лишь говорят, когда и сколько платить в счёт уже начисленного.
Без флага пять строк «по 2,59 млн 25-го числа» подменили бы собой начисление
всего займа и обнулили бы тот самый долг, который они гасят.

Revision ID: loan07_debt_plan
Revises: ff11_transfer_fact_applied
"""

import sqlalchemy as sa
from alembic import op

revision = "loan07_debt_plan"
down_revision = "ff11_transfer_fact_applied"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "loan_schedule_entry",
        sa.Column(
            "is_debt_plan",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("loan_schedule_entry", "is_debt_plan")
