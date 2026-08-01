# ruff: noqa: RUF002, RUF003
"""Газелька возит и переезды: ссылка заказа на stock_transfers

До сих пор `gazelka_orders` умел ссылаться только на заявку на сборку —
агрегатор считался «возит только сборки на WB». Канон юзера 01.08.2026:
переезд между нашими складами передаётся в Газельку по той же схеме.

Ровно ОДНА из двух ссылок не NULL (CHECK). Обе NULL — законно: так выглядит
audit-запись попытки, чей документ потом удалили (`ondelete=SET NULL`).

Revision ID: trv06_gazelka_transfer
Revises: trv05_transfer_ff_cost
Create Date: 2026-08-01 15:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "trv06_gazelka_transfer"
down_revision: str | None = "trv05_transfer_ff_cost"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gazelka_orders", sa.Column("stock_transfer_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "gazelka_orders_stock_transfer_id_fkey",
        "gazelka_orders",
        "stock_transfers",
        ["stock_transfer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_gazelka_orders_stock_transfer_id", "gazelka_orders", ["stock_transfer_id"]
    )
    op.create_check_constraint(
        "ck_gazelka_orders_single_link",
        "gazelka_orders",
        "assembly_request_id IS NULL OR stock_transfer_id IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_gazelka_orders_single_link", "gazelka_orders", type_="check")
    op.drop_index("ix_gazelka_orders_stock_transfer_id", table_name="gazelka_orders")
    op.drop_constraint(
        "gazelka_orders_stock_transfer_id_fkey", "gazelka_orders", type_="foreignkey"
    )
    op.drop_column("gazelka_orders", "stock_transfer_id")
