"""
Stock Analytics Service — forecast stock depletion based on sales trends.

Uses WbFunnelDaily data:
- stocks_wb: current warehouse stock
- orders_count / orders_sum_rub: daily orders
- vendor_code, subject, brand: product metadata
"""

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily

logger = logging.getLogger("dds.stock_analytics")


# ─── Pure computation helpers (testable without DB) ──────────────────────────

def compute_days_left(stocks: int, avg_daily: float) -> int:
    """How many days until stock runs out at current sales rate."""
    if avg_daily <= 0 or stocks <= 0:
        return 0
    return int(stocks / avg_daily)


def compute_trend_pct(current_avg: float, prev_avg: float) -> float:
    """Trend % change between two periods."""
    if prev_avg <= 0:
        return 0.0
    return round((current_avg - prev_avg) / prev_avg * 100, 1)


def classify_traffic_light(days_left: int) -> str:
    """Classify stock status as traffic light color."""
    if days_left < 7:
        return "red"
    elif days_left <= 14:
        return "orange"
    elif days_left <= 29:
        return "yellow"
    else:
        return "green"


def build_traffic_light_counts(articles: list[dict]) -> dict[str, int]:
    """Count articles in each traffic light category."""
    counts = {"red": 0, "orange": 0, "yellow": 0, "green": 0}
    for a in articles:
        color = a.get("traffic_light", "green")
        counts[color] = counts.get(color, 0) + 1
    return counts


# ─── Main service function ───────────────────────────────────────────────────

