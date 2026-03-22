"""Capital analysis — classify products by liquidity and ROI.

Calculates capital distribution (liquid / transition / illiquid),
ROI projections, and price recommendations per product group.
"""

import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.funnel.bdr_rates import BdrRatesLookup
from backend.services.funnel.product_trends import get_product_trends
from backend.services.funnel.stock_helpers import (
    load_rf_stocks,
    load_wb_stock_history,
)

logger = logging.getLogger("dds.funnel.capital")

_MIN_ORDERS = 5


def _classify_turnover(turnover_days: float) -> str:
    """Returns 'liquid', 'transition', or 'illiquid'."""
    if turnover_days < 30:
        return "liquid"
    if turnover_days <= 60:
        return "transition"
    return "illiquid"


def _calc_recommendation(
    margin: float,
    turnover: float,
    drr: float,
    buyout_pct: float,
    elasticity: float,
) -> dict:
    """Calculate price recommendation with ROI projections."""
    roi = margin * (30 / turnover) if turnover > 0 else 0.0

    # ROI at -10%
    new_margin_10 = margin - 10
    new_turnover_10 = turnover / (1 + 0.10 * elasticity) if turnover > 0 else 0.0
    roi_10 = new_margin_10 * (30 / new_turnover_10) if new_turnover_10 > 0 and new_margin_10 > 0 else 0.0

    # ROI at -20%
    new_margin_20 = margin - 20
    new_turnover_20 = turnover / (1 + 0.20 * elasticity) if turnover > 0 else 0.0
    roi_20 = new_margin_20 * (30 / new_turnover_20) if new_turnover_20 > 0 and new_margin_20 > 0 else 0.0

    base = {
        "current_roi": round(roi, 2),
        "roi_at_minus_10": round(roi_10, 2),
        "roi_at_minus_20": round(roi_20, 2),
    }

    if buyout_pct < 70:
        return {"type": "check_quality", "label": "Проверь качество", **base}
    if margin < 5 and drr > 15:
        return {"type": "cut_ads", "label": "Убери рекламу", **base}
    if turnover > 60:
        return {"type": "liquidate", "label": "Ликвидируй", **base}
    if roi > 20 and turnover < 30:
        return {"type": "star", "label": "Звезда", **base}
    if roi_10 > roi and new_margin_10 > 0:
        return {"type": "reduce_10", "label": "Снизь на 10%", **base}
    if roi_20 > roi and new_margin_20 > 0:
        return {"type": "reduce_20", "label": "Снизь на 20%", **base}
    return {"type": "hold", "label": "Держи", **base}


def _enrich_products(
    products: list[dict],
    rf_stocks_map: dict[int, int],
    include_rf: bool,
    bdr_rates_map: BdrRatesLookup | None,
    period_days: int,
    elasticity: float,
) -> list[dict]:
    """Enrich product dicts with capital metrics."""
    enriched: list[dict] = []
    for p in products:
        if p["orders_count"] < _MIN_ORDERS:
            continue

        cost = p["cost_price"]
        if cost <= 0:
            continue

        # Buyout from BDR
        buyout_pct = 100.0
        if bdr_rates_map:
            bdr = bdr_rates_map.get(p["nm_id"])
            if bdr:
                buyout_pct = round(bdr.buyout_pct * 100, 1)

        rf_qty = rf_stocks_map.get(p["nm_id"], 0) if include_rf else 0
        total_stock = p["stocks_wb"] + rf_qty
        capital = total_stock * cost

        turnover = p["turnover_days"]
        category = _classify_turnover(turnover)
        margin = p["margin"]
        drr = p["drr"]

        roi_monthly = margin * (30 / turnover) if turnover > 0 else 0.0
        sales_per_day = p["orders_count"] / period_days if period_days > 0 else 0.0

        rec = _calc_recommendation(margin, turnover, drr, buyout_pct, elasticity)

        enriched.append(
            {
                **p,
                "capital": round(capital, 2),
                "category": category,
                "roi_monthly": round(roi_monthly, 2),
                "sales_per_day": round(sales_per_day, 2),
                "buyout_pct": buyout_pct,
                "rf_qty": rf_qty,
                "total_stock": total_stock,
                "recommendation": rec,
            }
        )
    return enriched


