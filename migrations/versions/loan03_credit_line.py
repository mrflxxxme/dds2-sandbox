"""loan: кредитная линия, календарь начисления и история ставок

Revision ID: loan03_credit_line
Revises: ff10_req_transfer
Create Date: 2026-07-27

Зачем: появились продукты, которые не описываются срочным займом с одной ставкой.
- ВКЛ ВБ Банка: тело растёт на выборках траншей и падает на погашениях, есть лимит,
  проценты считаются по календарному месяцу, а ставка = ключевая ЦБ + спред и
  меняется прямо внутри периода (июнь 2026: 19.5 % до 21-го, дальше 19.25 %).
- Одним полем `rate` такое не описать: исторические начисления пересчитались бы по
  новой ставке и разошлись с выпиской банка.

Дефолты повторяют прежнее поведение (TERM + PERIOD_25), поэтому существующие
займы считаются ровно как раньше.
"""

import sqlalchemy as sa
from alembic import op

revision = "loan03_credit_line"
down_revision = "ff10_req_transfer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "loan",
        sa.Column("loan_kind", sa.String(length=20), nullable=False, server_default="TERM"),
    )
    op.add_column(
        "loan",
        sa.Column("accrual_kind", sa.String(length=20), nullable=False, server_default="PERIOD_25"),
    )
    op.add_column("loan", sa.Column("credit_limit", sa.Numeric(18, 2), nullable=True))

    op.create_table(
        "loan_rate_period",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("loan_id", sa.Integer(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(6, 4), nullable=False),
        sa.Column("base_rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("spread", sa.Numeric(6, 4), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["loan_id"], ["loan.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_loan_rate_period_loan_from", "loan_rate_period", ["loan_id", "valid_from"]
    )


def downgrade() -> None:
    op.drop_index("ix_loan_rate_period_loan_from", table_name="loan_rate_period")
    op.drop_table("loan_rate_period")
    op.drop_column("loan", "credit_limit")
    op.drop_column("loan", "accrual_kind")
    op.drop_column("loan", "loan_kind")
