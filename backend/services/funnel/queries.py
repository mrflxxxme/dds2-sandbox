# ruff: noqa: RUF001
"""
Funnel queries — data aggregation and filtering.

Handles:
- Aggregated funnel data by day
- Detailed per-product data
- Summary totals
- Filter dropdowns
- Cost overrides

Profit calculation uses BDR rates (from wb_finance_rows) when available,
falling back to tariff-based calculation when BDR data is missing.
"""

import logging
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from backend.models import WbFunnelDaily
from backend.services.funnel.bdr_rates import BdrRatesLookup, compute_profit_bdr
from backend.services.tariff_service import get_avg_buyout_map, get_tariff_map

logger = logging.getLogger("dds.funnel")


def _compute_metrics_legacy(
    orders_sum: float,
    revenue: float,
    adv: float,
    tax_rate: float,
    views: int,
    clicks: int,
    orders_count: int,
    cost_total: float = 0,
    commission_rate_pct: float = 0,
) -> dict:
    """Fallback: compute funnel metrics using flat tariff rate (old formula).

    Used when BDR data is not available for an article.
    """
    commission = revenue * commission_rate_pct / 100
    tax = revenue * tax_rate / 100
    profit = revenue - adv - commission - cost_total - tax
    margin = (profit / revenue * 100) if revenue else 0
    ctr = (clicks / views * 100) if views else 0
    cpc = (adv / clicks) if clicks else 0
    cpm = (adv / views * 1000) if views else 0
    cr = (orders_count / clicks * 100) if clicks else 0
    drr = (adv / orders_sum * 100) if orders_sum else 0
    return {
        "revenue": round(revenue, 2),
        "tax": round(tax, 2),
        "profit": round(profit, 2),
        "margin": round(margin, 2),
        "ctr": round(ctr, 2),
        "cpc": round(cpc, 2),
        "cpm": round(cpm, 2),
        "cr": round(cr, 2),
        "drr": round(drr, 2),
        "commission": round(commission, 2),
        "commission_rate": round(commission_rate_pct, 2),
        "cost_total": round(cost_total, 2),
        "has_bdr": False,
        "spp_rate": 0,
        "to_pay_rate": 0,
    }


def _traffic_metrics(
    orders_sum: float,
    adv: float,
    views: int,
    clicks: int,
    orders_count: int,
) -> dict:
    """Compute traffic metrics (CTR, CPC, CPM, CR, DRR)."""
    return {
        "ctr": round((clicks / views * 100) if views else 0, 2),
        "cpc": round((adv / clicks) if clicks else 0, 2),
        "cpm": round((adv / views * 1000) if views else 0, 2),
        "cr": round((orders_count / clicks * 100) if clicks else 0, 2),
        "drr": round((adv / orders_sum * 100) if orders_sum else 0, 2),
    }


