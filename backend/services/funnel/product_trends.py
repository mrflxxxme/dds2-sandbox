# ruff: noqa: RUF001
"""Funnel product trends — per-product metrics with linear regression.

Extracted from analysis.py for maintainability.
"""

import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily
from backend.services.funnel.bdr_rates import BdrRatesLookup, compute_profit_bdr
from backend.services.tariff_service import get_avg_buyout_map, get_tariff_map

logger = logging.getLogger("dds.funnel")


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
    mean_x = (n - 1) / 2.0
    sum_xy = sum(i * v for i, v in enumerate(values))
    sum_xx = sum(i * i for i in range(n))
    denom = sum_xx - n * mean_x * mean_x
    if abs(denom) < 1e-9:
        return 0.0
    a = (sum_xy - n * mean_x * mean_y) / denom
    predicted = mean_y + a * (n - 1 - mean_x)
    trend_pct = (predicted - mean_y) / abs(mean_y) * 100
    return round(trend_pct, 1)


async def get_product_trends(
    db: AsyncSession,
    pid: int,
    tax_info: dict,
    trend_days: int = 7,
    brand: str | None = None,
    search: str | None = None,
    bdr_rates_map: BdrRatesLookup | None = None,
) -> dict:
    """
    Per-product metrics with linear regression trends.
    Returns list of products with aggregated values and trend percentages.
    """
    today = date.today()
    d_from = today - timedelta(days=trend_days)

    tax_rate = tax_info.get("usn_rate", 0) + tax_info.get("nds_rate", 0)  # legacy fallback

    q = select(WbFunnelDaily).where(
        WbFunnelDaily.project_id == pid,
        WbFunnelDaily.date >= d_from,
        WbFunnelDaily.date <= today,
    )
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)
    if search:
        _s = search.replace("%", r"\%").replace("_", r"\_")
        vc_filter = WbFunnelDaily.vendor_code.ilike(f"%{_s}%")
        if search.isdigit():
            vc_filter = or_(vc_filter, WbFunnelDaily.nm_id == int(search))
        q = q.where(vc_filter)

    q = q.order_by(WbFunnelDaily.nm_id, WbFunnelDaily.date)
    result = await db.execute(q)
    rows = result.scalars().all()

    # Load maps (for fallback)
    tariff_map = await get_tariff_map(db, pid)
    buyout_map = await get_avg_buyout_map(db, pid)

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

        total_orders_count = sum(r.orders_count or 0 for r in daily_rows)
        total_orders_sum = sum(float(r.orders_sum_rub or 0) for r in daily_rows)
        total_open_card = sum(r.open_card or 0 for r in daily_rows)
        total_add_to_cart = sum(r.add_to_cart or 0 for r in daily_rows)
        total_adv_sum = sum(float(r.adv_sum or 0) for r in daily_rows)
        total_adv_views = sum(r.adv_views or 0 for r in daily_rows)
        total_adv_clicks = sum(r.adv_clicks or 0 for r in daily_rows)

        latest = daily_rows[-1]
        stocks_wb = latest.stocks_wb or 0
        avg_price = float(latest.avg_price or 0)
        # Weighted average cost_price across all days (weighted by orders_count)
        _cost_num = sum(float(r.cost_price or 0) * (r.orders_count or 0) for r in daily_rows)
        _cost_den = sum(r.orders_count or 0 for r in daily_rows)
        cost_price = round(_cost_num / _cost_den, 2) if _cost_den > 0 else float(latest.cost_price or 0)
        tariff_rate = tariff_map.get(meta["subject"] or "", 0)

        avg_daily_orders = total_orders_count / n_days if n_days else 0
        turnover_days = round(stocks_wb / avg_daily_orders, 1) if avg_daily_orders > 0 else 0

        # Profit calculation: BDR or legacy
        bdr = bdr_rates_map.get(nm_id) if bdr_rates_map else None  # avg for totals
        if bdr:
            m = compute_profit_bdr(total_orders_sum, total_orders_count, total_adv_sum, cost_price, bdr, tax_info)
            revenue = m["revenue"]
            commission = m["commission"]
            profit = m["profit"]
            margin = m["margin"]
            commission_rate = m["commission_rate"]
            has_tariff = True
            has_bdr = True
            spp_rate = m["spp_rate"]
            to_pay_rate = m["to_pay_rate"]
            buyout = bdr.buyout_pct * 100
            cost_total = m["cost_total"]
        else:
            buyout = buyout_map.get(nm_id, 100)
            revenue = total_orders_sum * buyout / 100
            commission = revenue * tariff_rate / 100
            tax = revenue * tax_rate / 100
            cost_total = cost_price * total_orders_count * buyout / 100
            profit = revenue - commission - cost_total - total_adv_sum - tax
            margin = round((profit / revenue * 100), 2) if revenue else 0
            commission_rate = tariff_rate
            has_tariff = tariff_rate > 0
            has_bdr = False
            spp_rate = 0
            to_pay_rate = 0

        drr = round((total_adv_sum / total_orders_sum * 100), 2) if total_orders_sum else 0
        ctr = round((total_adv_clicks / total_adv_views * 100), 2) if total_adv_views else 0

        add_to_cart_pct = round((total_add_to_cart / total_open_card * 100), 2) if total_open_card else 0
        cart_to_order_pct = round((total_orders_count / total_add_to_cart * 100), 2) if total_add_to_cart else 0

        # ── Linear regression trends per metric ──
        daily_orders = [r.orders_count or 0 for r in daily_rows]
        daily_revenue_arr = [float(r.orders_sum_rub or 0) for r in daily_rows]
        daily_open = [r.open_card or 0 for r in daily_rows]
        daily_adv = [float(r.adv_sum or 0) for r in daily_rows]
        daily_stocks = [r.stocks_wb or 0 for r in daily_rows]
        daily_avg_price = [float(r.avg_price or 0) for r in daily_rows]

        daily_drr = []
        daily_margin = []
        daily_ctr = []
        daily_add_to_cart_pct = []
        daily_cart_to_order = []
        daily_turnover = []
        daily_profit = []

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
            daily_turnover.append(stk / oc if oc else 0)

            day_bdr = bdr_rates_map.get(nm_id, r.date) if bdr_rates_map else None
            if day_bdr:
                dm = compute_profit_bdr(os_, oc, adv, float(r.cost_price or 0), day_bdr, tax_info)
                daily_margin.append(dm["margin"])
                daily_profit.append(dm["profit"])
            else:
                rev = os_ * buyout / 100 if buyout else os_
                comm = rev * tariff_rate / 100
                t = rev * tax_rate / 100
                cp = float(r.cost_price or 0) * oc * buyout / 100
                p = rev - comm - cp - adv - t
                daily_margin.append((p / rev * 100) if rev else 0)
                daily_profit.append(p)

        output.append(
            {
                "nm_id": nm_id,
                "vendor_code": meta["vendor_code"],
                "subject": meta["subject"],
                "brand": meta["brand"],
                "n_days": n_days,
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
                "commission": round(commission, 2),
                "commission_rate": round(commission_rate, 2),
                "has_tariff": has_tariff,
                "has_bdr": has_bdr,
                "spp_rate": round(spp_rate, 2),
                "to_pay_rate": round(to_pay_rate, 2),
                "avg_price": avg_price,
                "drr": drr,
                "adv_sum": round(total_adv_sum, 2),
                "ctr": ctr,
                "cost_price": cost_price,
                "cost_total": round(cost_total, 2),
                "trend_turnover": _linear_regression_trend(daily_turnover),
                "trend_orders": _linear_regression_trend(daily_orders),
                "trend_revenue": _linear_regression_trend(daily_revenue_arr),
                "trend_open": _linear_regression_trend(daily_open),
                "trend_add_to_cart_pct": _linear_regression_trend(daily_add_to_cart_pct),
                "trend_cart_to_order": _linear_regression_trend(daily_cart_to_order),
                "trend_margin": _linear_regression_trend(daily_margin),
                "trend_profit": _linear_regression_trend(daily_profit),
                "trend_avg_price": _linear_regression_trend(daily_avg_price),
                "trend_drr": _linear_regression_trend(daily_drr),
                "trend_adv": _linear_regression_trend(daily_adv),
                "trend_ctr": _linear_regression_trend(daily_ctr),
                "trend_stocks": _linear_regression_trend(daily_stocks),
            }
        )

    output.sort(key=lambda x: x["orders_sum_rub"], reverse=True)

    return {
        "products": output,
        "trend_days": trend_days,
        "total_products": len(output),
    }
