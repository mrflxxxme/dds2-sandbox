"""Add missing project_id indexes to 7 tables.

BUG-4: transactions, accounts, brand_notes, counterparty_categories,
import_log, project_invites, telegram_chat_bindings — missing indexes
verified on production.

Uses IF NOT EXISTS because some model definitions include indexes
that may or may not have been applied via prior migrations.

Revision ID: bugfix01_add_project_id_indexes
Revises: sc10_mix_group_fields
Create Date: 2026-04-09
"""

from alembic import op

revision: str = "bugfix01_add_project_id_indexes"
down_revision: str | None = "sc10_mix_group_fields"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Use raw SQL with IF NOT EXISTS to handle cases where index may already exist
    indexes = [
        ("ix_txn_project_id", "transactions", "project_id"),
        ("ix_accounts_project_id", "accounts", "project_id"),
        ("ix_brand_notes_project_id", "brand_notes", "project_id"),
        ("ix_cp_cat_project_id", "counterparty_categories", "project_id"),
        ("ix_import_log_project_id", "import_log", "project_id"),
        ("ix_project_invites_project_id", "project_invites", "project_id"),
        ("ix_telegram_chat_bindings_project_id", "telegram_chat_bindings", "project_id"),
    ]
    for idx_name, table, column in indexes:
        op.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({column})")


def downgrade() -> None:
    op.drop_index("ix_telegram_chat_bindings_project_id", table_name="telegram_chat_bindings")
    op.drop_index("ix_project_invites_project_id", table_name="project_invites")
    op.drop_index("ix_import_log_project_id", table_name="import_log")
    op.drop_index("ix_cp_cat_project_id", table_name="counterparty_categories")
    op.drop_index("ix_brand_notes_project_id", table_name="brand_notes")
    op.drop_index("ix_accounts_project_id", table_name="accounts")
    op.drop_index("ix_txn_project_id", table_name="transactions")
