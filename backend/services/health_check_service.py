"""Daily health check service for Wildberries e-commerce analytics.

Runs 4 independent checks and returns a structured report:
1. Urgent shipments (deficit on WB warehouses)
2. Overdue assembly requests
3. Category-A product health
4. Illiquid product actions
"""

import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.assembly import (
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
)
from backend.models.cost import Nomenclature
from backend.utils.time import utcnow

logger = logging.getLogger("dds.health_check")

# Statuses considered "active" for assembly cross-check
_ACTIVE_ASSEMBLY_STATUSES = [
    AssemblyStatus.PENDING,
    AssemblyStatus.IN_PROGRESS,
    AssemblyStatus.READY,
    AssemblyStatus.VEHICLE_ASSIGNED,
]

# Statuses that can be overdue
_OVERDUE_ASSEMBLY_STATUSES = [
    AssemblyStatus.PENDING,
    AssemblyStatus.IN_PROGRESS,
    AssemblyStatus.READY,
    AssemblyStatus.VEHICLE_ASSIGNED,
    AssemblyStatus.SHIPPED,
]


async def get_daily_health(
    db: AsyncSession,
    project_id: int,
    tax_info: dict,
    brand: str | None = None,
) -> dict:
    """Run all health checks and return structured report.

    Each check is independent: if one fails, others still return data.
    """
    urgent_shipments: list[dict] = []
    overdue_assemblies: list[dict] = []
    category_a_health: list[dict] = []
    illiquid_actions: list[dict] = []

    # --- Check 1: Urgent shipments ---
    try:
        urgent_shipments = await _check_urgent_shipments(db, project_id)
    except Exception:
        logger.exception("Health check: urgent_shipments failed for project %s", project_id)
        pass  # graceful: continue with other checks

    # --- Check 2: Overdue assemblies ---
    try:
        overdue_assemblies = await _check_overdue_assemblies(db, project_id)
    except Exception:
        logger.exception("Health check: overdue_assemblies failed for project %s", project_id)
        pass  # graceful: continue with other checks

    # --- Check 3: Category-A health ---
    try:
        category_a_health = await _check_category_a_health(
            db, project_id, tax_info, brand,
        )
    except Exception:
        logger.exception("Health check: category_a_health failed for project %s", project_id)
        pass  # graceful: continue with other checks

    # --- Check 4: Illiquid actions ---
    try:
        illiquid_actions = await _check_illiquid_actions(
            db, project_id, tax_info, brand,
        )
    except Exception:
        logger.exception("Health check: illiquid_actions failed for project %s", project_id)
        pass  # graceful: continue with other checks

    # --- Summary ---
    urgent_without_assembly = sum(
        1 for item in urgent_shipments if not item["has_assembly"]
    )
    category_a_issue_count = sum(
        len(item["issues"]) for item in category_a_health
    )

    health_score = 100
    health_score -= 5 * urgent_without_assembly
    health_score -= 10 * len(overdue_assemblies)
    health_score -= 3 * category_a_issue_count
    health_score -= 2 * len(illiquid_actions)
    health_score = max(0, health_score)

    frozen_capital = sum(item["frozen_value"] for item in illiquid_actions)

    return {
        "urgent_shipments": urgent_shipments,
        "overdue_assemblies": overdue_assemblies,
        "category_a_health": category_a_health,
        "illiquid_actions": illiquid_actions,
        "summary": {
            "urgent_count": len(urgent_shipments),
            "overdue_count": len(overdue_assemblies),
            "category_a_issues": category_a_issue_count,
            "illiquid_count": len(illiquid_actions),
            "frozen_capital": round(frozen_capital, 2),
            "health_score": health_score,
        },
    }


# ---------------------------------------------------------------------------
# Check 1: Urgent shipments
# ---------------------------------------------------------------------------

