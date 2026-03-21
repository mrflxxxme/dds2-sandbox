"""Create assembly_requests and assembly_request_items tables.

Revision ID: m4_assembly
Revises: m3_fbo_cargo
Create Date: 2026-03-21
"""

import sqlalchemy as sa
from alembic import op

revision = "m4_assembly"
down_revision = "m3_fbo_cargo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assembly_requests",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("warehouse_id", sa.Integer, sa.ForeignKey("warehouses.id"), nullable=False),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("wb_fbo_supply_id", sa.Integer, sa.ForeignKey("wb_fbo_supplies.id"), nullable=False),
        sa.Column("outbound_shipment_id", sa.Integer, sa.ForeignKey("outbound_shipments.id"), nullable=True),
        sa.Column("estimated_ready_date", sa.Date, nullable=True),
        sa.Column("actual_ready_date", sa.Date, nullable=True),
        sa.Column("pallets_count", sa.Integer, nullable=False),
        sa.Column("pallet_weight_kg", sa.Numeric(10, 2), nullable=False),
        sa.Column("vehicle_info", sa.String(300), nullable=True),
        sa.Column("vehicle_assigned_at", sa.DateTime, nullable=True),
        sa.Column("shipped_at", sa.DateTime, nullable=True),
        sa.Column("comment", sa.Text, nullable=True),
        # TimestampMixin
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        # SoftDeleteMixin
        sa.Column("is_deleted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )

    op.create_index("ix_assembly_requests_project_id", "assembly_requests", ["project_id"])
    op.create_index("ix_assembly_requests_warehouse_id", "assembly_requests", ["warehouse_id"])
    op.create_index("ix_assembly_requests_status", "assembly_requests", ["status"])

    # Partial unique: one active (non-deleted, non-cancelled) request per FBO supply per project
    op.create_index(
        "ix_assembly_requests_fbo_unique",
        "assembly_requests",
        ["project_id", "wb_fbo_supply_id"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false AND status != 'CANCELLED'"),
    )

    op.create_table(
        "assembly_request_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "assembly_request_id",
            sa.Integer,
            sa.ForeignKey("assembly_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("nomenclature_id", sa.Integer, sa.ForeignKey("nomenclature.id"), nullable=False),
        sa.Column("barcode", sa.String(50), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
    )

    op.create_index("ix_assembly_request_items_request_id", "assembly_request_items", ["assembly_request_id"])


def downgrade() -> None:
    op.drop_table("assembly_request_items")
    op.drop_table("assembly_requests")
