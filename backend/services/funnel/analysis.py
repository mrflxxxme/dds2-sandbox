"""
Funnel analysis — advanced analytics and trend detection.

Handles:
- Day analysis with anomaly detection
- Product trends with linear regression
"""

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily
from backend.models.wb_finance import WbFinanceRow

logger = logging.getLogger("dds.funnel")


async def get_day_analysis(db: AsyncSession, pid: int, tax_rate: float,
                           target_date: str, brand: Optional[str],
                           subject: Optional[str], trend_days: int = 14) -> dict:
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

    async def _day_agg(d: date) -> dict:
        q = select(
            func.coalesce(func.sum(WbFunnelDaily.open_card), 0).label("open_card"),
            func.coalesce(func.sum(WbFunnelDaily.add_to_cart), 0).label("add_to_cart"),
            func.coalesce(func.sum(WbFunnelDaily.orders_count), 0).label("orders_count"),
            func.coalesce(func.sum(WbFunnelDaily.orders_sum_rub), 0).label("orders_sum"),
            func.coalesce(func.sum(WbFunnelDaily.adv_sum), 0).label("adv_sum"),
            func.coalesce(func.sum(WbFunnelDaily.adv_views), 0).label("adv_views"),
            func.coalesce(func.sum(WbFunnelDaily.adv_clicks), 0).label("adv_clicks"),
            func.coalesce(func.sum(
                func.coalesce(WbFunnelDaily.cost_price, 0) * WbFunnelDaily.orders_count
            ), 0).label("cost_total"),
        ).where(WbFunnelDaily.date == d)
        q = _base_filter(q)
        row = (await db.execute(q)).one()
        os_ = float(row.orders_sum)
        adv = float(row.adv_sum)
        cost_total = float(row.cost_total)

        # Get WB Finance data for this day
        fq = select(
            func.coalesce(func.sum(WbFinanceRow.delivery_rub), 0).label("logistics"),
            func.coalesce(func.sum(WbFinanceRow.storage_fee), 0).label("storage"),
            func.coalesce(func.sum(WbFinanceRow.retail_amount), 0).label("total_retail"),
            func.coalesce(func.sum(WbFinanceRow.ppvz_for_pay), 0).label("total_ppvz"),
        ).where(
            WbFinanceRow.project_id == pid,
            WbFinanceRow.rr_dt == d,
        )
        fin_row = (await db.execute(fq)).one()
        logistics = abs(float(fin_row.logistics))
        storage = abs(float(fin_row.storage))
        commission = max(float(fin_row.total_retail) - float(fin_row.total_ppvz), 0)

        revenue = os_
        tax = revenue * tax_rate / 100
        profit = revenue - adv - commission - logistics - storage - cost_total - tax
        drr = (adv / os_ * 100) if os_ else 0
        views = int(row.adv_views)
        clicks = int(row.adv_clicks)
        ctr = (clicks / views * 100) if views else 0
        cpc = (adv / clicks) if clicks else 0
        return {
            "date": d.isoformat(),
            "open_card": int(row.open_card),
            "add_to_cart": int(row.add_to_cart),
            "orders_count": int(row.orders_count),
            "orders_sum": round(os_, 2),
            "revenue": round(revenue, 2),
            "adv_sum": round(adv, 2),
            "adv_views": views,
            "adv_clicks": clicks,
            "ctr": round(ctr, 2),
            "cpc": round(cpc, 2),
            "drr": round(drr, 2),
            "tax": round(tax, 2),
            "profit": round(profit, 2),
            "margin": round((profit / revenue * 100) if revenue else 0, 2),
            "logistics": round(logistics, 2),
            "commission": round(commission, 2),
            "storage": round(storage, 2),
            "cost_total": round(cost_total, 2),
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
        top_products.append({
            "nm_id": r.nm_id, "vendor_code": r.vendor_code,
            "brand": r.brand, "subject": r.subject,
            "orders_count": r.orders_count or 0,
            "orders_sum": round(os_, 2), "adv_sum": round(adv, 2),
            "drr": round(drr, 2),
            "open_card": r.open_card or 0, "add_to_cart": r.add_to_cart or 0,
        })

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
        trend.append({
            "date": r.date.isoformat(),
            "orders_sum": round(os_, 2), "adv_sum": round(adv, 2),
            "orders_count": int(r.orders_count or 0),
            "open_card": int(r.open_card or 0),
            "drr": round((adv / os_ * 100) if os_ else 0, 2),
        })

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
        r.nm_id: {"avg_sum": float(r.avg_orders_sum or 0), "avg_cnt": float(r.avg_orders_count or 0)}
        for r in avg_rows
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

from backend.services.funnel.product_trends import (  # noqa: F401
    get_product_trends,
    _linear_regression_trend,
)

