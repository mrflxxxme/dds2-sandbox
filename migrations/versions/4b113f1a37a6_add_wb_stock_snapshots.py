"""add_wb_stock_snapshots

Revision ID: 4b113f1a37a6
Revises: m6_assembly_vehicle_fields
Create Date: 2026-03-21 15:51:48.623496

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b113f1a37a6"
down_revision: str | None = "m6_assembly_vehicle_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_stock_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.Column("warehouse_name", sa.String(200), nullable=False),
        sa.Column("nm_id", sa.Integer(), nullable=False),
        sa.Column("vendor_code", sa.String(100), nullable=True),
        sa.Column("barcode", sa.String(100), nullable=True),
        sa.Column("quantity", sa.Integer(), server_default="0", nullable=False),
        sa.Column("in_way_to_client", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wb_stock_snap_project_date", "wb_stock_snapshots", ["project_id", "synced_at"])
    op.create_index("ix_wb_stock_snap_nm", "wb_stock_snapshots", ["project_id", "nm_id", "synced_at"])


def downgrade() -> None:
    op.drop_index("ix_wb_stock_snap_nm", table_name="wb_stock_snapshots")
    op.drop_index("ix_wb_stock_snap_project_date", table_name="wb_stock_snapshots")
    op.drop_table("wb_stock_snapshots")
