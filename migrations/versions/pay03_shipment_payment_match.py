# ruff: noqa: RUF002, RUF003
"""shipment↔выписка авто-связка + архив бесхозных платежей

Task1: outbound_shipments.matched_transaction_id / matched_at + partial-unique
индекс «одна транзакция → один забор» (зеркало uq_payment_request_matched_txn).
Task2: payment_txn_archive — маркер «исходящий платёж без забора разобран».
Всё аддитивно (nullable-колонки + новая таблица-маркер).

Revision ID: pay03_shipment_payment_match
Revises: pay02_payment_shipment_archive
Create Date: 2026-06-26 11:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "pay03_shipment_payment_match"
down_revision: str | None = "pay02_payment_shipment_archive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── Task1: прямая связка забор → транзакция выписки ───────────────────
    op.add_column(
        "outbound_shipments",
        sa.Column("matched_transaction_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "outbound_shipments",
        sa.Column("matched_at", sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        "fk_outbound_shipments_matched_transaction_id",
        "outbound_shipments",
        "transactions",
        ["matched_transaction_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_outbound_shipments_matched_transaction_id",
        "outbound_shipments",
        ["matched_transaction_id"],
    )
    # «одна транзакция → один забор»
    op.create_index(
        "uq_outbound_shipment_matched_txn",
        "outbound_shipments",
        ["matched_transaction_id"],
        unique=True,
        postgresql_where=sa.text("matched_transaction_id IS NOT NULL AND is_deleted = false"),
    )

    # ─── Task2: архив бесхозных платежей перевозчику ──────────────────────
    op.create_table(
        "payment_txn_archive",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("archived_by_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["archived_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_txn_archive_project_id", "payment_txn_archive", ["project_id"])
    op.create_index(
        "uq_payment_txn_archive",
        "payment_txn_archive",
        ["project_id", "transaction_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_payment_txn_archive", table_name="payment_txn_archive")
    op.drop_index("ix_payment_txn_archive_project_id", table_name="payment_txn_archive")
    op.drop_table("payment_txn_archive")

    op.drop_index("uq_outbound_shipment_matched_txn", table_name="outbound_shipments")
    op.drop_index("ix_outbound_shipments_matched_transaction_id", table_name="outbound_shipments")
    op.drop_constraint(
        "fk_outbound_shipments_matched_transaction_id", "outbound_shipments", type_="foreignkey"
    )
    op.drop_column("outbound_shipments", "matched_at")
    op.drop_column("outbound_shipments", "matched_transaction_id")
