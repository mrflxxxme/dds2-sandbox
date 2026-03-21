"""Add WB FBO supplies tables (2 tables).

Revision ID: m2_wb_fbo
Revises: m1_warehouse
Create Date: 2026-03-20
"""

import sqlalchemy as sa
from alembic import op

revision = "m2_wb_fbo"
down_revision = "m1_warehouse"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── wb_fbo_supplies ──────────────────────────────────────────────────
    op.create_table(
        "wb_fbo_supplies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("wb_supply_id", sa.String(50), nullable=False),
        sa.Column("wb_status", sa.String(30), nullable=False, server_default="ACTIVE"),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("created_at_wb", sa.DateTime(), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=True),
        sa.Column("actual_date", sa.Date(), nullable=True),
        sa.Column("warehouse_name", sa.String(200), nullable=True),
        sa.Column("total_qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("accepted_qty", sa.Integer(), server_default="0", nullable=False),
        sa.Column("outbound_shipment_id", sa.Integer(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["outbound_shipment_id"], ["outbound_shipments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "wb_supply_id", name="uq_wb_fbo_supply"),
    )
    op.create_index("ix_wb_fbo_supplies_project_id", "wb_fbo_supplies", ["project_id"])
    op.create_index("ix_wb_fbo_supplies_wb_status", "wb_fbo_supplies", ["wb_status"])
    op.create_index("ix_wb_fbo_supplies_created_at_wb", "wb_fbo_supplies", ["created_at_wb"])

    # ── wb_fbo_supply_items ──────────────────────────────────────────────
    op.create_table(
        "wb_fbo_supply_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("supply_id", sa.Integer(), nullable=False),
        sa.Column("wb_order_id", sa.String(50), nullable=False),
        sa.Column("nm_id", sa.Integer(), nullable=True),
        sa.Column("barcode", sa.String(50), nullable=False),
        sa.Column("article_seller", sa.String(100), nullable=True),
        sa.Column("product_name", sa.String(500), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("accepted_qty", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["supply_id"],
            ["wb_fbo_supplies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wb_fbo_supply_items_supply_id", "wb_fbo_supply_items", ["supply_id"])


def downgrade() -> None:
    op.drop_table("wb_fbo_supply_items")
    op.drop_table("wb_fbo_supplies")