async def get_funnel_aggregated(
    db: AsyncSession,
    pid: int,
    tax_info: dict,
    date_from: str | None,
    date_to: str | None,
    brand: str | None,
    subject: str | None,
    bdr_rates_map: BdrRatesLookup | None = None,
    nm_ids: set[int] | None = None,
) -> list[dict]:
    """Get funnel data aggregated by day.

    Uses BDR rates for profit calculation when available.
    Falls back to tariff-based calculation otherwise.
    """
    q = select(WbFunnelDaily).where(WbFunnelDaily.project_id == pid)
    if nm_ids is not None:
        q = q.where(WbFunnelDaily.nm_id.in_(nm_ids))

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)

    q = q.order_by(WbFunnelDaily.date.desc())
    result = await db.execute(q)
    rows = result.scalars().all()

    # Load maps
    tariff_map = await get_tariff_map(db, pid)
    buyout_map = await get_avg_buyout_map(db, pid)
    tax_rate = tax_info.get("usn_rate", 0) + tax_info.get("nds_rate", 0)  # legacy fallback

    has_bdr = bool(bdr_rates_map)

    # Aggregate raw rows into per-day totals
    from collections import defaultdict

    day_agg: dict = defaultdict(
        lambda: {
            "open_card": 0,
            "add_to_cart": 0,
            "orders_count": 0,
            "orders_sum_rub": 0.0,
            "revenue": 0.0,
            "adv_sum": 0.0,
            "adv_views": 0,
            "adv_clicks": 0,
            "cost_total": 0.0,
            "commission": 0.0,
            "tax": 0.0,
            "profit": 0.0,
            "has_tariff_gaps": False,
            "has_bdr": has_bdr,
            "cart_to_order_pcts": [],
            "add_to_cart_pcts": [],
            "avg_prices": [],
            # BDR aggregate fields
            "bdr_revenue": 0.0,
            "bdr_profit": 0.0,
            "bdr_tax": 0.0,
            "bdr_commission": 0.0,
            "bdr_cost_total": 0.0,
            # Weighted SPP/to_pay accumulators (weight = orders_sum)
            "w_spp_sum": 0.0,
            "w_topay_sum": 0.0,
            "w_total": 0.0,
            # Legacy aggregate fields
            "leg_revenue": 0.0,
            "leg_commission": 0.0,
        }
    )

    for r in rows:
        d_key = r.date.isoformat()
        agg = day_agg[d_key]
        agg["date"] = r.date

        orders_sum = float(r.orders_sum_rub or 0)
        nm_id = r.nm_id
        cost_per_unit = float(r.cost_price or 0)
        orders_count = int(r.orders_count or 0)
        adv = float(r.adv_sum or 0)

        agg["open_card"] += int(r.open_card or 0)
        agg["add_to_cart"] += int(r.add_to_cart or 0)
        agg["orders_count"] += orders_count
        agg["orders_sum_rub"] += orders_sum
        agg["adv_sum"] += adv
        agg["adv_views"] += int(r.adv_views or 0)
        agg["adv_clicks"] += int(r.adv_clicks or 0)

        if r.cart_to_order_pct:
            agg["cart_to_order_pcts"].append(float(r.cart_to_order_pct))
        if r.add_to_cart_pct:
            agg["add_to_cart_pcts"].append(float(r.add_to_cart_pct))
        if r.avg_price:
            agg["avg_prices"].append(float(r.avg_price))

        bdr = bdr_rates_map.get(nm_id, r.date) if bdr_rates_map else None
        if bdr:
            m = compute_profit_bdr(orders_sum, orders_count, adv, cost_per_unit, bdr, tax_info)
            agg["bdr_revenue"] += m["revenue"]
            agg["bdr_profit"] += m["profit"]
            agg["bdr_tax"] += m["tax"]
            agg["bdr_commission"] += m["commission"]
            agg["bdr_cost_total"] += m["cost_total"]
            # Weighted average for SPP and to_pay_rate
            agg["w_spp_sum"] += bdr.spp_rate * orders_sum
            agg["w_topay_sum"] += bdr.to_pay_rate * orders_sum
            agg["w_total"] += orders_sum
        else:
            buyout_pct = buyout_map.get(nm_id, 100)
            revenue = orders_sum * buyout_pct / 100
            subj = r.subject or ""
            rate = tariff_map.get(subj, 0)
            if rate == 0 and revenue > 0:
                agg["has_tariff_gaps"] = True
            agg["leg_revenue"] += revenue
            agg["leg_commission"] += revenue * rate / 100
            agg["cost_total"] += cost_per_unit * orders_count * buyout_pct / 100

    data = []
    for d_key in sorted(day_agg.keys(), reverse=True):
        agg = day_agg[d_key]
        orders_sum = agg["orders_sum_rub"]
        adv = agg["adv_sum"]
        views = agg["adv_views"]
        clicks = agg["adv_clicks"]
        orders_count = agg["orders_count"]

        # Combine BDR + legacy parts
        revenue = agg["bdr_revenue"] + agg["leg_revenue"]
        commission = agg["bdr_commission"] + agg["leg_commission"]
        cost_total = agg["bdr_cost_total"] + agg["cost_total"]

        # Tax: BDR part already computed; legacy part uses flat rate
        leg_tax = agg["leg_revenue"] * tax_rate / 100
        tax = agg["bdr_tax"] + leg_tax

        profit = (
            agg["bdr_profit"]
            + (
                agg["leg_revenue"]
                - adv * (agg["leg_revenue"] / revenue if revenue else 0)
                - agg["leg_commission"]
                - agg["cost_total"]
                - leg_tax
            )
            if revenue
            else 0
        )

        margin = (profit / revenue * 100) if revenue else 0

        buyout_pct = (revenue / orders_sum * 100) if orders_sum else 0

        traffic = _traffic_metrics(orders_sum, adv, views, clicks, orders_count)

        avg_cart_to_order = (
            sum(agg["cart_to_order_pcts"]) / len(agg["cart_to_order_pcts"]) if agg["cart_to_order_pcts"] else 0
        )
        avg_add_to_cart = sum(agg["add_to_cart_pcts"]) / len(agg["add_to_cart_pcts"]) if agg["add_to_cart_pcts"] else 0
        avg_price = sum(agg["avg_prices"]) / len(agg["avg_prices"]) if agg["avg_prices"] else 0

        data.append(
            {
                "date": d_key,
                "open_card": agg["open_card"],
                "add_to_cart": agg["add_to_cart"],
                "orders_count": orders_count,
                "orders_sum_rub": round(orders_sum, 2),
                "buyout_percent": round(buyout_pct, 2),
                "revenue": round(revenue, 2),
                "adv_sum": round(adv, 2),
                "adv_views": views,
                "adv_clicks": clicks,
                "avg_price": round(avg_price, 2),
                "add_to_cart_pct": round(avg_add_to_cart, 2),
                "cart_to_order_pct": round(avg_cart_to_order, 2),
                "tax": round(tax, 2),
                "profit": round(profit, 2),
                "margin": round(margin, 2),
                "commission": round(commission, 2),
                "commission_rate": round((commission / revenue * 100) if revenue else 0, 2),
                "cost_total": round(cost_total, 2),
                "spp_rate": round(agg["w_spp_sum"] / agg["w_total"] * 100, 2) if agg["w_total"] > 0 else 0,
                "to_pay_rate": round(agg["w_topay_sum"] / agg["w_total"] * 100, 2) if agg["w_total"] > 0 else 0,
                "has_tariff_gaps": agg["has_tariff_gaps"],
                "has_bdr": agg["has_bdr"],
                **traffic,
            }
        )

    return data


