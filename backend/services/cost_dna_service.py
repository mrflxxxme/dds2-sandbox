"""
Cost-DNA service — per-category breakdown of revenue.

Decomposes each ruble of revenue per WB category into:
- Cost components (factory, duty, delivery, VAT) — from cost_order_items
- Marketplace fees (commission, logistics, storage, advertising, other) — from wb_finance_rows
- Taxes — from TaxRate
- Margin

Used as a forecasting tool to compare prognosis cost vs realization,
and as fallback for new SKUs without cost data.

Pattern: revenue + WB-fees come from wb_finance_rows GROUP BY subject_name,
ad spend comes from wb_funnel_daily.adv_sum GROUP BY subject (BDR/OPIU pattern).
408k untagged wb_finance_rows (subject_name='') are ignored; per-category ad
spend from wb_funnel_daily covers them.
"""

import logging
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.schemas.reports import CostDnaCategory, CostDnaResponse, CostDnaTotals
from backend.services import cost_dna_helpers as helpers
from backend.services.bdr_loaders import load_tax_settings

logger = logging.getLogger("dds.cost_dna")


@cached(prefix="reports:cost_dna", ttl=300)
async def get_cost_dna(db: AsyncSession, project_id: int, period_days: int) -> dict:
    """
    Compute Cost-DNA report for a project.

    Args:
        db: Async DB session.
        project_id: Project ID for multi-tenant filter.
        period_days: Rolling period (30 or 60 days back from yesterday).

    Returns:
        CostDnaResponse as dict (Pydantic .model_dump() — for cache serialization).
    """
    if period_days not in (30, 60):
        period_days = 30

    yesterday = date.today() - timedelta(days=1)
    date_to = yesterday
    date_from = yesterday - timedelta(days=period_days - 1)
    prev_date_to = date_from - timedelta(days=1)
    prev_date_from = prev_date_to - timedelta(days=period_days - 1)

    # Current period
    categories, totals, has_tax = await _compute_period(db, project_id, date_from, date_to)

    # Previous period — for trend (only need totals.margin_pct + per-category margin_pct)
    try:
        prev_categories, prev_totals, _ = await _compute_period(db, project_id, prev_date_from, prev_date_to)
        prev_margin_by_cat = {c["category"]: c["margin_pct"] for c in prev_categories}
    except Exception as e:
        logger.warning("cost_dna prev period compute failed: %s", e)
        prev_margin_by_cat = {}
        prev_totals = None

    # Attach trend to current categories
    enriched_cats: list[CostDnaCategory] = []
    for c in categories:
        prev_m = prev_margin_by_cat.get(c["category"])
        c["margin_pct_prev"] = prev_m
        if c["margin_pct"] is not None and prev_m is not None:
            c["margin_trend"] = "up" if c["margin_pct"] >= prev_m else "down"
        else:
            c["margin_trend"] = None
        # strip private temp fields before Pydantic validation
        clean = {k: v for k, v in c.items() if not k.startswith("_")}
        enriched_cats.append(CostDnaCategory(**clean))

    totals_obj = CostDnaTotals(**totals)
    if prev_totals is not None:
        totals_obj.margin_pct_prev = prev_totals["margin_pct"]
        totals_obj.margin_trend = "up" if totals_obj.margin_pct >= prev_totals["margin_pct"] else "down"

    response = CostDnaResponse(
        period_days=period_days,
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        prev_date_from=prev_date_from.isoformat(),
        prev_date_to=prev_date_to.isoformat(),
        categories=enriched_cats,
        totals=totals_obj,
        has_tax_settings=has_tax,
    )
    return response.model_dump()


async def _compute_period(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
) -> tuple[list[dict], dict, bool]:
    """Compute category breakdown + totals for a single date range.

    Returns:
        (list of category dicts with private _* fields, totals dict, has_tax flag)
    """
    # 1. Revenue + WB-fees per subject
    sql = helpers.build_revenue_by_subject_sql()
    result = await db.execute(
        text(sql),
        {"project_id": project_id, "date_from": date_from, "date_to": date_to},
    )
    revenue_rows = result.fetchall()

    # 2. Cost components per subject — weighted by actual sales in the period.
    #    Per-article cost (weighted by purchase qty) * per-article sale qty in
    #    [date_from, date_to], then summed by subject. Fixes the 2026-04-14 bug
    #    where subject-level weighting by purchase qty inflated cost_total when
    #    bulk-bought slow-sellers differed from sale mix.
    cost_by_subject = await helpers.load_cost_components_by_subject(db, project_id, date_from, date_to)

    # 3. Ad spend per subject for the period
    ads_by_subject = await helpers.load_ads_by_subject(db, project_id, date_from, date_to)

    # 4. Tax settings (per-period — uses date_from month)
    tax_info = await load_tax_settings(db, project_id, date_from, date_to)
    has_tax = float(tax_info.get("usn_rate", 0)) > 0 or float(tax_info.get("nds_rate", 0)) > 0

    # 5. Compute per-category metrics
    categories: list[dict] = []
    for row in revenue_rows:
        cost_row = cost_by_subject.get(row.subject)
        adv = ads_by_subject.get(row.subject, 0)
        cat = helpers.compute_category_metrics(row, cost_row, adv, tax_info)
        categories.append(cat)

    # 6. Sort by revenue desc, compute share
    categories.sort(key=lambda c: c["revenue"], reverse=True)
    total_revenue = sum(c["revenue"] for c in categories)
    if total_revenue > 0:
        for c in categories:
            c["revenue_share_pct"] = round(c["revenue"] / total_revenue * 100, 2)

    # 7. Totals row — aggregate across categories
    totals = _aggregate_totals(categories, tax_info)

    return categories, totals, has_tax


