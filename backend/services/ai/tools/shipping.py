"""
Shipping/logistics tools — delivery tracking, costs, geography.

Used by logistics agent to monitor shipments and delivery costs.
"""

from .logistics import (
    _TOOL_LOGISTICS_HISTORY,
    _TOOL_DAILY_HEALTH,
    _TOOL_ORDER_GEOGRAPHY,
    _TOOL_WAREHOUSE_STOCKS,
)

SHIPPING_TOOLS: list[dict] = [
    _TOOL_LOGISTICS_HISTORY,
    _TOOL_DAILY_HEALTH,
    _TOOL_ORDER_GEOGRAPHY,
    _TOOL_WAREHOUSE_STOCKS,
]