async def get_funnel_by_sku(
    db: AsyncSession,
    pid: int,
    tax_info: dict,
    date_from: str | None,
    date_to: str | None,
    brand: str | None,
    subject: str | None,
    bdr_rates_map: BdrRatesLookup | None = None,
    limit: int = 500,
    nm_ids: set[int] | None = None,
) -> list[dict]:
    """Get funnel data aggregated by SKU (nm_id) for the entire period.

    Same profit logic as get_funnel_aggregated but grouped by product instead of day.
    """
    q = select(WbFunnelDaily).where(WbFunnelDaily.project_id == pid)
    if nm_ids is not None:
        q = q.where(WbFunnelDaily.nm_id.in_(nm_ids))

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)

    q = q.order_by(WbFunnelDaily.date.desc())
    result = await db.execute(q)
    rows = result.scalars().all()

    tariff_map = await get_tariff_map(db, pid)
    buyout_map = await get_avg_buyout_map(db, pid)
    tax_rate = tax_info.get("usn_rate", 0) + tax_info.get("nds_rate", 0)
    has_bdr = bool(bdr_rates_map)

    from collections import defaultdict

    sku_agg: dict = defaultdict(
        lambda: {
            "nm_id": 0,
            "vendor_code": "",
            "brand": "",
            "subject": "",
            "max_date": None,
            "open_card": 0,
            "add_to_cart": 0,
            "orders_count": 0,
            "orders_sum_rub": 0.0,
            "revenue": 0.0,
            "adv_sum": 0.0,
            "adv_views": 0,
            "adv_clicks": 0,
            "cost_total": 0.0,
            "commission": 0.0,
            "tax": 0.0,
            "profit": 0.0,
            "has_tariff_gaps": False,
            "has_bdr": has_bdr,
            "cart_to_order_pcts": [],
            "add_to_cart_pcts": [],
            "avg_prices": [],
            "bdr_revenue": 0.0,
            "bdr_profit": 0.0,
            "bdr_tax": 0.0,
            "bdr_commission": 0.0,
            "bdr_cost_total": 0.0,
            "w_spp_sum": 0.0,
            "w_topay_sum": 0.0,
            "w_total": 0.0,
            "leg_revenue": 0.0,
            "leg_commission": 0.0,
        }
    )

    for r in rows:
        nm_id = r.nm_id
        agg = sku_agg[nm_id]

        # Keep latest product info (rows ordered by date desc)
        if agg["nm_id"] == 0:
            agg["nm_id"] = nm_id
            agg["vendor_code"] = r.vendor_code or ""
            agg["brand"] = r.brand or ""
            agg["subject"] = r.subject or ""

        orders_sum = float(r.orders_sum_rub or 0)
        cost_per_unit = float(r.cost_price or 0)
        orders_count = int(r.orders_count or 0)
        adv = float(r.adv_sum or 0)

        agg["open_card"] += int(r.open_card or 0)
        agg["add_to_cart"] += int(r.add_to_cart or 0)
        agg["orders_count"] += orders_count
        agg["orders_sum_rub"] += orders_sum
        agg["adv_sum"] += adv
        agg["adv_views"] += int(r.adv_views or 0)
        agg["adv_clicks"] += int(r.adv_clicks or 0)

        if r.cart_to_order_pct:
            agg["cart_to_order_pcts"].append(float(r.cart_to_order_pct))
        if r.add_to_cart_pct:
            agg["add_to_cart_pcts"].append(float(r.add_to_cart_pct))
        if r.avg_price:
            agg["avg_prices"].append(float(r.avg_price))

        bdr = bdr_rates_map.get(nm_id, r.date) if bdr_rates_map else None
        if bdr:
            m = compute_profit_bdr(orders_sum, orders_count, adv, cost_per_unit, bdr, tax_info)
            agg["bdr_revenue"] += m["revenue"]
            agg["bdr_profit"] += m["profit"]
            agg["bdr_tax"] += m["tax"]
            agg["bdr_commission"] += m["commission"]
            agg["bdr_cost_total"] += m["cost_total"]
            agg["w_spp_sum"] += bdr.spp_rate * orders_sum
            agg["w_topay_sum"] += bdr.to_pay_rate * orders_sum
            agg["w_total"] += orders_sum
        else:
            buyout_pct = buyout_map.get(nm_id, 100)
            revenue = orders_sum * buyout_pct / 100
            subj = r.subject or ""
            rate = tariff_map.get(subj, 0)
            if rate == 0 and revenue > 0:
                agg["has_tariff_gaps"] = True
            agg["leg_revenue"] += revenue
            agg["leg_commission"] += revenue * rate / 100
            agg["cost_total"] += cost_per_unit * orders_count * buyout_pct / 100

    data = []
    for _nm_id, agg in sku_agg.items():
        orders_sum = agg["orders_sum_rub"]
        adv = agg["adv_sum"]
        views = agg["adv_views"]
        clicks = agg["adv_clicks"]
        orders_count = agg["orders_count"]

        revenue = agg["bdr_revenue"] + agg["leg_revenue"]
        commission = agg["bdr_commission"] + agg["leg_commission"]
        cost_total = agg["bdr_cost_total"] + agg["cost_total"]

        leg_tax = agg["leg_revenue"] * tax_rate / 100
        tax = agg["bdr_tax"] + leg_tax

        profit = (
            agg["bdr_profit"]
            + (
                agg["leg_revenue"]
                - adv * (agg["leg_revenue"] / revenue if revenue else 0)
                - agg["leg_commission"]
                - agg["cost_total"]
                - leg_tax
            )
            if revenue
            else 0
        )

        margin = (profit / revenue * 100) if revenue else 0
        buyout_pct = (revenue / orders_sum * 100) if orders_sum else 0
        traffic = _traffic_metrics(orders_sum, adv, views, clicks, orders_count)

        avg_cart_to_order = (
            sum(agg["cart_to_order_pcts"]) / len(agg["cart_to_order_pcts"]) if agg["cart_to_order_pcts"] else 0
        )
        avg_add_to_cart = sum(agg["add_to_cart_pcts"]) / len(agg["add_to_cart_pcts"]) if agg["add_to_cart_pcts"] else 0
        avg_price = sum(agg["avg_prices"]) / len(agg["avg_prices"]) if agg["avg_prices"] else 0

        data.append(
            {
                "nm_id": agg["nm_id"],
                "vendor_code": agg["vendor_code"],
                "brand": agg["brand"],
                "subject": agg["subject"],
                "open_card": agg["open_card"],
                "add_to_cart": agg["add_to_cart"],
                "orders_count": orders_count,
                "orders_sum_rub": round(orders_sum, 2),
                "buyout_percent": round(buyout_pct, 2),
                "revenue": round(revenue, 2),
                "adv_sum": round(adv, 2),
                "adv_views": views,
                "adv_clicks": clicks,
                "avg_price": round(avg_price, 2),
                "add_to_cart_pct": round(avg_add_to_cart, 2),
                "cart_to_order_pct": round(avg_cart_to_order, 2),
                "tax": round(tax, 2),
                "profit": round(profit, 2),
                "margin": round(margin, 2),
                "commission": round(commission, 2),
                "commission_rate": round((commission / revenue * 100) if revenue else 0, 2),
                "cost_total": round(cost_total, 2),
                "spp_rate": round(agg["w_spp_sum"] / agg["w_total"] * 100, 2) if agg["w_total"] > 0 else 0,
                "to_pay_rate": round(agg["w_topay_sum"] / agg["w_total"] * 100, 2) if agg["w_total"] > 0 else 0,
                "has_tariff_gaps": agg["has_tariff_gaps"],
                "has_bdr": agg["has_bdr"],
                **traffic,
            }
        )

    # Sort by orders_sum descending, limit
    # Filter out groups with no orders and no ads
    data = [d for d in data if d["orders_sum_rub"] > 0 or d["adv_sum"] > 0]
    data.sort(key=lambda x: x["orders_sum_rub"], reverse=True)
    return data[:limit]