def _group_products(
    products: list[dict],
    group_by: str,
    parent_filter: str | None,
) -> list[dict]:
    """Group products and aggregate metrics by capital weight."""
    # Apply parent filter
    if parent_filter:
        if group_by == "brand":
            products = [p for p in products if p.get("brand") == parent_filter]
        elif group_by == "category":
            products = [p for p in products if p.get("subject") == parent_filter]

    if group_by == "article":
        return _article_rows(products)

    key_field = "brand" if group_by == "brand" else "subject"
    buckets: dict[str, list[dict]] = defaultdict(list)
    for p in products:
        buckets[p.get(key_field) or "—"].append(p)

    groups: list[dict] = []
    for key, items in buckets.items():
        groups.append(_aggregate_group(key, group_by, items))

    groups.sort(key=lambda g: g["capital"], reverse=True)
    return groups


def _article_rows(products: list[dict]) -> list[dict]:
    """Each product is its own row (no grouping)."""
    rows: list[dict] = []
    for p in products:
        cap = p["capital"]
        liquid = cap if p["category"] == "liquid" else 0.0
        illiquid = cap if p["category"] == "illiquid" else 0.0
        frozen = cap if p["category"] != "liquid" else 0.0

        rows.append(
            {
                "group_key": p["vendor_code"] or str(p["nm_id"]),
                "group_type": "article",
                "nm_id": p["nm_id"],
                "vendor_code": p["vendor_code"],
                "capital": cap,
                "liquid_pct": round(liquid / cap * 100, 1) if cap > 0 else 0.0,
                "illiquid_pct": round(illiquid / cap * 100, 1) if cap > 0 else 0.0,
                "frozen_amount": round(frozen, 2),
                "roi_monthly": p["roi_monthly"],
                "turnover_days": p["turnover_days"],
                "sales_per_day": p["sales_per_day"],
                "margin": p["margin"],
                "drr": p["drr"],
                "recommendation": p["recommendation"],
                "children_count": 0,
            }
        )
    rows.sort(key=lambda r: r["capital"], reverse=True)
    return rows


def _aggregate_group(key: str, group_type: str, items: list[dict]) -> dict:
    """Aggregate a group of products — weighted averages by capital."""
    total_cap = sum(i["capital"] for i in items)
    liquid_cap = sum(i["capital"] for i in items if i["category"] == "liquid")
    illiquid_cap = sum(i["capital"] for i in items if i["category"] == "illiquid")
    frozen = total_cap - liquid_cap

    w_margin = _wavg(items, "margin", "capital")
    w_drr = _wavg(items, "drr", "capital")
    w_turnover = _wavg(items, "turnover_days", "capital")
    w_roi = _wavg(items, "roi_monthly", "capital")
    total_spd = sum(i["sales_per_day"] for i in items)

    # Group-level recommendation based on weighted averages
    avg_buyout = _wavg(items, "buyout_pct", "capital")
    rec = _calc_recommendation(w_margin, w_turnover, w_drr, avg_buyout, 1.8)

    return {
        "group_key": key,
        "group_type": group_type,
        "nm_id": None,
        "vendor_code": None,
        "capital": round(total_cap, 2),
        "liquid_pct": round(liquid_cap / total_cap * 100, 1) if total_cap > 0 else 0.0,
        "illiquid_pct": round(illiquid_cap / total_cap * 100, 1) if total_cap > 0 else 0.0,
        "frozen_amount": round(frozen, 2),
        "roi_monthly": round(w_roi, 2),
        "turnover_days": round(w_turnover, 1),
        "sales_per_day": round(total_spd, 2),
        "margin": round(w_margin, 2),
        "drr": round(w_drr, 2),
        "recommendation": rec,
        "children_count": len(items),
    }


def _wavg(items: list[dict], field: str, weight_field: str) -> float:
    """Capital-weighted average."""
    total_w = sum(i[weight_field] for i in items)
    if total_w <= 0:
        return 0.0
    return sum(i[field] * i[weight_field] for i in items) / total_w


