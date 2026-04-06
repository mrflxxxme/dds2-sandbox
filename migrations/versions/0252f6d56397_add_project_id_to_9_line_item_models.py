"""add project_id to 9 line-item models

Revision ID: 0252f6d56397
Revises: auth01_soft_del
Create Date: 2026-04-05 19:03:55.133351

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0252f6d56397"
down_revision: str | None = "auth01_soft_del"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Table -> (parent_table, fk_column_in_child, pk_column_in_parent)
TABLES = [
    # 1. CategoryChangeLog -> Transaction (via txn_id string match)
    {
        "table": "category_change_log",
        "parent_table": "transactions",
        "fk_col": "txn_id",
        "parent_pk": "txn_id",
        "parent_project_col": "project_id",
    },
    # 2. InboundReceiptItem -> InboundReceipt (via receipt_id)
    {
        "table": "inbound_receipt_items",
        "parent_table": "inbound_receipts",
        "fk_col": "receipt_id",
        "parent_pk": "id",
        "parent_project_col": "project_id",
    },
    # 3. OutboundShipmentItem -> OutboundShipment (via shipment_id)
    {
        "table": "outbound_shipment_items",
        "parent_table": "outbound_shipments",
        "fk_col": "shipment_id",
        "parent_pk": "id",
        "parent_project_col": "project_id",
    },
    # 4. StockTransferItem -> StockTransfer (via transfer_id)
    {
        "table": "stock_transfer_items",
        "parent_table": "stock_transfers",
        "fk_col": "transfer_id",
        "parent_pk": "id",
        "parent_project_col": "project_id",
    },
    # 5. AssemblyRequestItem -> AssemblyRequest (via assembly_request_id)
    {
        "table": "assembly_request_items",
        "parent_table": "assembly_requests",
        "fk_col": "assembly_request_id",
        "parent_pk": "id",
        "parent_project_col": "project_id",
    },
    # 6. AssemblyStatusHistory -> AssemblyRequest (via assembly_request_id)
    {
        "table": "assembly_status_history",
        "parent_table": "assembly_requests",
        "fk_col": "assembly_request_id",
        "parent_pk": "id",
        "parent_project_col": "project_id",
    },
    # 7. WbFboSupplyItem -> WbFboSupply (via supply_id)
    {
        "table": "wb_fbo_supply_items",
        "parent_table": "wb_fbo_supplies",
        "fk_col": "supply_id",
        "parent_pk": "id",
        "parent_project_col": "project_id",
    },
    # 8. FactoryOrderItem -> FactoryOrder (via factory_order_id)
    {
        "table": "factory_order_items",
        "parent_table": "factory_orders",
        "fk_col": "factory_order_id",
        "parent_pk": "id",
        "parent_project_col": "project_id",
    },
    # 9. CostOrderItem -> CostOrder (via order_no string match)
    {
        "table": "cost_order_items",
        "parent_table": "cost_orders",
        "fk_col": "order_no",
        "parent_pk": "order_no",
        "parent_project_col": "project_id",
    },
]

# Index names for each table
INDEX_NAMES = {
    "category_change_log": "ix_category_change_log_project_id",
    "inbound_receipt_items": "ix_inbound_receipt_items_project_id",
    "outbound_shipment_items": "ix_outbound_shipment_items_project_id",
    "stock_transfer_items": "ix_stock_transfer_items_project_id",
    "assembly_request_items": "ix_assembly_request_items_project_id",
    "assembly_status_history": "ix_assembly_status_history_project_id",
    "wb_fbo_supply_items": "ix_wb_fbo_supply_items_project_id",
    "factory_order_items": "ix_factory_order_items_project_id",
    "cost_order_items": "ix_cost_item_project_id",
}

# FK constraint names
FK_NAMES = {
    "category_change_log": "fk_category_change_log_project_id",
    "inbound_receipt_items": "fk_inbound_receipt_items_project_id",
    "outbound_shipment_items": "fk_outbound_shipment_items_project_id",
    "stock_transfer_items": "fk_stock_transfer_items_project_id",
    "assembly_request_items": "fk_assembly_request_items_project_id",
    "assembly_status_history": "fk_assembly_status_history_project_id",
    "wb_fbo_supply_items": "fk_wb_fbo_supply_items_project_id",
    "factory_order_items": "fk_factory_order_items_project_id",
    "cost_order_items": "fk_cost_order_items_project_id",
}


def upgrade() -> None:
    for t in TABLES:
        table = t["table"]
        parent = t["parent_table"]
        fk_col = t["fk_col"]
        parent_pk = t["parent_pk"]

        # 1. Add nullable column
        op.add_column(table, sa.Column("project_id", sa.Integer(), nullable=True))

        # 2. Backfill from parent table
        op.execute(
            sa.text(
                f"UPDATE {table} c "  # noqa: S608
                f"SET project_id = p.project_id "
                f"FROM {parent} p "
                f"WHERE c.{fk_col} = p.{parent_pk}"
            )
        )

        # 3. Delete orphans (rows without a parent)
        op.execute(sa.text(f"DELETE FROM {table} WHERE project_id IS NULL"))  # noqa: S608

        # 4. Make NOT NULL
        op.alter_column(table, "project_id", nullable=False)

        # 5. Add FK constraint
        op.create_foreign_key(
            FK_NAMES[table],
            table,
            "projects",
            ["project_id"],
            ["id"],
        )

        # 6. Add index
        op.create_index(INDEX_NAMES[table], table, ["project_id"])


def downgrade() -> None:
    for t in reversed(TABLES):
        table = t["table"]

        # Drop index
        op.drop_index(INDEX_NAMES[table], table_name=table)

        # Drop FK
        op.drop_constraint(FK_NAMES[table], table, type_="foreignkey")

        # Drop column
        op.drop_column(table, "project_id")