async def get_funnel_abc_analysis(
    db: AsyncSession,
    pid: int,
    tax_info: dict,
    date_from: str | None,
    date_to: str | None,
    brand: str | None,
    subject: str | None,
    bdr_rates_map: BdrRatesLookup | None = None,
    limit: int = 500,
) -> list[dict]:
    """ABC analysis — group by nm_id, compute revenue/profit from BDR, assign ABC categories.

    Uses the same _assign_abc logic as ad_campaigns_service.
    Returns simplified rows: ABC badges, revenue, profit, margin, orders, ads, DRR.
    """
    from backend.services.funnel.ad_campaigns_service import _assign_abc

    q = select(WbFunnelDaily).where(WbFunnelDaily.project_id == pid)

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)

    q = q.order_by(WbFunnelDaily.date.desc())
    result = await db.execute(q)
    rows = result.scalars().all()

    tariff_map = await get_tariff_map(db, pid)
    buyout_map = await get_avg_buyout_map(db, pid)
    tax_rate = tax_info.get("usn_rate", 0) + tax_info.get("nds_rate", 0)

    from collections import defaultdict

    sku_agg: dict = defaultdict(
        lambda: {
            "nm_id": 0,
            "vendor_code": "",
            "brand": "",
            "subject": "",
            "orders_count": 0,
            "orders_sum": 0.0,
            "adv_sum": 0.0,
            "adv_views": 0,
            "adv_clicks": 0,
            "bdr_revenue": 0.0,
            "bdr_profit": 0.0,
            "leg_revenue": 0.0,
            "leg_profit": 0.0,
        }
    )

    for r in rows:
        nm_id = r.nm_id
        agg = sku_agg[nm_id]

        if agg["nm_id"] == 0:
            agg["nm_id"] = nm_id
            agg["vendor_code"] = r.vendor_code or ""
            agg["brand"] = r.brand or ""
            agg["subject"] = r.subject or ""

        orders_sum = float(r.orders_sum_rub or 0)
        cost_per_unit = float(r.cost_price or 0)
        orders_count = int(r.orders_count or 0)
        adv = float(r.adv_sum or 0)

        agg["orders_count"] += orders_count
        agg["orders_sum"] += orders_sum
        agg["adv_sum"] += adv
        agg["adv_views"] += int(r.adv_views or 0)
        agg["adv_clicks"] += int(r.adv_clicks or 0)

        bdr = bdr_rates_map.get(nm_id, r.date) if bdr_rates_map else None
        if bdr:
            m = compute_profit_bdr(orders_sum, orders_count, adv, cost_per_unit, bdr, tax_info)
            agg["bdr_revenue"] += m["revenue"]
            agg["bdr_profit"] += m["profit"]
        else:
            buyout_pct = buyout_map.get(nm_id, 100)
            revenue = orders_sum * buyout_pct / 100
            subj = r.subject or ""
            rate = tariff_map.get(subj, 0)
            commission = revenue * rate / 100
            cost_total = cost_per_unit * orders_count * buyout_pct / 100
            tax = revenue * tax_rate / 100
            leg_profit = revenue - adv - commission - cost_total - tax
            agg["leg_revenue"] += revenue
            agg["leg_profit"] += leg_profit

    data = []
    for _nm_id, agg in sku_agg.items():
        revenue = agg["bdr_revenue"] + agg["leg_revenue"]
        profit = agg["bdr_profit"] + agg["leg_profit"]
        orders_sum = agg["orders_sum"]
        adv = agg["adv_sum"]

        margin_pct = (profit / revenue * 100) if revenue else 0
        drr = (adv / orders_sum * 100) if orders_sum else 0

        data.append(
            {
                "nm_id": agg["nm_id"],
                "vendor_code": agg["vendor_code"],
                "subject": agg["subject"],
                "brand": agg["brand"],
                "orders_sum": round(orders_sum, 2),
                "orders_count": agg["orders_count"],
                "adv_sum": round(adv, 2),
                "adv_views": agg["adv_views"],
                "adv_clicks": agg["adv_clicks"],
                "revenue": round(revenue, 2),
                "profit": round(profit, 2),
                "abc_revenue": "C",
                "abc_profit": "C",
                "drr": round(drr, 2),
                "margin_pct": round(margin_pct, 2),
            }
        )

    # ABC classification
    _assign_abc(data, "revenue", "abc_revenue")
    _assign_abc(data, "profit", "abc_profit")

    # Sort by revenue descending
    data.sort(key=lambda x: x["revenue"], reverse=True)
    return data[:limit]


