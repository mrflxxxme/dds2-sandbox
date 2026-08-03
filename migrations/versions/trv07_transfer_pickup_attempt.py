# ruff: noqa: RUF002, RUF003
"""Забор переезда — по ПОПЫТКЕ отправки, а не один на документ

`uq_outbound_shipments_stock_transfer` (trv03) держал ровно один живой забор на
переезд. Обоснование там было «переотправки у переезда нет» — и оно неверно:
RETURNED → READY → SHIPPED разрешён таблицей переходов, кнопка «Переотправить»
есть в карточке, а `return_transfer` забор намеренно не удаляет («перевозка
состоялась и оплачена»). Уникальность вынуждала второй круг ПЕРЕЗАПИСЫВАТЬ
документ первого — вместе с его `pickup_cost`, `shipped_date` и связкой с уже
проведённым платежом: оплаченный рейс исчезал из «Оплат» и из отчёта
«Логистика переездов», две перевозки схлопывались в одну.

Ключ становится парой `(stock_transfer_id, attempt_no)` — ровно как у заявки на
сборку, где `attempt_no` живёт с самого начала. Данные не трогаем: у всех
существующих заборов `attempt_no = 1` (NOT NULL DEFAULT 1), то есть пара
уникальна ровно там же, где была уникальна одиночная колонка.

Revision ID: trv07_transfer_pickup_attempt
Revises: trv06_gazelka_transfer
Create Date: 2026-08-02 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "trv07_transfer_pickup_attempt"
down_revision: str | None = "trv06_gazelka_transfer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_outbound_shipments_stock_transfer", table_name="outbound_shipments")
    op.create_index(
        "uq_outbound_shipments_stock_transfer",
        "outbound_shipments",
        ["stock_transfer_id", "attempt_no"],
        unique=True,
        postgresql_where=sa.text("stock_transfer_id IS NOT NULL AND is_deleted = false"),
    )


def downgrade() -> None:
    # Обратно сузить ключ можно только если ни у одного переезда не завелось
    # второй попытки: иначе CREATE UNIQUE INDEX упадёт, и это ПРАВИЛЬНО —
    # молча схлопывать два оплаченных рейса в один нельзя.
    op.drop_index("uq_outbound_shipments_stock_transfer", table_name="outbound_shipments")
    op.create_index(
        "uq_outbound_shipments_stock_transfer",
        "outbound_shipments",
        ["stock_transfer_id"],
        unique=True,
        postgresql_where=sa.text("stock_transfer_id IS NOT NULL AND is_deleted = false"),
    )
