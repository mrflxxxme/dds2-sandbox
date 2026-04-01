"""add supply chain: factory_orders, factory_order_items, extend cost_orders

Revision ID: sc01_supply_chain
Revises: 29bfdee909d9
Create Date: 2026-03-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "sc01_supply_chain"
down_revision: str | None = "29bfdee909d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# VehicleStatus enum values
vehicle_status_enum = sa.Enum(
    "FORMING",
    "IN_TRANSIT",
    "CUSTOMS",
    "DELIVERED",
    name="vehiclestatus",
)


def upgrade() -> None:
    # Create VehicleStatus enum type
    vehicle_status_enum.create(op.get_bind(), checkfirst=True)

    # 1. factory_orders table
    op.create_table(
        "factory_orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("order_number", sa.String(50), nullable=False),
        sa.Column("factory_name", sa.String(200), nullable=True),
        sa.Column("order_date", sa.Date(), nullable=True),
        sa.Column("expected_ready_date", sa.Date(), nullable=True),
        sa.Column("total_cny", sa.Numeric(14, 2), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "order_number", name="uq_factory_order_project_number"),
    )
    op.create_index("ix_factory_orders_project_id", "factory_orders", ["project_id"])

    # 2. factory_order_items table
    op.create_table(
        "factory_order_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("factory_order_id", sa.Integer(), nullable=False),
        sa.Column("barcode", sa.String(50), nullable=False),
        sa.Column("subject", sa.String(100), nullable=True),
        sa.Column("article_seller", sa.String(100), nullable=True),
        sa.Column("qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("assigned_qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("price_cny", sa.Numeric(12, 4), server_default="0", nullable=False),
        sa.Column("box_size", sa.String(50), nullable=True),
        sa.Column("pcs_per_box", sa.Integer(), nullable=True),
        sa.Column("weight_kg", sa.Numeric(10, 4), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["factory_order_id"], ["factory_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_factory_order_items_order_id", "factory_order_items", ["factory_order_id"])

    # 3. Extend cost_orders with supply chain fields
    op.add_column(
        "cost_orders",
        sa.Column("status", vehicle_status_enum, server_default="FORMING", nullable=True),
    )
    op.add_column(
        "cost_orders",
        sa.Column("target_warehouse_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "cost_orders",
        sa.Column("inbound_receipt_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_cost_orders_target_warehouse",
        "cost_orders",
        "warehouses",
        ["target_warehouse_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_cost_orders_inbound_receipt",
        "cost_orders",
        "inbound_receipts",
        ["inbound_receipt_id"],
        ["id"],
    )

    # 4. Extend cost_order_items with factory_order_item FK
    op.add_column(
        "cost_order_items",
        sa.Column("factory_order_item_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_cost_order_items_factory_item",
        "cost_order_items",
        "factory_order_items",
        ["factory_order_item_id"],
        ["id"],
    )


def downgrade() -> None:
    # 4. Remove factory_order_item_id from cost_order_items
    op.drop_constraint("fk_cost_order_items_factory_item", "cost_order_items", type_="foreignkey")
    op.drop_column("cost_order_items", "factory_order_item_id")

    # 3. Remove supply chain fields from cost_orders
    op.drop_constraint("fk_cost_orders_inbound_receipt", "cost_orders", type_="foreignkey")
    op.drop_constraint("fk_cost_orders_target_warehouse", "cost_orders", type_="foreignkey")
    op.drop_column("cost_orders", "inbound_receipt_id")
    op.drop_column("cost_orders", "target_warehouse_id")
    op.drop_column("cost_orders", "status")

    # 2. Drop factory_order_items
    op.drop_index("ix_factory_order_items_order_id", "factory_order_items")
    op.drop_table("factory_order_items")

    # 1. Drop factory_orders
    op.drop_index("ix_factory_orders_project_id", "factory_orders")
    op.drop_table("factory_orders")

    # Drop enum
    vehicle_status_enum.drop(op.get_bind(), checkfirst=True)
