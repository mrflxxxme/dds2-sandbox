"""
Reports domain tools: DDS report, OPIU report, BDR data, capital analysis.
"""

import json
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.ai.tools.common import _json, build_tax_info


async def get_dds_report(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
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


async def get_opiu_report(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.opiu_service import get_opiu

    date_from = date.fromisoformat(inp["date_from"])
    date_to = date.fromisoformat(inp["date_to"])
    result = await get_opiu(db, project_id, date_from, date_to, brand=brand)
    if not result:
        return json.dumps({"message": "No OPIU data for this period"})

    rows = result.get("rows", [])
    key_rows = [r for r in rows if r.get("total") and abs(float(r["total"])) > 0]
    return _json(
        {
            "period": result.get("period"),
            "rows": key_rows,
        }
    )


async def get_bdr_data(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.wb_bdr_service import get_wb_bdr

    date_from = date.fromisoformat(inp["date_from"])
    date_to = date.fromisoformat(inp["date_to"])
    article_filter = inp.get("article")

    result = await get_wb_bdr(
        db,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
        brand=brand,
        article=article_filter,
    )
    if not result:
        return json.dumps({"message": "No BDR data for this period"})

    summary = result.get("summary", {})
    articles = result.get("articles", [])

    top_articles = sorted(articles, key=lambda a: float(a.get("realization", 0)), reverse=True)
    simplified = []
    for a in top_articles:
        simplified.append(
            {
                "article": a.get("sa_name", ""),
                "brand": a.get("brand", ""),
                "realization": a.get("realization", 0),
                "commission": a.get("commission", 0),
                "logistics": a.get("logistics", 0),
                "storage": a.get("storage", 0),
                "penalties": a.get("penalties", 0),
                "adv_sum": a.get("adv_sum", 0),
                "cost_total": a.get("cost_total", 0),
                "to_pay": a.get("to_pay", 0),
                "net_profit": a.get("net_profit", 0),
                "margin_pct": a.get("margin_pct", 0),
                "sale_qty": a.get("sale_qty", 0),
                "ret_qty": a.get("ret_qty", 0),
                "buyout_pct": a.get("buyout_pct", 0),
            }
        )

    return _json(
        {
            "period": result.get("period"),
            "summary": {
                "realization": summary.get("realization", 0),
                "commission": summary.get("commission", 0),
                "logistics": summary.get("logistics", 0),
                "storage": summary.get("storage", 0),
                "penalties": summary.get("penalties", 0),
                "adv_sum": summary.get("adv_sum", 0),
                "cost_total": summary.get("cost_total", 0),
                "to_pay": summary.get("to_pay", 0),
                "net_profit": summary.get("net_profit", 0),
                "margin_pct": summary.get("margin_pct", 0),
                "tax_sum": summary.get("tax_sum", 0),
            },
            "total_articles": len(articles),
            "top_articles": simplified,
        }
    )


async def get_capital_analysis(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.funnel.capital import get_capital_analysis as _get_capital_analysis

    tax_info = build_tax_info(tax_rate)
    result = await _get_capital_analysis(
        db,
        project_id,
        tax_info,
        period_days=inp.get("period_days", 7),
        brand=brand,
        group_by=inp.get("group_by", "brand"),
        include_rf_stocks=True,
        elasticity=inp.get("elasticity", 1.8),
    )
    summary = result.get("summary", {})
    groups = result.get("groups", [])
    top_groups = sorted(groups, key=lambda g: g.get("capital", 0), reverse=True)
    simplified_groups = []
    for g in top_groups:
        simplified_groups.append(
            {
                "name": g.get("group_key", ""),
                "capital": g.get("capital", 0),
                "liquid_pct": g.get("liquid_pct", 0),
                "illiquid_pct": g.get("illiquid_pct", 0),
                "frozen_amount": g.get("frozen_amount", 0),
                "roi_monthly": g.get("roi_monthly", 0),
                "turnover_days": g.get("turnover_days", 0),
                "recommendation": g.get("recommendation", {}),
            }
        )
    return _json(
        {
            "summary": summary,
            "groups": simplified_groups,
            "total_products": result.get("total_products", 0),
        }
    )
