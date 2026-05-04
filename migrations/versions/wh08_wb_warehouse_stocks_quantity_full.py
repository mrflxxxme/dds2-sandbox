"""wh08: add quantity_full to wb_warehouse_stocks

Column already exists on production (added out-of-band). This migration
brings the schema graph in sync so fresh DBs (CI, local recreate) match
the model in backend/models/integrations.py:WbWarehouseStock.

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


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("wb_warehouse_stocks")}
    if "quantity_full" in cols:
        op.drop_column("wb_warehouse_stocks", "quantity_full")
