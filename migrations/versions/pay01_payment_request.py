# ruff: noqa: RUF002, RUF003
"""payment-request domain: заявки на оплату перевозчику + документы + аудит статусов

Новый домен «Заявка на оплату»: логист по отгрузке (OutboundShipment) формирует
заявку на оплату перевозчику (реквизиты вручную/из контрагента), прикладывает
счёт+акт, передаёт в оплату; оператор создаёт черновик платёжки в банке (Faktura
write); матчер выписки авто-ставит PAID по совпадению ИНН+сумма.

Аддитивно: 3 новые таблицы + 4 банковские колонки на counterparty (реквизиты
перевозчика для авто-заполнения). Без переноса данных.

Revision ID: pay01_payment_request
Revises: ffp1_ff_portal
Create Date: 2026-06-25 18:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "pay01_payment_request"
down_revision: str | None = "ffp1_ff_portal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ─── counterparty: банковские реквизиты перевозчика ────────────────────
    op.add_column("counterparty", sa.Column("bank_account", sa.String(length=20), nullable=True))
    op.add_column("counterparty", sa.Column("bik", sa.String(length=9), nullable=True))
    op.add_column("counterparty", sa.Column("bank_name", sa.String(length=255), nullable=True))
    op.add_column("counterparty", sa.Column("corr_account", sa.String(length=20), nullable=True))

    # ─── payment_request ───────────────────────────────────────────────────
    op.create_table(
        "payment_request",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="DRAFT", nullable=False),
        sa.Column("source", sa.String(length=20), server_default="MANUAL", nullable=False),
        sa.Column("counterparty_id", sa.Integer(), nullable=True),
        sa.Column("outbound_shipment_id", sa.Integer(), nullable=True),
        sa.Column("assembly_request_id", sa.Integer(), nullable=True),
        sa.Column("payee_inn", sa.String(length=12), nullable=True),
        sa.Column("payee_kpp", sa.String(length=9), nullable=True),
        sa.Column("payee_account", sa.String(length=20), nullable=True),
        sa.Column("payee_bik", sa.String(length=9), nullable=True),
        sa.Column("payee_bank_name", sa.String(length=255), nullable=True),
        sa.Column("payee_corr_account", sa.String(length=20), nullable=True),
        sa.Column("payee_name", sa.String(length=500), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), server_default="RUB", nullable=False),
        sa.Column("pickup_date", sa.Date(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("matched_transaction_id", sa.Integer(), nullable=True),
        sa.Column("matched_at", sa.DateTime(), nullable=True),
        sa.Column("bank_guid", sa.String(length=36), nullable=True),
        sa.Column("bank_doc_id", sa.String(length=100), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("amount > 0", name="ck_payment_request_amount_positive"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["counterparty_id"], ["counterparty.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["outbound_shipment_id"], ["outbound_shipments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assembly_request_id"], ["assembly_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["matched_transaction_id"], ["transactions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_request_project_id", "payment_request", ["project_id"])
    op.create_index("ix_payment_request_status", "payment_request", ["project_id", "status"])
    op.create_index("ix_payment_request_outbound_shipment_id", "payment_request", ["outbound_shipment_id"])
    op.create_index("ix_payment_request_counterparty_id", "payment_request", ["counterparty_id"])
    # Partial unique indexes — все WHERE is_deleted = false (иначе soft-delete занимает слот).
    op.create_index(
        "uq_payment_request_project_number",
        "payment_request",
        ["project_id", "number"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "uq_payment_request_bank_guid",
        "payment_request",
        ["bank_guid"],
        unique=True,
        postgresql_where=sa.text("bank_guid IS NOT NULL AND is_deleted = false"),
    )
    # «debit consumed once»: одна банковская транзакция матчит максимум одну заявку.
    op.create_index(
        "uq_payment_request_matched_txn",
        "payment_request",
        ["matched_transaction_id"],
        unique=True,
        postgresql_where=sa.text("matched_transaction_id IS NOT NULL AND is_deleted = false"),
    )

    # ─── payment_request_document ──────────────────────────────────────────
    op.create_table(
        "payment_request_document",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("payment_request_id", sa.Integer(), nullable=False),
        sa.Column("minio_path", sa.String(length=500), nullable=False),
        sa.Column("doc_type", sa.String(length=20), server_default="INVOICE", nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("uploaded_by_user_id", sa.Integer(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["payment_request_id"], ["payment_request.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_request_document_project_id", "payment_request_document", ["project_id"])
    op.create_index(
        "ix_payment_request_document_request_id", "payment_request_document", ["payment_request_id"]
    )

    # ─── payment_request_event ─────────────────────────────────────────────
    op.create_table(
        "payment_request_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("payment_request_id", sa.Integer(), nullable=False),
        sa.Column("old_status", sa.String(length=20), nullable=True),
        sa.Column("new_status", sa.String(length=20), nullable=False),
        sa.Column("changed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("changed_by", sa.String(length=100), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["payment_request_id"], ["payment_request.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payment_request_event_request_id", "payment_request_event", ["payment_request_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_payment_request_event_request_id", table_name="payment_request_event")
    op.drop_table("payment_request_event")

    op.drop_index("ix_payment_request_document_request_id", table_name="payment_request_document")
    op.drop_index("ix_payment_request_document_project_id", table_name="payment_request_document")
    op.drop_table("payment_request_document")

    op.drop_index("uq_payment_request_matched_txn", table_name="payment_request")
    op.drop_index("uq_payment_request_bank_guid", table_name="payment_request")
    op.drop_index("uq_payment_request_project_number", table_name="payment_request")
    op.drop_index("ix_payment_request_counterparty_id", table_name="payment_request")
    op.drop_index("ix_payment_request_outbound_shipment_id", table_name="payment_request")
    op.drop_index("ix_payment_request_status", table_name="payment_request")
    op.drop_index("ix_payment_request_project_id", table_name="payment_request")
    op.drop_table("payment_request")

    op.drop_column("counterparty", "corr_account")
    op.drop_column("counterparty", "bank_name")
    op.drop_column("counterparty", "bik")
    op.drop_column("counterparty", "bank_account")
