"""
Logistics & supply chain tools for DDS AI agent.

Individual tools are exported as named variables for reuse by
supply.py (supply manager) and shipping.py (logistics agent).
"""

_TOOL_STOCK_INFO: dict = {
    "name": "get_stock_info",
    "description": (
        "Get stock levels and restock forecast including RF warehouses AND in-transit shipments. "
        "Returns per-product: stocks_wb (on WB), stocks_rf (RF warehouses like Натали, Газпром), "
        "in_transit (shipped but not yet received by WB), total_stock = wb + rf + transit, "
        "average daily sales, days left, traffic light (red/orange/yellow/green). "
        "Use for questions about inventory, restocking, stock-outs, what's available."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "search": {"type": "string", "description": "Filter by vendor code or subject (optional)"},
            "include_rf_stocks": {"type": "boolean", "description": "Include RF warehouse stocks (default: true)", "default": True},
        },
        "required": [],
    },
}

_TOOL_WAREHOUSE_NEED: dict = {
    "name": "get_warehouse_need",
    "description": (
        "Get warehouse restocking recommendations — how many units to send to each WB warehouse. "
        "Returns per-warehouse list with articles that need restocking, deficit, available RF stock. "
        "Use for: where to ship inventory, supply planning, what to send."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "supply_days": {"type": "integer", "description": "Days of supply to plan for (default: 14)", "default": 14},
        },
        "required": [],
    },
}

_TOOL_WAREHOUSE_STOCKS: dict = {
    "name": "get_warehouse_stocks",
    "description": (
        "Get current stock levels broken down by WB warehouse. "
        "Returns per-warehouse: total quantity, number of articles. "
        "Use for: stock distribution across warehouses."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_TOOL_ORDER_GEOGRAPHY: dict = {
    "name": "get_order_geography",
    "description": (
        "Get order geography — where orders are being delivered. "
        "Returns per-city/region breakdown with order counts. "
        "Use for: delivery destinations, which cities buy most."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "date_from": {"type": "string", "description": "Start date YYYY-MM-DD"},
            "date_to": {"type": "string", "description": "End date YYYY-MM-DD"},
        },
        "required": ["date_from", "date_to"],
    },
}

_TOOL_PRODUCT_INFO: dict = {
    "name": "get_product_info",
    "description": (
        "Get detailed info about a specific product by vendor code or nm_id. "
        "Returns: name, brand, subject, stocks, orders, revenue, cost price, margin."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "search": {"type": "string", "description": "Vendor code, nm_id, or product name"},
        },
        "required": ["search"],
    },
}

_TOOL_ANOMALIES: dict = {
    "name": "get_anomalies",
    "description": (
        "Detect product anomalies: loss-making hits, stock-outs, toxic ads, dead stock, low buyout. "
        "Returns prioritized list with severity, loss amount, and action plan. "
        "Use for: problems, anomalies, what's wrong, losses, risks."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "period_days": {"type": "integer", "description": "Analysis period in days (default: 7)", "default": 7},
        },
        "required": [],
    },
}

_TOOL_DAILY_HEALTH: dict = {
    "name": "get_daily_health",
    "description": (
        "Daily health check: urgent shipments (no assembly request), overdue assemblies, "
        "category A product issues, illiquid product action plan. Health score 0-100. "
        "Use for: daily review, morning briefing, what needs attention."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_TOOL_LOGISTICS_HISTORY: dict = {
    "name": "get_logistics_history",
    "description": (
        "Shipping history: assembly requests with pickup costs, pallets, weights, delivery dates. "
        "Average cost per destination warehouse. "
        "Use for: shipping costs, pallet prices, how much to ship to Электросталь/Тула/etc."
    ),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

# Combined list for backward compatibility (used by old logistician agent)
LOGISTICS_TOOLS: list[dict] = [
    _TOOL_STOCK_INFO,
    _TOOL_WAREHOUSE_NEED,
    _TOOL_WAREHOUSE_STOCKS,
    _TOOL_ORDER_GEOGRAPHY,
    _TOOL_PRODUCT_INFO,
    _TOOL_ANOMALIES,
    _TOOL_DAILY_HEALTH,
    _TOOL_LOGISTICS_HISTORY,
]
