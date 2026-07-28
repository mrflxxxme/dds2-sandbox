"""payroll_salary_period — история фикс-окладов по месяцам.

Оклад менялся во времени («у бух была 50к, с июля 80к»), а одно поле
fixed_salary применялось ко ВСЕМ месяцам ведомости и ОПиУ задним числом.
Теперь периоды: оклад месяца M = amount периода с max(valid_from) <= M,
до первого периода — 0. Существующие fixed_salary переносятся периодом
с valid_from 2020-01-01 (поведение «на все месяцы» сохраняется, юзер
уточняет даты в UI), колонка удаляется.

Revision ID: payroll03_salary_periods
Revises: payroll02_position
"""

import sqlalchemy as sa
from alembic import op

revision = "payroll03_salary_periods"
down_revision = "payroll02_position"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payroll_salary_period",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("payroll_employee.id"), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("employee_id", "valid_from", name="uq_payroll_salary_period"),
    )
    op.create_index("ix_payroll_salary_period_employee_id", "payroll_salary_period", ["employee_id"])

    op.execute(
        """
        INSERT INTO payroll_salary_period (employee_id, valid_from, amount, created_at)
        SELECT id, DATE '2020-01-01', fixed_salary, NOW()
        FROM payroll_employee
        WHERE fixed_salary IS NOT NULL
        """
    )
    op.drop_column("payroll_employee", "fixed_salary")


def downgrade() -> None:
    op.add_column("payroll_employee", sa.Column("fixed_salary", sa.Numeric(18, 2), nullable=True))
    # Восстанавливаем последним (максимальный valid_from) периодом.
    op.execute(
        """
        UPDATE payroll_employee pe
        SET fixed_salary = sp.amount
        FROM (
            SELECT DISTINCT ON (employee_id) employee_id, amount
            FROM payroll_salary_period
            ORDER BY employee_id, valid_from DESC
        ) sp
        WHERE sp.employee_id = pe.id
        """
    )
    op.drop_table("payroll_salary_period")
