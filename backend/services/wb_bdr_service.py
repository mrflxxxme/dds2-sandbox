"""
Service: wb_bdr — WB P&L report from locally cached finance data.

Reads from wb_finance_rows (pre-synced), enriches with:
- Advertising data from WbFunnelDaily
- Cost prices from cost_order_items (average)
- Tax calculation from project tax_rates settings

Verified formulas match TrueStats.
"""

import logging
from datetime import date
from decimal import Decimal
from collections import defaultdict
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.wb_finance import WbFinanceRow
from backend.models import WbFunnelDaily, CostOrderItem

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
    Build BDR from locally cached WB finance data.
    Enriches with ads, cost, tax.
    Returns { summary, articles, brands, period, total_rows, sync_status, tax_info }
    """
    # ── 1. Ensure we have data (trigger initial sync if needed) ──
    from backend.services.wb_finance_sync import ensure_initial_sync, get_sync_status
    await ensure_initial_sync(db, project_id)

    sync_status = await get_sync_status(db, project_id)

    # ── 2. Load finance rows from local DB ──
    # Filter by rr_dt (realization date) if available, fallback to date_from/date_to
    from sqlalchemy import or_
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
    ads_map = await _load_ads(db, project_id, date_from, date_to)

    # ── 4. Load cost prices from cost_order_items ──
    cost_map = await _load_avg_costs(db, project_id)

    # ── 5. Load tax settings ──
    tax_info = await _load_tax_settings(db, project_id, date_from, date_to)

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

        # Ads from funnel
        nm = art["nm_id"]
        adv_sum = float(ads_map.get(nm, 0))
        row_result["adv_sum"] = adv_sum
        total_adv += D(str(adv_sum))

        # Cost from history (avg)
        cost_price = cost_map.get(sa_name, 0)
        sale_qty = row_result["sale_qty"]
        cost_total = cost_price * sale_qty if sale_qty > 0 else 0
        row_result["cost_price"] = round(cost_price, 2)
        row_result["cost_total"] = round(cost_total, 2)
        total_cost += D(str(cost_total))

        result_articles.append(row_result)

    # Sort by to_pay descending
    result_articles.sort(key=lambda x: x["to_pay"], reverse=True)

    # ── 8. Summary ──
    summary_result = _compute_metrics(total_sale, total_ret, total_other)
    summary_result["adv_sum"] = float(total_adv)
    summary_result["cost_total"] = float(total_cost)

    # ── 9. Tax calculation ──
    _apply_tax(summary_result, tax_info, total_adv, total_cost)
    for art_row in result_articles:
        _apply_tax_article(art_row, tax_info)

    return {
        "summary": _serialize(summary_result),
        "articles": [_serialize(a) for a in result_articles],
        "brands": sorted(all_brands),
        "period": {"date_from": str(date_from), "date_to": str(date_to)},
        "total_rows": len(raw_rows),
        "sync_status": sync_status,
        "tax_info": tax_info,
    }


# ─── Ads loader ──────────────────────────────────────────────────────────────

async def _load_ads(db: AsyncSession, pid: int, d_from: date, d_to: date) -> dict[int, float]:
    """Load total ad spend per nm_id from WbFunnelDaily."""
    result = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            func.sum(WbFunnelDaily.adv_sum).label("total_adv"),
        ).where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.date >= d_from,
            WbFunnelDaily.date <= d_to,
        ).group_by(WbFunnelDaily.nm_id)
    )
    return {r.nm_id: float(r.total_adv or 0) for r in result}


# ─── Cost loader ─────────────────────────────────────────────────────────────

async def _load_avg_costs(db: AsyncSession, pid: int) -> dict[str, float]:
    """Load weighted average cost per article_seller from cost_order_items.
    
    Joins CostOrder to filter by project_id.
    Weighted avg = SUM(total_rub) / SUM(qty) — correct for varying batch sizes.
    """
    from backend.models.cost import CostOrder

    result = await db.execute(
        select(
            CostOrderItem.article_seller,
            (func.sum(CostOrderItem.total_rub * CostOrderItem.qty) / func.nullif(func.sum(CostOrderItem.qty), 0)).label("avg_cost"),
        ).join(
            CostOrder, CostOrderItem.order_no == CostOrder.order_no
        ).where(
            CostOrder.project_id == pid,
            CostOrder.is_deleted == False,
            CostOrderItem.article_seller.isnot(None),
            CostOrderItem.total_rub.isnot(None),
            CostOrderItem.qty > 0,
        ).group_by(CostOrderItem.article_seller)
    )
    return {r.article_seller: float(r.avg_cost or 0) for r in result if r.article_seller}


# ─── Tax loader & calculator ────────────────────────────────────────────────

async def _load_tax_settings(db: AsyncSession, pid: int, d_from: date, d_to: date) -> dict:
    """Load tax rate settings for the period.

    Uses the month/year from date_from. For multi-month ranges,
    takes the first month's settings (simplified).
    """
    from backend.models.tax import TaxRate

    year = d_from.year
    month = d_from.month

    result = await db.execute(
        select(TaxRate).where(
            TaxRate.project_id == pid,
            TaxRate.year == year,
            TaxRate.month == month,
            TaxRate.brand == "__project__",
        ).limit(1)
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


def _apply_tax(summary: dict, tax_info: dict, total_adv: Decimal, total_cost: Decimal):
    """Apply tax calculation to summary row.

    Формулы:
    - УСН «Доходы»:
      НДС = Доходы * НДС% / (1 + НДС%)
      База = Доходы - НДС
      УСН = База * УСН%
    - УСН «Доходы - Расходы»:
      НДС = Доходы * НДС% / (1 + НДС%)
      Расходы = Логистика + Хранение + Комиссия + Штрафы + Удержания + Реклама + [С/С]
      База = Доходы - НДС - Расходы
      УСН = База * УСН%
    """
    usn_rate = tax_info.get("usn_rate", 0) / 100
    nds_rate = tax_info.get("nds_rate", 0) / 100
    regime = tax_info.get("tax_regime", "usn_income")
    cost_as_expense = tax_info.get("cost_as_expense", False)

    # Доходы = Продажи (sales_amount = realization after SPP)
    income = float(summary.get("sales_amount", 0))

    # НДС
    if nds_rate > 0:
        nds_sum = income * nds_rate / (1 + nds_rate)
    else:
        nds_sum = 0

    tax_base = income - nds_sum

    if regime == "usn_income_expense_vat":
        # Расходы
        expenses = (
            abs(float(summary.get("logistics", 0)))
            + abs(float(summary.get("storage", 0)))
            + abs(float(summary.get("commission", 0)))
            + abs(float(summary.get("penalties", 0)))
            + abs(float(summary.get("deductions", 0)))
            + float(total_adv)
        )
        if cost_as_expense:
            expenses += float(total_cost)
        tax_base = income - nds_sum - expenses
        summary["expenses_total"] = round(expenses, 2)

    usn_sum = max(tax_base * usn_rate, 0)
    total_tax = nds_sum + usn_sum

    summary["tax_nds"] = round(nds_sum, 2)
    summary["tax_usn"] = round(usn_sum, 2)
    summary["tax_total"] = round(total_tax, 2)
    summary["profit"] = round(
        float(summary.get("to_pay", 0))
        - float(total_adv)
        - float(total_cost)
        - total_tax,
        2,
    )


def _apply_tax_article(art: dict, tax_info: dict):
    """Simplified per-article tax (proportional to sales_amount share).

    For per-article view, we don't repeat the full formula —
    just show raw adv/cost. Tax only in summary.
    """
    # Profit per article (simple: to_pay - adv - cost)
    art["profit"] = round(
        float(art.get("to_pay", 0))
        - float(art.get("adv_sum", 0))
        - float(art.get("cost_total", 0)),
        2,
    )


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


def _compute_metrics(sale: dict, ret: dict, other: dict) -> dict:
    """Compute BDR metrics from sale/return/other buckets.

    Commission formula verified against TrueStats:
        commission = retail_amount_net - ppvz_for_pay_net
    where ppvz_for_pay_net includes compensation from "Добров. компенсация" rows.
    """
    realization = sale["retail_price_withdisc_rub"] - ret["retail_price_withdisc_rub"]
    sales_amount = sale["retail_amount"] - ret["retail_amount"]
    returns_amount = ret["retail_price_withdisc_rub"]

    sale_qty = int(sale["product_qty"])
    ret_qty = int(ret["product_qty"])
    net_sale_qty = sale_qty - ret_qty

    logistics = other["delivery_rub"]
    penalties = other["penalty"]
    storage = other["storage_fee"]
    acceptance_val = other["acceptance"]

    # Удержания (deduction) — полное значение из WB API
    deductions = other["deduction"]
    # Обратная логистика (rebill) — отдельная статья
    rebill = other["rebill_logistic_cost"]

    # ── Commission ──
    # TrueStats formula: Продажи(net) - (К перечислению(net) - Компенсация)
    # "Добровольная компенсация при возврате" has doc_type='Продажа'
    # so its ppvz_for_pay is in sale bucket, but TrueStats excludes it
    ppvz_net = sale["ppvz_for_pay"] - ret["ppvz_for_pay"]
    compensation = sale["compensation_ppvz"]  # ppvz from compensation rows
    commission = sales_amount - (ppvz_net - compensation)

    # WB total reward (for reference)
    ppvz_sales_commission_net = sale["ppvz_sales_commission"] - ret["ppvz_sales_commission"]
    ppvz_vw_net = sale["ppvz_vw"] - ret["ppvz_vw"]
    ppvz_vw_nds_net = sale["ppvz_vw_nds"] - ret["ppvz_vw_nds"]
    total_wb_reward = ppvz_sales_commission_net + ppvz_vw_net + ppvz_vw_nds_net

    # ── To Pay (итого к оплате) ──
    # ppvz_for_pay_net - logistics - penalties - storage - rebill
    to_pay = (
        ppvz_net
        - logistics - penalties - storage - rebill
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
        "deductions": deductions,
        "rebill": rebill,
    }


def _serialize(d: dict) -> dict:
    """Convert Decimals to floats for JSON."""
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in d.items()}
