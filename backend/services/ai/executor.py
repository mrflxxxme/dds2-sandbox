"""
Tool executor — bridges Claude tool calls to DDS service functions.

Each tool call is executed with project_id + brand filtering (multi-tenant safety).
"""

import json
import logging
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("dds.ai")


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal and date types."""

    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, date):
            return o.isoformat()
        return super().default(o)


def _json(data) -> str:
    """Serialize to JSON string, truncating if too large."""
    result = json.dumps(data, cls=DecimalEncoder, ensure_ascii=False)
    if len(result) > 15000:
        result = result[:15000] + "... (truncated)"
    return result


async def execute_tool(
    db: AsyncSession,
    project_id: int,
    brand: str | None,
    tax_rate: float,
    tool_name: str,
    tool_input: dict,
) -> str:
    """Execute a tool call and return the result as a JSON string."""
    try:
        if tool_name == "get_funnel_data":
            return await _get_funnel_data(db, project_id, tax_rate, brand, tool_input)
        elif tool_name == "get_top_products":
            return await _get_top_products(db, project_id, tax_rate, brand, tool_input)
        elif tool_name == "get_cost_data":
            return await _get_cost_data(db, project_id, tool_input)
        elif tool_name == "get_dds_report":
            return await _get_dds_report(db, project_id, tool_input)
        elif tool_name == "get_opiu_report":
            return await _get_opiu_report(db, project_id, brand, tool_input)
        elif tool_name == "compare_periods":
            return await _compare_periods(db, project_id, tax_rate, brand, tool_input)
        elif tool_name == "get_product_info":
            return await _get_product_info(db, project_id, tax_rate, brand, tool_input)
        elif tool_name == "get_stock_info":
            return await _get_stock_info(db, project_id, brand, tool_input)
        else:
            return json.dumps({"error": "Unknown tool: " + tool_name})
    except Exception as e:
        logger.exception("Tool execution error: %s", tool_name)
        return json.dumps({"error": str(e)})


# ─── Tool implementations ────────────────────────────────────────────────────


async def _get_funnel_data(db, project_id, tax_rate, brand, inp):
    from backend.services.funnel.queries import get_funnel_aggregated

    data = await get_funnel_aggregated(
        db,
        project_id,
        tax_rate,
        date_from=inp.get("date_from"),
        date_to=inp.get("date_to"),
        brand=brand,
        subject=None,
    )
    # Summarize for LLM: aggregate totals + daily breakdown
    if not data:
        return json.dumps({"message": "No funnel data for this period", "data": []})

    total = {
        "days": len(data),
        "orders_count": sum(d.get("orders_count", 0) for d in data),
        "orders_sum_rub": sum(d.get("orders_sum_rub", 0) for d in data),
        "revenue": sum(d.get("revenue", 0) for d in data),
        "profit": sum(d.get("profit", 0) for d in data),
        "adv_sum": sum(d.get("adv_sum", 0) for d in data),
    }
    total_revenue = total["revenue"]
    total["margin"] = round(total["profit"] / total_revenue * 100, 1) if total_revenue else 0
    total["drr"] = round(total["adv_sum"] / total["orders_sum_rub"] * 100, 1) if total["orders_sum_rub"] else 0
    return _json({"summary": total, "daily": data[:14]})


async def _get_top_products(db, project_id, tax_rate, brand, inp):
    from backend.services.funnel.product_trends import get_product_trends

    result = await get_product_trends(
        db,
        project_id,
        tax_rate,
        trend_days=inp.get("trend_days", 7),
        brand=brand,
        search=inp.get("search"),
    )
    products = result.get("products", [])
    # Return top 15 by revenue
    top = sorted(products, key=lambda p: p.get("revenue", 0), reverse=True)[:15]
    return _json(
        {
            "total_products": result.get("total_products", len(products)),
            "trend_days": result.get("trend_days", 7),
            "top_by_revenue": top,
        }
    )


async def _get_cost_data(db, project_id, inp):
    from backend.services.funnel.queries import get_cost_overrides, get_missing_costs

    overrides = await get_cost_overrides(db, project_id)
    missing = await get_missing_costs(db, project_id)
    return _json(
        {
            "cost_overrides_count": len(overrides),
            "cost_overrides": overrides[:30],
            "missing_costs_count": len(missing),
            "missing_costs": missing[:20],
        }
    )


async def _get_dds_report(db, project_id, inp):
    from backend.services.reports.dds import get_dds_month

    data = await get_dds_month(db, project_id, inp["year"], inp["month"])
    if not data:
        return json.dumps({"message": "No DDS data for this month", "rows": []})

    total_income = sum(float(r.get("income", 0) or 0) for r in data)
    total_expense = sum(float(r.get("expense", 0) or 0) for r in data)
    return _json(
        {
            "year": inp["year"],
            "month": inp["month"],
            "total_income": round(total_income, 2),
            "total_expense": round(total_expense, 2),
            "net": round(total_income + total_expense, 2),
            "rows": data,
        }
    )


async def _get_opiu_report(db, project_id, brand, inp):
    from backend.services.opiu_service import get_opiu

    date_from = date.fromisoformat(inp["date_from"])
    date_to = date.fromisoformat(inp["date_to"])
    result = await get_opiu(db, project_id, date_from, date_to, brand=brand)
    if not result:
        return json.dumps({"message": "No OPIU data for this period"})

    # Extract key rows for LLM (skip empty ones)
    rows = result.get("rows", [])
    key_rows = [r for r in rows if r.get("total") and abs(float(r["total"])) > 0]
    return _json(
        {
            "period": result.get("period"),
            "rows": key_rows,
        }
    )


async def _compare_periods(db, project_id, tax_rate, brand, inp):
    from backend.services.funnel.queries import get_summary

    s1 = await get_summary(db, project_id, inp["period1_from"], inp["period1_to"], brand, None)
    s2 = await get_summary(db, project_id, inp["period2_from"], inp["period2_to"], brand, None)

    def _delta(a, b):
        if not a or not b:
            return None
        return round((b - a) / a * 100, 1) if a else None

    comparison = {}
    for key in ["orders_count", "orders_sum_rub", "adv_sum", "open_card", "add_to_cart"]:
        v1 = float(s1.get(key, 0) or 0)
        v2 = float(s2.get(key, 0) or 0)
        comparison[key] = {"period1": v1, "period2": v2, "delta_pct": _delta(v1, v2)}

    return _json(
        {
            "period1": {"from": inp["period1_from"], "to": inp["period1_to"]},
            "period2": {"from": inp["period2_from"], "to": inp["period2_to"]},
            "comparison": comparison,
        }
    )


async def _get_product_info(db, project_id, tax_rate, brand, inp):
    from backend.services.funnel.product_trends import get_product_trends

    result = await get_product_trends(
        db,
        project_id,
        tax_rate,
        trend_days=7,
        brand=brand,
        search=inp.get("search"),
    )
    products = result.get("products", [])
    if not products:
        return json.dumps({"message": "Product not found: " + str(inp.get("search"))})
    return _json({"products": products[:5]})


async def _get_stock_info(db, project_id, brand, inp):
    from backend.services.stock_forecast_service import get_stock_analytics

    result = await get_stock_analytics(
        db,
        project_id,
        brand_filter=brand,
        article_filter=inp.get("search"),
    )
    articles = result.get("articles", [])
    traffic = result.get("traffic_light_counts", {})

    # Top 20 by urgency (red first, then orange)
    priority = {"red": 0, "orange": 1, "yellow": 2, "green": 3}
    sorted_articles = sorted(
        articles, key=lambda a: (priority.get(a.get("traffic_light", "green"), 3), -a.get("orders_30d", 0))
    )

    return _json(
        {
            "data_date": result.get("data_date"),
            "total_articles": len(articles),
            "traffic_light_counts": traffic,
            "articles": sorted_articles[:20],
        }
    )
