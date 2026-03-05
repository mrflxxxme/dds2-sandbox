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
        ).where(WbFunnelDaily.date == d)
        q = _base_filter(q)
        row = (await db.execute(q)).one()
        os_ = float(row.orders_sum)
        adv = float(row.adv_sum)
        revenue = os_
        tax = revenue * tax_rate / 100
        profit = revenue - adv - tax
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


# ─── Product trends (linear regression) ─────────────────────────────────────

def _linear_regression_trend(values: list[float]) -> float:
    """
    Compute trendPct using simple linear regression on N data points.
    y = a*x + b where x = 0..N-1
    trendPct = (predicted_last - mean) / |mean| * 100
    Returns 0 if insufficient data or zero mean.
    """
    n = len(values)
    if n < 2:
        return 0.0
    mean_y = sum(values) / n
    if abs(mean_y) < 1e-9:
        return 0.0
    # x = 0, 1, ..., n-1
    mean_x = (n - 1) / 2.0
    sum_xy = sum(i * v for i, v in enumerate(values))
    sum_xx = sum(i * i for i in range(n))
    denom = sum_xx - n * mean_x * mean_x
    if abs(denom) < 1e-9:
        return 0.0
    a = (sum_xy - n * mean_x * mean_y) / denom
    # Predicted value at the last point
    predicted = mean_y + a * (n - 1 - mean_x)
    trend_pct = (predicted - mean_y) / abs(mean_y) * 100
    return round(trend_pct, 1)


