"""Add warehouse module tables (10 tables).

Revision ID: m1_warehouse
Revises: l1_add_missing_project_id_indexes
Create Date: 2026-03-20
"""

import sqlalchemy as sa
from alembic import op

revision = "m1_warehouse"
down_revision = "e1a3b5c7d9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. warehouses
    op.create_table(
        "warehouses",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("warehouse_type", sa.String(20), nullable=False),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("assembly_days", sa.Integer, nullable=True),
        sa.Column("external_id", sa.String(100), nullable=True),
        sa.Column("sort_order", sa.Integer, server_default="0"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.Column("is_deleted", sa.Boolean, server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_warehouses_project_id", "warehouses", ["project_id"])

    # 2. inbound_receipts
    op.create_table(
        "inbound_receipts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("warehouse_id", sa.Integer, sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="DRAFT", nullable=False),
        sa.Column("planned_date", sa.Date, nullable=True),
        sa.Column("actual_date", sa.Date, nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("tags", sa.Text, nullable=True),
        sa.Column("cost_order_id", sa.Integer, sa.ForeignKey("cost_orders.id"), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.Column("is_deleted", sa.Boolean, server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_inbound_receipts_project_id", "inbound_receipts", ["project_id"])
    op.create_index("ix_inbound_receipts_warehouse_id", "inbound_receipts", ["warehouse_id"])

    # 3. inbound_receipt_items
    op.create_table(
        "inbound_receipt_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("receipt_id", sa.Integer, sa.ForeignKey("inbound_receipts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nomenclature_id", sa.Integer, sa.ForeignKey("nomenclature.id"), nullable=False),
        sa.Column("barcode", sa.String(50), nullable=False),
        sa.Column("expected_qty", sa.Integer, nullable=False),
        sa.Column("actual_qty", sa.Integer, server_default="0"),
    )
    op.create_index("ix_inbound_receipt_items_receipt_id", "inbound_receipt_items", ["receipt_id"])

    # 4. outbound_shipments
    op.create_table(
        "outbound_shipments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("warehouse_id", sa.Integer, sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="DRAFT", nullable=False),
        sa.Column("destination", sa.String(200), nullable=True),
        sa.Column("wb_supply_id", sa.String(100), nullable=True),
        sa.Column("shipped_date", sa.Date, nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.Column("is_deleted", sa.Boolean, server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_outbound_shipments_project_id", "outbound_shipments", ["project_id"])
    op.create_index("ix_outbound_shipments_warehouse_id", "outbound_shipments", ["warehouse_id"])

    # 5. outbound_shipment_items
    op.create_table(
        "outbound_shipment_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "shipment_id", sa.Integer, sa.ForeignKey("outbound_shipments.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("nomenclature_id", sa.Integer, sa.ForeignKey("nomenclature.id"), nullable=False),
        sa.Column("barcode", sa.String(50), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
    )
    op.create_index("ix_outbound_shipment_items_shipment_id", "outbound_shipment_items", ["shipment_id"])

    # 6. stock_transfers
    op.create_table(
        "stock_transfers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("from_warehouse_id", sa.Integer, sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("to_warehouse_id", sa.Integer, sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), server_default="DRAFT", nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.Column("is_deleted", sa.Boolean, server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_stock_transfers_project_id", "stock_transfers", ["project_id"])

    # 7. stock_transfer_items
    op.create_table(
        "stock_transfer_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("transfer_id", sa.Integer, sa.ForeignKey("stock_transfers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("nomenclature_id", sa.Integer, sa.ForeignKey("nomenclature.id"), nullable=False),
        sa.Column("barcode", sa.String(50), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
    )
    op.create_index("ix_stock_transfer_items_transfer_id", "stock_transfer_items", ["transfer_id"])

    # 8. stock_movements (журнал — аудит)
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("warehouse_id", sa.Integer, sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("nomenclature_id", sa.Integer, sa.ForeignKey("nomenclature.id"), nullable=False),
        sa.Column("barcode", sa.String(50), nullable=False),
        sa.Column("movement_type", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("reference_type", sa.String(30), nullable=False),
        sa.Column("reference_id", sa.Integer, nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_stock_movements_project_id", "stock_movements", ["project_id"])
    op.create_index("ix_stock_movements_warehouse_id", "stock_movements", ["warehouse_id"])
    op.create_index("ix_stock_movements_created_at", "stock_movements", ["created_at"])

    # 9. warehouse_stock (материализованный баланс)
    op.create_table(
        "warehouse_stock",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("warehouse_id", sa.Integer, sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("nomenclature_id", sa.Integer, sa.ForeignKey("nomenclature.id"), nullable=False),
        sa.Column("barcode", sa.String(50), nullable=False),
        sa.Column("quantity", sa.Integer, server_default="0"),
        sa.Column("in_transit", sa.Integer, server_default="0"),
        sa.Column("cost_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.UniqueConstraint("project_id", "warehouse_id", "nomenclature_id", name="uq_warehouse_stock"),
    )
    op.create_index("ix_warehouse_stock_project_id", "warehouse_stock", ["project_id"])
    op.create_index("ix_warehouse_stock_warehouse_id", "warehouse_stock", ["warehouse_id"])

    # 10. stock_adjustments (корректировки)
    op.create_table(
        "stock_adjustments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("warehouse_id", sa.Integer, sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("nomenclature_id", sa.Integer, sa.ForeignKey("nomenclature.id"), nullable=False),
        sa.Column("barcode", sa.String(50), nullable=False),
        sa.Column("delta", sa.Integer, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_stock_adjustments_project_id", "stock_adjustments", ["project_id"])


def downgrade() -> None:
    op.drop_table("stock_adjustments")
    op.drop_table("warehouse_stock")
    op.drop_table("stock_movements")
    op.drop_table("stock_transfer_items")
    op.drop_table("stock_transfers")
    op.drop_table("outbound_shipment_items")
    op.drop_table("outbound_shipments")
    op.drop_table("inbound_receipt_items")
    op.drop_table("inbound_receipts")
    op.drop_table("warehouses")
