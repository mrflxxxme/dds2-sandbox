"""
BDR data loaders — async DB queries to fetch ads, orders/stocks, cost prices, tax settings.

Split from wb_bdr_service.py for maintainability (<400 LOC rule).
"""

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CostOrderItem, WbCostOverride, WbFunnelDaily
from backend.models.wb_order_cancel import WbOrderCancelDaily

logger = logging.getLogger("dds.bdr_loaders")

D = Decimal
ZERO = D("0")


# ─── Ads loader ──────────────────────────────────────────────────────────────


async def load_ads(db: AsyncSession, pid: int, d_from: date, d_to: date) -> dict[int, float]:
    """Load total ad spend per nm_id from WbFunnelDaily."""
    result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.sum(WbFunnelDaily.adv_sum).label("total_adv"),
        )
        .where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.date >= d_from,
            WbFunnelDaily.date <= d_to,
        )
        .group_by(WbFunnelDaily.nm_id)
    )
    return {r.nm_id: float(r.total_adv or 0) for r in result}


async def load_ads_monthly(db: AsyncSession, pid: int, d_from: date, d_to: date) -> dict[str, dict[int, float]]:
    """Load ad spend per nm_id grouped by month (YYYY-MM).

    Single query replaces N per-month calls to load_ads().
    Returns: { "2025-01": {nm_id: total_adv, ...}, ... }
    """
    month_label = func.to_char(WbFunnelDaily.date, "YYYY-MM").label("month_key")
    result = await db.execute(
        select(
            month_label,
            WbFunnelDaily.nm_id,
            func.sum(WbFunnelDaily.adv_sum).label("total_adv"),
        )
        .where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.date >= d_from,
            WbFunnelDaily.date <= d_to,
        )
        .group_by(month_label, WbFunnelDaily.nm_id)
    )
    monthly: dict[str, dict[int, float]] = {}
    for r in result:
        monthly.setdefault(r.month_key, {})[r.nm_id] = float(r.total_adv or 0)
    return monthly


# ─── Orders & Stocks loader ─────────────────────────────────────────────────


async def load_orders_stocks(db: AsyncSession, pid: int, d_from: date, d_to: date) -> dict[int, dict]:
    """Load orders count/sum and latest stocks per nm_id from WbFunnelDaily."""
    # Orders aggregation
    result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.sum(WbFunnelDaily.orders_count).label("orders_count"),
            func.sum(WbFunnelDaily.orders_sum_rub).label("orders_sum"),
        )
        .where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.date >= d_from,
            WbFunnelDaily.date <= d_to,
        )
        .group_by(WbFunnelDaily.nm_id)
    )
    orders_map = {}
    for r in result:
        orders_map[r.nm_id] = {
            "orders_count": int(r.orders_count or 0),
            "orders_sum": float(r.orders_sum or 0),
        }

    # Latest stocks (last day in period)
    sub = (
        select(
            WbFunnelDaily.nm_id,
            WbFunnelDaily.stocks_wb,
            func.row_number()
            .over(
                partition_by=WbFunnelDaily.nm_id,
                order_by=desc(WbFunnelDaily.date),
            )
            .label("rn"),
        )
        .where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.date >= d_from,
            WbFunnelDaily.date <= d_to,
        )
        .subquery()
    )

    stocks_result = await db.execute(select(sub.c.nm_id, sub.c.stocks_wb).where(sub.c.rn == 1))
    for r in stocks_result:
        if r.nm_id in orders_map:
            orders_map[r.nm_id]["stocks_wb"] = int(r.stocks_wb or 0)
        else:
            orders_map[r.nm_id] = {"orders_count": 0, "orders_sum": 0, "stocks_wb": int(r.stocks_wb or 0)}

    return orders_map


# ─── Cost loader ─────────────────────────────────────────────────────────────


