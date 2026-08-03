"""payroll_employee.position — должность для разбивки ФОТ в ОПиУ.

Строка «ФОТ (начислено)» делится на подстроки: «Менеджеры» (процентные
начисления команд) и фикс-оклады по должностям сотрудников.

Revision ID: payroll02_position
Revises: payroll01_domain
"""

import sqlalchemy as sa
from alembic import op

revision = "payroll02_position"
down_revision = "payroll01_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payroll_employee", sa.Column("position", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("payroll_employee", "position")
