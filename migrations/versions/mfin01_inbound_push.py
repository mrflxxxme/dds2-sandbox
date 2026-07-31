# ruff: noqa: RUF002, RUF003
"""migfull_shipment_orders.inbound_receipt_id: поставка (приёмка) у Натали из DDS

Создание поставки в WMS Натали (migfull-портал, ресурс /app/submissions) из
нашей приёмки машины (InboundReceipt). Колонка — маркер идемпотентности:
audit-строка SENT с этим FK блокирует повторное НЕОБРАТИМОЕ создание
(анти-дубль, как assembly_request_id у заявок на отгрузку).

Revision ID: mfin01_inbound_push
Revises: fbsasm03_order_history
Create Date: 2026-07-31 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "mfin01_inbound_push"
down_revision: str | None = "fbsasm03_order_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "migfull_shipment_orders",
        sa.Column("inbound_receipt_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_migfull_shipment_orders_inbound_receipt_id",
        "migfull_shipment_orders",
        "inbound_receipts",
        ["inbound_receipt_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_migfull_shipment_orders_inbound_receipt_id",
        "migfull_shipment_orders",
        ["inbound_receipt_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_migfull_shipment_orders_inbound_receipt_id", table_name="migfull_shipment_orders")
    op.drop_constraint(
        "fk_migfull_shipment_orders_inbound_receipt_id", "migfull_shipment_orders", type_="foreignkey"
    )
    op.drop_column("migfull_shipment_orders", "inbound_receipt_id")
