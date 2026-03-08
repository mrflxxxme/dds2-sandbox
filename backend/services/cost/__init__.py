"""
Cost package — re-exports all functions for backward compatibility.

Usage in routers remains unchanged:
    from backend.services import cost_service
    await cost_service.get_cost_orders(db, project_id)
"""

from backend.services.cost.helpers import (
    _order_no_to_int,
    safe_float,
    safe_decimal,
    auto_link_customs_dt,
    DEFAULT_VAT_RATE,
)
from backend.services.cost.nomenclature import (
    get_nomenclature,
    upload_nomenclature,
)
from backend.services.cost.duty import (
    get_duty_rules,
    upsert_duty_rule,
    delete_duty_rule,
)
from backend.services.cost.orders import (
    get_cost_orders,
    create_cost_order,
    update_cost_order,
    delete_cost_order,
)
from backend.services.cost.items import (
    get_cost_order_items,
    upload_order_items,
    recalculate_order_items,
)
from backend.services.cost.plan_gen import (
    generate_payment_plan,
)

# Keep detect_and_normalize_excel accessible
from backend.etl.cost_parsers import detect_and_normalize_excel  # noqa: F401

__all__ = [
    # Helpers
    "_order_no_to_int", "safe_float", "safe_decimal",
    "auto_link_customs_dt", "DEFAULT_VAT_RATE",
    # Nomenclature
    "get_nomenclature", "upload_nomenclature",
    # Duty
    "get_duty_rules", "upsert_duty_rule", "delete_duty_rule",
    # Orders
    "get_cost_orders", "create_cost_order", "update_cost_order", "delete_cost_order",
    # Items
    "get_cost_order_items", "upload_order_items", "recalculate_order_items",
    # Plan
    "generate_payment_plan",
    # ETL
    "detect_and_normalize_excel",
]
