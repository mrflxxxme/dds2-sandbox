"""Payroll domain: сотрудники, команды, тарифная лестница, отметки выплат.

Начисление ЗП командам = % от недельной «Чистой выплаты» БДР по брендам/
категориям команды (лестница из зп.xlsx, «Команда» = 45% от «Компании»).
Сид лестницы кладётся сразу в миграции: справочник глобальный (без project_id,
одна лестница для всех проектов — решение юзера 2026-07-28), valid_from
2026-01-01. Дубль порога 23 000 000 из исходного файла дедуплицирован.

Revision ID: payroll01_domain
Revises: loan07_debt_plan
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "payroll01_domain"
down_revision = "loan07_debt_plan"
branch_labels = None
depends_on = None

# (порог недельной выплаты, ставка «Компания», ставка «Команда» = 45%)
TARIFF_SEED = [
    ("200000", "0.0265", "0.011925"),
    ("375000", "0.0262", "0.011790"),
    ("500000", "0.0258", "0.011610"),
    ("750000", "0.0253", "0.011385"),
    ("1000000", "0.0247", "0.011115"),
    ("1250000", "0.0239", "0.010755"),
    ("1500000", "0.0231", "0.010395"),
    ("1750000", "0.0221", "0.009945"),
    ("2250000", "0.0216", "0.009720"),
    ("2500000", "0.0210", "0.009450"),
    ("3000000", "0.0204", "0.009180"),
    ("3500000", "0.0197", "0.008865"),
    ("4000000", "0.0189", "0.008505"),
    ("4500000", "0.0180", "0.008100"),
    ("5000000", "0.0177", "0.007965"),
    ("6000000", "0.0172", "0.007740"),
    ("7500000", "0.0167", "0.007515"),
    ("9000000", "0.0161", "0.007245"),
    ("10000000", "0.0155", "0.006975"),
    ("11000000", "0.0151", "0.006795"),
    ("12000000", "0.0148", "0.006660"),
    ("13000000", "0.0144", "0.006480"),
    ("14000000", "0.0140", "0.006300"),
    ("15000000", "0.0136", "0.006120"),
    ("16000000", "0.0131", "0.005895"),
    ("17000000", "0.0127", "0.005715"),
    ("18000000", "0.0122", "0.005490"),
    ("19000000", "0.0122", "0.005490"),
    ("20000000", "0.0122", "0.005490"),
    ("21000000", "0.0122", "0.005490"),
    ("22000000", "0.0122", "0.005490"),
    ("23000000", "0.0122", "0.005490"),
    ("25000000", "0.0122", "0.005490"),
]


def upgrade() -> None:
    op.create_table(
        "payroll_employee",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("counterparty_id", sa.Integer(), sa.ForeignKey("counterparty.id"), nullable=True),
        sa.Column("fixed_salary", sa.Numeric(18, 2), nullable=True),
        sa.Column("fixed_pay_days", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_payroll_employee_project_id", "payroll_employee", ["project_id"])
    op.create_index("ix_payroll_employee_counterparty_id", "payroll_employee", ["counterparty_id"])

    op.create_table(
        "payroll_team",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_payroll_team_project_id", "payroll_team", ["project_id"])

    op.create_table(
        "payroll_team_member",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("payroll_team.id"), nullable=False),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("payroll_employee.id"), nullable=False),
        sa.UniqueConstraint("team_id", "employee_id", name="uq_payroll_team_member"),
    )
    op.create_index("ix_payroll_team_member_team_id", "payroll_team_member", ["team_id"])
    op.create_index("ix_payroll_team_member_employee_id", "payroll_team_member", ["employee_id"])

    op.create_table(
        "payroll_team_scope",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("payroll_team.id"), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("value", sa.String(length=300), nullable=False),
        sa.UniqueConstraint("team_id", "kind", "value", name="uq_payroll_team_scope"),
    )
    op.create_index("ix_payroll_team_scope_team_id", "payroll_team_scope", ["team_id"])

    op.create_table(
        "payroll_tariff_step",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("threshold", sa.Numeric(18, 2), nullable=False),
        sa.Column("company_rate", sa.Numeric(10, 6), nullable=False),
        sa.Column("team_rate", sa.Numeric(10, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("valid_from", "threshold", name="uq_payroll_tariff_step"),
    )

    op.create_table(
        "payroll_payout_mark",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("payroll_employee.id"), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("pay_day", sa.Integer(), nullable=False),
        sa.Column("paid", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("employee_id", "month", "pay_day", name="uq_payroll_payout_mark"),
    )
    op.create_index("ix_payroll_payout_mark_project_id", "payroll_payout_mark", ["project_id"])
    op.create_index("ix_payroll_payout_mark_employee_id", "payroll_payout_mark", ["employee_id"])

    op.execute(
        "INSERT INTO payroll_tariff_step (valid_from, threshold, company_rate, team_rate, created_at) VALUES "
        + ", ".join(
            f"('2026-01-01', {t}, {c}, {k}, NOW())" for t, c, k in TARIFF_SEED
        )
    )


def downgrade() -> None:
    op.drop_table("payroll_payout_mark")
    op.drop_table("payroll_tariff_step")
    op.drop_table("payroll_team_scope")
    op.drop_table("payroll_team_member")
    op.drop_table("payroll_team")
    op.drop_table("payroll_employee")
