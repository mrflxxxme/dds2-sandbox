# ruff: noqa: RUF002, RUF003
"""Поставка у Натали из перемещения: источник TR-… в audit migfull-портала

Поставка (приёмка) на складе ФФ «Натали» умела создаваться только из НАШЕЙ
приёмки машины (`migfull_shipment_orders.inbound_receipt_id`). Перемещение
(`StockTransfer`, наш склад → Натали) собственной приёмки не создаёт — приход
у ФФ заводит эта же поставка, поэтому источником документа становится сам
переезд. Колонка нужна анти-дублю: создание на портале НЕОБРАТИМО (нет
delete/cancel), повтор без `force_resend` обязан упираться в 409.

Revision ID: mfp01_migfull_order_transfer
Revises: trv03_transfer_invariants
Create Date: 2026-07-31 18:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "mfp01_migfull_order_transfer"
down_revision: str | None = "trv03_transfer_invariants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "migfull_shipment_orders",
        sa.Column("stock_transfer_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_migfull_shipment_orders_stock_transfer_id",
        "migfull_shipment_orders",
        "stock_transfers",
        ["stock_transfer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_migfull_shipment_orders_stock_transfer_id",
        "migfull_shipment_orders",
        ["stock_transfer_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_migfull_shipment_orders_stock_transfer_id", table_name="migfull_shipment_orders")
    op.drop_constraint(
        "fk_migfull_shipment_orders_stock_transfer_id",
        "migfull_shipment_orders",
        type_="foreignkey",
    )
    op.drop_column("migfull_shipment_orders", "stock_transfer_id")
