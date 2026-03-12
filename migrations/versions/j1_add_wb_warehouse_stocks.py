"""add wb_warehouse_stocks

Revision ID: j1_add_wb_warehouse_stocks
Revises: i1_add_audit_log
Create Date: 2026-03-12 14:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "j1_add_wb_warehouse_stocks"
down_revision: Union[str, None] = "i1_add_audit_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wb_warehouse_stocks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_name", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="0", nullable=False),
        sa.Column("in_way_to_client", sa.Integer(), server_default="0", nullable=False),
        sa.Column("in_way_from_client", sa.Integer(), server_default="0", nullable=False),
        sa.Column("vendor_code", sa.String(length=100), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_wb_wh_stocks_project",
        "wb_warehouse_stocks",
        ["project_id", "nm_id", "warehouse_name"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_wb_wh_stocks_project", table_name="wb_warehouse_stocks")
    op.drop_table("wb_warehouse_stocks")
