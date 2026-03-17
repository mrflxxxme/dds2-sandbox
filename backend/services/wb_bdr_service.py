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
- Orchestration + SQL aggregation → this file

Optimized: SQL-level aggregation instead of loading all rows into Python.
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
    load_cost_overrides,
    load_orders_stocks,
    load_tax_settings,
)

logger = logging.getLogger("dds.wb_bdr")

D = Decimal
ZERO = D("0")


# ─── SQL aggregation query ────────────────────────────────────────────────────

_DATE_FILTER = """(
    (rr_dt BETWEEN :date_from AND :date_to)
    OR (rr_dt IS NULL AND date_from >= :date_from AND date_to <= :date_to)
  )"""

_SELECT_COLS_BDR = """
    sa_name,
    MAX(NULLIF(nm_id, 0)) AS nm_id,
    MAX(NULLIF(brand_name, '')) AS brand_name,
    MAX(NULLIF(subject_name, '')) AS subject_name,
    COUNT(*) AS row_count,

    -- Realization components (sale/ret split)
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_price_withdisc_rub ELSE 0 END), 0) AS sale_retail,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN retail_price_withdisc_rub ELSE 0 END), 0) AS ret_retail,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_amount ELSE 0 END), 0) AS sale_amount,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN retail_amount ELSE 0 END), 0) AS ret_amount,

    -- ppvz_for_pay (sale/ret split for commission calc)
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN ppvz_for_pay ELSE 0 END), 0) AS ppvz_sale,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN ppvz_for_pay ELSE 0 END), 0) AS ppvz_ret,

    -- Compensation (Добровольная компенсация при возврате)
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа'
        AND supplier_oper_name = 'Добровольная компенсация при возврате'
        THEN ppvz_for_pay ELSE 0 END), 0) AS comp_ppvz,

    -- WB reward components (for total_wb_reward)
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN ppvz_sales_commission ELSE 0 END), 0) AS ppvz_comm_sale,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN ppvz_sales_commission ELSE 0 END), 0) AS ppvz_comm_ret,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN ppvz_vw ELSE 0 END), 0) AS ppvz_vw_sale,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN ppvz_vw ELSE 0 END), 0) AS ppvz_vw_ret,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN ppvz_vw_nds ELSE 0 END), 0) AS ppvz_vw_nds_sale,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN ppvz_vw_nds ELSE 0 END), 0) AS ppvz_vw_nds_ret,

    -- Product quantities (only from oper_name Продажа/Возврат)
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа'
        AND supplier_oper_name IN ('Продажа', 'Возврат')
        THEN quantity ELSE 0 END), 0) AS sale_qty,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат'
        AND supplier_oper_name IN ('Продажа', 'Возврат')
        THEN quantity ELSE 0 END), 0) AS ret_qty,

    -- ALL buckets sums (WB distributes across doc_types)
    COALESCE(SUM(delivery_rub), 0) AS logistics,
    COALESCE(SUM(penalty), 0) AS penalties,
    COALESCE(SUM(storage_fee), 0) AS storage,
    COALESCE(SUM(acceptance), 0) AS acceptance_total,
    COALESCE(SUM(rebill_logistic_cost), 0) AS rebill,
    COALESCE(SUM(deduction), 0) AS deductions_total,

    -- Deduction splits (from all buckets)
    COALESCE(SUM(CASE WHEN deduction != 0
        AND (bonus_type_name LIKE '%%Продвижение%%' OR bonus_type_name LIKE '%%Медиа%%')
        THEN deduction ELSE 0 END), 0) AS ad_deduction,
    COALESCE(SUM(CASE WHEN deduction != 0
        AND bonus_type_name LIKE '%%отзыв%%'
        THEN deduction ELSE 0 END), 0) AS other_deduction,
    COALESCE(SUM(CASE WHEN deduction != 0
        AND (LOWER(bonus_type_name) LIKE '%%кредит%%' OR LOWER(bonus_type_name) LIKE '%%заём%%')
        THEN deduction ELSE 0 END), 0) AS loan_deduction
"""


def _build_bdr_aggregate_sql(brand: str | None, article: str | None) -> str:
    """Build per-article SQL aggregation query with optional filters."""
    where = f"project_id = :project_id AND {_DATE_FILTER}"
    where += " AND LOWER(COALESCE(sa_name, '')) != 'неопознанный товар'"
    if brand:
        where += " AND brand_name = :brand"
    if article:
        where += " AND LOWER(sa_name) LIKE :article_like"
    return f"SELECT {_SELECT_COLS_BDR} FROM wb_finance_rows" f" WHERE {where} GROUP BY sa_name ORDER BY sa_name"  # noqa: S608


