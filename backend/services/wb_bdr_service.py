"""
Service: wb_bdr — WB P&L report from locally cached finance data.

Reads from wb_finance_rows (pre-synced), enriches with:
- Advertising data from WbFunnelDaily
- Cost prices from cost_order_items (average)
- Tax calculation from project tax_rates settings

Verified formulas match TrueStats.

Architecture:
- Loaders (DB queries) → bdr_loaders.py
- Enrichment (tax, ABC) → bdr_enrichment.py
- SQL queries & metrics computation → wb_bdr_helpers.py
- Orchestration → this file
"""

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.services.bdr_enrichment import (
    apply_tax,
    apply_tax_article,
    compute_abc,
    enrich_article,
)
from backend.services.bdr_loaders import (
    load_ads,
    load_avg_costs,
    load_cancel_stats,
    load_cost_overrides,
    load_orders_stocks,
    load_tax_settings,
)
from backend.services.wb_bdr_helpers import (
    build_bdr_aggregate_sql,
    build_brands_sql_bdr,
    build_group_nm_ids_sql,
    build_group_sa_names_sql,
    build_total_count_sql,
    compute_metrics_from_sql,
    empty_metrics,
    serialize,
)

logger = logging.getLogger("dds.wb_bdr")

D = Decimal
ZERO = D("0")

# Keep old private names as aliases for backwards compatibility
_build_bdr_aggregate_sql = build_bdr_aggregate_sql
_build_brands_sql_bdr = build_brands_sql_bdr
_build_total_count_sql = build_total_count_sql
_empty_metrics = empty_metrics
_compute_metrics_from_sql = compute_metrics_from_sql
_serialize = serialize


