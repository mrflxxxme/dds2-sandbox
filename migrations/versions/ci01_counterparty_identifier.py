"""counterparty_identifier + contract/invoice match indexes

Revision ID: ci01_counterparty_identifier
Revises: pay07_payment_request_general
Create Date: 2026-06-28

Несколько контрактов/счетов/ИНН на одного контрагента (поставщик / торговый дом /
логист) — ключи матчинга строк выписки → counterparty_id (одного поля
contract_number на Counterparty не хватает). Плюс индексы по
transactions.contract_number / invoice_id для матчера sync_supplier_payments.
"""

from alembic import op
import sqlalchemy as sa

revision = "ci01_counterparty_identifier"
down_revision = "pay07_payment_request_general"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "counterparty_identifier",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("counterparty_id", sa.Integer(), sa.ForeignKey("counterparty.id"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("value", sa.String(length=100), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_counterparty_identifier_project_id", "counterparty_identifier", ["project_id"])

    # Partial unique + FK indexes + match indexes — CONCURRENTLY (no table lock).
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_counterparty_identifier_value "
            "ON counterparty_identifier (project_id, kind, value) WHERE is_deleted = false"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_counterparty_identifier_cp "
            "ON counterparty_identifier (counterparty_id) WHERE is_deleted = false"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transactions_contract_number "
            "ON transactions (project_id, contract_number) WHERE contract_number IS NOT NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transactions_invoice_id "
            "ON transactions (project_id, invoice_id) WHERE invoice_id IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transactions_invoice_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transactions_contract_number")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_counterparty_identifier_cp")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_counterparty_identifier_value")
    op.drop_index("ix_counterparty_identifier_project_id", table_name="counterparty_identifier")
    op.drop_table("counterparty_identifier")
