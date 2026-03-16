"""add missing project_id and performance indexes

Revision ID: l1_add_missing_project_id_indexes
Revises: k1_add_order_city_map
Create Date: 2026-03-16 12:00:00.000000
"""
from alembic import op

revision = 'l1_add_missing_project_id_indexes'
down_revision = 'd03e6ced935c'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # refs models
    op.create_index("ix_overrides_project_id", "overrides", ["project_id"])
    op.create_index("ix_opening_bal_project_id", "opening_balances", ["project_id"])

    # planning models
    op.create_index("ix_orders_project_id", "orders", ["project_id"])
    op.create_index("ix_planned_payments_project_id", "planned_payments", ["project_id"])
    op.create_index("ix_planned_incomes_project_id", "planned_incomes", ["project_id"])
    op.create_index("ix_payment_fact_links_payment_id", "payment_fact_links", ["payment_id"])

    # cost models
    op.create_index("ix_cost_orders_project_id", "cost_orders", ["project_id"])

    # customs models
    op.create_index("ix_customs_alloc_project_id", "customs_alloc", ["project_id"])
    op.create_index("ix_customs_dt_project_id", "customs_dt", ["project_id"])

    # integrations models
    op.create_index("ix_sync_log_integration_id", "sync_log", ["integration_id"])
    op.create_index("ix_sync_log_started_at", "sync_log", ["started_at"])

    # audit log
    op.create_index("ix_audit_log_project_id", "audit_log", ["project_id"])
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_user_id", table_name="audit_log")
    op.drop_index("ix_audit_log_project_id", table_name="audit_log")
    op.drop_index("ix_sync_log_started_at", table_name="sync_log")
    op.drop_index("ix_sync_log_integration_id", table_name="sync_log")
    op.drop_index("ix_customs_dt_project_id", table_name="customs_dt")
    op.drop_index("ix_customs_alloc_project_id", table_name="customs_alloc")
    op.drop_index("ix_cost_orders_project_id", table_name="cost_orders")
    op.drop_index("ix_payment_fact_links_payment_id", table_name="payment_fact_links")
    op.drop_index("ix_planned_incomes_project_id", table_name="planned_incomes")
    op.drop_index("ix_planned_payments_project_id", table_name="planned_payments")
    op.drop_index("ix_orders_project_id", table_name="orders")
    op.drop_index("ix_opening_bal_project_id", table_name="opening_balances")
    op.drop_index("ix_overrides_project_id", table_name="overrides")
