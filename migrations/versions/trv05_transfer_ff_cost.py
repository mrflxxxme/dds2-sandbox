# ruff: noqa: RUF002, RUF003
"""Доп-услуги ФФ по переезду: ручная сумма и комментарий

У переезда появляется ожидаемая стоимость услуг ФФ — как у заявки на сборку.
Тарифицируемая часть считается по ставке склада-ИСТОЧНИКА
(`FfServiceType.TRANSFER_ASSEMBLY` — отдельный тип услуги, добавлен в enum
приложения; DDL для него не нужен, `warehouse_tariffs.service_type` — VARCHAR).

Здесь — вторая половина: ручная сумма за то, что в тарифную сетку не
укладывается (стрейч, маркировка), зеркало `assembly_requests.ff_custom_cost`.

Revision ID: trv05_transfer_ff_cost
Revises: trv04_transfer_status
Create Date: 2026-08-01 12:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "trv05_transfer_ff_cost"
down_revision: str | None = "trv04_transfer_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Numeric(18, 2) — деньги, не Float (Iron rule проекта).
    op.add_column("stock_transfers", sa.Column("ff_custom_cost", sa.Numeric(18, 2), nullable=True))
    op.add_column(
        "stock_transfers",
        sa.Column("ff_custom_cost_comment", sa.String(length=300), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stock_transfers", "ff_custom_cost_comment")
    op.drop_column("stock_transfers", "ff_custom_cost")