async def get_funnel_by_brand(
    db: AsyncSession,
    pid: int,
    tax_info: dict,
    date_from: str | None,
    date_to: str | None,
    brand: str | None,
    subject: str | None,
    bdr_rates_map: BdrRatesLookup | None = None,
    limit: int = 500,
    nm_ids: set[int] | None = None,
) -> list[dict]:
    """Get funnel data aggregated by brand for the entire period.

    Same profit logic as get_funnel_by_sku but grouped by brand.
    """
    q = select(WbFunnelDaily).where(WbFunnelDaily.project_id == pid)
    if nm_ids is not None:
        q = q.where(WbFunnelDaily.nm_id.in_(nm_ids))

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)

    q = q.order_by(WbFunnelDaily.date.desc())
    result = await db.execute(q)
    rows = result.scalars().all()

    tariff_map = await get_tariff_map(db, pid)
    buyout_map = await get_avg_buyout_map(db, pid)
    tax_rate = tax_info.get("usn_rate", 0) + tax_info.get("nds_rate", 0)
    has_bdr = bool(bdr_rates_map)

    from collections import defaultdict

    grp_agg: dict = defaultdict(
        lambda: {
            "brand": "",
            "open_card": 0,
            "add_to_cart": 0,
            "orders_count": 0,
            "orders_sum_rub": 0.0,
            "revenue": 0.0,
            "adv_sum": 0.0,
            "adv_views": 0,
            "adv_clicks": 0,
            "cost_total": 0.0,
            "commission": 0.0,
            "tax": 0.0,
            "profit": 0.0,
            "has_tariff_gaps": False,
            "has_bdr": has_bdr,
            "cart_to_order_pcts": [],
            "add_to_cart_pcts": [],
            "avg_prices": [],
            "bdr_revenue": 0.0,
            "bdr_profit": 0.0,
            "bdr_tax": 0.0,
            "bdr_commission": 0.0,
            "bdr_cost_total": 0.0,
            "w_spp_sum": 0.0,
            "w_topay_sum": 0.0,
            "w_total": 0.0,
            "leg_revenue": 0.0,
            "leg_commission": 0.0,
        }
    )

    for r in rows:
        grp_key = r.brand or "Без бренда"
        nm_id = r.nm_id
        agg = grp_agg[grp_key]

        if not agg["brand"]:
            agg["brand"] = grp_key

        orders_sum = float(r.orders_sum_rub or 0)
        cost_per_unit = float(r.cost_price or 0)
        orders_count = int(r.orders_count or 0)
        adv = float(r.adv_sum or 0)

        agg["open_card"] += int(r.open_card or 0)
        agg["add_to_cart"] += int(r.add_to_cart or 0)
        agg["orders_count"] += orders_count
        agg["orders_sum_rub"] += orders_sum
        agg["adv_sum"] += adv
        agg["adv_views"] += int(r.adv_views or 0)
        agg["adv_clicks"] += int(r.adv_clicks or 0)

        if r.cart_to_order_pct:
            agg["cart_to_order_pcts"].append(float(r.cart_to_order_pct))
        if r.add_to_cart_pct:
            agg["add_to_cart_pcts"].append(float(r.add_to_cart_pct))
        if r.avg_price:
            agg["avg_prices"].append(float(r.avg_price))

        bdr = bdr_rates_map.get(nm_id, r.date) if bdr_rates_map else None
        if bdr:
            m = compute_profit_bdr(orders_sum, orders_count, adv, cost_per_unit, bdr, tax_info)
            agg["bdr_revenue"] += m["revenue"]
            agg["bdr_profit"] += m["profit"]
            agg["bdr_tax"] += m["tax"]
            agg["bdr_commission"] += m["commission"]
            agg["bdr_cost_total"] += m["cost_total"]
            agg["w_spp_sum"] += bdr.spp_rate * orders_sum
            agg["w_topay_sum"] += bdr.to_pay_rate * orders_sum
            agg["w_total"] += orders_sum
        else:
            buyout_pct = buyout_map.get(nm_id, 100)
            revenue = orders_sum * buyout_pct / 100
            subj = r.subject or ""
            rate = tariff_map.get(subj, 0)
            if rate == 0 and revenue > 0:
                agg["has_tariff_gaps"] = True
            agg["leg_revenue"] += revenue
            agg["leg_commission"] += revenue * rate / 100
            agg["cost_total"] += cost_per_unit * orders_count * buyout_pct / 100

    data = []
    for _key, agg in grp_agg.items():
        orders_sum = agg["orders_sum_rub"]
        adv = agg["adv_sum"]
        views = agg["adv_views"]
        clicks = agg["adv_clicks"]
        orders_count = agg["orders_count"]

        revenue = agg["bdr_revenue"] + agg["leg_revenue"]
        commission = agg["bdr_commission"] + agg["leg_commission"]
        cost_total = agg["bdr_cost_total"] + agg["cost_total"]

        leg_tax = agg["leg_revenue"] * tax_rate / 100
        tax = agg["bdr_tax"] + leg_tax

        profit = (
            agg["bdr_profit"]
            + (
                agg["leg_revenue"]
                - adv * (agg["leg_revenue"] / revenue if revenue else 0)
                - agg["leg_commission"]
                - agg["cost_total"]
                - leg_tax
            )
            if revenue
            else 0
        )

        margin = (profit / revenue * 100) if revenue else 0
        buyout_pct = (revenue / orders_sum * 100) if orders_sum else 0
        traffic = _traffic_metrics(orders_sum, adv, views, clicks, orders_count)

        avg_cart_to_order = (
            sum(agg["cart_to_order_pcts"]) / len(agg["cart_to_order_pcts"]) if agg["cart_to_order_pcts"] else 0
        )
        avg_add_to_cart = sum(agg["add_to_cart_pcts"]) / len(agg["add_to_cart_pcts"]) if agg["add_to_cart_pcts"] else 0
        avg_price = sum(agg["avg_prices"]) / len(agg["avg_prices"]) if agg["avg_prices"] else 0

        data.append(
            {
                "brand": agg["brand"],
                "open_card": agg["open_card"],
                "add_to_cart": agg["add_to_cart"],
                "orders_count": orders_count,
                "orders_sum_rub": round(orders_sum, 2),
                "buyout_percent": round(buyout_pct, 2),
                "revenue": round(revenue, 2),
                "adv_sum": round(adv, 2),
                "adv_views": views,
                "adv_clicks": clicks,
                "avg_price": round(avg_price, 2),
                "add_to_cart_pct": round(avg_add_to_cart, 2),
                "cart_to_order_pct": round(avg_cart_to_order, 2),
                "tax": round(tax, 2),
                "profit": round(profit, 2),
                "margin": round(margin, 2),
                "commission": round(commission, 2),
                "commission_rate": round((commission / revenue * 100) if revenue else 0, 2),
                "cost_total": round(cost_total, 2),
                "spp_rate": round(agg["w_spp_sum"] / agg["w_total"] * 100, 2) if agg["w_total"] > 0 else 0,
                "to_pay_rate": round(agg["w_topay_sum"] / agg["w_total"] * 100, 2) if agg["w_total"] > 0 else 0,
                "has_tariff_gaps": agg["has_tariff_gaps"],
                "has_bdr": agg["has_bdr"],
                **traffic,
            }
        )

    # Filter out groups with no orders and no ads
    data = [d for d in data if d["orders_sum_rub"] > 0 or d["adv_sum"] > 0]
    data.sort(key=lambda x: x["orders_sum_rub"], reverse=True)
    return data[:limit]


