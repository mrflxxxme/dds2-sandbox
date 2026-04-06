"""
Tool executor — bridges Claude tool calls to DDS service functions.

Each tool call is executed with project_id + brand filtering (multi-tenant safety).
Tool implementations live in backend.services.ai.tools.<domain> modules.
"""

import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.ai.tools import funnel, health, planning, products, reports, warehouse
from backend.services.ai.tools.common import normalize_brand

logger = logging.getLogger("dds.ai")

# Unified signature: (db, project_id, tax_rate, brand, inp) -> str
ToolFunc = Callable[[AsyncSession, int, float, str | None, dict], Coroutine[Any, Any, str]]

TOOL_REGISTRY: dict[str, ToolFunc] = {
    # Funnel
    "get_funnel_data": funnel.get_funnel_data,
    "get_top_products": funnel.get_top_products,
    "get_day_analysis": funnel.get_day_analysis,
    "get_ad_campaigns": funnel.get_ad_campaigns,
    "compare_periods": funnel.compare_periods,
    # Reports
    "get_dds_report": reports.get_dds_report,
    "get_opiu_report": reports.get_opiu_report,
    "get_bdr_data": reports.get_bdr_data,
    "get_capital_analysis": reports.get_capital_analysis,
    # Products
    "get_cost_data": products.get_cost_data,
    "get_product_info": products.get_product_info,
    # Warehouse
    "get_stock_info": warehouse.get_stock_info,
    "get_warehouse_need": warehouse.get_warehouse_need,
    "get_warehouse_stocks": warehouse.get_warehouse_stocks,
    "get_logistics_history": warehouse.get_logistics_history,
    # Planning
    "get_plan_fact": planning.get_plan_fact,
    "get_order_geography": planning.get_order_geography,
    # Health
    "get_anomalies": health.get_anomalies,
    "get_daily_health": health.get_daily_health,
}


async def execute_tool(
    db: AsyncSession,
    project_id: int,
    brand: str | None,
    tax_rate: float,
    tool_name: str,
    tool_input: dict,
) -> str:
    """Execute a tool call and return the result as a JSON string."""
    brand = normalize_brand(brand)
    try:
        tool_func = TOOL_REGISTRY.get(tool_name)
        if not tool_func:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        return await tool_func(db, project_id, tax_rate, brand, tool_input)
    except Exception as e:
        logger.exception("Tool execution error: %s", tool_name)
        return json.dumps({"error": str(e)})
