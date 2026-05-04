# ruff: noqa: RUF001, RUF002
"""wh08: sync wb_warehouse_stocks schema with production

Production был патчен out-of-band: добавлены column quantity_full,
UNIQUE CONSTRAINT uq_wh_stock_nm_wh, INDEX ix_wh_stock_project; старый
UNIQUE INDEX ix_wb_wh_stocks_project из j1-миграции удалён. Эта миграция
приводит Alembic-граф в соответствие с проддом, чтобы CI/свежие БД имели
ту же схему, что использует код (sync_warehouse_stocks ссылается на
constraint="uq_wh_stock_nm_wh" в ON CONFLICT).

Все операции идемпотентны (через inspector), поэтому на проде no-op,
на чистой БД создают нужные объекты.

Revision ID: wh08_wb_stocks_qty_full
Revises: 8dd2f6d8f782
Create Date: 2026-05-04 11:20:00
"""

import sqlalchemy as sa
from alembic import op

revision = "wh08_wb_stocks_qty_full"
down_revision = "8dd2f6d8f782"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    cols = {c["name"] for c in insp.get_columns("wb_warehouse_stocks")}
    if "quantity_full" not in cols:
        op.add_column(
            "wb_warehouse_stocks",
            sa.Column("quantity_full", sa.Integer(), nullable=False, server_default="0"),
        )
        op.alter_column("wb_warehouse_stocks", "quantity_full", server_default=None)

    indexes = {ix["name"] for ix in insp.get_indexes("wb_warehouse_stocks")}
    constraints = {c["name"] for c in insp.get_unique_constraints("wb_warehouse_stocks")}

    # Старый unique index от j1 миграции — заменяем на named UNIQUE CONSTRAINT.
    if "ix_wb_wh_stocks_project" in indexes:
        op.drop_index("ix_wb_wh_stocks_project", table_name="wb_warehouse_stocks")

    if "uq_wh_stock_nm_wh" not in constraints:
        op.create_unique_constraint(
            "uq_wh_stock_nm_wh",
            "wb_warehouse_stocks",
            ["project_id", "nm_id", "warehouse_name"],
        )

    if "ix_wh_stock_project" not in indexes:
        op.create_index(
            "ix_wh_stock_project",
            "wb_warehouse_stocks",
            ["project_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    indexes = {ix["name"] for ix in insp.get_indexes("wb_warehouse_stocks")}
    constraints = {c["name"] for c in insp.get_unique_constraints("wb_warehouse_stocks")}
    cols = {c["name"] for c in insp.get_columns("wb_warehouse_stocks")}

    if "ix_wh_stock_project" in indexes:
        op.drop_index("ix_wh_stock_project", table_name="wb_warehouse_stocks")

    if "uq_wh_stock_nm_wh" in constraints:
        op.drop_constraint("uq_wh_stock_nm_wh", "wb_warehouse_stocks", type_="unique")

    if "ix_wb_wh_stocks_project" not in indexes:
        op.create_index(
            "ix_wb_wh_stocks_project",
            "wb_warehouse_stocks",
            ["project_id", "nm_id", "warehouse_name"],
            unique=True,
        )

    if "quantity_full" in cols:
        op.drop_column("wb_warehouse_stocks", "quantity_full")