async def get_funnel_by_subject(
    db: AsyncSession,
    pid: int,
    tax_info: dict,
    date_from: str | None,
    date_to: str | None,
    brand: str | None,
    subject: str | None,
    bdr_rates_map: BdrRatesLookup | None = None,
    limit: int = 500,
    nm_ids: set[int] | None = None,
) -> list[dict]:
    """Get funnel data aggregated by subject (category) for the entire period.

    Same profit logic as get_funnel_by_sku but grouped by subject.
    """
    q = select(WbFunnelDaily).where(WbFunnelDaily.project_id == pid)
    if nm_ids is not None:
        q = q.where(WbFunnelDaily.nm_id.in_(nm_ids))

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)

    q = q.order_by(WbFunnelDaily.date.desc())
    result = await db.execute(q)
    rows = result.scalars().all()

    tariff_map = await get_tariff_map(db, pid)
    buyout_map = await get_avg_buyout_map(db, pid)
    tax_rate = tax_info.get("usn_rate", 0) + tax_info.get("nds_rate", 0)
    has_bdr = bool(bdr_rates_map)

    from collections import defaultdict

    grp_agg: dict = defaultdict(
        lambda: {
            "subject": "",
            "open_card": 0,
            "add_to_cart": 0,
            "orders_count": 0,
            "orders_sum_rub": 0.0,
            "revenue": 0.0,
            "adv_sum": 0.0,
            "adv_views": 0,
            "adv_clicks": 0,
            "cost_total": 0.0,
            "commission": 0.0,
            "tax": 0.0,
            "profit": 0.0,
            "has_tariff_gaps": False,
            "has_bdr": has_bdr,
            "cart_to_order_pcts": [],
            "add_to_cart_pcts": [],
            "avg_prices": [],
            "bdr_revenue": 0.0,
            "bdr_profit": 0.0,
            "bdr_tax": 0.0,
            "bdr_commission": 0.0,
            "bdr_cost_total": 0.0,
            "w_spp_sum": 0.0,
            "w_topay_sum": 0.0,
            "w_total": 0.0,
            "leg_revenue": 0.0,
            "leg_commission": 0.0,
        }
    )

    for r in rows:
        grp_key = r.subject or "Без категории"
        nm_id = r.nm_id
        agg = grp_agg[grp_key]

        if not agg["subject"]:
            agg["subject"] = grp_key

        orders_sum = float(r.orders_sum_rub or 0)
        cost_per_unit = float(r.cost_price or 0)
        orders_count = int(r.orders_count or 0)
        adv = float(r.adv_sum or 0)

        agg["open_card"] += int(r.open_card or 0)
        agg["add_to_cart"] += int(r.add_to_cart or 0)
        agg["orders_count"] += orders_count
        agg["orders_sum_rub"] += orders_sum
        agg["adv_sum"] += adv
        agg["adv_views"] += int(r.adv_views or 0)
        agg["adv_clicks"] += int(r.adv_clicks or 0)

        if r.cart_to_order_pct:
            agg["cart_to_order_pcts"].append(float(r.cart_to_order_pct))
        if r.add_to_cart_pct:
            agg["add_to_cart_pcts"].append(float(r.add_to_cart_pct))
        if r.avg_price:
            agg["avg_prices"].append(float(r.avg_price))

        bdr = bdr_rates_map.get(nm_id, r.date) if bdr_rates_map else None
        if bdr:
            m = compute_profit_bdr(orders_sum, orders_count, adv, cost_per_unit, bdr, tax_info)
            agg["bdr_revenue"] += m["revenue"]
            agg["bdr_profit"] += m["profit"]
            agg["bdr_tax"] += m["tax"]
            agg["bdr_commission"] += m["commission"]
            agg["bdr_cost_total"] += m["cost_total"]
            agg["w_spp_sum"] += bdr.spp_rate * orders_sum
            agg["w_topay_sum"] += bdr.to_pay_rate * orders_sum
            agg["w_total"] += orders_sum
        else:
            buyout_pct = buyout_map.get(nm_id, 100)
            revenue = orders_sum * buyout_pct / 100
            subj = r.subject or ""
            rate = tariff_map.get(subj, 0)
            if rate == 0 and revenue > 0:
                agg["has_tariff_gaps"] = True
            agg["leg_revenue"] += revenue
            agg["leg_commission"] += revenue * rate / 100
            agg["cost_total"] += cost_per_unit * orders_count * buyout_pct / 100

    data = []
    for _key, agg in grp_agg.items():
        orders_sum = agg["orders_sum_rub"]
        adv = agg["adv_sum"]
        views = agg["adv_views"]
        clicks = agg["adv_clicks"]
        orders_count = agg["orders_count"]

        revenue = agg["bdr_revenue"] + agg["leg_revenue"]
        commission = agg["bdr_commission"] + agg["leg_commission"]
        cost_total = agg["bdr_cost_total"] + agg["cost_total"]

        leg_tax = agg["leg_revenue"] * tax_rate / 100
        tax = agg["bdr_tax"] + leg_tax

        profit = (
            agg["bdr_profit"]
            + (
                agg["leg_revenue"]
                - adv * (agg["leg_revenue"] / revenue if revenue else 0)
                - agg["leg_commission"]
                - agg["cost_total"]
                - leg_tax
            )
            if revenue
            else 0
        )

        margin = (profit / revenue * 100) if revenue else 0
        buyout_pct = (revenue / orders_sum * 100) if orders_sum else 0
        traffic = _traffic_metrics(orders_sum, adv, views, clicks, orders_count)

        avg_cart_to_order = (
            sum(agg["cart_to_order_pcts"]) / len(agg["cart_to_order_pcts"]) if agg["cart_to_order_pcts"] else 0
        )
        avg_add_to_cart = sum(agg["add_to_cart_pcts"]) / len(agg["add_to_cart_pcts"]) if agg["add_to_cart_pcts"] else 0
        avg_price = sum(agg["avg_prices"]) / len(agg["avg_prices"]) if agg["avg_prices"] else 0

        data.append(
            {
                "subject": agg["subject"],
                "open_card": agg["open_card"],
                "add_to_cart": agg["add_to_cart"],
                "orders_count": orders_count,
                "orders_sum_rub": round(orders_sum, 2),
                "buyout_percent": round(buyout_pct, 2),
                "revenue": round(revenue, 2),
                "adv_sum": round(adv, 2),
                "adv_views": views,
                "adv_clicks": clicks,
                "avg_price": round(avg_price, 2),
                "add_to_cart_pct": round(avg_add_to_cart, 2),
                "cart_to_order_pct": round(avg_cart_to_order, 2),
                "tax": round(tax, 2),
                "profit": round(profit, 2),
                "margin": round(margin, 2),
                "commission": round(commission, 2),
                "commission_rate": round((commission / revenue * 100) if revenue else 0, 2),
                "cost_total": round(cost_total, 2),
                "spp_rate": round(agg["w_spp_sum"] / agg["w_total"] * 100, 2) if agg["w_total"] > 0 else 0,
                "to_pay_rate": round(agg["w_topay_sum"] / agg["w_total"] * 100, 2) if agg["w_total"] > 0 else 0,
                "has_tariff_gaps": agg["has_tariff_gaps"],
                "has_bdr": agg["has_bdr"],
                **traffic,
            }
        )

    # Filter out groups with no orders and no ads
    data = [d for d in data if d["orders_sum_rub"] > 0 or d["adv_sum"] > 0]
    data.sort(key=lambda x: x["orders_sum_rub"], reverse=True)
    return data[:limit]


