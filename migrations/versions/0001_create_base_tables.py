"""create all base tables

Revision ID: 0001_create_base
Revises: (root)
Create Date: 2026-03-17

Tables are created in their ORIGINAL state — columns added by later migrations
(project_id, is_deleted, deleted_at, vat_rate) are intentionally omitted here.
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_create_base"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # === ENUMS ===
    event_type2 = sa.Enum("INTERNAL_TRANSFER", "FX_BUY", "CUSTOMS_PAYMENT", "OPER", name="eventtype2")
    duty_basis = sa.Enum("WEIGHT", "AREA", "INVOICE", name="dutybasis")

    # === AUTH ===
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(100), unique=True, nullable=False),
        sa.Column("email", sa.String(200), unique=True),
        sa.Column("first_name", sa.String(100)),
        sa.Column("last_name", sa.String(100)),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("role", sa.String(20), server_default="member", nullable=False),
        sa.Column("created_at", sa.DateTime),
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), unique=True, nullable=False),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime),
        sa.Column("tax_rate", sa.Numeric(5, 2)),
        # vat_rate added by d1_add_vat_rate migration
    )

    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("joined_at", sa.DateTime),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )

    op.create_table(
        "project_invites",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("email", sa.String(200)),
        sa.Column("invite_token", sa.String(100), unique=True, nullable=False),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("created_at", sa.DateTime),
        sa.Column("accepted_at", sa.DateTime),
        sa.Column("accepted_by_id", sa.Integer, sa.ForeignKey("users.id")),
    )

    # === REFERENCES ===
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("account", sa.String(50), unique=True, nullable=False),
        sa.Column("bank", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("account_type", sa.String(30)),
        sa.Column("is_our_account", sa.Boolean, default=True),
        sa.Column("account_name", sa.String(100)),
        sa.Column("is_customs_payee", sa.Boolean, default=False),
        # is_deleted, deleted_at added by d03e6ced935c
        sa.Index("ix_accounts_project_id", "project_id"),
    )

    op.create_table(
        "counterparty_categories",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("cp_key", sa.String(100), unique=True, nullable=False),
        sa.Column("cp_name", sa.String(200)),
        sa.Column("cat_lvl1", sa.String(100)),
        sa.Column("cat_lvl2", sa.String(100)),
        sa.Column("note", sa.Text),
        sa.Column("cp_key_auto", sa.String(100)),
        sa.Column("cp_name_auto", sa.String(200)),
        # is_deleted, deleted_at added by d03e6ced935c
        sa.Index("ix_cp_cat_project_id", "project_id"),
    )

    op.create_table(
        "overrides",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("txn_id", sa.String(300), unique=True, nullable=False),
        sa.Column("cat_lvl1", sa.String(100)),
        sa.Column("cat_lvl2", sa.String(100)),
        sa.Column("order_id", sa.String(100)),
        sa.Column("comment", sa.Text),
        sa.Column("updated_at", sa.DateTime),
        # is_deleted, deleted_at added by d03e6ced935c
        sa.Index("ix_overrides_project_id", "project_id"),
    )

    op.create_table(
        "opening_balances",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("date_open", sa.Date, nullable=False),
        sa.Column("account", sa.String(50), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("opening_balance", sa.Numeric(18, 2), server_default="0"),
        sa.UniqueConstraint("date_open", "account", "currency"),
        sa.Index("ix_opening_bal_project_id", "project_id"),
    )

    # category_ref: project_id added by b2c3d4e5f6g7, is_deleted by d03e6ced935c
    op.create_table(
        "category_ref",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("direction", sa.String(10), nullable=False),
        sa.Column("cat_lvl1", sa.String(100), nullable=False),
        sa.Column("cat_lvl2", sa.String(100), nullable=False),
        sa.Column("sort_order", sa.Integer, server_default="0"),
        sa.UniqueConstraint("direction", "cat_lvl1", "cat_lvl2", name="uq_cat_ref"),
    )

    op.create_table(
        "project_settings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.Text, nullable=False, server_default="{}"),
        sa.UniqueConstraint("project_id", "key", name="uq_project_setting"),
    )

    # === CATEGORY RULES ===
    op.create_table(
        "category_rules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("keyword", sa.String(200), nullable=False),
        sa.Column("direction", sa.String(10), nullable=False, server_default="expense"),
        sa.Column("cat_lvl1", sa.String(100), nullable=False),
        sa.Column("cat_lvl2", sa.String(100)),
        sa.Column("priority", sa.Integer, server_default="0"),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("is_deleted", sa.Boolean, server_default="false", nullable=False),
        sa.Column("deleted_at", sa.DateTime),
        sa.UniqueConstraint("project_id", "keyword", "direction", name="uq_category_rule"),
        sa.Index("ix_catrule_project", "project_id"),
    )

    # === TRANSACTIONS ===
    # project_id nullable here; h1_project_id_not_null makes it NOT NULL later
    # is_deleted, deleted_at added by 69703897c946
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("date", sa.DateTime, nullable=False),
        sa.Column("bank", sa.String(20), nullable=False),
        sa.Column("account", sa.String(50), sa.ForeignKey("accounts.account"), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("counterparty", sa.String(300)),
        sa.Column("inn", sa.String(20)),
        sa.Column("counterparty_account", sa.String(50)),
        sa.Column("purpose", sa.Text),
        sa.Column("income", sa.Numeric(18, 2), server_default="0"),
        sa.Column("expense", sa.Numeric(18, 2), server_default="0"),
        sa.Column("txn_id", sa.String(300), unique=True, nullable=False),
        sa.Column("cp_key", sa.String(100)),
        sa.Column("net", sa.Numeric(18, 2)),
        sa.Column("is_internal", sa.Boolean, default=False),
        sa.Column("event_type", sa.String(30)),
        sa.Column("is_cashflow", sa.Integer),
        sa.Column("cat_lvl1", sa.String(100)),
        sa.Column("cat_lvl2", sa.String(100)),
        sa.Column("order_id", sa.String(100)),
        sa.Column("status", sa.String(20)),
        sa.Column("account_text", sa.String(50)),
        sa.Column("is_fx", sa.Boolean, default=False),
        sa.Column("event_type2", event_type2),
        sa.Column("is_cashflow2", sa.Integer, server_default="1"),
        sa.Column("cat_lvl1_2", sa.String(100)),
        sa.Column("cat_lvl2_2", sa.String(100)),
        sa.Column("purpose_tag", sa.String(30)),
        sa.Column("invoice_id", sa.String(100)),
        sa.Column("annex_id", sa.String(50)),
        sa.Index("ix_txn_date", "date"),
        sa.Index("ix_txn_account", "account"),
        sa.Index("ix_txn_currency", "currency"),
        sa.Index("ix_txn_cat", "cat_lvl1_2", "cat_lvl2_2"),
        sa.Index("ix_txn_status", "status"),
        sa.Index("ix_txn_project_date", "project_id", "date"),
        sa.Index("ix_txn_project_cashflow", "project_id", "is_cashflow2", "cat_lvl1_2"),
        sa.Index("ix_txn_project_status", "project_id", "status"),
    )

    op.create_table(
        "category_change_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("txn_id", sa.String(300), nullable=False),
        sa.Column("changed_at", sa.DateTime),
        sa.Column("changed_by", sa.String(100)),
        sa.Column("old_cat_lvl1", sa.String(100)),
        sa.Column("old_cat_lvl2", sa.String(100)),
        sa.Column("new_cat_lvl1", sa.String(100)),
        sa.Column("new_cat_lvl2", sa.String(100)),
        sa.Column("scope", sa.String(20)),
    )

    # project_id nullable here; h1_project_id_not_null makes it NOT NULL later
    op.create_table(
        "import_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("filename", sa.String(300)),
        sa.Column("source_type", sa.String(30)),
        sa.Column("imported_at", sa.DateTime),
        sa.Column("rows_raw", sa.Integer, server_default="0"),
        sa.Column("rows_inserted", sa.Integer, server_default="0"),
        sa.Column("rows_skipped", sa.Integer, server_default="0"),
        sa.Column("status", sa.String(20), server_default="OK"),
        sa.Column("error_msg", sa.Text),
        sa.Column("file_url", sa.String(500)),
    )

    # === PLANNING ===
    # is_deleted, deleted_at added by 69703897c946
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("order_name", sa.String(200)),
        sa.Column("category", sa.String(100)),
        sa.Column("transport_type", sa.String(30)),
        sa.Column("order_no", sa.Integer, unique=True),
        sa.Column("supplier", sa.String(100)),
        sa.Column("planned_ship_date", sa.Date),
        sa.Column("actual_ship_date", sa.Date),
        sa.Column("order_amount", sa.Numeric(18, 2)),
        sa.Column("deposit", sa.Numeric(18, 2)),
        sa.Column("logistics_cny", sa.Numeric(18, 2)),
        sa.Column("customs_rub", sa.Numeric(18, 2)),
        sa.Column("source_file", sa.String(200)),
        sa.Index("ix_orders_project_id", "project_id"),
    )

    # lead_time: project_id added by b2c3d4e5f6g7
    op.create_table(
        "lead_time",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("direction", sa.String(30), unique=True, nullable=False),
        sa.Column("days", sa.Integer, nullable=False),
    )

    # is_deleted, deleted_at added by 69703897c946
    op.create_table(
        "planned_payments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("pay_date", sa.Date),
        sa.Column("order_no", sa.Integer, sa.ForeignKey("orders.order_no")),
        sa.Column("direction", sa.String(50)),
        sa.Column("amount", sa.Numeric(18, 2)),
        sa.Column("currency", sa.String(10)),
        sa.Column("fx_rate", sa.Numeric(12, 6)),
        sa.Column("amount_rub", sa.Numeric(18, 2)),
        sa.Column("paid_rub", sa.Numeric(18, 2), server_default="0"),
        sa.Column("paid_amount", sa.Numeric(18, 2), server_default="0"),
        sa.Column("is_paid", sa.Boolean, server_default="false"),
        sa.Index("ix_planned_payments_project_id", "project_id"),
    )

    # is_deleted, deleted_at added by d03e6ced935c
    op.create_table(
        "planned_incomes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("amount_rub", sa.Numeric(18, 2)),
        sa.Column("source", sa.String(50), server_default="WB"),
        sa.Index("ix_planned_incomes_project_id", "project_id"),
    )

    # is_deleted, deleted_at added by d03e6ced935c
    # uq_wb_payout_project_request added by c3d4e5f6g7h8 (replaces this unique)
    op.create_table(
        "wb_payouts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("request_id", sa.String(100), unique=True, nullable=False),
        sa.Column("amount_rub", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(10), server_default="RUB"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("wb_status_raw", sa.String(300)),
        sa.Column("status", sa.String(20), server_default="PENDING"),
        sa.Column("bank_comment", sa.Text),
        sa.Column("matched_txn_id", sa.String(300)),
        sa.Column("matched_at", sa.DateTime),
        sa.Column("imported_at", sa.DateTime),
        sa.Index("ix_wb_payout_status", "status"),
        sa.Index("ix_wb_payout_created", "created_at"),
    )

    # is_deleted, deleted_at added by d03e6ced935c
    op.create_table(
        "payment_fact_links",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("payment_id", sa.Integer, sa.ForeignKey("planned_payments.id"), nullable=False),
        sa.Column("txn_id", sa.String(100), nullable=False),
        sa.Column("amount_rub", sa.Numeric(18, 2), nullable=False),
        sa.Column("note", sa.String(200)),
        sa.Index("ix_payment_fact_links_payment_id", "payment_id"),
    )

    # === COST ===
    # nomenclature: project_id added by b2c3d4e5f6g7
    op.create_table(
        "nomenclature",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("barcode", sa.String(50), unique=True, nullable=False),
        sa.Column("brand", sa.String(100)),
        sa.Column("subject", sa.String(100)),
        sa.Column("article_seller", sa.String(100)),
        sa.Column("article_wb", sa.Integer),
        sa.Column("volume_l", sa.Numeric(10, 4)),
        sa.Column("updated_at", sa.DateTime),
    )

    # duty_rules: project_id added by b2c3d4e5f6g7, is_deleted by d03e6ced935c
    op.create_table(
        "duty_rules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("subject", sa.String(100), unique=True, nullable=False),
        sa.Column("basis", duty_basis, nullable=False),
        sa.Column("rate", sa.Numeric(10, 6), nullable=False),
        sa.Column("util_collect_rub", sa.Numeric(10, 2), server_default="0"),
        sa.Column("note", sa.String(200)),
    )

    # is_deleted, deleted_at added by 69703897c946
    op.create_table(
        "cost_orders",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("order_no", sa.String(50), unique=True, nullable=False),
        sa.Column("invoice_no", sa.String(100)),
        sa.Column("ship_date", sa.Date),
        sa.Column("actual_arrival_date", sa.Date),
        sa.Column("transport_type", sa.String(30), server_default="AUTO"),
        sa.Column("delivery_cost_cny", sa.Numeric(14, 2), server_default="0"),
        sa.Column("delivery_cost_usd", sa.Numeric(14, 2), server_default="0"),
        sa.Column("rate_cny", sa.Numeric(10, 4), server_default="1"),
        sa.Column("rate_eur", sa.Numeric(10, 4), server_default="1"),
        sa.Column("rate_usd", sa.Numeric(10, 4), server_default="1"),
        sa.Column("note", sa.Text),
        sa.Column("dt_number", sa.String(100)),
        sa.Column("created_at", sa.DateTime),
        sa.Index("ix_cost_orders_project_id", "project_id"),
    )

    op.create_table(
        "cost_order_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("order_no", sa.String(50), sa.ForeignKey("cost_orders.order_no"), nullable=False),
        sa.Column("barcode", sa.String(50), nullable=False),
        sa.Column("subject", sa.String(100)),
        sa.Column("article_seller", sa.String(100)),
        sa.Column("qty", sa.Integer, server_default="1"),
        sa.Column("price_cny", sa.Numeric(12, 4), server_default="0"),
        sa.Column("weight_kg", sa.Numeric(10, 4)),
        sa.Column("area_m2", sa.Numeric(10, 4)),
        sa.Column("volume_m3", sa.Numeric(10, 6)),
        sa.Column("cost_rub", sa.Numeric(14, 2)),
        sa.Column("delivery_rub", sa.Numeric(14, 2)),
        sa.Column("duty_rub", sa.Numeric(14, 2)),
        sa.Column("vat_rub", sa.Numeric(14, 2)),
        sa.Column("util_rub", sa.Numeric(14, 2)),
        sa.Column("total_rub", sa.Numeric(14, 2)),
        sa.Column("total_cny", sa.Numeric(14, 4)),
        sa.Column("unrecognized", sa.Boolean, server_default="false"),
        sa.Index("ix_cost_item_order", "order_no"),
    )

    # === CUSTOMS ===
    # customs_topup: project_id added by b2c3d4e5f6g7, is_deleted by d03e6ced935c
    op.create_table(
        "customs_topup",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("topup_txn_id", sa.String(300), nullable=False, unique=True),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("amount_rub", sa.Numeric(18, 2)),
        sa.Column("purpose", sa.Text),
        sa.Column("account", sa.String(50)),
        sa.Column("counterparty_account", sa.String(50)),
    )

    # customs_alloc: project_id added by b2c3d4e5f6g7
    op.create_table(
        "customs_alloc",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("topup_txn_id", sa.String(300), sa.ForeignKey("customs_topup.topup_txn_id"), nullable=False),
        sa.Column("pay_date", sa.Date),
        sa.Column("pay_amount", sa.Numeric(18, 2)),
        sa.Column("order_no", sa.Integer),
        sa.Column("alloc_amount", sa.Numeric(18, 2)),
        sa.Column("comment", sa.Text),
    )

    # customs_dt: project_id added by b2c3d4e5f6g7, is_deleted by d03e6ced935c
    op.create_table(
        "customs_dt",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("dt_number", sa.String(100), nullable=False),
        sa.Column("dt_date", sa.Date, nullable=False),
        sa.Column("amount_rub", sa.Numeric(18, 2), nullable=False),
        sa.Column("order_no", sa.Integer),
        sa.Column("note", sa.Text),
        sa.Index("ix_customs_dt_number", "dt_number"),
    )

    # === INTEGRATIONS ===
    # is_deleted, deleted_at added by d03e6ced935c
    # uq_integration_project_service_label added by c3d4e5f6g7h8 (replaces this)
    op.create_table(
        "integration_keys",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("service", sa.String(50), nullable=False),
        sa.Column("label", sa.String(200)),
        sa.Column("encrypted_key", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("last_sync_at", sa.DateTime),
        sa.UniqueConstraint("service", "label", name="uq_integration_service_label"),
    )

    op.create_table(
        "sync_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("integration_id", sa.Integer, sa.ForeignKey("integration_keys.id")),
        sa.Column("service", sa.String(50), nullable=False),
        sa.Column("sync_type", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime),
        sa.Column("finished_at", sa.DateTime),
        sa.Column("status", sa.String(20), server_default="RUNNING"),
        sa.Column("rows_fetched", sa.Integer, server_default="0"),
        sa.Column("rows_inserted", sa.Integer, server_default="0"),
        sa.Column("error_msg", sa.Text),
        sa.Index("ix_sync_log_integration_id", "integration_id"),
        sa.Index("ix_sync_log_started_at", "started_at"),
    )

    op.create_table(
        "wb_funnel_daily",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("nm_id", sa.Integer, nullable=False),
        sa.Column("vendor_code", sa.String(100)),
        sa.Column("subject", sa.String(200)),
        sa.Column("brand", sa.String(200)),
        sa.Column("open_card", sa.Integer, server_default="0"),
        sa.Column("add_to_cart", sa.Integer, server_default="0"),
        sa.Column("orders_count", sa.Integer, server_default="0"),
        sa.Column("orders_sum_rub", sa.Numeric(18, 2), server_default="0"),
        sa.Column("buyout_percent", sa.Numeric(6, 2)),
        sa.Column("cart_to_order_pct", sa.Numeric(6, 2)),
        sa.Column("add_to_cart_pct", sa.Numeric(6, 2)),
        sa.Column("avg_price", sa.Numeric(18, 2)),
        sa.Column("stocks_wb", sa.Integer, server_default="0"),
        sa.Column("stocks_mp", sa.Integer, server_default="0"),
        sa.Column("adv_views", sa.Integer, server_default="0"),
        sa.Column("adv_clicks", sa.Integer, server_default="0"),
        sa.Column("adv_sum", sa.Numeric(18, 2), server_default="0"),
        sa.Column("cost_price", sa.Numeric(18, 2)),
        sa.UniqueConstraint("project_id", "date", "nm_id", name="uq_funnel_daily"),
        sa.Index("ix_funnel_project_date", "project_id", "date"),
    )

    op.create_table(
        "wb_cost_override",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id")),
        sa.Column("nm_id", sa.Integer, nullable=False),
        sa.Column("cost_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("updated_at", sa.DateTime),
        sa.UniqueConstraint("project_id", "nm_id", name="uq_cost_override_nm"),
    )

    # === TAX ===
    op.create_table(
        "tax_rates",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("brand", sa.String(200), nullable=False),
        sa.Column("tax_regime", sa.String(50), nullable=False, server_default="usn_income"),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("month", sa.Integer, nullable=False),
        sa.Column("usn_rate", sa.Numeric(6, 2), server_default="0"),
        sa.Column("nds_rate", sa.Numeric(6, 2), server_default="0"),
        sa.Column("cost_as_expense", sa.Boolean, server_default="false"),
        sa.Column("updated_at", sa.DateTime),
        sa.UniqueConstraint("project_id", "brand", "year", "month", name="uq_tax_rate_brand_month"),
        sa.Index("ix_tax_rate_project_year", "project_id", "year"),
    )


def downgrade() -> None:
    op.drop_table("tax_rates")
    op.drop_table("wb_cost_override")
    op.drop_table("wb_funnel_daily")
    op.drop_table("sync_log")
    op.drop_table("integration_keys")
    op.drop_table("customs_dt")
    op.drop_table("customs_alloc")
    op.drop_table("customs_topup")
    op.drop_table("cost_order_items")
    op.drop_table("cost_orders")
    op.drop_table("duty_rules")
    op.drop_table("nomenclature")
    op.drop_table("payment_fact_links")
    op.drop_table("wb_payouts")
    op.drop_table("planned_incomes")
    op.drop_table("planned_payments")
    op.drop_table("lead_time")
    op.drop_table("orders")
    op.drop_table("import_log")
    op.drop_table("category_change_log")
    op.drop_table("transactions")
    op.drop_table("category_rules")
    op.drop_table("project_settings")
    op.drop_table("category_ref")
    op.drop_table("opening_balances")
    op.drop_table("overrides")
    op.drop_table("counterparty_categories")
    op.drop_table("accounts")
    op.drop_table("project_invites")
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_table("users")
    sa.Enum(name="eventtype2").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="dutybasis").drop(op.get_bind(), checkfirst=True)
