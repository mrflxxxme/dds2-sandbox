"""ff_billing: тарифы услуг ФФ, посуточное хранение, счета ФФ и сверка

Новые таблицы:
  * warehouse_tariffs — ставки услуг ФФ по складу (5 типов, история valid_from/to)
  * ff_storage_daily — суточное начисление хранения (штуки/короба/паллеты/₽)
  * ff_invoices — счета ФФ за период + метчинг с оплатой (выписка/заявка)
  * ff_invoice_lines — разнесение счёта по заявкам/дням/приёмкам/заборам

Плюс assembly_requests.ff_custom_cost / ff_custom_cost_comment — ручная сумма
доп-услуг ФФ по конкретной сборке.

Revision ID: ffb01_ff_billing
Revises: wh09_wh_extra_cp
Create Date: 2026-07-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "ffb01_ff_billing"
down_revision: str | None = "wh09_wh_extra_cp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "warehouse_tariffs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("service_type", sa.String(length=20), nullable=False),
        sa.Column("unit", sa.String(length=10), nullable=True),
        sa.Column("rate", sa.Numeric(18, 2), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("rate >= 0", name="ck_warehouse_tariffs_rate_nonneg"),
    )
    op.create_index("ix_warehouse_tariffs_project_id", "warehouse_tariffs", ["project_id"])
    op.create_index("ix_warehouse_tariffs_wh_service", "warehouse_tariffs", ["warehouse_id", "service_type"])

    op.create_table(
        "ff_storage_daily",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("units", sa.Integer(), server_default="0", nullable=False),
        sa.Column("boxes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("pallets", sa.Integer(), server_default="0", nullable=False),
        sa.Column("storage_rate", sa.Numeric(18, 2), nullable=True),
        sa.Column("storage_cost", sa.Numeric(18, 2), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "warehouse_id", "snapshot_date", name="uq_ff_storage_daily_day"),
    )
    op.create_index("ix_ff_storage_daily_wh_date", "ff_storage_daily", ["warehouse_id", "snapshot_date"])
    op.create_index("ix_ff_storage_daily_project_id", "ff_storage_daily", ["project_id"])

    op.create_table(
        "ff_invoices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=True),
        sa.Column("counterparty_id", sa.Integer(), nullable=True),
        sa.Column("number", sa.String(length=100), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("kind", sa.String(length=20), server_default="MIXED", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="NEW", nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("our_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("payee_inn", sa.String(length=12), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("minio_path", sa.String(length=500), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("payment_request_id", sa.Integer(), nullable=True),
        sa.Column("matched_transaction_id", sa.Integer(), nullable=True),
        sa.Column("matched_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.ForeignKeyConstraint(["counterparty_id"], ["counterparty.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["payment_request_id"], ["payment_request.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["matched_transaction_id"], ["transactions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("amount > 0", name="ck_ff_invoices_amount_positive"),
    )
    op.create_index("ix_ff_invoices_project_id", "ff_invoices", ["project_id"])
    op.create_index("ix_ff_invoices_project_status", "ff_invoices", ["project_id", "status"])
    op.create_index("ix_ff_invoices_warehouse_id", "ff_invoices", ["warehouse_id"])
    op.create_index("ix_ff_invoices_counterparty_id", "ff_invoices", ["counterparty_id"])
    op.create_index("ix_ff_invoices_payment_request_id", "ff_invoices", ["payment_request_id"])
    op.create_index("ix_ff_invoices_matched_transaction_id", "ff_invoices", ["matched_transaction_id"])

    op.create_table(
        "ff_invoice_lines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("assembly_request_id", sa.Integer(), nullable=True),
        sa.Column("outbound_shipment_id", sa.Integer(), nullable=True),
        sa.Column("inbound_receipt_id", sa.Integer(), nullable=True),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("date_to", sa.Date(), nullable=True),
        sa.Column("qty_units", sa.Integer(), nullable=True),
        sa.Column("qty_boxes", sa.Integer(), nullable=True),
        sa.Column("qty_pallets", sa.Integer(), nullable=True),
        sa.Column("our_cost", sa.Numeric(18, 2), nullable=True),
        sa.Column("allocated_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("label", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["invoice_id"], ["ff_invoices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assembly_request_id"], ["assembly_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["outbound_shipment_id"], ["outbound_shipments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["inbound_receipt_id"], ["inbound_receipts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ff_invoice_lines_invoice_id", "ff_invoice_lines", ["invoice_id"])
    op.create_index("ix_ff_invoice_lines_project_id", "ff_invoice_lines", ["project_id"])
    op.create_index("ix_ff_invoice_lines_assembly_request_id", "ff_invoice_lines", ["assembly_request_id"])
    op.create_index("ix_ff_invoice_lines_outbound_shipment_id", "ff_invoice_lines", ["outbound_shipment_id"])
    op.create_index("ix_ff_invoice_lines_inbound_receipt_id", "ff_invoice_lines", ["inbound_receipt_id"])

    op.add_column("assembly_requests", sa.Column("ff_custom_cost", sa.Numeric(18, 2), nullable=True))
    op.add_column("assembly_requests", sa.Column("ff_custom_cost_comment", sa.String(length=300), nullable=True))


def downgrade() -> None:
    op.drop_column("assembly_requests", "ff_custom_cost_comment")
    op.drop_column("assembly_requests", "ff_custom_cost")

    op.drop_index("ix_ff_invoice_lines_inbound_receipt_id", table_name="ff_invoice_lines")
    op.drop_index("ix_ff_invoice_lines_outbound_shipment_id", table_name="ff_invoice_lines")
    op.drop_index("ix_ff_invoice_lines_assembly_request_id", table_name="ff_invoice_lines")
    op.drop_index("ix_ff_invoice_lines_project_id", table_name="ff_invoice_lines")
    op.drop_index("ix_ff_invoice_lines_invoice_id", table_name="ff_invoice_lines")
    op.drop_table("ff_invoice_lines")

    op.drop_index("ix_ff_invoices_matched_transaction_id", table_name="ff_invoices")
    op.drop_index("ix_ff_invoices_payment_request_id", table_name="ff_invoices")
    op.drop_index("ix_ff_invoices_counterparty_id", table_name="ff_invoices")
    op.drop_index("ix_ff_invoices_warehouse_id", table_name="ff_invoices")
    op.drop_index("ix_ff_invoices_project_status", table_name="ff_invoices")
    op.drop_index("ix_ff_invoices_project_id", table_name="ff_invoices")
    op.drop_table("ff_invoices")

    op.drop_index("ix_ff_storage_daily_project_id", table_name="ff_storage_daily")
    op.drop_index("ix_ff_storage_daily_wh_date", table_name="ff_storage_daily")
    op.drop_table("ff_storage_daily")

    op.drop_index("ix_warehouse_tariffs_wh_service", table_name="warehouse_tariffs")
    op.drop_index("ix_warehouse_tariffs_project_id", table_name="warehouse_tariffs")
    op.drop_table("warehouse_tariffs")