async def get_funnel_detailed(
    db: AsyncSession,
    pid: int,
    tax_info: dict,
    date_from: str | None,
    date_to: str | None,
    brand: str | None,
    vendor_code: str | None,
    subject: str | None,
    bdr_rates_map: BdrRatesLookup | None = None,
    nm_ids: set[int] | None = None,
) -> list[dict]:
    """Get detailed funnel data per product.

    Uses BDR rates when available, falls back to tariff-based calculation.
    """
    q = select(WbFunnelDaily).where(WbFunnelDaily.project_id == pid)
    if nm_ids is not None:
        q = q.where(WbFunnelDaily.nm_id.in_(nm_ids))

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)
    if vendor_code:
        _vc = vendor_code.replace("%", r"\%").replace("_", r"\_")
        vc_filter: ColumnElement[bool] = WbFunnelDaily.vendor_code.ilike(f"%{_vc}%", escape="\\")
        if vendor_code.isdigit():
            vc_filter = or_(vc_filter, WbFunnelDaily.nm_id == int(vendor_code))
        q = q.where(vc_filter)
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)

    q = q.order_by(WbFunnelDaily.date.desc(), WbFunnelDaily.orders_sum_rub.desc())
    result = await db.execute(q)
    rows = result.scalars().all()

    # Load maps (for fallback)
    tariff_map = await get_tariff_map(db, pid)
    buyout_map = await get_avg_buyout_map(db, pid)
    tax_rate = tax_info.get("usn_rate", 0) + tax_info.get("nds_rate", 0)

    data = []
    for r in rows:
        orders_sum = float(r.orders_sum_rub or 0)
        adv = float(r.adv_sum or 0)
        cost_per_unit = float(r.cost_price or 0)
        orders_count = r.orders_count or 0
        views = r.adv_views or 0
        clicks = r.adv_clicks or 0
        nm_id = r.nm_id

        bdr = bdr_rates_map.get(nm_id, r.date) if bdr_rates_map else None
        if bdr:
            m = compute_profit_bdr(orders_sum, orders_count, adv, cost_per_unit, bdr, tax_info)
            m.update(_traffic_metrics(orders_sum, adv, views, clicks, orders_count))
        else:
            buyout_pct = buyout_map.get(nm_id, 100)
            revenue = orders_sum * buyout_pct / 100
            cost_total = cost_per_unit * orders_count * buyout_pct / 100
            rate = tariff_map.get(r.subject or "", 0)
            m = _compute_metrics_legacy(
                orders_sum, revenue, adv, tax_rate, views, clicks, orders_count, cost_total, commission_rate_pct=rate
            )
            m["buyout_pct"] = round(buyout_pct, 2)

        has_tariff = bool(tariff_map.get(r.subject or "", 0) > 0) or m.get("has_bdr", False)

        data.append(
            {
                "id": r.id,
                "date": r.date.isoformat(),
                "nm_id": nm_id,
                "vendor_code": r.vendor_code,
                "subject": r.subject,
                "brand": r.brand,
                "open_card": r.open_card,
                "add_to_cart": r.add_to_cart,
                "orders_count": orders_count,
                "orders_sum_rub": orders_sum,
                "buyout_percent": m.get("buyout_pct", buyout_map.get(nm_id, 100)),
                "cost_price": cost_per_unit,
                "adv_sum": adv,
                "adv_views": views,
                "adv_clicks": clicks,
                "add_to_cart_pct": float(r.add_to_cart_pct or 0),
                "cart_to_order_pct": float(r.cart_to_order_pct or 0),
                "avg_price": float(r.avg_price or 0),
                "stocks_wb": r.stocks_wb,
                "stocks_mp": r.stocks_mp,
                "has_tariff": has_tariff,
                **m,
            }
        )

    return data


