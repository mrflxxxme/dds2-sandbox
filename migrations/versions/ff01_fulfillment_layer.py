# ruff: noqa: RUF002, RUF003
"""fulfillment layer: ff stock snapshots, ff request mirrors, warehouse-scoped integration keys

Revision ID: ff01_fulfillment_layer
Revises: asm_source_draft_id
Create Date: 2026-06-11 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "ff01_fulfillment_layer"
down_revision: str | None = "asm_source_draft_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── integration_keys: привязка ключа к складу + конфиг провайдера ──────
    op.add_column("integration_keys", sa.Column("warehouse_id", sa.Integer(), nullable=True))
    op.add_column("integration_keys", sa.Column("config", JSONB(), nullable=True))
    op.create_index("ix_integration_keys_warehouse_id", "integration_keys", ["warehouse_id"])
    op.create_foreign_key(
        "fk_integration_keys_warehouse_id",
        "integration_keys",
        "warehouses",
        ["warehouse_id"],
        ["id"],
    )

    # ── fulfillment_stocks: снапшот остатков ФФ (зеркало, не WarehouseStock) ──
    op.create_table(
        "fulfillment_stocks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("barcode", sa.String(length=100), nullable=False),
        sa.Column("nomenclature_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=500), nullable=True),
        sa.Column("vendor_code", sa.String(length=200), nullable=True),
        sa.Column("qty_good", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("qty_reserve", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("qty_defect", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("qty_nominal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("external_product_id", sa.String(length=100), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.ForeignKeyConstraint(["nomenclature_id"], ["nomenclature.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "warehouse_id", "barcode", name="uq_ff_stock_wh_barcode"),
    )
    op.create_index("ix_fulfillment_stocks_project_wh", "fulfillment_stocks", ["project_id", "warehouse_id"])
    op.create_index("ix_fulfillment_stocks_nomenclature_id", "fulfillment_stocks", ["nomenclature_id"])

    # ── fulfillment_requests: зеркало заявок ФФ + связь с нашими документами ──
    op.create_table(
        "fulfillment_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("number", sa.String(length=100), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="other"),
        sa.Column("type_id", sa.Integer(), nullable=True),
        sa.Column("type_name", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("stage_code", sa.String(length=100), nullable=True),
        sa.Column("stage_title", sa.String(length=200), nullable=True),
        sa.Column("is_completed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("expired", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("external_created_at", sa.Date(), nullable=True),
        sa.Column("raw", JSONB(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.Column("assembly_request_id", sa.Integer(), nullable=True),
        sa.Column("inbound_receipt_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.ForeignKeyConstraint(["assembly_request_id"], ["assembly_requests.id"]),
        sa.ForeignKeyConstraint(["inbound_receipt_id"], ["inbound_receipts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "provider", "external_id", name="uq_ff_request_external"),
    )
    op.create_index("ix_fulfillment_requests_project_wh", "fulfillment_requests", ["project_id", "warehouse_id"])
    op.create_index("ix_fulfillment_requests_assembly_request_id", "fulfillment_requests", ["assembly_request_id"])
    op.create_index("ix_fulfillment_requests_inbound_receipt_id", "fulfillment_requests", ["inbound_receipt_id"])


def downgrade() -> None:
    op.drop_index("ix_fulfillment_requests_inbound_receipt_id", table_name="fulfillment_requests")
    op.drop_index("ix_fulfillment_requests_assembly_request_id", table_name="fulfillment_requests")
    op.drop_index("ix_fulfillment_requests_project_wh", table_name="fulfillment_requests")
    op.drop_table("fulfillment_requests")

    op.drop_index("ix_fulfillment_stocks_nomenclature_id", table_name="fulfillment_stocks")
    op.drop_index("ix_fulfillment_stocks_project_wh", table_name="fulfillment_stocks")
    op.drop_table("fulfillment_stocks")

    op.drop_constraint("fk_integration_keys_warehouse_id", "integration_keys", type_="foreignkey")
    op.drop_index("ix_integration_keys_warehouse_id", table_name="integration_keys")
    op.drop_column("integration_keys", "config")
    op.drop_column("integration_keys", "warehouse_id")
