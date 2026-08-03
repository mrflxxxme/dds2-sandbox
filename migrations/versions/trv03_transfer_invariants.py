# ruff: noqa: RUF002, RUF003
"""Инварианты переезда после ревью: один забор на переезд + SET NULL у перевозчика

1. Частичный уникальный индекс на `outbound_shipments.stock_transfer_id`.
   Инвариант «переезд = один забор = один платёж» держался ТОЛЬКО на row-lock
   при переходе DRAFT → IN_TRANSIT; на уровне БД два забора на один переезд
   ничем не запрещались, а это прямой путь к двойной заявке на оплату одной
   перевозки. Переотправки у переезда нет (в отличие от заявки с `attempt_no`),
   поэтому уникальность честная. Индекс заменяет обычный `ix_...`, а не
   добавляется к нему.

2. `stock_transfers.counterparty_id` → `ON DELETE SET NULL`. Было NO ACTION —
   расходилось с соседями (`assembly_requests.counterparty_id` и
   `outbound_shipments.counterparty_id` оба SET NULL) и уронило бы жёсткое
   удаление контрагента FK-violation'ом. В приложении контрагент только
   софт-делится, так что это про консистентность и чистящие скрипты.

Revision ID: trv03_transfer_invariants
Revises: trv02_transfer_pallets
Create Date: 2026-07-31 16:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "trv03_transfer_invariants"
down_revision: str | None = "trv02_transfer_pallets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Один живой забор на переезд.
    op.drop_index("ix_outbound_shipments_stock_transfer_id", table_name="outbound_shipments")
    op.create_index(
        "uq_outbound_shipments_stock_transfer",
        "outbound_shipments",
        ["stock_transfer_id"],
        unique=True,
        postgresql_where=sa.text("stock_transfer_id IS NOT NULL AND is_deleted = false"),
    )

    # 1b. Одна заявка — один переезд. Проверка «уже сконвертирована» в сервисе
    # не атомарна (SELECT, потом INSERT, без FOR UPDATE), поэтому двойной клик
    # порождал два переезда по одному составу, а их отправка списала бы склад
    # дважды. Индекс закрывает гонку на уровне БД.
    op.create_index(
        "uq_stock_transfers_converted_from_assembly",
        "stock_transfers",
        ["converted_from_assembly_id"],
        unique=True,
        postgresql_where=sa.text("converted_from_assembly_id IS NOT NULL AND is_deleted = false"),
    )

    # 2. Перевозчик переезда — SET NULL, как у заявки и забора.
    op.drop_constraint("fk_stock_transfers_counterparty_id", "stock_transfers", type_="foreignkey")
    op.create_foreign_key(
        "fk_stock_transfers_counterparty_id",
        "stock_transfers",
        "counterparty",
        ["counterparty_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_stock_transfers_counterparty_id", "stock_transfers", type_="foreignkey")
    op.create_foreign_key(
        "fk_stock_transfers_counterparty_id",
        "stock_transfers",
        "counterparty",
        ["counterparty_id"],
        ["id"],
    )

    op.drop_index("uq_stock_transfers_converted_from_assembly", table_name="stock_transfers")

    op.drop_index("uq_outbound_shipments_stock_transfer", table_name="outbound_shipments")
    op.create_index(
        "ix_outbound_shipments_stock_transfer_id",
        "outbound_shipments",
        ["stock_transfer_id"],
    )
