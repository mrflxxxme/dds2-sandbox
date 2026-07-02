"""transactions.machine_order_no — ручная привязка оплаты к машине

Revision ID: tx01_machine_payment_link
Revises: pay08_payment_categories
Create Date: 2026-06-28

Привязка платежа из выписки к конкретной машине (CostOrder) — одна оплата →
одна машина, машина → много оплат (депозит + доплата). Не FK (order_no —
бизнес-ключ, машина может быть удалена/пересоздана), индекс для свёртки.
"""

from alembic import op
import sqlalchemy as sa

revision = "tx01_machine_payment_link"
down_revision = "pay08_payment_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("machine_order_no", sa.String(length=50), nullable=True))
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_transactions_machine_order_no "
            "ON transactions (project_id, machine_order_no) WHERE machine_order_no IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_transactions_machine_order_no")
    op.drop_column("transactions", "machine_order_no")
