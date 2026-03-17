"""
Funnel analysis — advanced analytics and trend detection.

Handles:
- Day analysis with anomaly detection
- Product trends with linear regression
"""

import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily
from backend.services.tariff_service import get_avg_buyout_map, get_tariff_map

logger = logging.getLogger("dds.funnel")


async def get_day_analysis(
    db: AsyncSession,
    pid: int,
    tax_rate: float,
    target_date: str,
    brand: str | None,
    subject: str | None,
    trend_days: int = 14,
) -> dict:
    """Day analysis: summary, comparison, top products, trend, anomalies."""
    td = date.fromisoformat(target_date)
    prev_d = td - timedelta(days=1)
    trend_start = td - timedelta(days=trend_days - 1)

    def _base_filter(q):
        q = q.where(WbFunnelDaily.project_id == pid)
        if brand:
            q = q.where(WbFunnelDaily.brand == brand)
        if subject:
            q = q.where(WbFunnelDaily.subject == subject)
        return q

    # Load maps once for all day aggregations
    tariff_map = await get_tariff_map(db, pid)
    buyout_map = await get_avg_buyout_map(db, pid)

    async def _day_agg(d: date) -> dict:
        # Fetch raw rows to apply per-nm_id avg buyout
        q = select(WbFunnelDaily).where(WbFunnelDaily.date == d)
        q = _base_filter(q)
        raw_rows = (await db.execute(q)).scalars().all()

        total_open = 0
        total_cart = 0
        total_orders = 0
        total_os = 0.0
        total_revenue = 0.0
        total_adv = 0.0
        total_views = 0
        total_clicks = 0
        total_cost = 0.0
        total_commission = 0.0

        for r in raw_rows:
            total_open += int(r.open_card or 0)
            total_cart += int(r.add_to_cart or 0)
            total_orders += int(r.orders_count or 0)
            orders_sum = float(r.orders_sum_rub or 0)
            total_os += orders_sum
            total_adv += float(r.adv_sum or 0)
            total_views += int(r.adv_views or 0)
            total_clicks += int(r.adv_clicks or 0)
            total_cost += float(r.cost_price or 0) * (r.orders_count or 0)

            buyout_pct = buyout_map.get(r.nm_id, 100)
            revenue = orders_sum * buyout_pct / 100
            total_revenue += revenue

            rate = tariff_map.get(r.subject or "", 0)
            total_commission += revenue * rate / 100

        tax = total_revenue * tax_rate / 100
        profit = total_revenue - total_adv - total_commission - total_cost - tax
        drr = (total_adv / total_os * 100) if total_os else 0
        ctr = (total_clicks / total_views * 100) if total_views else 0
        cpc = (total_adv / total_clicks) if total_clicks else 0
        return {
            "date": d.isoformat(),
            "open_card": total_open,
            "add_to_cart": total_cart,
            "orders_count": total_orders,
            "orders_sum": round(total_os, 2),
            "revenue": round(total_revenue, 2),
            "adv_sum": round(total_adv, 2),
            "adv_views": total_views,
            "adv_clicks": total_clicks,
            "ctr": round(ctr, 2),
            "cpc": round(cpc, 2),
            "drr": round(drr, 2),
            "tax": round(tax, 2),
            "profit": round(profit, 2),
            "margin": round((profit / total_revenue * 100) if total_revenue else 0, 2),
            "commission": round(total_commission, 2),
            "cost_total": round(total_cost, 2),
        }

    today_agg = await _day_agg(td)
    prev_agg = await _day_agg(prev_d)

    # Comparison
    def _pct(cur, prev):
        if prev == 0:
            return 100.0 if cur > 0 else 0.0
        return round((cur - prev) / abs(prev) * 100, 1)

    comparison = {}
    for key in ["orders_sum", "revenue", "adv_sum", "orders_count", "profit", "drr", "open_card", "add_to_cart"]:
        comparison[key] = {
            "current": today_agg[key],
            "previous": prev_agg[key],
            "change_pct": _pct(today_agg[key], prev_agg[key]),
        }

    # Top products
    q_top = select(WbFunnelDaily).where(WbFunnelDaily.date == td)
    q_top = _base_filter(q_top)
    q_top = q_top.order_by(WbFunnelDaily.orders_sum_rub.desc()).limit(50)
    top_rows = (await db.execute(q_top)).scalars().all()

    top_products = []
    for r in top_rows:
        os_ = float(r.orders_sum_rub or 0)
        adv = float(r.adv_sum or 0)
        drr = (adv / os_ * 100) if os_ else 0
        top_products.append(
            {
                "nm_id": r.nm_id,
                "vendor_code": r.vendor_code,
                "brand": r.brand,
                "subject": r.subject,
                "orders_count": r.orders_count or 0,
                "orders_sum": round(os_, 2),
                "adv_sum": round(adv, 2),
                "drr": round(drr, 2),
                "open_card": r.open_card or 0,
                "add_to_cart": r.add_to_cart or 0,
            }
        )

    # Trend
    q_trend = select(
        WbFunnelDaily.date,
        func.sum(WbFunnelDaily.orders_sum_rub).label("orders_sum"),
        func.sum(WbFunnelDaily.adv_sum).label("adv_sum"),
        func.sum(WbFunnelDaily.orders_count).label("orders_count"),
        func.sum(WbFunnelDaily.open_card).label("open_card"),
    ).where(WbFunnelDaily.date >= trend_start, WbFunnelDaily.date <= td)
    q_trend = _base_filter(q_trend)
    q_trend = q_trend.group_by(WbFunnelDaily.date).order_by(WbFunnelDaily.date)
    trend_rows = (await db.execute(q_trend)).all()

    trend = []
    for r in trend_rows:
        os_ = float(r.orders_sum or 0)
        adv = float(r.adv_sum or 0)
        trend.append(
            {
                "date": r.date.isoformat(),
                "orders_sum": round(os_, 2),
                "adv_sum": round(adv, 2),
                "orders_count": int(r.orders_count or 0),
                "open_card": int(r.open_card or 0),
                "drr": round((adv / os_ * 100) if os_ else 0, 2),
            }
        )

    # Anomalies
    q_avg = select(
        WbFunnelDaily.nm_id,
        func.avg(WbFunnelDaily.orders_sum_rub).label("avg_orders_sum"),
        func.avg(WbFunnelDaily.orders_count).label("avg_orders_count"),
    ).where(WbFunnelDaily.date >= trend_start, WbFunnelDaily.date < td)
    q_avg = _base_filter(q_avg)
    q_avg = q_avg.group_by(WbFunnelDaily.nm_id)
    avg_rows = (await db.execute(q_avg)).all()
    avg_map = {
        r.nm_id: {"avg_sum": float(r.avg_orders_sum or 0), "avg_cnt": float(r.avg_orders_count or 0)} for r in avg_rows
    }

    anomalies = []
    for p in top_products:
        avg = avg_map.get(p["nm_id"])
        if not avg:
            continue
        flags = []
        if avg["avg_sum"] > 0:
            pct_change = (p["orders_sum"] - avg["avg_sum"]) / avg["avg_sum"] * 100
            if abs(pct_change) > 30:
                flags.append(
                    f"{'📈' if pct_change > 0 else '📉'} Выручка "
                    f"{'+' if pct_change > 0 else ''}{round(pct_change)}% vs средней"
                )
        if p["drr"] > 20:
            flags.append(f"⚠️ ДРР {p['drr']}% (>20%)")
        if p["adv_sum"] > 0 and p["orders_count"] == 0:
            flags.append("🚫 Реклама без заказов")
        if flags:
            anomalies.append({**p, "flags": flags})

    return {
        "target_date": target_date,
        "summary": today_agg,
        "comparison": comparison,
        "top_products": top_products,
        "trend": trend,
        "anomalies": anomalies,
    }


# ─── Product trends (re-exported from product_trends.py) ────────────────────

from backend.services.funnel.product_trends import (  # noqa: F401, E402
    _linear_regression_trend,
    get_product_trends,
)