def _aggregate_totals(categories: list[dict], tax_info: dict) -> dict:
    """Sum component RUB across categories, then express as % of total revenue."""
    total_revenue = sum(c["revenue"] for c in categories)
    if total_revenue <= 0:
        return {
            "revenue": 0,
            "cost_factory_pct": 0,
            "cost_duty_pct": 0,
            "cost_delivery_pct": 0,
            "cost_vat_pct": 0,
            "cost_total_pct": 0,
            "mp_commission_pct": 0,
            "mp_logistics_pct": 0,
            "mp_storage_pct": 0,
            "mp_advertising_pct": 0,
            "mp_other_pct": 0,
            "mp_total_pct": 0,
            "tax_pct": 0,
            "margin_pct": 0,
            "margin_pct_prev": None,
            "margin_trend": None,
        }

    sum_logistics = sum(c["_logistics"] for c in categories)
    sum_storage = sum(c["_storage"] for c in categories)
    sum_mp_commission = sum(c["_mp_commission"] for c in categories)
    sum_opiu_commission = sum(c["_opiu_commission"] for c in categories)
    sum_penalty = sum(c["_penalty"] for c in categories)
    sum_acceptance = sum(c["_acceptance"] for c in categories)
    sum_other_deduction = sum(c["_other_deduction"] for c in categories)
    sum_other_combined = sum_penalty + sum_acceptance + sum_other_deduction
    sum_adv = sum(c["_adv_sum"] for c in categories)
    sum_sales = sum(c["_sales_amount"] for c in categories)

    # Cost totals — sum of projected per-category cost (already qty-projected)
    sum_proj_factory = sum(c["_proj_factory"] for c in categories if c["has_cost_data"])
    sum_proj_duty = sum(c["_proj_duty"] for c in categories if c["has_cost_data"])
    sum_proj_delivery = sum(c["_proj_delivery"] for c in categories if c["has_cost_data"])
    sum_proj_vat = sum(c["_proj_vat"] for c in categories if c["has_cost_data"])
    sum_proj_cost = sum(c["_proj_cost_total"] for c in categories if c["has_cost_data"])

    rev = total_revenue

    # MP %
    mp_commission_pct = abs(sum_mp_commission) / rev * 100
    mp_logistics_pct = abs(sum_logistics) / rev * 100
    mp_storage_pct = abs(sum_storage) / rev * 100
    mp_advertising_pct = sum_adv / rev * 100
    mp_other_pct = abs(sum_other_combined) / rev * 100
    mp_total_pct = mp_commission_pct + mp_logistics_pct + mp_storage_pct + mp_advertising_pct + mp_other_pct

    # Cost % — projected RUB / total revenue (covers categories with cost data only;
    # categories without cost contribute 0 → understates total cost realistically)
    cost_factory_pct = sum_proj_factory / rev * 100
    cost_duty_pct = sum_proj_duty / rev * 100
    cost_delivery_pct = sum_proj_delivery / rev * 100
    cost_vat_pct = sum_proj_vat / rev * 100
    cost_total_pct = sum_proj_cost / rev * 100

    # Tax — full ОПИУ logic on aggregated sales + expenses (+ projected cost if cost_as_expense)
    from backend.services.cost_dna_helpers import _compute_tax

    tax_total_rub = _compute_tax(
        sales_amount=sum_sales,
        log=sum_logistics,
        stor=sum_storage,
        opiu_comm=sum_opiu_commission,
        pen=sum_penalty,
        other_ded=sum_other_deduction,
        adv=sum_adv,
        cost_val=sum_proj_cost,
        tax_info=tax_info,
    )
    tax_pct = tax_total_rub / rev * 100 if rev else 0

    margin_pct = 100 - cost_total_pct - mp_total_pct - tax_pct

    return {
        "revenue": round(rev, 2),
        "cost_factory_pct": round(cost_factory_pct, 2),
        "cost_duty_pct": round(cost_duty_pct, 2),
        "cost_delivery_pct": round(cost_delivery_pct, 2),
        "cost_vat_pct": round(cost_vat_pct, 2),
        "cost_total_pct": round(cost_total_pct, 2),
        "mp_commission_pct": round(mp_commission_pct, 2),
        "mp_logistics_pct": round(mp_logistics_pct, 2),
        "mp_storage_pct": round(mp_storage_pct, 2),
        "mp_advertising_pct": round(mp_advertising_pct, 2),
        "mp_other_pct": round(mp_other_pct, 2),
        "mp_total_pct": round(mp_total_pct, 2),
        "tax_pct": round(tax_pct, 2),
        "margin_pct": round(margin_pct, 2),
        "margin_pct_prev": None,
        "margin_trend": None,
    }
