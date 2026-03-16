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
- Orchestration + aggregation → this file
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.wb_finance import WbFinanceRow

from backend.services.bdr_loaders import (
    load_ads,
    load_orders_stocks,
    load_avg_costs,
    load_cost_overrides,
    load_tax_settings,
)
from backend.cache import cached
from backend.services.bdr_enrichment import (
    apply_tax,
    apply_tax_article,
    enrich_article,
    compute_abc,
)

logger = logging.getLogger("dds.wb_bdr")

D = Decimal
ZERO = D("0")


@cached(prefix="reports:wb_bdr", ttl=3600)
async def get_wb_bdr(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
    brand: Optional[str] = None,
    article: Optional[str] = None,
) -> dict:
    """
    Build BDR from locally cached WB finance data.
    Enriches with ads, cost, tax.
    Returns { summary, articles, brands, period, total_rows, sync_status, tax_info }
    """
    # ── 1. Get sync status ──
    from backend.services.wb_finance_sync import get_sync_status

    sync_status = await get_sync_status(db, project_id)

    # ── 2. Load finance rows from local DB ──
    q = select(WbFinanceRow).where(
        WbFinanceRow.project_id == project_id,
        or_(
            # New rows with rr_dt — filter by actual realization date
            WbFinanceRow.rr_dt.between(date_from, date_to),
            # Old rows without rr_dt — fallback to week boundaries
            (WbFinanceRow.rr_dt.is_(None)) & (WbFinanceRow.date_from >= date_from) & (WbFinanceRow.date_to <= date_to),
        ),
    )
    result = await db.execute(q)
    raw_rows = result.scalars().all()

    if not raw_rows:
        return {
            "summary": _serialize(_compute_metrics(_empty_totals(), _empty_totals(), _empty_totals())),
            "articles": [],
            "brands": [],
            "period": {"date_from": str(date_from), "date_to": str(date_to)},
            "total_rows": 0,
            "sync_status": sync_status,
            "tax_info": {},
        }

    # ── 3. Load ads data from WbFunnelDaily ──
    ads_map = await load_ads(db, project_id, date_from, date_to)

    # ── 3b. Load orders & stocks from WbFunnelDaily ──
    orders_stocks_map = await load_orders_stocks(db, project_id, date_from, date_to)

    # ── 4. Load cost prices from cost_order_items + manual overrides ──
    cost_map = await load_avg_costs(db, project_id)
    cost_overrides = await load_cost_overrides(db, project_id)

    # ── 5. Load tax settings ──
    tax_info = await load_tax_settings(db, project_id, date_from, date_to)

    # Period days (for turnover / GMROI Year)
    period_days = max((date_to - date_from).days + 1, 1)

    # ── 6. Aggregate per article ──
    articles_data: dict[str, dict] = {}
    all_brands: set[str] = set()

    for row in raw_rows:
        brand_name = row.brand_name or ""
        sa_name = row.sa_name or ""
        subject = row.subject_name or ""
        nm_id = row.nm_id or 0
        doc_type = row.doc_type_name or ""
        oper_name = row.supplier_oper_name or ""

        if brand_name:
            all_brands.add(brand_name)

        # Apply filters
        if brand and brand_name != brand:
            continue
        if article and article.lower() not in sa_name.lower():
            continue

        # Skip WB system placeholder — not a real product
        if sa_name.lower() in ("неопознанный товар",):
            continue

        # Get or create article bucket
        if sa_name not in articles_data:
            articles_data[sa_name] = {
                "sa_name": sa_name,
                "brand": brand_name,
                "subject": subject,
                "nm_id": nm_id,
                "sale": _empty_totals(),
                "ret": _empty_totals(),
                "other": _empty_totals(),
            }

        art = articles_data[sa_name]
        if brand_name and not art["brand"]:
            art["brand"] = brand_name
        if subject and not art["subject"]:
            art["subject"] = subject
        if nm_id and not art["nm_id"]:
            art["nm_id"] = nm_id

        if doc_type == "Продажа":
            target = art["sale"]
        elif doc_type == "Возврат":
            target = art["ret"]
        else:
            target = art["other"]

        _accumulate_row(target, row, oper_name)

    # ── 7. Build results with enrichments ──
    total_sale = _empty_totals()
    total_ret = _empty_totals()
    total_other = _empty_totals()
    total_adv = ZERO
    total_cost = ZERO

    result_articles = []
    for sa_name, art in articles_data.items():
        for key in total_sale:
            total_sale[key] += art["sale"].get(key, ZERO)
            total_ret[key] += art["ret"].get(key, ZERO)
            total_other[key] += art["other"].get(key, ZERO)

        row_result = _compute_metrics(art["sale"], art["ret"], art["other"])
        row_result["sa_name"] = sa_name
        row_result["brand"] = art["brand"]
        row_result["subject"] = art["subject"]
        row_result["nm_id"] = art["nm_id"]

        # Ads: per-article always from WbFunnelDaily (finance report ads have empty sa_name)
        nm = art["nm_id"]
        adv_sum = float(ads_map.get(nm, 0))
        row_result["adv_sum"] = adv_sum
        total_adv += D(str(adv_sum))

        # Cost from history (avg), fallback to manual override by nm_id
        cost_price = cost_map.get(sa_name, 0)
        if cost_price == 0 and nm in cost_overrides:
            cost_price = cost_overrides[nm]
        sale_qty = row_result["sale_qty"]
        cost_total = cost_price * sale_qty if sale_qty > 0 else 0
        row_result["cost_price"] = round(cost_price, 2)
        row_result["cost_total"] = round(cost_total, 2)
        total_cost += D(str(cost_total))

        # Orders & stocks from funnel
        os_data = orders_stocks_map.get(nm, {})
        row_result["orders_count"] = os_data.get("orders_count", 0)
        row_result["orders_sum"] = os_data.get("orders_sum", 0)
        row_result["stocks_wb"] = os_data.get("stocks_wb", 0)

        # Avg profit per item
        # (profit computed after tax below, placeholder)
        row_result["_period_days"] = period_days

        result_articles.append(row_result)

    # Sort by to_pay descending
    result_articles.sort(key=lambda x: x["to_pay"], reverse=True)

    # ── 8. Summary ──
    summary_result = _compute_metrics(total_sale, total_ret, total_other)

    # Ads: always from WbFunnelDaily (summed from per-article)
    summary_result["adv_sum"] = float(total_adv)
    summary_result["cost_total"] = float(total_cost)

    # ── 9. Tax calculation ──
    apply_tax(summary_result, tax_info, D(str(summary_result["adv_sum"])), total_cost)
    for art_row in result_articles:
        apply_tax_article(art_row, tax_info)

    # ── 10. Computed enrichment fields ──
    total_real = float(summary_result.get("realization", 0)) or 1
    total_sales = float(summary_result.get("sales_amount", 0)) or 1
    # Summary-level aggregates for orders/stocks
    sum_orders_count = 0
    sum_orders_sum = 0.0
    sum_stocks_wb = 0
    for art_row in result_articles:
        enrich_article(art_row, total_real, total_sales, period_days)
        sum_orders_count += art_row.get("orders_count", 0)
        sum_orders_sum += art_row.get("orders_sum", 0)
        sum_stocks_wb += art_row.get("stocks_wb", 0)

    # Summary enrichment
    summary_result["orders_count"] = sum_orders_count
    summary_result["orders_sum"] = round(sum_orders_sum, 2)
    summary_result["stocks_wb"] = sum_stocks_wb
    enrich_article(summary_result, total_real, total_sales, period_days)

    # ── 11. ABC analysis ──
    compute_abc(result_articles)

    return {
        "summary": _serialize(summary_result),
        "articles": [_serialize(a) for a in result_articles],
        "brands": sorted(all_brands),
        "period": {"date_from": str(date_from), "date_to": str(date_to)},
        "total_rows": len(raw_rows),
        "sync_status": sync_status,
        "tax_info": tax_info,
    }


