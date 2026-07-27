"""fulfillment: связь заявки ФФ с внутренним перемещением

Товар может приехать на ФФ-склад не от поставщика, а с нашего же склада
(`stock_transfers`, «Входящее ← апл»). Приходует его ФФ обычной заявкой-приёмкой,
а нашей `inbound_receipts` для такого переезда не существует — связать было
нечем. Колонка `stock_transfer_id` — третий (и по-прежнему взаимоисключающий)
слот связи рядом с assembly_request_id / inbound_receipt_id.

Revision ID: ff10_req_transfer
Revises: fbs06_supply_reject_dt
Create Date: 2026-07-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ff10_req_transfer"
down_revision: str | None = "fbs06_supply_reject_dt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "fulfillment_requests",
        sa.Column("stock_transfer_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fulfillment_requests_stock_transfer_id_fkey",
        "fulfillment_requests",
        "stock_transfers",
        ["stock_transfer_id"],
        ["id"],
    )
    # FK обязан иметь индекс: по нему ходит и подбор кандидатов (исключить уже
    # связанные перемещения), и обогащение списка заявок.
    op.create_index(
        "ix_fulfillment_requests_stock_transfer_id",
        "fulfillment_requests",
        ["stock_transfer_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_fulfillment_requests_stock_transfer_id", table_name="fulfillment_requests")
    op.drop_constraint(
        "fulfillment_requests_stock_transfer_id_fkey", "fulfillment_requests", type_="foreignkey"
    )
    op.drop_column("fulfillment_requests", "stock_transfer_id")