async def _check_urgent_shipments(
    db: AsyncSession,
    project_id: int,
) -> list[dict]:
    """Find articles with deficit on WB and cross-check assembly requests."""
    from backend.services.warehouse_stock_service import get_warehouse_need

    need_data = await get_warehouse_need(db, project_id)
    articles = need_data.get("articles", [])

    # Filter to articles with deficit AND available RF stock to send
    deficit_articles = [
        a for a in articles
        if a.get("deficit", 0) > 0 and a.get("can_send", 0) > 0
    ]
    if not deficit_articles:
        return []

    # Collect nm_ids to check for active assembly requests
    nm_ids = [a["nm_id"] for a in deficit_articles]

    # Query active assembly requests for these nm_ids via Nomenclature.article_wb
    active_assembly_nm_ids: set[int] = set()
    if nm_ids:
        asm_result = await db.execute(
            select(Nomenclature.article_wb)
            .join(
                AssemblyRequestItem,
                AssemblyRequestItem.nomenclature_id == Nomenclature.id,
            )
            .join(
                AssemblyRequest,
                AssemblyRequest.id == AssemblyRequestItem.assembly_request_id,
            )
            .where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted.is_(False),
                AssemblyRequest.status.in_(_ACTIVE_ASSEMBLY_STATUSES),
                Nomenclature.article_wb.in_(nm_ids),
            )
            .group_by(Nomenclature.article_wb)
        )
        active_assembly_nm_ids = {row[0] for row in asm_result if row[0] is not None}

    # Build result — pick the warehouse with the highest need per article
    # Articles from get_warehouse_need already have aggregated data
    result: list[dict] = []
    for art in deficit_articles:
        nm_id = art["nm_id"]

        # Find the WB warehouse with the biggest need for this article
        # from the warehouses list in need_data
        target_warehouse = ""
        max_need = 0
        for wh in need_data.get("warehouses", []):
            wh_art = wh.get("articles", {}).get(nm_id)
            if wh_art and wh_art.get("need", 0) > max_need:
                max_need = wh_art["need"]
                target_warehouse = wh.get("name", "")

        # days_left on WB: stocks_wb / avg_daily
        stocks_wb = art.get("stocks_wb", 0)
        # Estimate avg_daily from any warehouse data
        total_avg_daily = 0.0
        for wh in need_data.get("warehouses", []):
            wh_art = wh.get("articles", {}).get(nm_id)
            if wh_art:
                total_avg_daily += wh_art.get("avg_daily", 0)
        days_left_wb = round(stocks_wb / total_avg_daily, 1) if total_avg_daily > 0 else 0.0

        result.append({
            "nm_id": nm_id,
            "vendor_code": art.get("vendor_code", ""),
            "subject": art.get("subject", ""),
            "deficit": art["deficit"],
            "can_send": art.get("can_send", 0),
            "has_assembly": nm_id in active_assembly_nm_ids,
            "days_left_wb": days_left_wb,
            "warehouse": target_warehouse,
        })

    result.sort(key=lambda x: x["days_left_wb"])
    return result


# ---------------------------------------------------------------------------
# Check 2: Overdue assemblies
# ---------------------------------------------------------------------------

async def _check_overdue_assemblies(
    db: AsyncSession,
    project_id: int,
) -> list[dict]:
    """Find assembly requests past their delivery date."""
    today = utcnow().date()

    stmt = (
        select(AssemblyRequest)
        .options(selectinload(AssemblyRequest.items))
        .options(selectinload(AssemblyRequest.warehouse))
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_(_OVERDUE_ASSEMBLY_STATUSES),
            AssemblyRequest.delivery_date < today,
            AssemblyRequest.delivery_date.isnot(None),
        )
        .order_by(AssemblyRequest.delivery_date)
        .limit(100)
    )
    result = await db.execute(stmt)
    requests = result.scalars().all()

    overdue: list[dict] = []
    for req in requests:
        days_overdue = (today - req.delivery_date).days
        items_count = len(req.items) if req.items else 0
        total_qty = sum(item.quantity for item in req.items) if req.items else 0

        overdue.append({
            "id": req.id,
            "number": req.number,
            "status": req.status,
            "warehouse_name": req.warehouse.name if req.warehouse else "",
            "delivery_date": req.delivery_date.isoformat(),
            "days_overdue": days_overdue,
            "items_count": items_count,
            "total_qty": total_qty,
        })

    return overdue


# ---------------------------------------------------------------------------
# Check 3: Category-A product health
# ---------------------------------------------------------------------------

async def _check_category_a_health(
    db: AsyncSession,
    project_id: int,
    tax_info: dict,
    brand: str | None = None,
) -> list[dict]:
    """Check health of top revenue products (ABC profit = A)."""
    from backend.services.stock_forecast_service import get_stock_analytics

    # Get stock analytics with RF + transit data
    stock_data = await get_stock_analytics(
        db, project_id, mode="wb_rf_transit", brand_filter=brand,
    )
    stock_articles = stock_data.get("articles", [])
    stock_map: dict[int, dict] = {a["nm_id"]: a for a in stock_articles}

    # Get funnel data with ABC classification (last 30 days)
    from backend.services.funnel.queries import get_funnel_by_sku

    today = utcnow().date()
    date_from = (today - timedelta(days=30)).isoformat()
    date_to = today.isoformat()

    sku_data = await get_funnel_by_sku(
        db, project_id, tax_info,
        date_from=date_from,
        date_to=date_to,
        brand=brand,
        subject=None,
    )

    # Filter to Category A by profit
    category_a = [p for p in sku_data if p.get("abc_profit") == "A"]

    result: list[dict] = []
    for product in category_a:
        nm_id = product["nm_id"]
        stock_info = stock_map.get(nm_id, {})
        days_left = stock_info.get("days_left", 0.0)
        stocks_total = stock_info.get("stocks_wb", 0)
        if "stocks_rf" in stock_info:
            stocks_total += stock_info.get("stocks_rf", 0)
        if "in_transit" in stock_info:
            stocks_total += stock_info.get("in_transit", 0)

        margin_pct = product.get("margin_pct", 0.0)
        drr = product.get("drr", 0.0)
        revenue_30d = product.get("revenue", 0.0)

        # Build issues list
        issues: list[str] = []
        if days_left < 14:
            issues.append("stock-out risk")
        if drr > 10:
            issues.append("high DRR")
        if margin_pct < 12:
            issues.append("margin drop")

        if not issues:
            continue

        result.append({
            "nm_id": nm_id,
            "vendor_code": product.get("vendor_code", ""),
            "subject": product.get("subject", ""),
            "revenue_30d": round(revenue_30d, 2),
            "margin_pct": round(margin_pct, 2),
            "drr": round(drr, 2),
            "days_left": round(days_left, 1),
            "stocks_total": stocks_total,
            "issues": issues,
        })

    result.sort(key=lambda x: x["revenue_30d"], reverse=True)
    return result