# ─── Aggregation helpers ────────────────────────────────────────────────────

def _empty_totals() -> dict:
    return {
        "retail_price_withdisc_rub": ZERO,
        "retail_amount": ZERO,
        "ppvz_for_pay": ZERO,
        "ppvz_sales_commission": ZERO,
        "delivery_rub": ZERO,
        "penalty": ZERO,
        "additional_payment": ZERO,
        "storage_fee": ZERO,
        "acceptance": ZERO,
        "deduction": ZERO,
        "ppvz_reward": ZERO,
        "rebill_logistic_cost": ZERO,
        "ppvz_vw": ZERO,
        "ppvz_vw_nds": ZERO,
        "quantity": ZERO,
        "product_qty": ZERO,
        "compensation_ppvz": ZERO,  # ppvz from Добровольная компенсация rows
        "ad_deduction": ZERO,      # deduction from Продвижение/Медиа (fin mode ads)
        "other_deduction": ZERO,   # deduction from Списание за отзыв etc.
        "loan_deduction": ZERO,    # deduction from WB loan payments (not operating expense)
    }


def _accumulate_row(target: dict, row: WbFinanceRow, oper_name: str):
    """Accumulate ORM row values into target bucket."""
    target["retail_price_withdisc_rub"] += row.retail_price_withdisc_rub or ZERO
    target["retail_amount"] += row.retail_amount or ZERO
    target["ppvz_for_pay"] += row.ppvz_for_pay or ZERO
    target["ppvz_sales_commission"] += row.ppvz_sales_commission or ZERO
    target["delivery_rub"] += row.delivery_rub or ZERO
    target["penalty"] += row.penalty or ZERO
    target["additional_payment"] += row.additional_payment or ZERO
    target["storage_fee"] += row.storage_fee or ZERO
    target["acceptance"] += row.acceptance or ZERO
    target["deduction"] += row.deduction or ZERO
    target["ppvz_reward"] += row.ppvz_reward or ZERO
    target["rebill_logistic_cost"] += row.rebill_logistic_cost or ZERO
    target["ppvz_vw"] += row.ppvz_vw or ZERO
    target["ppvz_vw_nds"] += row.ppvz_vw_nds or ZERO
    target["quantity"] += D(str(row.quantity or 0))

    if oper_name in ("Продажа", "Возврат"):
        target["product_qty"] += D(str(row.quantity or 0))

    # Добровольная компенсация has doc_type='Продажа' but its ppvz_for_pay
    # must be excluded from commission (TrueStats convention)
    if oper_name == "Добровольная компенсация при возврате":
        target["compensation_ppvz"] += row.ppvz_for_pay or ZERO

    # Split deductions by type for financial mode
    bonus = row.bonus_type_name or ""
    deduction_val = row.deduction or ZERO
    if deduction_val and bonus:
        if "Продвижение" in bonus or "Медиа" in bonus:
            target["ad_deduction"] += deduction_val
        elif "отзыв" in bonus:
            target["other_deduction"] += deduction_val
        elif "кредит" in bonus.lower() or "заём" in bonus.lower():
            target["loan_deduction"] += deduction_val


