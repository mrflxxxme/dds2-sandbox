# ruff: noqa: RUF002, RUF003
"""N заборов → одна оплата: снимаем partial-unique с matched_transaction_id

Агрегированный платёж перевозчику («оплата по счетам №787, №795, №796») покрывает
несколько заборов. Ручная привязка ставит один и тот же matched_transaction_id
нескольким заборам → нужно снять «одна транзакция → один забор». Защита от двойного
матча у авто-матчера держится на consumed-set (in-memory), а не на этом индексе.
Обычный (не-уникальный) индекс ix_outbound_shipments_matched_transaction_id остаётся.

Revision ID: pay04_shipment_multilink
Revises: pay03_shipment_payment_match
Create Date: 2026-06-26 12:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "pay04_shipment_multilink"
down_revision: str | None = "pay03_shipment_payment_match"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_outbound_shipment_matched_txn", table_name="outbound_shipments")


def downgrade() -> None:
    # Восстановить 1:1-инвариант (откат сразу после upgrade — N:1-данных ещё нет).
    op.create_index(
        "uq_outbound_shipment_matched_txn",
        "outbound_shipments",
        ["matched_transaction_id"],
        unique=True,
        postgresql_where=sa.text("matched_transaction_id IS NOT NULL AND is_deleted = false"),
    )