# ---------------------------------------------------------------------------
# Check 4: Illiquid product actions
# ---------------------------------------------------------------------------

def _decide_illiquid_action(
    margin: float,
    turnover_days: float,
    drr: float,
) -> tuple[str, str]:
    """Apply decision tree for illiquid product recommendation.

    Returns (recommendation_code, reason_text).

    Thresholds adjusted for China sourcing cycle:
    Order: 25d + Delivery: 15d + WB shipment: 14d + Min stock: 20d = 74 days.
    So turnover < 75 days is NORMAL for this business.
    """
    # Decision tree: что лучше — снизить цену или увеличить рекламу?

    if margin > 20 and drr < 5:
        return (
            "increase_ads",
            f"Маржа {margin:.0f}%, ДРР всего {drr:.0f}% — увеличь рекламу, маржа позволяет",
        )
    if margin > 15 and drr < 10:
        return (
            "increase_ads",
            f"Маржа {margin:.0f}% позволяет увеличить рекламу (ДРР {drr:.0f}%)",
        )
    if drr > 15 and margin > 5:
        return (
            "cut_ads_reduce_price",
            f"ДРР {drr:.0f}% слишком высокий — убери рекламу, снизь цену на 10%",
        )
    if drr > 15 and margin <= 5:
        return (
            "cut_ads_reduce_price",
            f"ДРР {drr:.0f}%, маржа {margin:.0f}% — убери рекламу, снизь цену на 15-20%",
        )
    if margin < 0:
        return (
            "deep_sale",
            f"Убыточный товар (маржа {margin:.0f}%) — поставь максимальную скидку, распродавай",
        )
    if turnover_days > 150:
        # Более 2x полного цикла — критично
        return (
            "deep_sale",
            f"Оборачиваемость {turnover_days:.0f} дней (2+ цикла закупки) — агрессивная распродажа",
        )
    if turnover_days > 100:
        return (
            "reduce_price_20",
            f"Оборачиваемость {turnover_days:.0f} дней — снизь цену на 15-20%",
        )
    if margin > 5:
        return (
            "reduce_price_10",
            f"Замедляется ({turnover_days:.0f} дн) — снизь цену на 10%",
        )
    return (
        "reduce_price_10",
        f"Оборачиваемость {turnover_days:.0f} дн, маржа {margin:.0f}% — снизь цену на 10%",
    )


async def _check_illiquid_actions(
    db: AsyncSession,
    project_id: int,
    tax_info: dict,
    brand: str | None = None,
) -> list[dict]:
    """Find illiquid products and generate action recommendations."""
    from backend.services.funnel.capital import get_capital_analysis

    capital_data = await get_capital_analysis(
        db, project_id, tax_info,
        period_days=30,
        brand=brand,
        group_by="article",
        include_rf_stocks=True,
    )

    groups = capital_data.get("groups", [])

    result: list[dict] = []
    for group in groups:
        turnover_days = group.get("turnover_days", 0.0)
        # Skip normal turnover: China sourcing cycle = 74 days
        # (order 25d + delivery 15d + WB shipment 14d + min stock 20d)
        if turnover_days <= 75:
            continue

        margin = group.get("margin", 0.0)
        drr = group.get("drr", 0.0)
        frozen_value = group.get("frozen_amount", 0.0)

        recommendation, reason = _decide_illiquid_action(margin, turnover_days, drr)

        nm_id = group.get("nm_id", 0)
        vendor_code = group.get("vendor_code") or group.get("group_key", "")
        # Get stocks from capital data if available
        stocks_total = 0
        capital_val = group.get("capital", 0.0)

        result.append({
            "nm_id": nm_id,
            "vendor_code": vendor_code,
            "subject": group.get("subject", ""),
            "turnover_days": round(turnover_days, 1),
            "margin_pct": round(margin, 2),
            "drr": round(drr, 2),
            "frozen_value": round(frozen_value, 2),
            "stocks_total": stocks_total,
            "recommendation": recommendation,
            "reason": reason,
        })

    result.sort(key=lambda x: x["frozen_value"], reverse=True)
    return result
