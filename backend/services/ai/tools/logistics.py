"""
Logistics domain tools for DDS AI agent.

Covers stock levels, restocking recommendations, warehouse distribution,
order geography, and individual product details. These tools answer
questions about inventory, supply planning, delivery destinations,
and per-product lookup.
"""

LOGISTICS_TOOLS = [
    {
        "name": "get_stock_info",
        "description": (
            "Get stock levels and restock forecast including RF warehouses AND in-transit shipments. "
            "Returns per-product: stocks_wb (on WB), stocks_rf (RF warehouses like Натали, Газпром), "
            "in_transit (shipped but not yet received by WB), total_stock = wb + rf + transit, "
            "average daily sales, days left, traffic light (red/orange/yellow/green). "
            "Smart forecast accounts for delivery dates of in-transit shipments. "
            "Use for questions about inventory, restocking, stock-outs, what's available."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Filter by vendor code or subject (optional)",
                },
                "include_rf_stocks": {
                    "type": "boolean",
                    "description": "Include RF warehouse stocks (default: true)",
                    "default": True,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_warehouse_need",
        "description": (
            "Get warehouse restocking recommendations — how many units to send to each WB warehouse. "
            "Returns per-warehouse list with articles that need restocking. "
            "Use for questions about where to ship inventory, warehouse restocking, supply planning."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "supply_days": {
                    "type": "integer",
                    "description": "Days of supply to plan for (default: 14)",
                    "default": 14,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_warehouse_stocks",
        "description": (
            "Get current stock levels broken down by WB warehouse. "
            "Returns per-warehouse: total quantity, number of articles. "
            "Use for questions about stock distribution across warehouses."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_order_geography",
        "description": (
            "Get order geography — where orders are being delivered. "
            "Returns per-city/region breakdown with order counts. "
            "Use for questions about order geography, delivery destinations, "
            "which cities/regions buy most, regional demand."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {
                    "type": "string",
                    "description": "Start date in YYYY-MM-DD format",
                },
                "date_to": {
                    "type": "string",
                    "description": "End date in YYYY-MM-DD format",
                },
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "get_product_info",
        "description": (
            "Get detailed info about a specific product by vendor code or nm_id. "
            "Returns: name, brand, subject, stocks, orders, revenue, cost price, margin. "
            "Use when user asks about a specific product."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Vendor code, nm_id, or product name to search for",
                },
            },
            "required": ["search"],
        },
    },
    {
        "name": "get_anomalies",
        "description": (
            "Detect product anomalies: loss-making hits (margin<10%), stock-outs, "
            "toxic ads (DRR>30%), dead stock (frozen capital), low buyout. "
            "Returns prioritized list with severity, loss amount, and action plan. "
            "Includes WB + RF warehouse stocks. "
            "Use for: problems, anomalies, what's wrong, losses, risks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period_days": {
                    "type": "integer",
                    "description": "Analysis period in days (default: 7)",
                    "default": 7,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_daily_health",
        "description": (
            "Comprehensive daily health check: "
            "1) Urgent shipments — products that need to be sent to WB but have no assembly request. "
            "2) Overdue assembly requests — past delivery date. "
            "3) Category A products health — are top sellers ok? (stock, ads, margin). "
            "4) Illiquid products action plan — specific recommendations: increase ads, reduce price, or liquidate. "
            "Returns health_score (0-100) and actionable items. "
            "Use for: daily review, morning briefing, what needs attention, health status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_logistics_history",
        "description": (
            "Get shipping history: all assembly requests with pickup costs, pallets, weights, "
            "delivery dates, warehouses. Shows average shipping cost per destination warehouse. "
            "Use for: shipping costs, pallet prices, delivery history, logistics expenses, "
            "how much does it cost to ship to Электросталь/Тула/etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]
