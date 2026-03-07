"""
Service: wb_bdr — WB P&L report from finance API.

Fetches WB reportDetailByPeriod, aggregates per-article:
- Splits by doc_type_name: Продажа / Возврат / (пустой = logistics/fines)
- Returns summary KPIs + per-article breakdown

Verified formulas match TrueStats.
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

    for row in raw:
        brand_name = row.get("brand_name", "") or ""
        sa_name = row.get("sa_name", "") or ""
        subject = row.get("subject_name", "") or ""
        nm_id = row.get("nm_id", 0) or 0
        doc_type = row.get("doc_type_name", "") or ""
        oper_name = row.get("supplier_oper_name", "") or ""

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

        _accumulate(target, row, oper_name)

    # --- Build summary from all articles ---
    total_sale = _empty_totals()
    total_ret = _empty_totals()
    total_other = _empty_totals()

    for art in articles_data.values():
        for key in total_sale:
            total_sale[key] += art["sale"].get(key, ZERO)
            total_ret[key] += art["ret"].get(key, ZERO)
            total_other[key] += art["other"].get(key, ZERO)

    summary_result = _compute_metrics(total_sale, total_ret, total_other)

    # --- Build per-article results ---
    result_articles = []
    for sa_name, art in articles_data.items():
        row_result = _compute_metrics(art["sale"], art["ret"], art["other"])
        row_result["sa_name"] = sa_name
        row_result["brand"] = art["brand"]
        row_result["subject"] = art["subject"]
        row_result["nm_id"] = art["nm_id"]
        result_articles.append(row_result)

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
        # Only real product qty (supplier_oper_name == Продажа/Возврат)
        "product_qty": ZERO,
    }


def _accumulate(target: dict, row: dict, oper_name: str):
    """Accumulate row values into target bucket."""
    for field in target:
        if field == "product_qty":
            continue  # handled separately below
        val = row.get(field, 0) or 0
        try:
            target[field] += D(str(val))
        except Exception:
            pass

    # Count product_qty only for actual product operations
    # (not loyalty compensations, etc.)
    if oper_name in ("Продажа", "Возврат"):
        qty = row.get("quantity", 0) or 0
        try:
            target["product_qty"] += D(str(qty))
        except Exception:
            pass


def _compute_metrics(sale: dict, ret: dict, other: dict) -> dict:
    """Compute BDR metrics from sale/return/other buckets."""
    realization = sale["retail_price_withdisc_rub"] - ret["retail_price_withdisc_rub"]
    sales_amount = sale["retail_amount"] - ret["retail_amount"]
    returns_amount = ret["retail_price_withdisc_rub"]

    # Use product_qty — only real product sales/returns
    sale_qty = int(sale["product_qty"])
    ret_qty = int(ret["product_qty"])
    # Net qty as TrueStats displays for "Продажи шт"
    net_sale_qty = sale_qty - ret_qty

    logistics = other["delivery_rub"]
    penalties = other["penalty"]
    storage = other["storage_fee"]
    acceptance_val = other["acceptance"]

    # Прочие удержания = rebill_logistic_cost (Джем, транзит, etc.)
    prochie_uderzhaniya = other["rebill_logistic_cost"]

    # deduction field (usually 0 in WB API, but include for completeness)
    deductions = other["deduction"]

    # Commission fields net (sale - ret)
    ppvz_sales_commission_net = sale["ppvz_sales_commission"] - ret["ppvz_sales_commission"]
    ppvz_reward_net = sale["ppvz_reward"] - ret["ppvz_reward"]
    ppvz_vw_net = sale["ppvz_vw"] - ret["ppvz_vw"]
    ppvz_vw_nds_net = sale["ppvz_vw_nds"] - ret["ppvz_vw_nds"]

    # TrueStats "Комиссия" = ppvz_sales_commission + ppvz_reward (net)
    commission = ppvz_sales_commission_net + ppvz_reward_net

    # TrueStats "Итоговое вознаграждение ВБ" = commission + vw + vw_nds (all net)
    total_wb_reward = ppvz_sales_commission_net + ppvz_vw_net + ppvz_vw_nds_net

    # К оплате = ppvz_for_pay(Продажа) - ppvz_for_pay(Возврат)
    #            - delivery - penalty + storage - deduction - rebill
    ppvz_net = sale["ppvz_for_pay"] - ret["ppvz_for_pay"]
    to_pay = (
        ppvz_net
        - logistics - penalties + storage - deductions - prochie_uderzhaniya
    )

    # Средняя цена продажи (based on net qty)
    avg_sale_price = sales_amount / D(str(net_sale_qty)) if net_sale_qty > 0 else ZERO

    # Средняя цена до скидок МП
    avg_retail_price = realization / D(str(net_sale_qty)) if net_sale_qty > 0 else ZERO

    # Процент выкупа = sale_qty / (sale_qty + ret_qty) * 100
    total_qty = sale_qty + ret_qty
    buyout_pct = D(str(sale_qty)) / D(str(total_qty)) * 100 if total_qty > 0 else ZERO

    return {
        "realization": realization,
        "sales_amount": sales_amount,
        "returns_amount": returns_amount,
        "to_pay": to_pay,
        "ppvz_for_pay": ppvz_net,
        "sale_qty": net_sale_qty,  # Net qty (as TrueStats)
        "sale_qty_gross": sale_qty,
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
        "deductions": prochie_uderzhaniya,  # rebill_logistic_cost
        "rebill": prochie_uderzhaniya,
    }


def _serialize(d: dict) -> dict:
    """Convert Decimals to floats for JSON."""
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in d.items()}
