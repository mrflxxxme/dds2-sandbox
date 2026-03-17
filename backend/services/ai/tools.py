"""
Claude function-calling tool definitions for DDS AI agent.

8 tools: funnel data, top products, cost data, DDS report, OPIU report,
compare periods, product info, export to Excel.
"""

TOOLS = [
    {
        "name": "get_funnel_data",
        "description": (
            "Get WB sales funnel data for a date range. "
            "Returns daily aggregated data: orders, revenue, profit, margin, DRR, CTR, CPC. "
            "Use for questions about sales performance, advertising efficiency, conversion."
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
        "name": "get_top_products",
        "description": (
            "Get top and bottom products ranked by various metrics. "
            "Returns per-product data with trends (linear regression): "
            "orders, revenue, profit, margin, DRR, stocks, turnover days. "
            "Use for questions about best/worst sellers, product performance, stock levels."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trend_days": {
                    "type": "integer",
                    "description": "Number of days for trend analysis (default: 7)",
                    "default": 7,
                },
                "search": {
                    "type": "string",
                    "description": "Search by vendor_code or subject (optional)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_cost_data",
        "description": (
            "Get cost price data per product. "
            "Returns cost overrides and products missing cost data. "
            "Use for questions about product costs, margin analysis, profitability."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_dds_report",
        "description": (
            "Get DDS (cash flow) report for a specific month. "
            "Returns cash flows grouped by category (income/expense/net). "
            "Use for questions about cash balance, money flow, income/expenses."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Year (e.g. 2026)"},
                "month": {"type": "integer", "description": "Month (1-12)"},
            },
            "required": ["year", "month"],
        },
    },
    {
        "name": "get_opiu_report",
        "description": (
            "Get OPIU (P&L) report for a date range. "
            "Returns hierarchical P&L: revenue, direct costs (cost price, logistics, "
            "commission, penalties, storage, advertising), gross margin, EBITDA, net profit. "
            "Use for questions about profitability, margins, P&L breakdown."
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
        "name": "compare_periods",
        "description": (
            "Compare two date periods side by side. "
            "Returns funnel summary for each period and the delta. "
            "Use for week-over-week, month-over-month, or any period comparison."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period1_from": {"type": "string", "description": "Period 1 start (YYYY-MM-DD)"},
                "period1_to": {"type": "string", "description": "Period 1 end (YYYY-MM-DD)"},
                "period2_from": {"type": "string", "description": "Period 2 start (YYYY-MM-DD)"},
                "period2_to": {"type": "string", "description": "Period 2 end (YYYY-MM-DD)"},
            },
            "required": ["period1_from", "period1_to", "period2_from", "period2_to"],
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
        "name": "get_stock_info",
        "description": (
            "Get stock levels and restock forecast. "
            "Returns per-product: stocks on WB, average daily sales, days left, "
            "traffic light (red/orange/yellow/green). "
            "Use for questions about inventory, restocking, stock-outs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "Filter by vendor code or subject (optional)",
                },
            },
            "required": [],
        },
    },
]
