"""
Stock Forecast Service — forecast stock depletion based on sales trends.

Uses WbFunnelDaily data for trend analysis and traffic-light classification.
Supports 3 modes:
  - wb: only WB stocks
  - wb_rf: WB + fulfillment warehouse stocks
  - wb_rf_transit: WB + RF + in-transit (assembly requests)
"""

import logging
from datetime import date, timedelta

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily

logger = logging.getLogger("dds.stock_analytics")


# ─── Pure computation helpers (testable without DB) ──────────────────────────


def compute_days_left(stocks: int, avg_daily: float) -> int:
    """How many days until stock runs out at current sales rate."""
    if avg_daily <= 0:
        return 999 if stocks > 0 else 0
    if stocks <= 0:
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


# ─── Extra stock loaders ─────────────────────────────────────────────────────


async def _load_rf_stocks(db: AsyncSession, project_id: int) -> dict[int, int]:
    """Load RF warehouse stocks grouped by nm_id.

    Links WarehouseStock (nomenclature_id) → Nomenclature (article_wb = nm_id).
    Only FULFILLMENT warehouses.
    """
    from backend.models.cost import Nomenclature
    from backend.models.warehouse import Warehouse, WarehouseStock, WarehouseType

    result = await db.execute(
        select(
            Nomenclature.article_wb,
            func.sum(WarehouseStock.quantity).label("qty"),
        )
        .join(Nomenclature, Nomenclature.id == WarehouseStock.nomenclature_id)
        .join(Warehouse, Warehouse.id == WarehouseStock.warehouse_id)
        .where(
            WarehouseStock.project_id == project_id,
            Warehouse.warehouse_type == WarehouseType.FULFILLMENT,
            Warehouse.is_deleted.is_(False),
            Nomenclature.article_wb.isnot(None),
        )
        .group_by(Nomenclature.article_wb)
    )
    return {int(r.article_wb): int(r.qty or 0) for r in result.all()}


