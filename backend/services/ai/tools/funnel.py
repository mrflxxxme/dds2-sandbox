"""
Funnel domain tools: funnel data, top products, day analysis, ad campaigns, period comparison.
"""

import json

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.ai.tools.common import _json, build_tax_info


async def get_funnel_data(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.funnel.queries import get_funnel_aggregated

    tax_info = build_tax_info(tax_rate)
    data = await get_funnel_aggregated(
        db,
        project_id,
        tax_info,
        date_from=inp.get("date_from"),
        date_to=inp.get("date_to"),
        brand=brand,
        subject=None,
    )
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


async def get_top_products(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.funnel.product_trends import get_product_trends

    tax_info = build_tax_info(tax_rate)
    result = await get_product_trends(
        db,
        project_id,
        tax_info,
        trend_days=inp.get("trend_days", 7),
        brand=brand,
        search=inp.get("search"),
    )
    products = result.get("products", [])
    top = sorted(products, key=lambda p: p.get("revenue", 0), reverse=True)
    return _json(
        {
            "total_products": result.get("total_products", len(products)),
            "trend_days": result.get("trend_days", 7),
            "top_by_revenue": top,
        }
    )


async def get_day_analysis(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.funnel.analysis import get_day_analysis as _get_day_analysis

    target_date = inp.get("date")
    if not target_date:
        from backend.utils.time import utcnow

        target_date = utcnow().strftime("%Y-%m-%d")

    tax_info = build_tax_info(tax_rate)
    result = await _get_day_analysis(
        db,
        project_id,
        tax_info,
        target_date=target_date,
        brand=brand,
        subject=None,
    )
    if not result:
        return json.dumps({"message": "No data for this date"})

    return _json(
        {
            "date": target_date,
            "today": result.get("today"),
            "yesterday": result.get("yesterday"),
            "comparison": result.get("comparison"),
            "anomalies": result.get("anomalies", []),
            "top_products_count": len(result.get("products", [])),
        }
    )


async def get_ad_campaigns(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.funnel.ad_campaigns_service import get_ad_tab_data

    date_from = inp.get("date_from", "")
    date_to = inp.get("date_to", "")
    if not date_from or not date_to:
        from backend.utils.time import utcnow

        now = utcnow()
        date_to = now.strftime("%Y-%m-%d")
        from datetime import timedelta

        date_from = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    data = await get_ad_tab_data(
        db,
        project_id,
        date_from=date_from,
        date_to=date_to,
        brand=brand or "",
    )
    total_spend = sum(float(p.get("adv_sum", 0) or 0) for p in data)
    total_orders = sum(float(p.get("orders_sum_rub", 0) or 0) for p in data)
    avg_drr = round(total_spend / total_orders * 100, 1) if total_orders else 0

    products = []
    for p in data:
        products.append(
            {
                "nm_id": p.get("nm_id"),
                "vendor_code": p.get("vendor_code", ""),
                "subject": p.get("subject", ""),
                "adv_sum": p.get("adv_sum", 0),
                "adv_views": p.get("adv_views", 0),
                "adv_clicks": p.get("adv_clicks", 0),
                "orders_sum_rub": p.get("orders_sum_rub", 0),
                "orders_count": p.get("orders_count", 0),
                "drr": p.get("drr", 0),
                "cpc": p.get("cpc", 0),
                "ctr": p.get("ctr", 0),
                "profit": p.get("profit", 0),
                "margin": p.get("margin", 0),
                "abc_revenue": p.get("abc_revenue", ""),
                "campaigns": p.get("campaigns", []),
            }
        )
    products.sort(key=lambda x: float(x.get("adv_sum", 0) or 0), reverse=True)

    return _json(
        {
            "period": {"date_from": date_from, "date_to": date_to},
            "summary": {
                "total_spend": total_spend,
                "total_orders_rub": total_orders,
                "avg_drr": avg_drr,
                "products_with_ads": len([p for p in products if float(p.get("adv_sum", 0) or 0) > 0]),
            },
            "products": products,
        }
    )


async def compare_periods(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
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