async def _build_capital_trend(
    db: AsyncSession,
    project_id: int,
    period_days: int,
    products: list[dict],
) -> list[dict]:
    """Build daily trend from wb_stock_snapshots."""
    today = date.today()
    date_from = today - timedelta(days=period_days)

    cost_map: dict[int, float] = {p["nm_id"]: p["cost_price"] for p in products if p["cost_price"] > 0}
    rate_map: dict[int, float] = {}
    for p in products:
        spd = p.get("sales_per_day", 0)
        if spd > 0:
            rate_map[p["nm_id"]] = spd

    if not cost_map:
        return []

    history = await load_wb_stock_history(db, project_id, date_from, today)
    if not history:
        return []

    trend: list[dict] = []
    for d in sorted(history.keys()):
        day_stocks = history[d]
        liquid = 0.0
        transition = 0.0
        illiquid = 0.0
        total = 0.0
        roi_num = 0.0
        roi_den = 0.0

        for nm_id, qty in day_stocks.items():
            cost = cost_map.get(nm_id)
            if not cost or cost <= 0:
                continue

            cap = qty * cost
            total += cap

            spd = rate_map.get(nm_id, 0)
            turnover = qty / spd if spd > 0 else 999.0
            cat = _classify_turnover(turnover)

            if cat == "liquid":
                liquid += cap
            elif cat == "transition":
                transition += cap
            else:
                illiquid += cap

            # For weighted ROI
            if turnover > 0:
                roi_num += (30 / turnover) * cap
                roi_den += cap

        daily_roi = roi_num / roi_den * 100 if roi_den > 0 else 0.0

        trend.append(
            {
                "date": d.isoformat(),
                "liquid": round(liquid, 2),
                "transition": round(transition, 2),
                "illiquid": round(illiquid, 2),
                "total": round(total, 2),
                "roi_monthly": round(daily_roi, 2),
            }
        )

    return trend


def _build_summary(products: list[dict], trend: list[dict]) -> dict:
    """Build capital summary from enriched products."""
    total = sum(p["capital"] for p in products)
    liquid = sum(p["capital"] for p in products if p["category"] == "liquid")
    transition = sum(p["capital"] for p in products if p["category"] == "transition")
    illiquid = sum(p["capital"] for p in products if p["category"] == "illiquid")

    roi = _wavg(products, "roi_monthly", "capital") if products else 0.0

    # ROI trend: compare last vs first trend point
    roi_trend = 0.0
    if len(trend) >= 2:
        first_roi = trend[0]["roi_monthly"]
        last_roi = trend[-1]["roi_monthly"]
        if abs(first_roi) > 0.01:
            roi_trend = round((last_roi - first_roi) / abs(first_roi) * 100, 1)

    return {
        "total_capital": round(total, 2),
        "liquid_capital": round(liquid, 2),
        "liquid_pct": round(liquid / total * 100, 1) if total > 0 else 0.0,
        "transition_capital": round(transition, 2),
        "transition_pct": round(transition / total * 100, 1) if total > 0 else 0.0,
        "illiquid_capital": round(illiquid, 2),
        "illiquid_pct": round(illiquid / total * 100, 1) if total > 0 else 0.0,
        "roi_monthly": round(roi, 2),
        "roi_trend": roi_trend,
    }


async def get_capital_analysis(
    db: AsyncSession,
    project_id: int,
    tax_info: dict,
    period_days: int = 7,
    brand: str | None = None,
    group_by: str = "brand",
    parent_filter: str | None = None,
    include_rf_stocks: bool = False,
    elasticity: float = 1.8,
    bdr_rates_map: BdrRatesLookup | None = None,
) -> dict:
    """Run capital analysis and return structured report."""
    # 1. Get per-product data
    trends = await get_product_trends(
        db,
        project_id,
        tax_info,
        period_days,
        brand,
        bdr_rates_map=bdr_rates_map,
    )
    all_products = trends["products"]

    # 2. Load RF stocks if requested
    rf_stocks_map: dict[int, int] = {}
    if include_rf_stocks:
        try:
            rf_stocks_map = await load_rf_stocks(db, project_id)
        except Exception:
            logger.warning("Failed to load RF stocks, continuing without", exc_info=True)

    # 3. Enrich products
    enriched = _enrich_products(
        all_products,
        rf_stocks_map,
        include_rf_stocks,
        bdr_rates_map,
        period_days,
        elasticity,
    )

    # 4. Group
    groups = _group_products(enriched, group_by, parent_filter)

    # 5. Build trend
    trend = await _build_capital_trend(db, project_id, period_days, enriched)

    # 6. Summary
    summary = _build_summary(enriched, trend)

    return {
        "summary": summary,
        "trend": trend,
        "groups": groups,
        "total_products": len(enriched),
        "period_days": period_days,
        "elasticity": elasticity,
    }
