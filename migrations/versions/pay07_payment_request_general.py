# ruff: noqa: RUF002, RUF003
"""payment_request: общая заявка без проекта + назначение (category)

Любой пользователь может подать заявку на оплату (фотоконтент/таможня/другое),
не привязывая её к проекту. Делаем `project_id` nullable на заявке и её документах
(«общая» заявка = единый инбокс согласования) и добавляем `category` (назначение).

Существующие заявки — логистика → бэкфилл category = 'LOGISTICS'.
Нумерация «общих» (project_id IS NULL) заявок — отдельный partial-unique индекс
(в Postgres два NULL различны, поэтому (project_id, number) их не ловит).

Revision ID: pay07_payment_request_general
Revises: co01_category_overrides
Create Date: 2026-06-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "pay07_payment_request_general"
down_revision: str | None = "co01_category_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # project_id → nullable (заявка может быть «общей», без проекта).
    op.alter_column("payment_request", "project_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column(
        "payment_request_document", "project_id", existing_type=sa.Integer(), nullable=True
    )

    # Назначение оплаты (LOGISTICS / PHOTO_CONTENT / CUSTOMS / OTHER).
    op.add_column("payment_request", sa.Column("category", sa.String(length=30), nullable=True))
    op.execute("UPDATE payment_request SET category = 'LOGISTICS' WHERE category IS NULL")

    # Уникальность номера среди «общих» заявок (project_id IS NULL): обычный
    # (project_id, number) не работает — NULL-проекты в Postgres различны.
    op.create_index(
        "uq_payment_request_general_number",
        "payment_request",
        ["number"],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL AND is_deleted = false"),
    )


def downgrade() -> None:
    op.drop_index("uq_payment_request_general_number", table_name="payment_request")
    op.drop_column("payment_request", "category")
    # ⚠ Вернуть NOT NULL можно только если «общих» (project_id IS NULL) заявок нет —
    # иначе ALTER упадёт. Это ожидаемо: даунгрейд фичи требует отсутствия её данных.
    op.alter_column(
        "payment_request_document", "project_id", existing_type=sa.Integer(), nullable=False
    )
    op.alter_column("payment_request", "project_id", existing_type=sa.Integer(), nullable=False)