async def get_stock_analytics(
    db: AsyncSession,
    project_id: int,
    trend_days: int = 7,
    subject_filter: Optional[str] = None,
    brand_filter: Optional[str] = None,
    article_filter: Optional[str] = None,
) -> dict:
    """Build stock analytics report.

    Args:
        trend_days: period (7/14/30) for average daily sales calculation
        subject_filter: filter by product category
        brand_filter: filter by brand
        article_filter: filter by vendor_code (partial match)
    """
    today = date.today()
    date_30d_ago = today - timedelta(days=30)
    date_trend_ago = today - timedelta(days=trend_days)

    # ── 1. Find latest data date ──
    latest_date_result = await db.execute(
        select(func.max(WbFunnelDaily.date)).where(
            WbFunnelDaily.project_id == project_id,
        )
    )
    data_date = latest_date_result.scalar()
    if not data_date:
        return _empty_result()

    # ── 2. Latest stocks per nm_id (last available date) ──
    sub = select(
        WbFunnelDaily.nm_id,
        WbFunnelDaily.vendor_code,
        WbFunnelDaily.subject,
        WbFunnelDaily.brand,
        WbFunnelDaily.stocks_wb,
        func.row_number().over(
            partition_by=WbFunnelDaily.nm_id,
            order_by=desc(WbFunnelDaily.date),
        ).label("rn"),
    ).where(
        WbFunnelDaily.project_id == project_id,
    ).subquery()

    stocks_result = await db.execute(
        select(sub).where(sub.c.rn == 1)
    )
    stocks_map: dict[int, dict] = {}
    for r in stocks_result:
        stocks_map[r.nm_id] = {
            "nm_id": r.nm_id,
            "vendor_code": r.vendor_code or "",
            "subject": r.subject or "",
            "brand": r.brand or "",
            "stocks_wb": int(r.stocks_wb or 0),
        }

    # ── 3. Orders aggregated (last 30 days) ──
    orders_30d_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.sum(WbFunnelDaily.orders_count).label("orders_30d"),
            func.sum(WbFunnelDaily.orders_sum_rub).label("orders_sum_30d"),
        ).where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.date >= date_30d_ago,
            WbFunnelDaily.date <= data_date,
        ).group_by(WbFunnelDaily.nm_id)
    )
    orders_30d_map = {
        r.nm_id: {"orders_30d": int(r.orders_30d or 0), "orders_sum_30d": float(r.orders_sum_30d or 0)}
        for r in orders_30d_result
    }

    # ── 4. Average daily orders for trend period ──
    actual_trend_start = max(date_30d_ago, date_trend_ago)
    trend_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.sum(WbFunnelDaily.orders_count).label("total_orders"),
            func.count(func.distinct(WbFunnelDaily.date)).label("days_count"),
        ).where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.date >= actual_trend_start,
            WbFunnelDaily.date <= data_date,
        ).group_by(WbFunnelDaily.nm_id)
    )
    avg_daily_map: dict[int, float] = {}
    for r in trend_result:
        days = int(r.days_count or 1)
        avg_daily_map[r.nm_id] = round(int(r.total_orders or 0) / max(days, 1), 2)

    # ── 5. Previous period for trend comparison ──
    prev_start = actual_trend_start - timedelta(days=trend_days)
    prev_end = actual_trend_start - timedelta(days=1)
    prev_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.sum(WbFunnelDaily.orders_count).label("total_orders"),
            func.count(func.distinct(WbFunnelDaily.date)).label("days_count"),
        ).where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.date >= prev_start,
            WbFunnelDaily.date <= prev_end,
        ).group_by(WbFunnelDaily.nm_id)
    )
    prev_avg_map: dict[int, float] = {}
    for r in prev_result:
        days = int(r.days_count or 1)
        prev_avg_map[r.nm_id] = round(int(r.total_orders or 0) / max(days, 1), 2)

    # ── 6. Daily orders (last 14 days for table columns) ──
    daily_start = data_date - timedelta(days=13)
    daily_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            WbFunnelDaily.date,
            WbFunnelDaily.orders_count,
        ).where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.date >= daily_start,
            WbFunnelDaily.date <= data_date,
        ).order_by(WbFunnelDaily.date)
    )
    daily_map: dict[int, list[dict]] = {}
    all_dates: set[str] = set()
    for r in daily_result:
        nm = r.nm_id
        d_str = str(r.date)
        all_dates.add(d_str)
        if nm not in daily_map:
            daily_map[nm] = []
        daily_map[nm].append({"date": d_str, "orders": int(r.orders_count or 0)})

    sorted_dates = sorted(all_dates)

    # ── 7. Build articles list ──
    articles = []
    for nm_id, stock_info in stocks_map.items():
        vendor_code = stock_info["vendor_code"]
        subject = stock_info["subject"]
        brand = stock_info["brand"]

        # Apply filters
        if subject_filter and subject_filter != subject:
            continue
        if brand_filter and brand_filter != brand:
            continue
        if article_filter and article_filter.lower() not in vendor_code.lower():
            continue

        stocks = stock_info["stocks_wb"]
        o30 = orders_30d_map.get(nm_id, {})
        avg_daily = avg_daily_map.get(nm_id, 0)
        prev_avg = prev_avg_map.get(nm_id, 0)

        days_left = compute_days_left(stocks, avg_daily)
        trend_pct = compute_trend_pct(avg_daily, prev_avg)
        traffic = classify_traffic_light(days_left)

        articles.append({
            "nm_id": nm_id,
            "vendor_code": vendor_code,
            "subject": subject,
            "brand": brand,
            "orders_30d": o30.get("orders_30d", 0),
            "orders_sum_30d": o30.get("orders_sum_30d", 0),
            "trend_pct": trend_pct,
            "avg_daily": avg_daily,
            "stocks_wb": stocks,
            "days_left": days_left,
            "traffic_light": traffic,
            "daily_orders": daily_map.get(nm_id, []),
        })

    # Sort by days_left ascending (most critical first)
    articles.sort(key=lambda a: a["days_left"])

    # ── 8. Summaries ──
    total_orders_30d = sum(a["orders_30d"] for a in articles)
    total_avg_daily = sum(a["avg_daily"] for a in articles)
    critical_articles = [a for a in articles if a["traffic_light"] == "red"]
    most_critical = None
    if critical_articles:
        mc = critical_articles[0]
        most_critical = {"article": mc["vendor_code"], "days_left": mc["days_left"]}

    traffic_light = build_traffic_light_counts(articles)

    # Collect filter options
    subjects = sorted(set(a["subject"] for a in articles if a["subject"]))
    brands = sorted(set(a["brand"] for a in articles if a["brand"]))

    return {
        "data_date": str(data_date),
        "total_articles": len(articles),
        "orders_30d": total_orders_30d,
        "avg_daily": round(total_avg_daily, 2),
        "critical_count": len(critical_articles),
        "most_critical": most_critical,
        "traffic_light": traffic_light,
        "articles": articles,
        "dates": sorted_dates,
        "subjects": subjects,
        "brands": brands,
    }


def _empty_result() -> dict:
    return {
        "data_date": None,
        "total_articles": 0,
        "orders_30d": 0,
        "avg_daily": 0,
        "critical_count": 0,
        "most_critical": None,
        "traffic_light": {"red": 0, "orange": 0, "yellow": 0, "green": 0},
        "articles": [],
        "dates": [],
        "subjects": [],
        "brands": [],
    }
