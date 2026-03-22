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
from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily
from backend.services.funnel.bdr_rates import BdrRates, compute_profit_bdr
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
    bdr_rates_map: dict[int, BdrRates] | None = None,
) -> list[dict]:
    """Get funnel data aggregated by day.

    Uses BDR rates for profit calculation when available.
    Falls back to tariff-based calculation otherwise.
    """
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

        bdr = bdr_rates_map.get(nm_id) if bdr_rates_map else None
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
            agg["cost_total"] += cost_per_unit * orders_count

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


async def get_funnel_detailed(
    db: AsyncSession,
    pid: int,
    tax_info: dict,
    date_from: str | None,
    date_to: str | None,
    brand: str | None,
    vendor_code: str | None,
    subject: str | None,
    bdr_rates_map: dict[int, BdrRates] | None = None,
) -> list[dict]:
    """Get detailed funnel data per product.

    Uses BDR rates when available, falls back to tariff-based calculation.
    """
    q = select(WbFunnelDaily).where(WbFunnelDaily.project_id == pid)

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)
    if vendor_code:
        _vc = vendor_code.replace("%", r"\%").replace("_", r"\_")
        vc_filter = WbFunnelDaily.vendor_code.ilike(f"%{_vc}%")
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

        bdr = bdr_rates_map.get(nm_id) if bdr_rates_map else None
        if bdr:
            m = compute_profit_bdr(orders_sum, orders_count, adv, cost_per_unit, bdr, tax_info)
            m.update(_traffic_metrics(orders_sum, adv, views, clicks, orders_count))
        else:
            buyout_pct = buyout_map.get(nm_id, 100)
            revenue = orders_sum * buyout_pct / 100
            cost_total = cost_per_unit * orders_count
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

    # Cap max_date at yesterday — today's data is incomplete until day ends
    from datetime import date as date_type

    max_dt = d.max_date
    yesterday = date_type.today() - timedelta(days=1)
    if max_dt and max_dt >= date_type.today():
        max_dt = yesterday

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