async def load_avg_costs(db: AsyncSession, pid: int) -> dict[str, float]:
    """Load weighted average cost per article_seller from cost_order_items.

    Joins CostOrder to filter by project_id.
    Weighted avg = SUM(total_rub * qty) / SUM(qty) — correct for varying batch sizes.

    Keys are lowercased to allow case-insensitive lookup against WB-side strings
    (wb_funnel_daily.vendor_code, wb_finance_rows.sa_name, nomenclature.article_seller),
    which can differ in case from user-entered article_seller in cost_order_items
    (e.g. 'palatka_зеленая' vs 'PALATKA_зеленая'). All callers must lowercase their
    lookup key. Aggregation is done on lowercased key in SQL so weighted average
    stays correct even if same article was entered with different casing.
    """
    from backend.models.cost import CostOrder

    article_lower = func.lower(CostOrderItem.article_seller).label("article_seller_lower")
    result = await db.execute(
        select(
            article_lower,
            (func.sum(CostOrderItem.total_rub * CostOrderItem.qty) / func.nullif(func.sum(CostOrderItem.qty), 0)).label(
                "avg_cost"
            ),
        )
        .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
        .where(
            CostOrder.project_id == pid,
            CostOrder.is_deleted == False,
            CostOrderItem.is_deleted == False,
            CostOrderItem.article_seller.isnot(None),
            CostOrderItem.total_rub.isnot(None),
            CostOrderItem.qty > 0,
        )
        .group_by(article_lower)
    )
    return {r.article_seller_lower: float(r.avg_cost or 0) for r in result if r.article_seller_lower}


# ─── Cost overrides loader ───────────────────────────────────────────────────


async def load_cost_overrides(db: AsyncSession, pid: int) -> dict[int, float]:
    """Load manual cost overrides keyed by nm_id from WbCostOverride.

    Used as fallback when cost_order_items don't have data for an article.
    """
    result = await db.execute(
        select(WbCostOverride.nm_id, WbCostOverride.cost_price).where(
            WbCostOverride.project_id == pid,
        )
    )
    return {r.nm_id: float(r.cost_price or 0) for r in result}


# ─── Tax loader ──────────────────────────────────────────────────────────────


async def load_tax_settings(db: AsyncSession, pid: int, d_from: date, d_to: date) -> dict:
    """Load tax rate settings for the period.

    Uses the month/year from date_from. For multi-month ranges,
    takes the first month's settings (simplified).
    """
    from backend.models.tax import TaxRate

    year = d_from.year
    month = d_from.month

    result = await db.execute(
        select(TaxRate)
        .where(
            TaxRate.project_id == pid,
            TaxRate.year == year,
            TaxRate.month == month,
            TaxRate.brand == "__project__",
        )
        .limit(1)
    )
    row = result.scalar_one_or_none()

    if not row:
        return {
            "tax_regime": "usn_income",
            "usn_rate": 0,
            "nds_rate": 0,
            "cost_as_expense": False,
        }

    return {
        "tax_regime": row.tax_regime,
        "usn_rate": float(row.usn_rate or 0),
        "nds_rate": float(row.nds_rate or 0),
        "cost_as_expense": row.cost_as_expense or False,
    }


# ─── Cancel stats loader (for accurate buyout_pct) ──────────────────────────


async def load_cancel_stats(db: AsyncSession, pid: int, d_from: date, d_to: date) -> dict[int, dict[str, int]]:
    """Load order cancel stats per nm_id from wb_order_cancel_daily.

    Returns: {nm_id: {"total": N, "cancelled": M}, ...}
    """
    result = await db.execute(
        select(
            WbOrderCancelDaily.nm_id,
            func.sum(WbOrderCancelDaily.total_orders).label("total"),
            func.sum(WbOrderCancelDaily.cancelled_orders).label("cancelled"),
        )
        .where(
            WbOrderCancelDaily.project_id == pid,
            WbOrderCancelDaily.dt >= d_from,
            WbOrderCancelDaily.dt <= d_to,
        )
        .group_by(WbOrderCancelDaily.nm_id)
    )
    return {r.nm_id: {"total": int(r.total or 0), "cancelled": int(r.cancelled or 0)} for r in result}