def _build_brands_sql_bdr() -> str:
    """Get distinct brand names for filter dropdown."""
    sql = f"SELECT DISTINCT brand_name FROM wb_finance_rows WHERE project_id = :project_id AND {_DATE_FILTER} AND brand_name IS NOT NULL AND brand_name != '' ORDER BY brand_name"  # noqa: S608
    return sql


def _build_total_count_sql(brand: str | None, article: str | None) -> str:
    """Count total raw rows (for total_rows in response)."""
    where = f"project_id = :project_id AND {_DATE_FILTER}"
    where += " AND LOWER(COALESCE(sa_name, '')) != 'неопознанный товар'"
    if brand:
        where += " AND brand_name = :brand"
    if article:
        where += " AND LOWER(sa_name) LIKE :article_like"
    return f"SELECT COUNT(*) AS cnt FROM wb_finance_rows WHERE {where}"  # noqa: S608


@cached(prefix="reports:wb_bdr", ttl=3600)
async def get_wb_bdr(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
    brand: str | None = None,
    article: str | None = None,
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

    result = await db.execute(text(_build_bdr_aggregate_sql(brand, article)), params)
    agg_rows = result.mappings().all()

    if not agg_rows:
        return {
            "summary": _serialize(_empty_metrics()),
            "articles": [],
            "brands": [],
            "period": {"date_from": str(date_from), "date_to": str(date_to)},
            "total_rows": 0,
            "sync_status": sync_status,
            "tax_info": {},
        }

    # ── 3. Total row count (for response metadata) ──
    count_result = await db.execute(text(_build_total_count_sql(brand, article)), params)
    total_rows = count_result.scalar() or 0

    # ── 4. Load brands for filter dropdown ──
    brands_result = await db.execute(
        text(_build_brands_sql_bdr()),
        {"project_id": project_id, "date_from": date_from, "date_to": date_to},
    )
    all_brands = sorted(r[0] for r in brands_result)

    # ── 5. Load enrichment data (small queries) ──
    ads_map = await load_ads(db, project_id, date_from, date_to)
    orders_stocks_map = await load_orders_stocks(db, project_id, date_from, date_to)
    cost_map = await load_avg_costs(db, project_id)
    cost_overrides = await load_cost_overrides(db, project_id)
    tax_info = await load_tax_settings(db, project_id, date_from, date_to)

    period_days = max((date_to - date_from).days + 1, 1)

    # ── 6. Compute metrics per article from SQL rows ──
    total_metrics = _empty_metrics()
    total_adv = ZERO
    total_cost = ZERO

    result_articles = []
    for row in agg_rows:
        metrics = _compute_metrics_from_sql(row)
        sa_name = row["sa_name"] or ""
        nm_id = int(row["nm_id"] or 0)

        metrics["sa_name"] = sa_name
        metrics["brand"] = row["brand_name"] or ""
        metrics["subject"] = row["subject_name"] or ""
        metrics["nm_id"] = nm_id

        # Accumulate totals
        for key in total_metrics:
            total_metrics[key] += D(str(metrics.get(key, 0)))

        # Ads: per-article from WbFunnelDaily
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

        metrics["_period_days"] = period_days

        result_articles.append(metrics)

    # Sort by to_pay descending
    result_articles.sort(key=lambda x: x["to_pay"], reverse=True)

    # ── 7. Summary ──
    summary_result = {k: float(v) for k, v in total_metrics.items()}

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
        "summary": _serialize(summary_result),
        "articles": [_serialize(a) for a in result_articles],
        "brands": all_brands,
        "period": {"date_from": str(date_from), "date_to": str(date_to)},
        "total_rows": total_rows,
        "sync_status": sync_status,
        "tax_info": tax_info,
    }


# ─── Metrics computation ──────────────────────────────────────────────────────


def _empty_metrics() -> dict:
    """Empty metrics dict (same keys as _compute_metrics_from_sql output)."""
    return {
        "realization": ZERO,
        "sales_amount": ZERO,
        "returns_amount": ZERO,
        "to_pay": ZERO,
        "ppvz_for_pay": ZERO,
        "compensation": ZERO,
        "sale_qty": 0,
        "sale_qty_gross": 0,
        "ret_qty": 0,
        "buyout_pct": ZERO,
        "avg_sale_price": ZERO,
        "avg_retail_price": ZERO,
        "avg_logistics": ZERO,
        "commission": ZERO,
        "total_wb_reward": ZERO,
        "logistics": ZERO,
        "penalties": ZERO,
        "storage": ZERO,
        "acceptance": ZERO,
        "deductions": ZERO,
        "deductions_total": ZERO,
        "ad_deduction": ZERO,
        "other_deduction": ZERO,
        "loan_deduction": ZERO,
        "wb_deductions": ZERO,
        "rebill": ZERO,
    }


