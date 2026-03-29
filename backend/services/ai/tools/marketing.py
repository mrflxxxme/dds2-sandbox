"""
Marketing domain tools for DDS AI agent.

Covers sales funnel analytics, product rankings, period comparisons,
and daily KPI analysis with anomaly detection. These tools answer
questions about sales performance, advertising efficiency, conversion,
best/worst sellers, and trend analysis.
"""

MARKETING_TOOLS = [
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
        "name": "get_day_analysis",
        "description": (
            "Get daily dashboard analysis with anomaly detection. "
            "Returns today/yesterday comparison, top products, 14-day trend, anomalies. "
            "Use for questions about today's metrics, daily KPIs, what changed today, anomalies."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Date to analyze in YYYY-MM-DD format (default: today)",
                },
            },
            "required": [],
        },
    },
]
