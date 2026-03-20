"""
Planning package — re-exports all functions for backward compatibility.

Usage in routers remains unchanged:
    from backend.services import planning_service
    await planning_service.get_orders(db, project_id)
"""

from backend.services.planning.brand_plan import (
    delete_brand_plan,
    get_brand_plans,
    get_plan_fact_brands,
    get_plan_fact_brands_range,
    get_plan_fact_daily,
    get_plan_fact_daily_range,
    get_wb_brands,
    upsert_brand_plan,
)
from backend.services.planning.cashflow import (
    calculate_cashflow_daily,
    get_order_summary,
)
from backend.services.planning.crud import (
    delete_income,
    delete_order,
    delete_payment,
    get_incomes,
    get_lead_times,
    get_orders,
    get_payments,
    mark_paid,
    upsert_income,
    upsert_lead_time,
    upsert_order,
    upsert_payment,
)
from backend.services.planning.customs import (
    create_alloc,
    delete_alloc,
    delete_customs_dt,
    get_customs_alloc,
    get_customs_dt_list,
    get_customs_topup,
    parse_fts_pdf,
    update_customs_dt,
    upload_fts_and_create_dts,
)
from backend.services.planning.fact_links import (
    create_fact_link,
    delete_fact_link,
    get_accounts_list,
    get_candidate_transactions,
    get_fact_links,
    update_payment_paid_amount,
)
from backend.services.planning.wb import (
    delete_wb_payout,
    get_wb_payouts,
    manual_reconcile_wb,
    reconcile_wb_payouts,
    refresh_wb_forecast,
    upload_wb_payouts,
)

__all__ = [
    # CRUD
    "get_orders",
    "upsert_order",
    "delete_order",
    "get_lead_times",
    "upsert_lead_time",
    "get_payments",
    "upsert_payment",
    "delete_payment",
    "mark_paid",
    "get_incomes",
    "upsert_income",
    "delete_income",
    # Customs
    "get_customs_topup",
    "get_customs_alloc",
    "create_alloc",
    "delete_alloc",
    "upload_fts_and_create_dts",
    "get_customs_dt_list",
    "update_customs_dt",
    "delete_customs_dt",
    "parse_fts_pdf",
    # Fact Links
    "get_fact_links",
    "create_fact_link",
    "delete_fact_link",
    "get_candidate_transactions",
    "get_accounts_list",
    "update_payment_paid_amount",
    # Cashflow
    "calculate_cashflow_daily",
    "get_order_summary",
    # WB
    "upload_wb_payouts",
    "get_wb_payouts",
    "delete_wb_payout",
    "manual_reconcile_wb",
    "reconcile_wb_payouts",
    "refresh_wb_forecast",
    # Brand Plans
    "get_brand_plans",
    "upsert_brand_plan",
    "delete_brand_plan",
    "get_wb_brands",
    "get_plan_fact_daily",
    "get_plan_fact_daily_range",
    "get_plan_fact_brands",
    "get_plan_fact_brands_range",
]
