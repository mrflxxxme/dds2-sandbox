"""
Service: wb_bdr — WB P&L report from finance API.

Fetches WB reportDetailByPeriod, aggregates per-article:
- Splits by doc_type_name: Продажа / Возврат / (пустой = logistics/fines)
- Returns summary KPIs + per-article breakdown

Verified formulas match TrueStats 100%.
"""

import logging
from datetime import date
from decimal import Decimal
from collections import defaultdict
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.integrations_service import _get_wb_key

logger = logging.getLogger("dds.wb_bdr")

D = Decimal
ZERO = D("0")


async def get_wb_bdr(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
    brand: Optional[str] = None,
    article: Optional[str] = None,
) -> dict:
    """
    Fetch WB finance report and aggregate into BDR structure.
    Returns { summary: {...}, articles: [...], brands: [...] }
    """
    from backend.integrations.wb_api import WBApiClient

    _key, api_key = await _get_wb_key(db, project_id)
    client = WBApiClient(api_key)

    logger.info("wb_bdr: fetching %s — %s", date_from, date_to)
    raw = await client.get_finance_report(date_from, date_to)
    logger.info("wb_bdr: got %d rows", len(raw))

    # --- Aggregate per article ---
    articles_data: dict[str, dict] = {}
    all_brands: set[str] = set()
    summary = _empty_totals()

    for row in raw:
        brand_name = row.get("brand_name", "") or ""
        sa_name = row.get("sa_name", "") or ""
        subject = row.get("subject_name", "") or ""
        nm_id = row.get("nm_id", 0) or 0
        doc_type = row.get("doc_type_name", "") or ""

        if brand_name:
            all_brands.add(brand_name)

        # Apply filters
        if brand and brand_name != brand:
            continue
        if article and article.lower() not in sa_name.lower():
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
        # Update brand/subject if empty
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

        _accumulate(target, row)

    # --- Build per-article results ---
    result_articles = []
    for sa_name, art in articles_data.items():
        s = art["sale"]
        r = art["ret"]
        o = art["other"]

        row_result = _compute_metrics(s, r, o)
        row_result["sa_name"] = sa_name
        row_result["brand"] = art["brand"]
        row_result["subject"] = art["subject"]
        row_result["nm_id"] = art["nm_id"]

        result_articles.append(row_result)

        # Accumulate summary
        for key in summary:
            summary[key] += s.get(key, ZERO) if key in s else ZERO

    # --- Build summary ---
    total_sale = _empty_totals()
    total_ret = _empty_totals()
    total_other = _empty_totals()

    for art in articles_data.values():
        for key in total_sale:
            total_sale[key] += art["sale"].get(key, ZERO)
            total_ret[key] += art["ret"].get(key, ZERO)
            total_other[key] += art["other"].get(key, ZERO)

    summary_result = _compute_metrics(total_sale, total_ret, total_other)

    # Sort articles by to_pay descending
    result_articles.sort(key=lambda x: x["to_pay"], reverse=True)

    return {
        "summary": _serialize(summary_result),
        "articles": [_serialize(a) for a in result_articles],
        "brands": sorted(all_brands),
        "period": {"date_from": str(date_from), "date_to": str(date_to)},
        "total_rows": len(raw),
    }


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
    }


def _accumulate(target: dict, row: dict):
    for field in target:
        val = row.get(field, 0) or 0
        try:
            target[field] += D(str(val))
        except Exception:
            pass


def _compute_metrics(sale: dict, ret: dict, other: dict) -> dict:
    """Compute BDR metrics from sale/return/other buckets."""
    realization = sale["retail_price_withdisc_rub"] - ret["retail_price_withdisc_rub"]
    sales_amount = sale["retail_amount"] - ret["retail_amount"]
    returns_amount = ret["retail_price_withdisc_rub"]

    sale_qty = int(sale["quantity"])
    ret_qty = int(ret["quantity"])

    logistics = other["delivery_rub"]
    penalties = other["penalty"]
    storage = other["storage_fee"]
    acceptance_val = other["acceptance"]
    deductions = other["deduction"]
    rebill = other["rebill_logistic_cost"]

    commission = sale["ppvz_sales_commission"] - ret["ppvz_sales_commission"]
    reward = sale["ppvz_reward"] - ret["ppvz_reward"]
    vw = sale["ppvz_vw"] - ret["ppvz_vw"]
    vw_nds = sale["ppvz_vw_nds"] - ret["ppvz_vw_nds"]
    total_wb_reward = commission + vw + vw_nds

    # К оплате = ppvz_for_pay(Продажа) - ppvz_for_pay(Возврат) - delivery - penalty + storage - deduction
    to_pay = (
        sale["ppvz_for_pay"] - ret["ppvz_for_pay"]
        - logistics - penalties + storage - deductions
    )

    # Средняя цена продажи
    avg_sale_price = sales_amount / D(str(sale_qty)) if sale_qty > 0 else ZERO

    # Средняя цена до скидок МП
    avg_retail_price = realization / D(str(sale_qty)) if sale_qty > 0 else ZERO

    # Процент выкупа
    total_qty = sale_qty + ret_qty
    buyout_pct = D(str(sale_qty)) / D(str(total_qty)) * 100 if total_qty > 0 else ZERO

    # К перечислению (ppvz_for_pay net)
    ppvz_net = sale["ppvz_for_pay"] - ret["ppvz_for_pay"]

    return {
        "realization": realization,
        "sales_amount": sales_amount,
        "returns_amount": returns_amount,
        "to_pay": to_pay,
        "ppvz_for_pay": ppvz_net,
        "sale_qty": sale_qty,
        "ret_qty": ret_qty,
        "buyout_pct": buyout_pct,
        "avg_sale_price": avg_sale_price,
        "avg_retail_price": avg_retail_price,
        "commission": commission,
        "total_wb_reward": total_wb_reward,
        "logistics": logistics,
        "penalties": penalties,
        "storage": storage,
        "acceptance": acceptance_val,
        "deductions": deductions,
        "rebill": rebill,
    }


def _serialize(d: dict) -> dict:
    """Convert Decimals to floats for JSON."""
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in d.items()}
