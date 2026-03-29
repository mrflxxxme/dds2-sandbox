"""
Supply chain tools — stock needs, RF warehouses, assembly tracking.

Used by supply_manager agent to monitor product flow from RF to WB.
"""

from .logistics import (
    _TOOL_STOCK_INFO,
    _TOOL_WAREHOUSE_NEED,
    _TOOL_WAREHOUSE_STOCKS,
    _TOOL_ANOMALIES,
    _TOOL_DAILY_HEALTH,
    _TOOL_PRODUCT_INFO,
)

SUPPLY_TOOLS: list[dict] = [
    _TOOL_STOCK_INFO,
    _TOOL_WAREHOUSE_NEED,
    _TOOL_WAREHOUSE_STOCKS,
    _TOOL_PRODUCT_INFO,
    _TOOL_ANOMALIES,
    _TOOL_DAILY_HEALTH,
]
