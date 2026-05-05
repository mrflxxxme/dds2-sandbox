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

from sqlalchemy import and_, case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily, WbWarehouseStock
from backend.models.wb_finance import WbFinanceRow

logger = logging.getLogger("dds.stock_analytics")

# Default assembly time (days from request placement → ready_date) used when
# AssemblyRequest has neither estimated_ready_date nor actual_ready_date.
_DEFAULT_ASSEMBLY_DAYS = 3


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


async def _load_rf_stocks_per_warehouse(db: AsyncSession, project_id: int) -> dict[int, dict[int, int]]:
    """Load RF warehouse stocks grouped by nm_id and warehouse_id.

    Returns {nm_id: {warehouse_id: qty, ...}}. Only FULFILLMENT warehouses.
    Per-warehouse split is required to compute average ETA to WB based on
    `WarehouseDeliveryTime` (different warehouses → different lead times).
    """
    from backend.models.cost import Nomenclature
    from backend.models.warehouse import Warehouse, WarehouseStock, WarehouseType

    result = await db.execute(
        select(
            Nomenclature.article_wb,
            WarehouseStock.warehouse_id,
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
        .group_by(Nomenclature.article_wb, WarehouseStock.warehouse_id)
    )
    out: dict[int, dict[int, int]] = {}
    for r in result.all():
        nm = int(r.article_wb)
        wh = int(r.warehouse_id)
        qty = int(r.qty or 0)
        if qty <= 0:
            continue
        out.setdefault(nm, {})[wh] = qty
    return out


async def _load_warehouse_total_days(db: AsyncSession, project_id: int, fallback_days: int) -> dict[int, int]:
    """For each FULFILLMENT warehouse, return total days RF→WB.

    total_days = assembly_days + avg(delivery_days across wb_warehouses) + wb_acceptance_days.
    If no `WarehouseDeliveryTime` rows exist for a warehouse, falls back to
    `fallback_days` (project setting).
    """
    from backend.models.warehouse import (
        Warehouse,
        WarehouseDeliveryTime,
        WarehouseType,
    )

    wh_result = await db.execute(
        select(
            Warehouse.id,
            Warehouse.assembly_days,
            Warehouse.wb_acceptance_days,
        ).where(
            Warehouse.project_id == project_id,
            Warehouse.warehouse_type == WarehouseType.FULFILLMENT,
            Warehouse.is_deleted.is_(False),
        )
    )
    warehouses = wh_result.all()

    avg_delivery_result = await db.execute(
        select(
            WarehouseDeliveryTime.warehouse_id,
            func.avg(WarehouseDeliveryTime.delivery_days).label("avg_days"),
        )
        .where(WarehouseDeliveryTime.project_id == project_id)
        .group_by(WarehouseDeliveryTime.warehouse_id)
    )
    avg_delivery: dict[int, float] = {int(r.warehouse_id): float(r.avg_days or 0) for r in avg_delivery_result.all()}

    out: dict[int, int] = {}
    for wh in warehouses:
        avg_d = avg_delivery.get(int(wh.id))
        if avg_d is None or avg_d <= 0:
            out[int(wh.id)] = max(0, int(fallback_days))
            continue
        assembly = int(wh.assembly_days or 0)
        acceptance = int(wh.wb_acceptance_days or 0)
        out[int(wh.id)] = max(0, int(assembly + round(avg_d) + acceptance))
    return out


async def _load_warehouse_post_assembly_days(db: AsyncSession, project_id: int, fallback_days: int) -> dict[int, int]:
    """Per warehouse: days from ready_date to WB arrival = avg(delivery) + wb_acceptance.

    Used for in-assembly ETA (ready_date already includes assembly time).
    Fallback if `WarehouseDeliveryTime` is empty: max(0, fallback_days - default_assembly_days).
    """
    from backend.models.warehouse import (
        Warehouse,
        WarehouseDeliveryTime,
        WarehouseType,
    )

    wh_result = await db.execute(
        select(
            Warehouse.id,
            Warehouse.wb_acceptance_days,
        ).where(
            Warehouse.project_id == project_id,
            Warehouse.warehouse_type == WarehouseType.FULFILLMENT,
            Warehouse.is_deleted.is_(False),
        )
    )
    warehouses = wh_result.all()

    avg_delivery_result = await db.execute(
        select(
            WarehouseDeliveryTime.warehouse_id,
            func.avg(WarehouseDeliveryTime.delivery_days).label("avg_days"),
        )
        .where(WarehouseDeliveryTime.project_id == project_id)
        .group_by(WarehouseDeliveryTime.warehouse_id)
    )
    avg_delivery: dict[int, float] = {int(r.warehouse_id): float(r.avg_days or 0) for r in avg_delivery_result.all()}

    fallback_post = max(0, int(fallback_days) - _DEFAULT_ASSEMBLY_DAYS)
    out: dict[int, int] = {}
    for wh in warehouses:
        avg_d = avg_delivery.get(int(wh.id))
        if avg_d is None or avg_d <= 0:
            out[int(wh.id)] = fallback_post
            continue
        acceptance = int(wh.wb_acceptance_days or 0)
        out[int(wh.id)] = max(0, int(round(avg_d) + acceptance))
    return out


async def _load_wb_breakdown(db: AsyncSession, project_id: int) -> dict[int, dict]:
    """Load WB stock breakdown per nm_id (sum across all WB warehouses).

    Returns {nm_id: {"quantity", "in_way_to_client", "in_way_from_client"}}.
    """
    result = await db.execute(
        select(
            WbWarehouseStock.nm_id,
            func.sum(WbWarehouseStock.quantity).label("qty"),
            func.sum(WbWarehouseStock.in_way_to_client).label("to_client"),
            func.sum(WbWarehouseStock.in_way_from_client).label("from_client"),
        )
        .where(WbWarehouseStock.project_id == project_id)
        .group_by(WbWarehouseStock.nm_id)
    )
    return {
        int(r.nm_id): {
            "quantity": int(r.qty or 0),
            "in_way_to_client": int(r.to_client or 0),
            "in_way_from_client": int(r.from_client or 0),
        }
        for r in result.all()
    }


async def _load_margin_pct(
    db: AsyncSession,
    project_id: int,
    trend_days: int,
) -> dict[int, float]:
    """Return {nm_id: margin_pct} for trend window.

    Reuses the same BDR pipeline as Сводные остатки so margin numbers match
    one-to-one. Window picked by closest of {7, 14, 30} ≥ trend_days.
    """
    from backend.models.cost import Nomenclature
    from backend.services.bdr_loaders import load_avg_costs, load_cost_overrides
    from backend.services.warehouse_stock_engine import _load_bdr_metrics

    nom_result = await db.execute(
        select(Nomenclature.id, Nomenclature.article_seller, Nomenclature.article_wb).where(
            Nomenclature.project_id == project_id,
            Nomenclature.article_wb.isnot(None),
        )
    )
    nm_id_to_nom_id: dict[int, int] = {}
    nom_to_article: dict[int, str | None] = {}
    nom_to_nm: dict[int, int] = {}
    for n in nom_result.all():
        nm_id_to_nom_id[int(n.article_wb)] = int(n.id)
        nom_to_article[int(n.id)] = n.article_seller
        nom_to_nm[int(n.id)] = int(n.article_wb)

    avg_costs = await load_avg_costs(db, project_id)
    overrides = await load_cost_overrides(db, project_id)
    cost_map: dict[int, float] = {}
    for nom_id, article in nom_to_article.items():
        key = (article or "").lower()
        if key and key in avg_costs:
            cost_map[nom_id] = avg_costs[key]
            continue
        nm_id = nom_to_nm.get(nom_id)
        if nm_id and overrides.get(nm_id, 0) > 0:
            cost_map[nom_id] = overrides[nm_id]

    bdr_map = await _load_bdr_metrics(db, project_id, nm_id_to_nom_id, cost_map)

    if trend_days <= 7:
        period_key = "trend_7"
    elif trend_days <= 14:
        period_key = "trend_14"
    else:
        period_key = "trend_30"

    out: dict[int, float] = {}
    for nom_id, metrics in bdr_map.items():
        nm_id = nom_to_nm.get(nom_id)
        if nm_id is None:
            continue
        trend = metrics.get(period_key) or {}
        rev = float(trend.get("revenue") or 0)
        pft = float(trend.get("profit") or 0)
        if rev > 0:
            out[nm_id] = round(pft / rev * 100, 1)
    return out


async def _load_realization(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
) -> dict[int, float]:
    """Load realization (BDR-style revenue) per nm_id for the period.

    Same formula as wb_bdr_service / warehouse_stock_engine:
    SUM(Продажа.retail_price_withdisc_rub) - SUM(Возврат.retail_price_withdisc_rub).
    Excludes 'Неопознанный товар' for parity with BDR. Returns {nm_id: revenue_rub}.
    """
    R = WbFinanceRow
    effective_dt = func.coalesce(R.sale_dt, R.rr_dt)
    date_filter = or_(
        effective_dt.between(date_from, date_to),
        and_(
            R.sale_dt.is_(None),
            R.rr_dt.is_(None),
            R.date_from >= date_from,
            R.date_to <= date_to,
        ),
    )
    result = await db.execute(
        select(
            R.nm_id,
            func.coalesce(
                func.sum(case((R.doc_type_name == "Продажа", R.retail_price_withdisc_rub), else_=0))
                - func.sum(case((R.doc_type_name == "Возврат", R.retail_price_withdisc_rub), else_=0)),
                0,
            ).label("realization"),
        )
        .where(
            R.project_id == project_id,
            date_filter,
            func.lower(func.coalesce(R.sa_name, "")) != "неопознанный товар",
        )
        .group_by(R.nm_id)
        .limit(10000)
    )
    return {int(r.nm_id): float(r.realization or 0) for r in result.all() if r.nm_id}


async def _load_buyout_pct(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
) -> dict[int, float]:
    """Load weighted avg buyout fraction (0..1) per nm_id.

    Wraps the single source-of-truth `bdr_loaders.load_cancel_stats` so BDR,
    warehouse summary and stock analytics all use identical buyout numbers.
    """
    from backend.services.bdr_loaders import load_cancel_stats

    stats = await load_cancel_stats(db, project_id, date_from, date_to)
    out: dict[int, float] = {}
    for nm_id, cs in stats.items():
        total = cs["total"]
        if total <= 0:
            continue
        delivered = total - cs["cancelled"]
        out[int(nm_id)] = max(0.0, min(1.0, delivered / total))
    return out


async def _load_in_assembly_with_eta(
    db: AsyncSession,
    project_id: int,
    post_assembly_days_map: dict[int, int],
    today_date: date,
) -> dict[int, list[dict]]:
    """Load active assembly requests as scheduled deliveries with ETA on WB.

    Returns {nm_id: [{qty, delivery_date, request_id}, ...]}.
    Statuses: PENDING / IN_PROGRESS / READY / VEHICLE_ASSIGNED (still physically on RF).

    ETA = ready_date + post_assembly_days[warehouse_id], где
    ready_date = estimated_ready_date OR (created_at + 3 дня по умолчанию),
    post_assembly_days = avg(delivery_days) + wb_acceptance_days (без assembly_days,
    т.к. ready_date уже учитывает время сборки).
    """
    from backend.models.assembly import (
        AssemblyRequest,
        AssemblyRequestItem,
        AssemblyStatus,
    )
    from backend.models.cost import Nomenclature

    in_progress_statuses = [
        AssemblyStatus.PENDING,
        AssemblyStatus.IN_PROGRESS,
        AssemblyStatus.READY,
        AssemblyStatus.VEHICLE_ASSIGNED,
    ]

    result = await db.execute(
        select(
            Nomenclature.article_wb,
            AssemblyRequestItem.quantity,
            AssemblyRequest.id.label("request_id"),
            AssemblyRequest.warehouse_id,
            AssemblyRequest.estimated_ready_date,
            AssemblyRequest.actual_ready_date,
            AssemblyRequest.created_at,
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .join(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_(in_progress_statuses),
            Nomenclature.article_wb.isnot(None),
        )
    )

    out: dict[int, list[dict]] = {}
    for r in result.all():
        ready = r.actual_ready_date or r.estimated_ready_date
        if ready is None:
            placement = r.created_at.date() if r.created_at else today_date
            ready = placement + timedelta(days=_DEFAULT_ASSEMBLY_DAYS)
        # If batch is "ready" in the past but not SHIPPED yet (no delivery_date),
        # it's physically still on our warehouse waiting for a vehicle. Schedule
        # delivery from today: max(ready, today) + post_assembly_days. Otherwise
        # stale READY/IN_PROGRESS would land in day-0 of the matrix (past-due
        # fallback in _build_forecast_with_transit) and look like "magic arrivals".
        effective_ready = max(ready, today_date)
        post_days = post_assembly_days_map.get(int(r.warehouse_id))
        if post_days is None:
            # warehouse has no delivery-time rows AND no fallback resolved earlier
            continue
        eta = effective_ready + timedelta(days=int(post_days))
        nm_id = int(r.article_wb)
        out.setdefault(nm_id, []).append(
            {
                "qty": int(r.quantity or 0),
                "delivery_date": eta,
                "request_id": int(r.request_id),
            }
        )
    return out


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

    # Only SHIPPED — stock already deducted from RF warehouse but not yet on WB.
    # PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED are still physically on RF warehouse
    # and already counted in stocks_rf, so including them would cause double counting.
    active_statuses = [
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
    remaining: float = stocks_wb
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
        # Floor at 0: marketplace stocks=0 не копит «отложенный спрос». Без клампа
        # дни между поступлениями уводили remaining в минус, и новая партия гасила
        # этот виртуальный дефицит (85 шт после 5 пустых дней показывались как 20).
        # Семантика: пустой остаток просто не продаётся — продажа не теряется в виде
        # отложенной потребности.
        if remaining < 0:
            remaining = 0
        forecast.append(int(remaining + 0.5))

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
      - wb_rf_transit: WB + RF + on-assembly + in-transit (full pipeline)
      - wb_assembly_transit: WB + on-assembly + in-transit (исключает свободный РФ)
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
    rf_stocks_per_wh: dict[int, dict[int, int]] = {}
    rf_total_days_map: dict[int, int] = {}
    rf_post_assembly_days_map: dict[int, int] = {}
    transit_map: dict[int, list[dict]] = {}
    in_assembly_map: dict[int, list[dict]] = {}

    if mode in ("wb_rf", "wb_rf_transit", "wb_assembly_transit"):
        from backend.services.settings_service import get_forecast_rf_default_days

        rf_stocks_per_wh = await _load_rf_stocks_per_warehouse(db, project_id)
        fallback_days = await get_forecast_rf_default_days(db, project_id)
        rf_total_days_map = await _load_warehouse_total_days(db, project_id, fallback_days)
        rf_post_assembly_days_map = await _load_warehouse_post_assembly_days(db, project_id, fallback_days)

    if mode in ("wb_rf_transit", "wb_assembly_transit"):
        transit_map = await _load_in_transit(db, project_id)
        in_assembly_map = await _load_in_assembly_with_eta(db, project_id, rf_post_assembly_days_map, today)

    # 6b. Effective WB stock = quantity + in_way_from_client + in_way_to_client * (1 - buyout)
    # Reflects "useful stock" — what is on warehouse + guaranteed returns from clients.
    wb_breakdown_map = await _load_wb_breakdown(db, project_id)
    buyout_map = await _load_buyout_pct(db, project_id, actual_trend_start, data_date)

    # 6c. BDR realization (revenue rub) per nm_id over the trend window — same
    # formula as warehouse summary "Реализ. БДР" column.
    realization_map = await _load_realization(db, project_id, actual_trend_start, data_date)
    # 6d. BDR margin % per nm_id — reuses Сводные остатки pipeline so numbers
    # match one-to-one. Independent of mode (loaded always).
    margin_pct_map = await _load_margin_pct(db, project_id, trend_days)

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

        # Effective WB stock — prefer per-warehouse breakdown; fallback to funnel value.
        wb_b = wb_breakdown_map.get(nm_id)
        buyout_pct = buyout_map.get(nm_id, 1.0)
        if wb_b is not None:
            stocks_wb = int(
                wb_b["quantity"] + wb_b["in_way_from_client"] + wb_b["in_way_to_client"] * (1.0 - buyout_pct) + 0.5
            )
        else:
            stocks_wb = stock_info["stocks_wb"]
        rf_per_wh = rf_stocks_per_wh.get(nm_id, {})
        stocks_rf_total = sum(rf_per_wh.values())
        transit_entries = transit_map.get(nm_id, [])
        in_transit_total = sum(e["qty"] for e in transit_entries)
        assembly_entries = in_assembly_map.get(nm_id, [])
        in_assembly_total = sum(e["qty"] for e in assembly_entries)
        # Free RF = qty NOT yet reserved by an active assembly request.
        # On-assembly qty физически на РФ, но в матрицу заходит отдельной поставкой
        # на ready_date + post_assembly_days, поэтому из «свободный РФ → WB»
        # исключаем чтобы не получить двойной счёт.
        free_rf = max(0, stocks_rf_total - in_assembly_total)

        # Total stock — used for days_left / traffic-light KPI.
        if mode == "wb_rf_transit":
            total_stock = stocks_wb + stocks_rf_total + in_transit_total
        elif mode == "wb_rf":
            total_stock = stocks_wb + stocks_rf_total
        elif mode == "wb_assembly_transit":
            # Только то что физически уже движется/собирается в сторону WB.
            total_stock = stocks_wb + in_assembly_total + in_transit_total
        else:
            total_stock = stocks_wb

        o30 = orders_30d_map.get(nm_id, {})
        avg_daily = avg_daily_map.get(nm_id, 0)
        prev_avg = prev_avg_map.get(nm_id, 0)

        days_left = compute_days_left(total_stock, avg_daily)
        trend_pct = compute_trend_pct(avg_daily, prev_avg)
        traffic = classify_traffic_light(days_left)

        # RF arrives on WB after avg(total_days) — synthetic transit entry.
        # Simple arithmetic mean across warehouses that hold any qty
        # (no qty-weighting; per user spec, variant B).
        rf_avg_days: int | None = None
        rf_synthetic: list[dict] = []
        if mode in ("wb_rf", "wb_rf_transit") and free_rf > 0 and rf_per_wh:
            wh_days_typed: list[int] = [d for wh_id in rf_per_wh if (d := rf_total_days_map.get(wh_id)) is not None]
            if wh_days_typed:
                rf_avg_days = max(0, int(round(sum(wh_days_typed) / len(wh_days_typed))))
                rf_synthetic.append(
                    {
                        "qty": free_rf,
                        "delivery_date": today_date + timedelta(days=rf_avg_days),
                    }
                )

        # Forecast — base = WB only; deliveries depend on mode:
        #   wb_rf            → free-RF only
        #   wb_rf_transit    → free-RF + on-assembly + in-transit
        #   wb_assembly_transit → on-assembly + in-transit (БЕЗ свободного РФ)
        if mode == "wb_assembly_transit":
            deliveries_for_matrix = assembly_entries + transit_entries
        elif mode == "wb_rf":
            deliveries_for_matrix = rf_synthetic
        elif mode == "wb_rf_transit":
            deliveries_for_matrix = rf_synthetic + assembly_entries + transit_entries
        else:
            deliveries_for_matrix = []

        if mode != "wb" and deliveries_for_matrix:
            forecast = _build_forecast_with_transit(
                stocks_wb,
                avg_daily,
                deliveries_for_matrix,
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
            "wb_buyout_pct": round(buyout_pct * 100, 2),
            "revenue_bdr": round(realization_map.get(nm_id, 0.0), 2),
            "margin_pct": margin_pct_map.get(nm_id),
            "days_left": days_left,
            "traffic_light": traffic,
            "forecast": forecast,
        }

        # Extra fields for modes
        if mode in ("wb_rf", "wb_rf_transit"):
            article_data["rf_avg_days"] = rf_avg_days
        if mode == "wb_rf":
            # Free RF only (in-assembly не виден в этом режиме).
            article_data["stocks_rf"] = free_rf
        if mode == "wb_rf_transit":
            # Show RF as free stock (physical RF minus reserved in assembly requests).
            # Physical total (for days_left) remains unchanged — same goods,
            # just split between "free RF", "on assembly", "in transit" columns.
            article_data["stocks_rf"] = free_rf
            article_data["in_assembly"] = in_assembly_total
            article_data["in_transit"] = in_transit_total
        if mode == "wb_assembly_transit":
            # RF shown info-only — does NOT feed deliveries_for_matrix in this mode
            # (mode answers "what arrives on WB if RF stays put"), but the column
            # must still be visible so the user sees the full warehouse picture.
            article_data["stocks_rf"] = free_rf
            article_data["in_assembly"] = in_assembly_total
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