async def get_summary(
    db: AsyncSession, pid: int, date_from: str | None, date_to: str | None, brand: str | None, subject: str | None
) -> dict:
    """Get summary totals for the period header."""
    q = select(
        func.sum(WbFunnelDaily.open_card).label("open_card"),
        func.sum(WbFunnelDaily.add_to_cart).label("add_to_cart"),
        func.sum(WbFunnelDaily.orders_count).label("orders_count"),
        func.sum(WbFunnelDaily.orders_sum_rub).label("orders_sum_rub"),
        func.sum(WbFunnelDaily.adv_sum).label("adv_sum"),
        func.sum(WbFunnelDaily.adv_views).label("adv_views"),
        func.sum(WbFunnelDaily.adv_clicks).label("adv_clicks"),
    ).where(WbFunnelDaily.project_id == pid)

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)

    result = await db.execute(q)
    row = result.one()

    return {
        "open_card": int(row.open_card or 0),
        "add_to_cart": int(row.add_to_cart or 0),
        "orders_count": int(row.orders_count or 0),
        "orders_sum_rub": float(row.orders_sum_rub or 0),
        "adv_sum": float(row.adv_sum or 0),
        "adv_views": int(row.adv_views or 0),
        "adv_clicks": int(row.adv_clicks or 0),
        "drr": round(float(row.adv_sum or 0) / float(row.orders_sum_rub or 1) * 100, 2)
        if float(row.orders_sum_rub or 0) > 0
        else 0,
    }


async def resolve_filter_nm_ids(
    db: AsyncSession,
    pid: int,
    tag: str | None = None,
    imt: str | None = None,
    color: str | None = None,
) -> set[int] | None:
    """nm_id, попадающие под фильтр по ярлыку, склейке и/или цвету.

    None — фильтр не задан (не ограничивать). Пустое множество — ничего
    не подошло (запросы вернут пустой результат). imt — имя алиаса или "#<imt_id>".
    Несколько фильтров комбинируются по AND (пересечение). Цвет парсится из
    артикула (`Nomenclature.article_seller`, см. `article_parser.parse_color`).
    """
    if not tag and not imt and not color:
        return None
    from backend.models.cost import Nomenclature
    from backend.models.refs import ImtAlias, ProductTag, ProductTagMap

    result: set[int] | None = None
    if tag:
        rows = await db.execute(
            select(ProductTagMap.nm_id)
            .join(ProductTag, ProductTag.id == ProductTagMap.tag_id)
            .where(
                ProductTagMap.project_id == pid,
                ProductTag.is_deleted.is_(False),
                ProductTag.name == tag,
            )
            .limit(50000)
        )
        result = {r[0] for r in rows}
    if imt:
        if imt.startswith("#") and imt[1:].isdigit():
            imt_id: int | None = int(imt[1:])
        else:
            imt_id = (
                await db.execute(select(ImtAlias.imt_id).where(ImtAlias.project_id == pid, ImtAlias.name == imt))
            ).scalar()
        imt_set: set[int] = set()
        if imt_id is not None:
            rows = await db.execute(
                select(Nomenclature.article_wb)
                .where(
                    Nomenclature.project_id == pid,
                    Nomenclature.imt_id == imt_id,
                    Nomenclature.article_wb.isnot(None),
                )
                .limit(50000)
            )
            imt_set = {r[0] for r in rows}
        result = imt_set if result is None else result & imt_set
    if color:
        from backend.services.funnel.article_parser import parse_color

        rows = await db.execute(
            select(Nomenclature.article_wb, Nomenclature.article_seller)
            .where(
                Nomenclature.project_id == pid,
                Nomenclature.article_wb.isnot(None),
            )
            .limit(50000)
        )
        color_set = {aw for aw, art in rows if parse_color(art) == color}
        result = color_set if result is None else result & color_set
    return result


async def get_color_options(
    db: AsyncSession, pid: int, subject: str | None = None, brand: str | None = None
) -> dict:
    """Список цветов (распарсенных из артикулов) для выпадающего фильтра.

    Опционально сужается до выбранной категории/бренда. Источник —
    `Nomenclature.article_seller` (тот же артикул, что в воронке).
    """
    from backend.models.cost import Nomenclature
    from backend.services.funnel.article_parser import parse_color

    q = select(Nomenclature.article_seller).where(
        Nomenclature.project_id == pid,
        Nomenclature.article_seller.isnot(None),
    )
    if subject:
        q = q.where(Nomenclature.subject == subject)
    if brand:
        q = q.where(Nomenclature.brand == brand)
    rows = await db.execute(q.distinct().limit(50000))

    colors: set[str] = set()
    for (article,) in rows:
        c = parse_color(article)
        if c:
            colors.add(c)
    return {"colors": sorted(colors)}


async def get_filters(db: AsyncSession, pid: int) -> dict:
    """Get unique brands, subjects, date range for filter dropdowns."""
    brands = await db.execute(
        select(WbFunnelDaily.brand)
        .where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.brand.isnot(None),
        )
        .distinct()
    )
    subjects = await db.execute(
        select(WbFunnelDaily.subject)
        .where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.subject.isnot(None),
        )
        .distinct()
    )
    dates = await db.execute(
        select(
            func.min(WbFunnelDaily.date).label("min_date"),
            func.max(WbFunnelDaily.date).label("max_date"),
        ).where(WbFunnelDaily.project_id == pid)
    )
    d = dates.one()

    # Allow today's date — ads data is available intraday
    max_dt = d.max_date

    return {
        "brands": sorted([r[0] for r in brands if r[0]]),
        "subjects": sorted([r[0] for r in subjects if r[0]]),
        "min_date": d.min_date.isoformat() if d.min_date else None,
        "max_date": max_dt.isoformat() if max_dt else None,
    }


# ─── Cost overrides (re-exported from cost_overrides.py) ────────────────────

from backend.services.funnel.cost_overrides import (  # noqa: F401, E402
    bulk_set_cost_overrides,
    get_cost_overrides,
    get_missing_costs,
    set_cost_override,
)
