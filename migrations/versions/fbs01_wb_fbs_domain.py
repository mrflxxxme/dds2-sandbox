"""WB FBS domain: склады продавца, привязки, трансляция остатков, задания, поставки

Плюс `nomenclature.chrt_id` — ключ методов остатков Marketplace API
(баркоды/sku там не принимаются с 09.02.2026).

Revision ID: fbs01_wb_fbs_domain
Revises: tx01_machine_payment_link
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "fbs01_wb_fbs_domain"
down_revision: str | None = "tx01_machine_payment_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── Nomenclature.chrt_id ────────────────────────────────────────────────
    op.add_column("nomenclature", sa.Column("chrt_id", sa.BigInteger(), nullable=True))
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_nomenclature_project_chrt "
            "ON nomenclature (project_id, chrt_id) WHERE chrt_id IS NOT NULL"
        )

    # ─── Склады продавца WB ──────────────────────────────────────────────────
    op.create_table(
        "wb_fbs_warehouses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("wb_warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("office_id", sa.BigInteger(), nullable=True),
        sa.Column("office_name", sa.String(length=200), nullable=True),
        sa.Column("cargo_type", sa.SmallInteger(), nullable=True),
        sa.Column("delivery_type", sa.SmallInteger(), nullable=True),
        sa.Column("is_processing", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_deleting", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("stock_source", sa.String(length=20), server_default="min_of_both", nullable=False),
        sa.Column("safety_stock_pct", sa.Numeric(5, 2), server_default="0", nullable=False),
        sa.Column("safety_stock_abs", sa.Integer(), server_default="0", nullable=False),
        sa.Column("subtract_draft_reserve", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("max_qty_per_sku", sa.Integer(), server_default="0", nullable=False),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "wb_warehouse_id", name="uq_wb_fbs_warehouse"),
    )
    op.create_index("ix_wb_fbs_warehouses_project_id", "wb_fbs_warehouses", ["project_id"])

    # ─── Привязка склад WB ← наш склад ───────────────────────────────────────
    op.create_table(
        "wb_fbs_warehouse_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("wb_warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "wb_warehouse_id", "warehouse_id", name="uq_wb_fbs_link"),
    )
    op.create_index("ix_wb_fbs_links_project_id", "wb_fbs_warehouse_links", ["project_id"])
    op.create_index("ix_wb_fbs_links_warehouse_id", "wb_fbs_warehouse_links", ["warehouse_id"])
    # Один наш склад — максимум одна активная привязка (анти-двойная-продажа).
    op.create_index(
        "uq_wb_fbs_link_warehouse_active",
        "wb_fbs_warehouse_links",
        ["project_id", "warehouse_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    # ─── Состояние переданных остатков ───────────────────────────────────────
    op.create_table(
        "wb_fbs_stock_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("wb_warehouse_id", sa.BigInteger(), nullable=False),
        sa.Column("chrt_id", sa.BigInteger(), nullable=False),
        sa.Column("barcode", sa.String(length=50), nullable=True),
        sa.Column("nomenclature_id", sa.Integer(), nullable=True),
        sa.Column("qty_sent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("qty_confirmed", sa.Integer(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["nomenclature_id"], ["nomenclature.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "wb_warehouse_id", "chrt_id", name="uq_wb_fbs_stock_state"),
    )
    op.create_index("ix_wb_fbs_stock_states_project_sent", "wb_fbs_stock_states", ["project_id", "sent_at"])
    op.create_index("ix_wb_fbs_stock_states_nomenclature", "wb_fbs_stock_states", ["nomenclature_id"])

    # ─── Прогоны трансляции ──────────────────────────────────────────────────
    op.create_table(
        "wb_fbs_stock_pushes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("wb_warehouse_id", sa.BigInteger(), nullable=True),
        sa.Column("trigger", sa.String(length=10), server_default="auto", nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=10), server_default="RUNNING", nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("rows_total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_changed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_sent", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_no_chrt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_mismatch", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wb_fbs_stock_pushes_project_started", "wb_fbs_stock_pushes", ["project_id", "started_at"])
    op.create_index("ix_wb_fbs_stock_pushes_user", "wb_fbs_stock_pushes", ["user_id"])

    # ─── Сборочные задания ───────────────────────────────────────────────────
    op.create_table(
        "wb_fbs_orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("wb_order_id", sa.BigInteger(), nullable=False),
        sa.Column("rid", sa.String(length=120), nullable=True),
        sa.Column("order_uid", sa.String(length=120), nullable=True),
        sa.Column("created_at_wb", sa.DateTime(), nullable=True),
        sa.Column("wb_warehouse_id", sa.BigInteger(), nullable=True),
        sa.Column("office_id", sa.BigInteger(), nullable=True),
        sa.Column("office_name", sa.String(length=200), nullable=True),
        sa.Column("nm_id", sa.BigInteger(), nullable=True),
        sa.Column("chrt_id", sa.BigInteger(), nullable=True),
        sa.Column("barcode", sa.String(length=50), nullable=True),
        sa.Column("nomenclature_id", sa.Integer(), nullable=True),
        sa.Column("article", sa.String(length=100), nullable=True),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("price", sa.Numeric(18, 2), nullable=True),
        sa.Column("converted_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("sale_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency_code", sa.String(length=8), nullable=True),
        sa.Column("cargo_type", sa.SmallInteger(), nullable=True),
        sa.Column("cross_border_type", sa.SmallInteger(), nullable=True),
        sa.Column("is_zero_order", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_pickup_point_shipment_allowed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("supplier_status", sa.String(length=20), server_default="new", nullable=False),
        sa.Column("wb_status", sa.String(length=30), nullable=True),
        sa.Column("is_cancellable", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("supply_id", sa.String(length=50), nullable=True),
        sa.Column("sticker_barcode", sa.String(length=60), nullable=True),
        sa.Column("sticker_part_a", sa.String(length=20), nullable=True),
        sa.Column("sticker_part_b", sa.String(length=20), nullable=True),
        sa.Column("sticker_file_key", sa.String(length=300), nullable=True),
        sa.Column("ddate", sa.Date(), nullable=True),
        sa.Column("seller_date", sa.DateTime(), nullable=True),
        sa.Column("comment", sa.String(length=300), nullable=True),
        sa.Column("address", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("written_off_at", sa.DateTime(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["nomenclature_id"], ["nomenclature.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "wb_order_id", name="uq_wb_fbs_order"),
    )
    op.create_index("ix_wb_fbs_orders_project_status", "wb_fbs_orders", ["project_id", "supplier_status"])
    op.create_index("ix_wb_fbs_orders_project_supply", "wb_fbs_orders", ["project_id", "supply_id"])
    op.create_index("ix_wb_fbs_orders_project_created", "wb_fbs_orders", ["project_id", "created_at_wb"])
    op.create_index("ix_wb_fbs_orders_project_barcode", "wb_fbs_orders", ["project_id", "barcode"])
    op.create_index("ix_wb_fbs_orders_nomenclature", "wb_fbs_orders", ["nomenclature_id"])
    # Горячий путь формулы остатка — только открытые задания.
    op.create_index(
        "ix_wb_fbs_orders_open",
        "wb_fbs_orders",
        ["project_id", "wb_warehouse_id", "nomenclature_id"],
        postgresql_where=sa.text("supplier_status IN ('new', 'confirm')"),
    )

    # ─── Поставки FBS ────────────────────────────────────────────────────────
    op.create_table(
        "wb_fbs_supplies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("wb_supply_id", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=True),
        sa.Column("done", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at_wb", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("scan_dt", sa.DateTime(), nullable=True),
        sa.Column("cargo_type", sa.SmallInteger(), nullable=True),
        sa.Column("cross_border_type", sa.SmallInteger(), nullable=True),
        sa.Column("is_b2b", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_pickup_point_shipment_allowed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("destination_office_id", sa.BigInteger(), nullable=True),
        sa.Column("recommended_wh_id", sa.BigInteger(), nullable=True),
        sa.Column("wb_warehouse_id", sa.BigInteger(), nullable=True),
        sa.Column("orders_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("qr_barcode", sa.String(length=60), nullable=True),
        sa.Column("qr_file_key", sa.String(length=300), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "wb_supply_id", name="uq_wb_fbs_supply"),
    )
    op.create_index("ix_wb_fbs_supplies_project_done", "wb_fbs_supplies", ["project_id", "done"])


def downgrade() -> None:
    op.drop_index("ix_wb_fbs_supplies_project_done", table_name="wb_fbs_supplies")
    op.drop_table("wb_fbs_supplies")

    op.drop_index("ix_wb_fbs_orders_open", table_name="wb_fbs_orders")
    op.drop_index("ix_wb_fbs_orders_nomenclature", table_name="wb_fbs_orders")
    op.drop_index("ix_wb_fbs_orders_project_barcode", table_name="wb_fbs_orders")
    op.drop_index("ix_wb_fbs_orders_project_created", table_name="wb_fbs_orders")
    op.drop_index("ix_wb_fbs_orders_project_supply", table_name="wb_fbs_orders")
    op.drop_index("ix_wb_fbs_orders_project_status", table_name="wb_fbs_orders")
    op.drop_table("wb_fbs_orders")

    op.drop_index("ix_wb_fbs_stock_pushes_user", table_name="wb_fbs_stock_pushes")
    op.drop_index("ix_wb_fbs_stock_pushes_project_started", table_name="wb_fbs_stock_pushes")
    op.drop_table("wb_fbs_stock_pushes")

    op.drop_index("ix_wb_fbs_stock_states_nomenclature", table_name="wb_fbs_stock_states")
    op.drop_index("ix_wb_fbs_stock_states_project_sent", table_name="wb_fbs_stock_states")
    op.drop_table("wb_fbs_stock_states")

    op.drop_index("uq_wb_fbs_link_warehouse_active", table_name="wb_fbs_warehouse_links")
    op.drop_index("ix_wb_fbs_links_warehouse_id", table_name="wb_fbs_warehouse_links")
    op.drop_index("ix_wb_fbs_links_project_id", table_name="wb_fbs_warehouse_links")
    op.drop_table("wb_fbs_warehouse_links")

    op.drop_index("ix_wb_fbs_warehouses_project_id", table_name="wb_fbs_warehouses")
    op.drop_table("wb_fbs_warehouses")

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_nomenclature_project_chrt")
    op.drop_column("nomenclature", "chrt_id")
