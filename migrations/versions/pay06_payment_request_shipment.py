# ruff: noqa: RUF002, RUF003
"""payment_request_shipment: одна заявка → несколько заборов (M:N)

Один счёт перевозчика может покрывать несколько отгрузок. Child-таблица связи
заявка↔забор. Backfill: существующие заявки с outbound_shipment_id → строка связи.
PaymentRequest.outbound_shipment_id остаётся как «первый/основной» забор (legacy).

Revision ID: pay06_payment_request_shipment
Revises: pay05_drop_draft_status
Create Date: 2026-06-26 13:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "pay06_payment_request_shipment"
down_revision: str | None = "pay05_drop_draft_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_request_shipment",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("payment_request_id", sa.Integer(), nullable=False),
        sa.Column("outbound_shipment_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["payment_request_id"], ["payment_request.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["outbound_shipment_id"], ["outbound_shipments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_request_shipment_project_id", "payment_request_shipment", ["project_id"])
    op.create_index("ix_payment_request_shipment_request_id", "payment_request_shipment", ["payment_request_id"])
    op.create_index(
        "ix_payment_request_shipment_outbound_shipment_id", "payment_request_shipment", ["outbound_shipment_id"]
    )
    op.create_index(
        "uq_payment_request_shipment",
        "payment_request_shipment",
        ["payment_request_id", "outbound_shipment_id"],
        unique=True,
    )

    # Backfill: каждая активная заявка с outbound_shipment_id → строка связи.
    op.execute(
        "INSERT INTO payment_request_shipment (project_id, payment_request_id, outbound_shipment_id) "
        "SELECT project_id, id, outbound_shipment_id FROM payment_request "
        "WHERE outbound_shipment_id IS NOT NULL AND is_deleted = false"
    )


def downgrade() -> None:
    op.drop_index("uq_payment_request_shipment", table_name="payment_request_shipment")
    op.drop_index(
        "ix_payment_request_shipment_outbound_shipment_id", table_name="payment_request_shipment"
    )
    op.drop_index("ix_payment_request_shipment_request_id", table_name="payment_request_shipment")
    op.drop_index("ix_payment_request_shipment_project_id", table_name="payment_request_shipment")
    op.drop_table("payment_request_shipment")