@cached(prefix="reports:wb_bdr", ttl=3600)
async def get_wb_bdr(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
    brand: str | None = None,
    article: str | None = None,
    group_by: str = "article",
) -> dict:
    """
    Build BDR from locally cached WB finance data.
    Enriches with ads, cost, tax.
    Returns { summary, articles, brands, period, total_rows, sync_status, tax_info }

    Uses SQL-level aggregation for performance (handles 1M+ rows).
    """
    # ── 1. Get sync status ──
    from backend.services.wb_finance_sync import get_sync_status

    sync_status = await get_sync_status(db, project_id)

    # ── 2. SQL aggregation — returns ~N articles instead of 634K rows ──
    params: dict = {
        "project_id": project_id,
        "date_from": date_from,
        "date_to": date_to,
    }
    if brand:
        params["brand"] = brand
    if article:
        params["article_like"] = f"%{article.lower()}%"

    result = await db.execute(text(build_bdr_aggregate_sql(brand, article, group_by=group_by)), params)
    agg_rows = result.mappings().all()

    if not agg_rows:
        return {
            "summary": serialize(_empty_metrics()),
            "articles": [],
            "brands": [],
            "period": {"date_from": str(date_from), "date_to": str(date_to)},
            "total_rows": 0,
            "sync_status": sync_status,
            "tax_info": {},
        }

    # ── 3. Total row count (for response metadata) ──
    count_result = await db.execute(text(build_total_count_sql(brand, article)), params)
    total_rows = count_result.scalar() or 0

    # ── 4. Load brands for filter dropdown ──
    brands_result = await db.execute(
        text(build_brands_sql_bdr()),
        {"project_id": project_id, "date_from": date_from, "date_to": date_to},
    )
    all_brands = sorted(r[0] for r in brands_result)

    # ── 5. Load enrichment data (small queries) ──
    ads_map = await load_ads(db, project_id, date_from, date_to)
    orders_stocks_map = await load_orders_stocks(db, project_id, date_from, date_to)
    cost_map = await load_avg_costs(db, project_id)
    cost_overrides = await load_cost_overrides(db, project_id)
    tax_info = await load_tax_settings(db, project_id, date_from, date_to)
    cancel_stats = await load_cancel_stats(db, project_id, date_from, date_to)

    period_days = max((date_to - date_from).days + 1, 1)

    # ── 5b. For brand/subject grouping, build nm_id→group and sa_name→group maps ──
    nm_to_group: dict[int, str] = {}
    sa_to_group: dict[str, str] = {}
    if group_by in ("brand", "subject"):
        nm_sql = build_group_nm_ids_sql(group_by, brand, article)
        if nm_sql:
            nm_result = await db.execute(text(nm_sql), params)
            for r in nm_result.mappings():
                nm_id_val = r["nm_id"]
                if nm_id_val:
                    nm_to_group[int(nm_id_val)] = r["group_key"] or ""

        sa_sql = build_group_sa_names_sql(group_by, brand, article)
        if sa_sql:
            sa_result = await db.execute(text(sa_sql), params)
            for r in sa_result.mappings():
                sa_to_group[r["sa_name"] or ""] = r["group_key"] or ""

    # Pre-aggregate enrichment data by group (brand/subject)
    if group_by in ("brand", "subject"):
        # Ads: aggregate by group
        grouped_ads: dict[str, float] = {}
        for nm_id_key, adv_val in ads_map.items():
            gk = nm_to_group.get(nm_id_key, "")
            if gk:
                grouped_ads[gk] = grouped_ads.get(gk, 0) + adv_val

        # Orders/stocks: aggregate by group
        grouped_orders: dict[str, dict] = {}
        for nm_id_key, os_val in orders_stocks_map.items():
            gk = nm_to_group.get(nm_id_key, "")
            if gk:
                if gk not in grouped_orders:
                    grouped_orders[gk] = {"orders_count": 0, "orders_sum": 0, "stocks_wb": 0}
                grouped_orders[gk]["orders_count"] += os_val.get("orders_count", 0)
                grouped_orders[gk]["orders_sum"] += os_val.get("orders_sum", 0)
                grouped_orders[gk]["stocks_wb"] += os_val.get("stocks_wb", 0)

        # Cancel stats: aggregate by group
        grouped_cancel: dict[str, dict] = {}
        for nm_id_key, cs_val in cancel_stats.items():
            gk = nm_to_group.get(nm_id_key, "")
            if gk:
                if gk not in grouped_cancel:
                    grouped_cancel[gk] = {"total": 0, "cancelled": 0}
                grouped_cancel[gk]["total"] += cs_val.get("total", 0)
                grouped_cancel[gk]["cancelled"] += cs_val.get("cancelled", 0)

        # Cost: aggregate avg_cost per article by group (sum costs for cost_total later)
        # grouped_cost_sum: group_key → sum of avg_cost_per_article across all articles in group
        # grouped_cost_count: group_key → number of articles with known cost in group
        # We compute group cost_total = avg(cost_prices) * sale_qty from SQL row
        grouped_cost_sum: dict[str, float] = {}
        grouped_cost_count: dict[str, int] = {}
        for sa_name_key, cost_val in cost_map.items():
            gk = sa_to_group.get(sa_name_key, "")
            if gk and cost_val > 0:
                grouped_cost_sum[gk] = grouped_cost_sum.get(gk, 0.0) + cost_val
                grouped_cost_count[gk] = grouped_cost_count.get(gk, 0) + 1
        # Also fold in cost_overrides (by nm_id → group) for articles missing cost_map entry
        for nm_id_key, override_val in cost_overrides.items():
            gk = nm_to_group.get(nm_id_key, "")
            if gk and override_val > 0:
                # Only add if this nm_id's sa_name is NOT already in cost_map for this group
                grouped_cost_sum[gk] = grouped_cost_sum.get(gk, 0.0) + override_val
                grouped_cost_count[gk] = grouped_cost_count.get(gk, 0) + 1
        # Build grouped_avg_cost: group_key → weighted-mean cost per unit
        grouped_avg_cost: dict[str, float] = {
            gk: grouped_cost_sum[gk] / grouped_cost_count[gk]
            for gk in grouped_cost_sum
            if grouped_cost_count.get(gk, 0) > 0
        }

    # ── 6. Compute metrics per article from SQL rows ──
    total_metrics = _empty_metrics()
    total_adv = ZERO
    total_cost = ZERO

    result_articles = []
    for row in agg_rows:
        metrics = compute_metrics_from_sql(row)
        sa_name = row["sa_name"] or ""
        nm_id = int(row["nm_id"] or 0)

        metrics["sa_name"] = sa_name
        metrics["brand"] = row["brand_name"] or ""
        metrics["subject"] = row["subject_name"] or ""
        metrics["nm_id"] = nm_id

        # Accumulate totals (skip percentages/averages — they are recalculated)
        _skip_sum = {"buyout_pct", "avg_sale_price", "avg_retail_price", "avg_logistics"}
        for key in total_metrics:
            if key not in _skip_sum:
                total_metrics[key] += D(str(metrics.get(key, 0)))

        if group_by in ("brand", "subject"):
            # Grouped enrichment: use pre-aggregated maps
            adv_sum = float(grouped_ads.get(sa_name, 0))
            metrics["adv_sum"] = adv_sum
            total_adv += D(str(adv_sum))

            # Cost: aggregate avg cost across articles in this group
            cost_price = grouped_avg_cost.get(sa_name, 0)
            sale_qty = metrics["sale_qty"]
            cost_total = cost_price * sale_qty if sale_qty > 0 else 0
            metrics["cost_price"] = round(cost_price, 2)
            metrics["cost_total"] = round(cost_total, 2)
            total_cost += D(str(cost_total))

            os_data = grouped_orders.get(sa_name, {})
            metrics["orders_count"] = os_data.get("orders_count", 0)
            metrics["orders_sum"] = os_data.get("orders_sum", 0)
            metrics["stocks_wb"] = os_data.get("stocks_wb", 0)

            cs = grouped_cancel.get(sa_name)
            if cs and cs["total"] > 0:
                delivered = cs["total"] - cs["cancelled"]
                metrics["buyout_pct"] = round(float(delivered / cs["total"] * 100), 2)

            metrics["nm_id"] = 0
        else:
            # Per-article enrichment (original logic)
            adv_sum = float(ads_map.get(nm_id, 0))
            metrics["adv_sum"] = adv_sum
            total_adv += D(str(adv_sum))

            # Cost from history (avg), fallback to manual override by nm_id
            cost_price = cost_map.get(sa_name, 0)
            if cost_price == 0 and nm_id in cost_overrides:
                cost_price = cost_overrides[nm_id]
            sale_qty = metrics["sale_qty"]
            cost_total = cost_price * sale_qty if sale_qty > 0 else 0
            metrics["cost_price"] = round(cost_price, 2)
            metrics["cost_total"] = round(cost_total, 2)
            total_cost += D(str(cost_total))

            # Orders & stocks from funnel
            os_data = orders_stocks_map.get(nm_id, {})
            metrics["orders_count"] = os_data.get("orders_count", 0)
            metrics["orders_sum"] = os_data.get("orders_sum", 0)
            metrics["stocks_wb"] = os_data.get("stocks_wb", 0)

            # Per-article buyout from order cancel data (more accurate than finance report)
            cs = cancel_stats.get(nm_id)
            if cs and cs["total"] > 0:
                delivered = cs["total"] - cs["cancelled"]
                metrics["buyout_pct"] = round(float(delivered / cs["total"] * 100), 2)

        metrics["_period_days"] = period_days

        result_articles.append(metrics)

    # Sort by to_pay descending
    result_articles.sort(key=lambda x: x["to_pay"], reverse=True)

    # ── 7. Summary ──
    summary_result = {k: float(v) for k, v in total_metrics.items()}

    # Recalculate averages/percentages from totals (cannot sum them across articles)
    total_sale_qty = summary_result.get("sale_qty_gross", 0) or summary_result.get("sale_qty", 0)
    total_ret_qty = summary_result.get("ret_qty", 0)
    net_sale_qty = summary_result.get("sale_qty", 0)

    # Buyout % from order cancel data (accurate), fallback to finance report formula
    total_orders_all = sum(cs["total"] for cs in cancel_stats.values())
    total_cancelled_all = sum(cs["cancelled"] for cs in cancel_stats.values())
    if total_orders_all > 0:
        delivered = total_orders_all - total_cancelled_all
        summary_result["buyout_pct"] = round(delivered / total_orders_all * 100, 2)
    else:
        total_qty = total_sale_qty + total_ret_qty
        summary_result["buyout_pct"] = round(total_sale_qty / total_qty * 100, 2) if total_qty > 0 else 0
    summary_result["avg_sale_price"] = (
        round(summary_result.get("sales_amount", 0) / net_sale_qty, 2) if net_sale_qty > 0 else 0
    )
    summary_result["avg_retail_price"] = (
        round(summary_result.get("realization", 0) / net_sale_qty, 2) if net_sale_qty > 0 else 0
    )
    summary_result["avg_logistics"] = (
        round(summary_result.get("logistics", 0) / net_sale_qty, 2) if net_sale_qty > 0 else 0
    )

    # Ads + cost totals
    summary_result["adv_sum"] = float(total_adv)
    summary_result["cost_total"] = float(total_cost)

    # ── 8. Tax calculation ──
    apply_tax(summary_result, tax_info, D(str(summary_result["adv_sum"])), total_cost)
    for art_row in result_articles:
        apply_tax_article(art_row, tax_info)

    # ── 9. Computed enrichment fields ──
    total_real = float(summary_result.get("realization", 0)) or 1
    total_sales = float(summary_result.get("sales_amount", 0)) or 1

    sum_orders_count = 0
    sum_orders_sum = 0.0
    sum_stocks_wb = 0
    for art_row in result_articles:
        enrich_article(art_row, total_real, total_sales, period_days)
        sum_orders_count += art_row.get("orders_count", 0)
        sum_orders_sum += art_row.get("orders_sum", 0)
        sum_stocks_wb += art_row.get("stocks_wb", 0)

    summary_result["orders_count"] = sum_orders_count
    summary_result["orders_sum"] = round(sum_orders_sum, 2)
    summary_result["stocks_wb"] = sum_stocks_wb
    enrich_article(summary_result, total_real, total_sales, period_days)

    # ── 10. ABC analysis ──
    compute_abc(result_articles)

    return {
        "summary": serialize(summary_result),
        "articles": [serialize(a) for a in result_articles],
        "brands": all_brands,
        "period": {"date_from": str(date_from), "date_to": str(date_to)},
        "total_rows": total_rows,
        "sync_status": sync_status,
        "tax_info": tax_info,
        "group_by": group_by,
    }
