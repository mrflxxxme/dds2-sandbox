"""loan: entity_type (физ/ИП) + lender_bank + parent_loan_id (цепочка продлений)

Revision ID: loan02_loan_entity_extend
Revises: cbr01_cbr_bic_directory
Create Date: 2026-06-30

Расширение займов под «сервис учёта займов»:
  - entity_type  — на физлицо или ИП был оформлен займ («на ип или физ»);
  - lender_bank  — банк лендера для выплаты процентов (справочно);
  - parent_loan_id — продление: новый займ ссылается на закрытый предшественник
    (цепочка 06 → 06-01 → 06-02 …), позволяет показать историю смены ставки.

Все колонки nullable (без server_default): старые займы не затрагиваются.
FK parent_loan_id → loan.id (self-ref) + частичный индекс CONCURRENTLY.
"""

from alembic import op
import sqlalchemy as sa

revision = "loan02_loan_entity_extend"
down_revision = "cbr01_cbr_bic_directory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("loan", sa.Column("entity_type", sa.String(length=12), nullable=True))
    op.add_column("loan", sa.Column("lender_bank", sa.String(length=50), nullable=True))
    op.add_column("loan", sa.Column("parent_loan_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_loan_parent_loan",
        "loan",
        "loan",
        ["parent_loan_id"],
        ["id"],
    )
    # Индекс под FK — CONCURRENTLY (вне транзакции): таблица растёт после импорта реестра.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_loan_parent "
            "ON loan (parent_loan_id) WHERE parent_loan_id IS NOT NULL"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_loan_parent")
    op.drop_constraint("fk_loan_parent_loan", "loan", type_="foreignkey")
    op.drop_column("loan", "parent_loan_id")
    op.drop_column("loan", "lender_bank")
    op.drop_column("loan", "entity_type")