def _compute_metrics(sale: dict, ret: dict, other: dict) -> dict:
    """Compute BDR metrics from sale/return/other buckets.

    Commission formula verified against TrueStats:
        commission = retail_amount_net - ppvz_for_pay_net
    where ppvz_for_pay_net includes compensation from "Добров. компенсация" rows.

    IMPORTANT: logistics, penalties, deductions etc. are summed from ALL 3 buckets
    because WB API distributes these values across sale/return/other rows.
    """
    realization = sale["retail_price_withdisc_rub"] - ret["retail_price_withdisc_rub"]
    sales_amount = sale["retail_amount"] - ret["retail_amount"]
    returns_amount = ret["retail_price_withdisc_rub"]

    sale_qty = int(sale["product_qty"])
    ret_qty = int(ret["product_qty"])
    net_sale_qty = sale_qty - ret_qty

    # ── Sum from ALL buckets (sale + ret + other) ──
    # WB API distributes these values across all doc_types
    logistics = sale["delivery_rub"] + ret["delivery_rub"] + other["delivery_rub"]
    penalties = sale["penalty"] + ret["penalty"] + other["penalty"]
    storage = sale["storage_fee"] + ret["storage_fee"] + other["storage_fee"]
    acceptance_val = sale["acceptance"] + ret["acceptance"] + other["acceptance"]
    rebill = sale["rebill_logistic_cost"] + ret["rebill_logistic_cost"] + other["rebill_logistic_cost"]

    # Удержания (deduction) — из всех бакетов
    deductions_total = sale["deduction"] + ret["deduction"] + other["deduction"]
    # Advertising deduction from fin report (Продвижение + Медиа)
    ad_deduction = sale["ad_deduction"] + ret["ad_deduction"] + other["ad_deduction"]
    # Other deductions (Списание за отзыв etc.)
    other_deduction = sale["other_deduction"] + ret["other_deduction"] + other["other_deduction"]
    # Loan payments (not operating expense)
    loan_deduction = sale["loan_deduction"] + ret["loan_deduction"] + other["loan_deduction"]

    # Operating deductions = total - ads - loans
    operating_deductions = deductions_total - ad_deduction - loan_deduction

    # ── Commission ──
    # TrueStats formula: Продажи(net) - (К перечислению(net) - Компенсация)
    ppvz_net = sale["ppvz_for_pay"] - ret["ppvz_for_pay"]
    compensation = sale["compensation_ppvz"]
    commission = sales_amount - (ppvz_net - compensation)

    # WB total reward (for reference)
    ppvz_sales_commission_net = sale["ppvz_sales_commission"] - ret["ppvz_sales_commission"]
    ppvz_vw_net = sale["ppvz_vw"] - ret["ppvz_vw"]
    ppvz_vw_nds_net = sale["ppvz_vw_nds"] - ret["ppvz_vw_nds"]
    total_wb_reward = ppvz_sales_commission_net + ppvz_vw_net + ppvz_vw_nds_net

    # ── To Pay (итого к оплате) ──
    # Вычитаем только ОПЕРАЦИОННЫЕ удержания (отзывы, Джем и пр.)
    # НЕ вычитаем: рекламу (ad_deduction — отдельная статья) и кредиты (loan_deduction — финансовые)
    to_pay = (
        ppvz_net
        - logistics - rebill - penalties - storage
        - operating_deductions - acceptance_val
    )

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
        "rebill": rebill,
    }


def _serialize(d: dict) -> dict:
    """Convert Decimals to floats for JSON."""
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in d.items()}
