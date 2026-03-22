# ruff: noqa: RUF001
"""add wb_order_cancel_daily table for buyout_pct with cancels

Revision ID: m8_wb_order_cancel_daily
Revises: m7_order_city_map_add_columns
Create Date: 2026-03-22 18:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "m8_wb_order_cancel_daily"
down_revision = "m7_order_city_map_add_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wb_order_cancel_daily",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("nm_id", sa.Integer, nullable=False),
        sa.Column("dt", sa.Date, nullable=False),
        sa.Column("total_orders", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cancelled_orders", sa.Integer, nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "nm_id", "dt", name="uq_wb_order_cancel_daily"),
        sa.Index("ix_wb_order_cancel_daily_project_dt", "project_id", "dt"),
    )


def downgrade() -> None:
    op.drop_table("wb_order_cancel_daily")
