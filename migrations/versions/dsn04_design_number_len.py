# ruff: noqa: RUF002, RUF003
"""design: номер заявки становится редактируемым — колонка расширяется до 40 символов

Revision ID: dsn04_design_number_len
Revises: dsnmrg_design_dev
Create Date: 2026-08-20

Волна B v2 (Р18): ведущий дизайнер задаёт заявке свой номер — например внешний
номер из таблицы заказчика. Автогенерация `DES-N` остаётся дефолтом и живёт
своей линейкой; расширяется только вместимость колонки.

Таблица НЕ переписывается (relfilenode не меняется), но партиальный
`uq_design_tasks_project_number` при смене типа колонки ПЕРЕСТРАИВАЕТСЯ под
ACCESS EXCLUSIVE — проверено на PostgreSQL 15. На текущем объёме `design_tasks`
это миллисекунды; на большой таблице такой ALTER так дёшево бы не обошёлся.

downgrade НЕ усекает данные намеренно: если к моменту отката в проекте уже есть
номера длиннее 20 символов, откат упадёт. Это честнее тихой потери номеров —
администратор увидит проблему и решит её осознанно.
"""

import sqlalchemy as sa
from alembic import op

revision = "dsn04_design_number_len"
down_revision = "dsnmrg_design_dev"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "design_tasks",
        "number",
        existing_type=sa.String(length=20),
        type_=sa.String(length=40),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "design_tasks",
        "number",
        existing_type=sa.String(length=40),
        type_=sa.String(length=20),
        existing_nullable=False,
    )
