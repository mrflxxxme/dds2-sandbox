"""
Finance domain tools for DDS AI agent.

Covers P&L reports (BDR, OPIU), cash flow (DDS), and cost price data.
These tools answer questions about profitability, margins, cash balance,
income/expenses, and product cost analysis.
"""

FINANCE_TOOLS = [
    {
        "name": "get_bdr_data",
        "description": (
            "Get BDR (P&L per product) from WB finance data — the MOST ACCURATE source for profit and margin. "
            "Returns per-article: realization, commission, logistics, storage, penalties, "
            "cost, advertising, tax, net profit, margin. "
            "ALWAYS use this tool instead of funnel data when asked about profit, margin, "
            "profitability, P&L per product, or financial analysis. "
            "Data comes from WB weekly reports (wb_finance_rows)."
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
                "article": {
                    "type": "string",
                    "description": "Filter by vendor code / article (optional)",
                },
            },
            "required": ["date_from", "date_to"],
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
]
