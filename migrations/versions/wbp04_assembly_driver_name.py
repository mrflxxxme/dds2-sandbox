"""assembly_requests: ФИО водителя (1:1 с полями WB-пропуска)

Revision ID: wbp04_assembly_driver_name
Revises: wbp03_supply_date_reject
Create Date: 2026-07-10

Модалка «Назначить машину» приведена к составу WB-пропуска: Имя, Фамилия,
Телефон, Марка, Госномер. Имя/Фамилия раньше нигде не хранились — их доставали
best-effort парсингом из свободной строки `vehicle_info`. Теперь это явные
колонки, а `vehicle_info` несёт чистый госномер.

Данные не мигрируем: у старых заявок `vehicle_info` остаётся свободной строкой
(«В874УА37 Крапива Дмитрий 8915…»), ФИО там NULL. Читатели терпят оба формата.
"""

from alembic import op
import sqlalchemy as sa

revision = "wbp04_assembly_driver_name"
down_revision = "wbp03_supply_date_reject"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assembly_requests",
        sa.Column("driver_first_name", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "assembly_requests",
        sa.Column("driver_last_name", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assembly_requests", "driver_last_name")
    op.drop_column("assembly_requests", "driver_first_name")
