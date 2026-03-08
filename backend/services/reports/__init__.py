"""
Reports package — re-exports all functions for backward compatibility.

Usage in routers remains unchanged:
    from backend.services import reports_service
    await reports_service.get_balance(db, project_id)
"""

from backend.services.reports.balance import (
    get_balance,
    get_balance_daily,
)
from backend.services.reports.dds import (
    get_dds_month,
    get_dds_pnl,
)
from backend.services.reports.dashboard import (
    get_dashboard_summary,
)
from backend.services.reports.queries import (
    get_fx_control,
    get_customs_control,
    get_income_daily,
    get_income_by_category_daily,
    get_daily_filtered,
    get_filtered_transactions,
    get_category_counterparties,
)

__all__ = [
    "get_balance",
    "get_balance_daily",
    "get_dds_month",
    "get_dds_pnl",
    "get_dashboard_summary",
    "get_fx_control",
    "get_customs_control",
    "get_income_daily",
    "get_income_by_category_daily",
    "get_daily_filtered",
    "get_filtered_transactions",
    "get_category_counterparties",
]