async def get_product_trends(
    db: AsyncSession, pid: int, tax_rate: float,
    trend_days: int = 7, brand: Optional[str] = None,
    search: Optional[str] = None,
) -> dict:
    """
    Per-product metrics with linear regression trends.
    Returns list of products with aggregated values and trend percentages.
    """
    today = date.today()
    d_from = today - timedelta(days=trend_days)

    # Fetch raw daily rows per product
    q = select(WbFunnelDaily).where(
        WbFunnelDaily.project_id == pid,
        WbFunnelDaily.date >= d_from,
        WbFunnelDaily.date <= today,
    )
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)
    if search:
        vc_filter = WbFunnelDaily.vendor_code.ilike(f"%{search}%")
        if search.isdigit():
            vc_filter = or_(vc_filter, WbFunnelDaily.nm_id == int(search))
        q = q.where(vc_filter)

    q = q.order_by(WbFunnelDaily.nm_id, WbFunnelDaily.date)
    result = await db.execute(q)
    rows = result.scalars().all()

    # Group by nm_id → ordered daily records
    products: dict = defaultdict(list)
    product_meta: dict = {}
    for r in rows:
        products[r.nm_id].append(r)
        product_meta[r.nm_id] = {
            "vendor_code": r.vendor_code,
            "subject": r.subject,
            "brand": r.brand,
        }

    output = []
    for nm_id, daily_rows in products.items():
        meta = product_meta[nm_id]
        n_days = len(daily_rows)
        if n_days == 0:
            continue

        # Aggregate totals for the period
        total_orders_count = sum(r.orders_count or 0 for r in daily_rows)
        total_orders_sum = sum(float(r.orders_sum_rub or 0) for r in daily_rows)
        total_open_card = sum(r.open_card or 0 for r in daily_rows)
        total_add_to_cart = sum(r.add_to_cart or 0 for r in daily_rows)
        total_adv_sum = sum(float(r.adv_sum or 0) for r in daily_rows)
        total_adv_views = sum(r.adv_views or 0 for r in daily_rows)
        total_adv_clicks = sum(r.adv_clicks or 0 for r in daily_rows)

        # Latest day values for stocks and avg_price
        latest = daily_rows[-1]
        stocks_wb = latest.stocks_wb or 0
        avg_price = float(latest.avg_price or 0)
        cost_price = float(latest.cost_price or 0)

        # Derived metrics
        avg_daily_orders = total_orders_count / n_days if n_days else 0
        turnover_days = round(stocks_wb / avg_daily_orders, 1) if avg_daily_orders > 0 else 0

        buyout = float(latest.buyout_percent or 100)
        revenue = total_orders_sum * buyout / 100 if buyout else total_orders_sum
        tax = revenue * tax_rate / 100
        cost_total = cost_price * total_orders_count
        profit = revenue - cost_total - total_adv_sum - tax
        margin = round((profit / revenue * 100), 2) if revenue else 0
        drr = round((total_adv_sum / total_orders_sum * 100), 2) if total_orders_sum else 0
        ctr = round((total_adv_clicks / total_adv_views * 100), 2) if total_adv_views else 0

        add_to_cart_pct = round((total_add_to_cart / total_open_card * 100), 2) if total_open_card else 0
        cart_to_order_pct = round((total_orders_count / total_add_to_cart * 100), 2) if total_add_to_cart else 0

        # ── Linear regression trends per metric ──
        # Build time series arrays (one value per day)
        daily_orders = [r.orders_count or 0 for r in daily_rows]
        daily_revenue = [float(r.orders_sum_rub or 0) for r in daily_rows]
        daily_open = [r.open_card or 0 for r in daily_rows]
        daily_cart = [r.add_to_cart or 0 for r in daily_rows]
        daily_adv = [float(r.adv_sum or 0) for r in daily_rows]
        daily_adv_views = [r.adv_views or 0 for r in daily_rows]
        daily_adv_clicks = [r.adv_clicks or 0 for r in daily_rows]
        daily_stocks = [r.stocks_wb or 0 for r in daily_rows]
        daily_avg_price = [float(r.avg_price or 0) for r in daily_rows]

        # Computed daily series for ratios
        daily_drr = []
        daily_margin = []
        daily_ctr = []
        daily_add_to_cart_pct = []
        daily_cart_to_order = []
        daily_turnover = []

        for r in daily_rows:
            os_ = float(r.orders_sum_rub or 0)
            adv = float(r.adv_sum or 0)
            oc = r.orders_count or 0
            views = r.adv_views or 0
            clicks = r.adv_clicks or 0
            open_c = r.open_card or 0
            cart_c = r.add_to_cart or 0
            stk = r.stocks_wb or 0

            daily_drr.append((adv / os_ * 100) if os_ else 0)
            daily_ctr.append((clicks / views * 100) if views else 0)
            daily_add_to_cart_pct.append((cart_c / open_c * 100) if open_c else 0)
            daily_cart_to_order.append((oc / cart_c * 100) if cart_c else 0)

            rev = os_ * buyout / 100 if buyout else os_
            t = rev * tax_rate / 100
            cp = float(r.cost_price or 0) * oc
            p = rev - cp - adv - t
            daily_margin.append((p / rev * 100) if rev else 0)
            daily_turnover.append(stk / oc if oc else 0)

        trend_orders = _linear_regression_trend(daily_orders)
        trend_revenue = _linear_regression_trend(daily_revenue)
        trend_open = _linear_regression_trend(daily_open)
        trend_adv = _linear_regression_trend(daily_adv)
        trend_drr = _linear_regression_trend(daily_drr)
        trend_margin = _linear_regression_trend(daily_margin)
        trend_ctr = _linear_regression_trend(daily_ctr)
        trend_avg_price = _linear_regression_trend(daily_avg_price)
        trend_add_to_cart_pct = _linear_regression_trend(daily_add_to_cart_pct)
        trend_cart_to_order = _linear_regression_trend(daily_cart_to_order)
        trend_stocks = _linear_regression_trend(daily_stocks)
        trend_turnover = _linear_regression_trend(daily_turnover)

        output.append({
            "nm_id": nm_id,
            "vendor_code": meta["vendor_code"],
            "subject": meta["subject"],
            "brand": meta["brand"],
            "n_days": n_days,

            # Aggregated values
            "turnover_days": turnover_days,
            "stocks_wb": stocks_wb,
            "orders_count": total_orders_count,
            "orders_sum_rub": round(total_orders_sum, 2),
            "revenue": round(revenue, 2),
            "open_card": total_open_card,
            "add_to_cart_pct": add_to_cart_pct,
            "cart_to_order_pct": cart_to_order_pct,
            "margin": margin,
            "profit": round(profit, 2),
            "avg_price": avg_price,
            "drr": drr,
            "adv_sum": round(total_adv_sum, 2),
            "ctr": ctr,
            "cost_price": cost_price,

            # Trend percentages (linear regression)
            "trend_turnover": trend_turnover,
            "trend_orders": trend_orders,
            "trend_revenue": trend_revenue,
            "trend_open": trend_open,
            "trend_add_to_cart_pct": trend_add_to_cart_pct,
            "trend_cart_to_order": trend_cart_to_order,
            "trend_margin": trend_margin,
            "trend_profit": _linear_regression_trend([
                float(r.orders_sum_rub or 0) * buyout / 100 - float(r.adv_sum or 0)
                - float(r.orders_sum_rub or 0) * buyout / 100 * tax_rate / 100
                - float(r.cost_price or 0) * (r.orders_count or 0)
                for r in daily_rows
            ]),
            "trend_avg_price": trend_avg_price,
            "trend_drr": trend_drr,
            "trend_adv": trend_adv,
            "trend_ctr": trend_ctr,
            "trend_stocks": trend_stocks,
        })

    # Sort by total revenue descending
    output.sort(key=lambda x: x["orders_sum_rub"], reverse=True)

    return {
        "products": output,
        "trend_days": trend_days,
        "total_products": len(output),
    }
