"""warehouse: nomenclature_id indexes + cost_price Numeric(18,2)

Revision ID: wh01_nom_idx_cost
Revises: sc02_vehicle_enh
Create Date: 2026-04-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "wh01_nom_idx_cost"
down_revision: str | None = "sc02_vehicle_enh"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add nomenclature_id indexes on warehouse item tables
    op.create_index(
        "ix_inbound_receipt_items_nomenclature_id",
        "inbound_receipt_items",
        ["nomenclature_id"],
    )
    op.create_index(
        "ix_outbound_shipment_items_nomenclature_id",
        "outbound_shipment_items",
        ["nomenclature_id"],
    )
    op.create_index(
        "ix_stock_transfer_items_nomenclature_id",
        "stock_transfer_items",
        ["nomenclature_id"],
    )
    op.create_index(
        "ix_stock_movements_nomenclature_id",
        "stock_movements",
        ["nomenclature_id"],
    )
    op.create_index(
        "ix_warehouse_stock_nomenclature_id",
        "warehouse_stock",
        ["nomenclature_id"],
    )
    op.create_index(
        "ix_stock_adjustments_nomenclature_id",
        "stock_adjustments",
        ["nomenclature_id"],
    )

    # 2. Fix cost_price precision: Numeric(14,2) → Numeric(18,2)
    op.alter_column(
        "warehouse_stock",
        "cost_price",
        existing_type=sa.Numeric(14, 2),
        type_=sa.Numeric(18, 2),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Revert cost_price precision
    op.alter_column(
        "warehouse_stock",
        "cost_price",
        existing_type=sa.Numeric(18, 2),
        type_=sa.Numeric(14, 2),
        existing_nullable=True,
    )

    # Drop nomenclature_id indexes
    op.drop_index("ix_stock_adjustments_nomenclature_id", table_name="stock_adjustments")
    op.drop_index("ix_warehouse_stock_nomenclature_id", table_name="warehouse_stock")
    op.drop_index("ix_stock_movements_nomenclature_id", table_name="stock_movements")
    op.drop_index("ix_stock_transfer_items_nomenclature_id", table_name="stock_transfer_items")
    op.drop_index("ix_outbound_shipment_items_nomenclature_id", table_name="outbound_shipment_items")
    op.drop_index("ix_inbound_receipt_items_nomenclature_id", table_name="inbound_receipt_items")
