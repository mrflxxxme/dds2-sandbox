"""warehouse: add defect_quantity, defect_in_transit, defect_delta, is_defect

Support for defective goods tracking per warehouse:
- WarehouseStock: parallel defect_quantity and defect_in_transit counters
- StockMovement: defect_delta audit field
- StockTransfer: is_defect flag + defect_reason for defect transfers

Revision ID: wh02_defect_stock
Revises: 7e4a6fb87649
Create Date: 2026-04-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "wh02_defect_stock"
down_revision: str | None = "7e4a6fb87649"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # WarehouseStock — parallel defect counters
    op.add_column("warehouse_stock", sa.Column("defect_quantity", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("warehouse_stock", sa.Column("defect_in_transit", sa.Integer(), nullable=False, server_default="0"))

    # StockMovement — defect audit
    op.add_column("stock_movements", sa.Column("defect_delta", sa.Integer(), nullable=False, server_default="0"))

    # StockTransfer — defect transfer flag
    op.add_column("stock_transfers", sa.Column("is_defect", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("stock_transfers", sa.Column("defect_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("stock_transfers", "defect_reason")
    op.drop_column("stock_transfers", "is_defect")
    op.drop_column("stock_movements", "defect_delta")
    op.drop_column("warehouse_stock", "defect_in_transit")
    op.drop_column("warehouse_stock", "defect_quantity")