async def _load_in_transit(db: AsyncSession, project_id: int) -> dict[int, list[dict]]:
    """Load in-transit quantities from active assembly requests.

    Returns {nm_id: [{qty, delivery_date}, ...]}.
    Links AssemblyRequestItem (nomenclature_id) → Nomenclature (article_wb = nm_id).
    """
    from backend.models.assembly import (
        AssemblyRequest,
        AssemblyRequestItem,
        AssemblyStatus,
    )
    from backend.models.cost import Nomenclature

    active_statuses = [
        AssemblyStatus.PENDING,
        AssemblyStatus.IN_PROGRESS,
        AssemblyStatus.READY,
        AssemblyStatus.VEHICLE_ASSIGNED,
        AssemblyStatus.SHIPPED,
    ]

    result = await db.execute(
        select(
            Nomenclature.article_wb,
            AssemblyRequestItem.quantity,
            AssemblyRequest.delivery_date,
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .join(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_(active_statuses),
            Nomenclature.article_wb.isnot(None),
        )
    )

    transit_map: dict[int, list[dict]] = {}
    for r in result.all():
        nm_id = int(r.article_wb)
        entry = {
            "qty": int(r.quantity or 0),
            "delivery_date": r.delivery_date,
        }
        transit_map.setdefault(nm_id, []).append(entry)
    return transit_map


def _build_forecast_with_transit(
    stocks_wb: int,
    avg_daily: float,
    transit_entries: list[dict],
    forecast_days: int,
    today_date: date,
) -> list[int]:
    """Build forecast considering in-transit deliveries arriving on specific dates."""
    forecast = []
    remaining = stocks_wb
    # Sort deliveries by date
    deliveries = sorted(
        [e for e in transit_entries if e.get("delivery_date")],
        key=lambda e: e["delivery_date"],
    )
    delivery_map: dict[int, int] = {}  # day_offset → qty arriving
    for d in deliveries:
        delta = (d["delivery_date"] - today_date).days
        if delta < 0:
            # Already past due — add to current stock
            remaining += d["qty"]
        elif delta < forecast_days:
            delivery_map[delta] = delivery_map.get(delta, 0) + d["qty"]

    for i in range(forecast_days):
        # Add arriving stock
        remaining += delivery_map.get(i, 0)
        # Subtract daily sales
        remaining -= avg_daily
        forecast.append(max(0, int(remaining + 0.5)))

    return forecast


# ─── Main service function ───────────────────────────────────────────────────


async def get_stock_analytics(
    db: AsyncSession,
    project_id: int,
    trend_days: int = 7,
    subject_filter: str | None = None,
    brand_filter: str | None = None,
    article_filter: str | None = None,
    mode: str = "wb",
) -> dict:
    """Build stock analytics report with trend analysis and traffic-light.

    Modes:
      - wb: only WB stocks
      - wb_rf: WB + fulfillment warehouse stocks
      - wb_rf_transit: WB + RF + in-transit assembly requests
    """
    today = date.today()
    date_30d_ago = today - timedelta(days=30)
    date_trend_ago = today - timedelta(days=trend_days)

    # 1. Find latest data date
    latest_date_result = await db.execute(
        select(func.max(WbFunnelDaily.date)).where(
            WbFunnelDaily.project_id == project_id,
        )
    )
    data_date = latest_date_result.scalar()
    if not data_date:
        return _empty_result()

    # 2. Latest stocks per nm_id (last available date)
    sub = (
        select(
            WbFunnelDaily.nm_id,
            WbFunnelDaily.vendor_code,
            WbFunnelDaily.subject,
            WbFunnelDaily.brand,
            WbFunnelDaily.stocks_wb,
            func.row_number()
            .over(
                partition_by=WbFunnelDaily.nm_id,
                order_by=desc(WbFunnelDaily.date),
            )
            .label("rn"),
        )
        .where(
            WbFunnelDaily.project_id == project_id,
        )
        .subquery()
    )

    stocks_result = await db.execute(select(sub).where(sub.c.rn == 1))
    stocks_map: dict[int, dict] = {}
    for r in stocks_result:
        stocks_map[r.nm_id] = {
            "nm_id": r.nm_id,
            "vendor_code": r.vendor_code or "",
            "subject": r.subject or "",
            "brand": r.brand or "",
            "stocks_wb": int(r.stocks_wb or 0),
        }

    # 3. Orders aggregated (last 30 days)
    orders_30d_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.sum(WbFunnelDaily.orders_count).label("orders_30d"),
            func.sum(WbFunnelDaily.orders_sum_rub).label("orders_sum_30d"),
        )
        .where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.date >= date_30d_ago,
            WbFunnelDaily.date <= data_date,
        )
        .group_by(WbFunnelDaily.nm_id)
    )
    orders_30d_map = {
        r.nm_id: {"orders_30d": int(r.orders_30d or 0), "orders_sum_30d": float(r.orders_sum_30d or 0)}
        for r in orders_30d_result
    }

    # 4. Average daily orders for trend period
    actual_trend_start = max(date_30d_ago, date_trend_ago)
    trend_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.sum(WbFunnelDaily.orders_count).label("total_orders"),
            func.count(func.distinct(WbFunnelDaily.date)).label("days_count"),
        )
        .where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.date >= actual_trend_start,
            WbFunnelDaily.date <= data_date,
        )
        .group_by(WbFunnelDaily.nm_id)
    )
    avg_daily_map: dict[int, float] = {}
    for r in trend_result:
        days = int(r.days_count or 1)
        avg_daily_map[r.nm_id] = round(int(r.total_orders or 0) / max(days, 1), 2)

    # 5. Previous period for trend comparison
    prev_start = actual_trend_start - timedelta(days=trend_days)
    prev_end = actual_trend_start - timedelta(days=1)
    prev_result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.sum(WbFunnelDaily.orders_count).label("total_orders"),
            func.count(func.distinct(WbFunnelDaily.date)).label("days_count"),
        )
        .where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.date >= prev_start,
            WbFunnelDaily.date <= prev_end,
        )
        .group_by(WbFunnelDaily.nm_id)
    )
    prev_avg_map: dict[int, float] = {}
    for r in prev_result:
        days = int(r.days_count or 1)
        prev_avg_map[r.nm_id] = round(int(r.total_orders or 0) / max(days, 1), 2)

    # 6. Load extra stock data based on mode
    rf_stocks_map: dict[int, int] = {}
    transit_map: dict[int, list[dict]] = {}

    if mode in ("wb_rf", "wb_rf_transit"):
        rf_stocks_map = await _load_rf_stocks(db, project_id)

    if mode == "wb_rf_transit":
        transit_map = await _load_in_transit(db, project_id)

    # 7. Stock forecast for 30 future days
    forecast_days = 30
    today_date = date.today()
    sorted_dates = [str(today_date + timedelta(days=i)) for i in range(forecast_days)]

    # 8. Build articles list
    articles = []
    for nm_id, stock_info in stocks_map.items():
        vendor_code = stock_info["vendor_code"]
        subject = stock_info["subject"]
        brand = stock_info["brand"]

        if subject_filter and subject_filter != subject:
            continue
        if brand_filter and brand_filter != brand:
            continue
        if article_filter and article_filter.lower() not in vendor_code.lower():
            continue

        stocks_wb = stock_info["stocks_wb"]
        stocks_rf = rf_stocks_map.get(nm_id, 0)
        transit_entries = transit_map.get(nm_id, [])
        in_transit_total = sum(e["qty"] for e in transit_entries)

        # Total stock depends on mode
        if mode == "wb_rf_transit":
            total_stock = stocks_wb + stocks_rf + in_transit_total
        elif mode == "wb_rf":
            total_stock = stocks_wb + stocks_rf
        else:
            total_stock = stocks_wb

        o30 = orders_30d_map.get(nm_id, {})
        avg_daily = avg_daily_map.get(nm_id, 0)
        prev_avg = prev_avg_map.get(nm_id, 0)

        days_left = compute_days_left(total_stock, avg_daily)
        trend_pct = compute_trend_pct(avg_daily, prev_avg)
        traffic = classify_traffic_light(days_left)

        # Forecast
        if mode == "wb_rf_transit" and transit_entries:
            # Smart forecast: deliveries arrive on specific dates
            base_stock = stocks_wb + stocks_rf
            forecast = _build_forecast_with_transit(
                base_stock,
                avg_daily,
                transit_entries,
                forecast_days,
                today_date,
            )
        else:
            forecast = []
            for i in range(forecast_days):
                projected = max(0, int(total_stock - avg_daily * i + 0.5))
                forecast.append(projected)

        article_data: dict = {
            "nm_id": nm_id,
            "vendor_code": vendor_code,
            "subject": subject,
            "brand": brand,
            "orders_30d": o30.get("orders_30d", 0),
            "orders_sum_30d": o30.get("orders_sum_30d", 0),
            "trend_pct": trend_pct,
            "avg_daily": avg_daily,
            "stocks_wb": stocks_wb,
            "days_left": days_left,
            "traffic_light": traffic,
            "forecast": forecast,
        }

        # Extra fields for modes
        if mode in ("wb_rf", "wb_rf_transit"):
            article_data["stocks_rf"] = stocks_rf
        if mode == "wb_rf_transit":
            article_data["in_transit"] = in_transit_total

        articles.append(article_data)

    # Filter out articles with no recent orders
    articles = [a for a in articles if a["orders_30d"] > 0]
    articles.sort(key=lambda a: a["days_left"])

    # 9. Summaries
    total_orders_30d = sum(a["orders_30d"] for a in articles)
    total_avg_daily = sum(a["avg_daily"] for a in articles)
    critical_articles = [a for a in articles if a["traffic_light"] == "red"]
    most_critical = None
    if critical_articles:
        mc = critical_articles[0]
        most_critical = {"article": mc["vendor_code"], "days_left": mc["days_left"]}

    traffic_light = build_traffic_light_counts(articles)
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
        "mode": mode,
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
        "mode": "wb",
    }
