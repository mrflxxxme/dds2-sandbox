"""loan: плановый график платежей и разовые комиссии

Revision ID: loan05_schedule_fees
Revises: loan04_line_fees
Create Date: 2026-07-27

Зачем:
- Аннуитет (Симпл Финанс, 30 млн под 21 %) описывается не ставкой, а ГРАФИКОМ:
  платёж фиксирован, а деление на тело и проценты банк считает по своему
  округлению. График храним как факт договора и сверяем с ним реальные платежи.
- Разовые комиссии (за выдачу 4.25 %, за установление лимита 0.25 %) — расход на
  ВЕСЬ срок договора, а не одного дня. Отдельная таблица с флагом амортизации,
  чтобы касса (LoanPayment) и стоимость денег (ОПиУ) не мешались.
"""

import sqlalchemy as sa
from alembic import op

revision = "loan05_schedule_fees"
down_revision = "loan04_line_fees"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "loan_schedule_entry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("loan_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=True),
        sa.Column("principal_due", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("interest_due", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("payment_total", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("balance_after", sa.Numeric(18, 2), nullable=True),
        sa.Column("is_fee", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["loan_id"], ["loan.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_loan_schedule_loan_seq", "loan_schedule_entry", ["loan_id", "seq"], unique=True
    )
    op.create_index("ix_loan_schedule_loan_due", "loan_schedule_entry", ["loan_id", "due_date"])

    op.create_table(
        "loan_fee",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("loan_id", sa.Integer(), nullable=False),
        sa.Column("fee_kind", sa.String(length=20), nullable=False, server_default="OTHER"),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("charged_at", sa.Date(), nullable=False),
        sa.Column("amortize", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("amortize_from", sa.Date(), nullable=True),
        sa.Column("amortize_to", sa.Date(), nullable=True),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["loan_id"], ["loan.id"]),
        sa.ForeignKeyConstraint(["payment_id"], ["loan_payment.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_loan_fee_loan_charged", "loan_fee", ["loan_id", "charged_at"])
    op.create_index("ix_loan_fee_payment", "loan_fee", ["payment_id"])


def downgrade() -> None:
    op.drop_index("ix_loan_fee_payment", table_name="loan_fee")
    op.drop_index("ix_loan_fee_loan_charged", table_name="loan_fee")
    op.drop_table("loan_fee")
    op.drop_index("ix_loan_schedule_loan_due", table_name="loan_schedule_entry")
    op.drop_index("ix_loan_schedule_loan_seq", table_name="loan_schedule_entry")
    op.drop_table("loan_schedule_entry")
