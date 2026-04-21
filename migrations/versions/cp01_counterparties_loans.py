"""counterparties + loans + faktura parser foundation

- Counterparty + CounterpartyDocument
- Loan + LoanPayment
- Transaction: +counterparty_id, +contract_number, +unk_number, +loan_id, +loan_payment_id, +loan_payment_type
- Supplier: +inn, +contract_number, +counterparty_id
- Warehouse: +counterparty_id
- CounterpartyCategory: +counterparty_id
- CustomsTopup: +counterparty_id
- EventType2 enum: +DEPOSIT_PLACE, +DEPOSIT_RETURN, +DEPOSIT_INTEREST

Revision ID: cp01_counterparties_loans
Revises: l3_partial_idx_sd
Create Date: 2026-04-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "cp01_counterparties_loans"
down_revision: str | None = "l3_partial_idx_sd"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── 1. ALTER TYPE event_type2 ADD VALUE (must be outside transaction) ──────
    with op.get_context().autocommit_block():
        # Note: PostgreSQL stores the enum as "eventtype2" (no underscore) — that's how
        # it was created in the initial schema (SAEnum with name derived from class name).
        op.execute("ALTER TYPE eventtype2 ADD VALUE IF NOT EXISTS 'DEPOSIT_PLACE'")
        op.execute("ALTER TYPE eventtype2 ADD VALUE IF NOT EXISTS 'DEPOSIT_RETURN'")
        op.execute("ALTER TYPE eventtype2 ADD VALUE IF NOT EXISTS 'DEPOSIT_INTEREST'")

    # ── 2. CREATE TABLE counterparty ──────────────────────────────────────────
    op.create_table(
        "counterparty",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("inn", sa.String(12), nullable=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("primary_type", sa.String(20), nullable=False, server_default="OTHER"),
        sa.Column(
            "secondary_types",
            postgresql.ARRAY(sa.String(20)),
            nullable=True,
            server_default="{}",
        ),
        sa.Column("kpp", sa.String(9), nullable=True),
        sa.Column("contract_number", sa.String(100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("contacts", postgresql.JSONB(), nullable=True),
        sa.Column("created_by_import", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_counterparty_project_id", "counterparty", ["project_id"])
    op.create_index("ix_counterparty_type", "counterparty", ["project_id", "primary_type"])

    # ── 3. CREATE TABLE loan ──────────────────────────────────────────────────
    op.create_table(
        "loan",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("counterparty_id", sa.Integer(), sa.ForeignKey("counterparty.id"), nullable=False),
        sa.Column("direction", sa.String(20), nullable=False, server_default="INCOMING"),
        sa.Column("principal", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
        sa.Column("rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("contract_number", sa.String(100), nullable=False),
        sa.Column("contract_date", sa.Date(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("maturity_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_loan_project_id", "loan", ["project_id"])
    op.create_index("ix_loan_counterparty", "loan", ["counterparty_id"])

    # ── 4. CREATE TABLE loan_payment ──────────────────────────────────────────
    op.create_table(
        "loan_payment",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("loan_id", sa.Integer(), sa.ForeignKey("loan.id"), nullable=False),
        sa.Column("transaction_id", sa.Integer(), sa.ForeignKey("transactions.id"), nullable=True),
        sa.Column("payment_type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("paid_at", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_loan_payment_loan_date", "loan_payment", ["loan_id", "paid_at"])

    # ── 5. CREATE TABLE counterparty_document ─────────────────────────────────
    op.create_table(
        "counterparty_document",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("counterparty_id", sa.Integer(), sa.ForeignKey("counterparty.id"), nullable=False),
        sa.Column("minio_path", sa.String(500), nullable=False),
        sa.Column("doc_type", sa.String(20), nullable=False, server_default="OTHER"),
        sa.Column("original_filename", sa.String(500), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column(
            "uploaded_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_counterparty_document_project_id", "counterparty_document", ["project_id"])

    # ── 6. ALTER existing tables: add nullable columns ────────────────────────

    # transactions
    op.add_column(
        "transactions",
        sa.Column("counterparty_id", sa.Integer(), sa.ForeignKey("counterparty.id"), nullable=True),
    )
    op.add_column("transactions", sa.Column("contract_number", sa.String(100), nullable=True))
    op.add_column("transactions", sa.Column("unk_number", sa.String(100), nullable=True))
    op.add_column(
        "transactions",
        sa.Column("loan_id", sa.Integer(), sa.ForeignKey("loan.id"), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column("loan_payment_id", sa.Integer(), sa.ForeignKey("loan_payment.id"), nullable=True),
    )
    op.add_column("transactions", sa.Column("loan_payment_type", sa.String(20), nullable=True))

    # suppliers
    op.add_column("suppliers", sa.Column("inn", sa.String(12), nullable=True))
    op.add_column("suppliers", sa.Column("contract_number", sa.String(100), nullable=True))
    op.add_column(
        "suppliers",
        sa.Column("counterparty_id", sa.Integer(), sa.ForeignKey("counterparty.id"), nullable=True),
    )

    # warehouses
    op.add_column(
        "warehouses",
        sa.Column("counterparty_id", sa.Integer(), sa.ForeignKey("counterparty.id"), nullable=True),
    )

    # counterparty_categories
    op.add_column(
        "counterparty_categories",
        sa.Column("counterparty_id", sa.Integer(), sa.ForeignKey("counterparty.id"), nullable=True),
    )

    # customs_topup
    op.add_column(
        "customs_topup",
        sa.Column("counterparty_id", sa.Integer(), sa.ForeignKey("counterparty.id"), nullable=True),
    )

    # ── 7. CREATE partial/unique indexes via CONCURRENTLY (outside transaction) ──
    with op.get_context().autocommit_block():
        # counterparty
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_counterparty_project_inn "
            "ON counterparty (project_id, inn) WHERE inn IS NOT NULL AND is_deleted = false"
        )
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_counterparty_project_contract "
            "ON counterparty (project_id, contract_number) "
            "WHERE contract_number IS NOT NULL AND is_deleted = false"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_counterparty_active "
            "ON counterparty (project_id, primary_type) WHERE is_deleted = false"
        )

        # loan
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_loan_project_status "
            "ON loan (project_id, status) WHERE is_deleted = false"
        )

        # loan_payment
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_loan_payment_transaction "
            "ON loan_payment (transaction_id) WHERE transaction_id IS NOT NULL"
        )

        # counterparty_document
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_counterparty_document_cp "
            "ON counterparty_document (counterparty_id) WHERE is_deleted = false"
        )

        # transactions
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transactions_counterparty_id "
            "ON transactions (counterparty_id) WHERE counterparty_id IS NOT NULL"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transactions_loan_id "
            "ON transactions (loan_id) WHERE loan_id IS NOT NULL"
        )

        # suppliers
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_suppliers_counterparty_id " "ON suppliers (counterparty_id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_suppliers_inn "
            "ON suppliers (project_id, inn) WHERE inn IS NOT NULL"
        )
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_suppliers_contract "
            "ON suppliers (project_id, contract_number) "
            "WHERE contract_number IS NOT NULL AND is_deleted = false"
        )

        # warehouses
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_warehouses_counterparty_id " "ON warehouses (counterparty_id)"
        )

        # counterparty_categories
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_counterparty_categories_counterparty_id "
            "ON counterparty_categories (counterparty_id)"
        )

        # customs_topup
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_customs_topup_counterparty_id "
            "ON customs_topup (counterparty_id)"
        )


def downgrade() -> None:
    # ── 1. DROP CONCURRENTLY indexes first (outside transaction) ─────────────
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_customs_topup_counterparty_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_counterparty_categories_counterparty_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_warehouses_counterparty_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_suppliers_contract")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_suppliers_inn")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_suppliers_counterparty_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transactions_loan_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transactions_counterparty_id")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_counterparty_document_cp")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_loan_payment_transaction")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_loan_project_status")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_counterparty_active")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_counterparty_project_contract")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS uq_counterparty_project_inn")

    # ── 2. DROP columns from existing tables ─────────────────────────────────
    op.drop_column("customs_topup", "counterparty_id")
    op.drop_column("counterparty_categories", "counterparty_id")
    op.drop_column("warehouses", "counterparty_id")
    op.drop_column("suppliers", "counterparty_id")
    op.drop_column("suppliers", "contract_number")
    op.drop_column("suppliers", "inn")
    op.drop_column("transactions", "loan_payment_type")
    op.drop_column("transactions", "loan_payment_id")
    op.drop_column("transactions", "loan_id")
    op.drop_column("transactions", "unk_number")
    op.drop_column("transactions", "contract_number")
    op.drop_column("transactions", "counterparty_id")

    # ── 3. DROP tables in reverse FK order ───────────────────────────────────
    op.drop_index("ix_counterparty_document_project_id", table_name="counterparty_document")
    op.drop_table("counterparty_document")

    op.drop_index("ix_loan_payment_loan_date", table_name="loan_payment")
    op.drop_table("loan_payment")

    op.drop_index("ix_loan_counterparty", table_name="loan")
    op.drop_index("ix_loan_project_id", table_name="loan")
    op.drop_table("loan")

    op.drop_index("ix_counterparty_type", table_name="counterparty")
    op.drop_index("ix_counterparty_project_id", table_name="counterparty")
    op.drop_table("counterparty")

    # NOTE: We do NOT remove DEPOSIT_PLACE / DEPOSIT_RETURN / DEPOSIT_INTEREST
    # from EventType2 enum — PostgreSQL does not support ALTER TYPE DROP VALUE.
    # The values are harmless and will be ignored by previous code versions.
