"""
Cost package — re-exports all functions for backward compatibility.

Usage in routers remains unchanged:
    from backend.services import cost_service
    await cost_service.get_cost_orders(db, project_id)
"""

# Keep detect_and_normalize_excel accessible
from backend.etl.cost_parsers import detect_and_normalize_excel
from backend.services.cost.duty import (
    bulk_update_nomenclature_area,
    bulk_update_nomenclature_weight,
    delete_duty_exception,
    delete_duty_rule,
    get_duty_exceptions,
    get_duty_rules,
    upsert_duty_exception,
    upsert_duty_rule,
)
from backend.services.cost.helpers import (
    DEFAULT_VAT_RATE,
    _order_no_to_int,
    auto_link_customs_dt,
    safe_decimal,
    safe_float,
)
from backend.services.cost.items import (
    get_cost_order_items,
    recalculate_order_items,
    upload_order_items,
)
from backend.services.cost.nomenclature import (
    get_missing_area_barcodes,
    get_missing_weight_barcodes,
    get_nomenclature,
    get_nomenclature_subjects,
    upload_nomenclature,
)
from backend.services.cost.opening_balance import (
    get_opening_balances,
    get_sku_valuation,
    get_valuation_summary,
    update_order_arrival_date,
    upsert_opening_balances,
)
from backend.services.cost.orders import (
    create_cost_order,
    delete_cost_order,
    get_cost_orders,
    update_cost_order,
)
from backend.services.cost.plan_gen import (
    generate_payment_plan,
)
from backend.services.cost.recalc import (
    find_orders_by_articles,
    find_orders_by_barcodes,
    find_orders_by_subjects,
    recalc_orders,
)

__all__ = [
    # Helpers
    "_order_no_to_int",
    "safe_float",
    "safe_decimal",
    "auto_link_customs_dt",
    "DEFAULT_VAT_RATE",
    # Nomenclature
    "get_nomenclature",
    "get_nomenclature_subjects",
    "get_missing_area_barcodes",
    "get_missing_weight_barcodes",
    "upload_nomenclature",
    # Duty
    "get_duty_rules",
    "upsert_duty_rule",
    "delete_duty_rule",
    "get_duty_exceptions",
    "upsert_duty_exception",
    "delete_duty_exception",
    "bulk_update_nomenclature_area",
    "bulk_update_nomenclature_weight",
    # Orders
    "get_cost_orders",
    "create_cost_order",
    "update_cost_order",
    "delete_cost_order",
    # Recalc (auto cost)
    "recalc_orders",
    "find_orders_by_barcodes",
    "find_orders_by_subjects",
    "find_orders_by_articles",
    # Items
    "get_cost_order_items",
    "upload_order_items",
    "recalculate_order_items",
    # Plan
    "generate_payment_plan",
    # Opening balance + valuation analytics
    "get_opening_balances",
    "upsert_opening_balances",
    "update_order_arrival_date",
    "get_sku_valuation",
    "get_valuation_summary",
    # ETL
    "detect_and_normalize_excel",
]