def _compute_metrics_from_sql(row) -> dict:
    """Compute BDR metrics from a SQL aggregation row.

    Commission formula verified against TrueStats:
        commission = sales_amount_net - (ppvz_net - compensation)

    IMPORTANT: logistics, penalties, deductions etc. are summed from ALL 3 buckets
    because WB API distributes these values across sale/return/other rows.
    """
    sale_retail = D(str(row["sale_retail"]))
    ret_retail = D(str(row["ret_retail"]))
    sale_amount = D(str(row["sale_amount"]))
    ret_amount = D(str(row["ret_amount"]))
    ppvz_sale = D(str(row["ppvz_sale"]))
    ppvz_ret = D(str(row["ppvz_ret"]))
    compensation = D(str(row["comp_ppvz"]))

    realization = sale_retail - ret_retail
    sales_amount = sale_amount - ret_amount
    returns_amount = ret_retail

    sale_qty = int(row["sale_qty"])
    ret_qty = int(row["ret_qty"])
    net_sale_qty = sale_qty - ret_qty

    # ALL buckets sums
    logistics = D(str(row["logistics"]))
    penalties = D(str(row["penalties"]))
    storage = D(str(row["storage"]))
    acceptance_val = D(str(row["acceptance_total"]))
    rebill = D(str(row["rebill"]))

    # Deductions
    deductions_total = D(str(row["deductions_total"]))
    ad_deduction = D(str(row["ad_deduction"]))
    other_deduction = D(str(row["other_deduction"]))
    loan_deduction = D(str(row["loan_deduction"]))

    # Operating deductions = total - ads - loans
    operating_deductions = deductions_total - ad_deduction - loan_deduction

    # Commission: TrueStats formula
    ppvz_net = ppvz_sale - ppvz_ret
    commission = sales_amount - (ppvz_net - compensation)

    # WB total reward (for reference)
    ppvz_comm_net = D(str(row["ppvz_comm_sale"])) - D(str(row["ppvz_comm_ret"]))
    ppvz_vw_net = D(str(row["ppvz_vw_sale"])) - D(str(row["ppvz_vw_ret"]))
    ppvz_vw_nds_net = D(str(row["ppvz_vw_nds_sale"])) - D(str(row["ppvz_vw_nds_ret"]))
    total_wb_reward = ppvz_comm_net + ppvz_vw_net + ppvz_vw_nds_net

    # To Pay
    to_pay = ppvz_net - logistics - rebill - penalties - storage - deductions_total - acceptance_val

    avg_sale_price = sales_amount / D(str(net_sale_qty)) if net_sale_qty > 0 else ZERO
    avg_retail_price = realization / D(str(net_sale_qty)) if net_sale_qty > 0 else ZERO
    avg_logistics = logistics / D(str(net_sale_qty)) if net_sale_qty > 0 else ZERO

    total_qty = sale_qty + ret_qty
    buyout_pct = D(str(sale_qty)) / D(str(total_qty)) * 100 if total_qty > 0 else ZERO

    return {
        "realization": realization,
        "sales_amount": sales_amount,
        "returns_amount": returns_amount,
        "to_pay": to_pay,
        "ppvz_for_pay": ppvz_net,
        "compensation": compensation,
        "sale_qty": net_sale_qty,
        "sale_qty_gross": sale_qty,
        "ret_qty": ret_qty,
        "buyout_pct": buyout_pct,
        "avg_sale_price": avg_sale_price,
        "avg_retail_price": avg_retail_price,
        "avg_logistics": avg_logistics,
        "commission": commission,
        "total_wb_reward": total_wb_reward,
        "logistics": logistics,
        "penalties": penalties,
        "storage": storage,
        "acceptance": acceptance_val,
        "deductions": operating_deductions,
        "deductions_total": deductions_total,
        "ad_deduction": ad_deduction,
        "other_deduction": other_deduction,
        "loan_deduction": loan_deduction,
        "wb_deductions": ad_deduction + loan_deduction,
        "rebill": rebill,
    }


def _serialize(d: dict) -> dict:
    """Convert Decimals to floats for JSON."""
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in d.items()}
