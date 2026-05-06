"""wb_orders table

Revision ID: dccee1b75a78
Revises: 817ba08e23e4
Create Date: 2026-05-05 19:30:09.998718

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dccee1b75a78"
down_revision: str | None = "817ba08e23e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("srid", sa.String(length=120), nullable=False),
        sa.Column("g_number", sa.String(length=40), nullable=True),
        sa.Column("sticker", sa.String(length=40), nullable=True),
        sa.Column("income_id", sa.BigInteger(), nullable=True),
        sa.Column("order_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_change_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("barcode", sa.String(length=40), nullable=True),
        sa.Column("vendor_code", sa.String(length=120), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("brand", sa.String(length=200), nullable=True),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.Column("tech_size", sa.String(length=40), nullable=True),
        sa.Column("warehouse_name", sa.String(length=120), nullable=True),
        sa.Column("warehouse_type", sa.String(length=40), nullable=True),
        sa.Column("country_name", sa.String(length=80), nullable=True),
        sa.Column("oblast_okrug_name", sa.String(length=120), nullable=True),
        sa.Column("region_name", sa.String(length=200), nullable=True),
        sa.Column("total_price", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("discount_percent", sa.Integer(), nullable=True),
        sa.Column("spp", sa.Integer(), nullable=True),
        sa.Column("finished_price", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("price_with_disc", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("is_cancel", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cancel_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_supply", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_realization", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), nullable=True, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "srid", name="uq_wb_orders_project_srid"),
    )
    op.create_index(
        "ix_wb_orders_project_date",
        "wb_orders",
        ["project_id", "order_date"],
        unique=False,
    )
    op.create_index(
        "ix_wb_orders_project_nm_date",
        "wb_orders",
        ["project_id", "nm_id", "order_date"],
        unique=False,
    )
    op.create_index(
        "ix_wb_orders_project_okrug_date",
        "wb_orders",
        ["project_id", "oblast_okrug_name", "order_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_wb_orders_project_okrug_date", table_name="wb_orders")
    op.drop_index("ix_wb_orders_project_nm_date", table_name="wb_orders")
    op.drop_index("ix_wb_orders_project_date", table_name="wb_orders")
    op.drop_table("wb_orders")
