# ruff: noqa: RUF001
"""
BDR rates — per-article coefficients from wb_finance_rows for funnel profit calculation.

Provides 3 coefficients per nm_id (averaged over lookback period):
- to_pay_rate: what % of realization WB actually pays out
- spp_rate: SPP discount % (1 - sales_amount / realization)
- buyout_pct: % of orders that are actually bought (not returned)

These replace the flat tariff-based commission and fix profit overstatement.
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("dds.funnel.bdr_rates")

# Simple in-memory cache (project_id -> (timestamp, rates_map))
_cache: dict[int, tuple[float, dict]] = {}
_CACHE_TTL = 3600  # 1 hour


@dataclass
class BdrRates:
    """Per-article BDR coefficients."""

    to_pay_rate: float  # to_pay / realization (e.g. 0.598 = 59.8%)
    spp_rate: float  # 1 - sales_amount / realization (e.g. 0.331 = 33.1%)
    buyout_pct: float  # sale_qty / (sale_qty + ret_qty) (e.g. 0.976 = 97.6%)


_BDR_RATES_SQL = text("""
SELECT
  nm_id,

  -- Realization (net: sales - returns, до СПП)
  COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_price_withdisc_rub ELSE 0 END), 0) -
  COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN retail_price_withdisc_rub ELSE 0 END), 0)
    AS realization,

  -- Sales amount (net: sales - returns, после СПП)
  COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_amount ELSE 0 END), 0) -
  COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN retail_amount ELSE 0 END), 0)
    AS sales_amount,

  -- to_pay (net: ppvz - logistics - penalties - storage - deductions - acceptance)
  COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN ppvz_for_pay ELSE 0 END), 0) -
  COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN ppvz_for_pay ELSE 0 END), 0)
  - COALESCE(SUM(delivery_rub), 0)
  - COALESCE(SUM(penalty), 0)
  - COALESCE(SUM(storage_fee), 0)
  - COALESCE(SUM(deduction), 0)
  - COALESCE(SUM(acceptance), 0)
    AS to_pay,

  -- Quantities (only from oper_name Продажа/Возврат)
  COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа'
      AND supplier_oper_name IN ('Продажа', 'Возврат')
      THEN quantity ELSE 0 END), 0) AS sale_qty,
  COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат'
      AND supplier_oper_name IN ('Продажа', 'Возврат')
      THEN quantity ELSE 0 END), 0) AS ret_qty

FROM wb_finance_rows
WHERE project_id = :project_id
  AND rr_dt >= :date_from
GROUP BY nm_id
HAVING
  -- Only articles with meaningful realization
  (COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_price_withdisc_rub ELSE 0 END), 0) -
   COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN retail_price_withdisc_rub ELSE 0 END), 0)) > 0
""")


async def _query_bdr_rates(db: AsyncSession, project_id: int, date_from: date) -> dict[int, BdrRates]:
    """Execute BDR rates query and build rates map."""
    result = await db.execute(
        _BDR_RATES_SQL,
        {"project_id": project_id, "date_from": date_from},
    )
    rows = result.fetchall()

    rates_map: dict[int, BdrRates] = {}
    for row in rows:
        realization = float(row.realization)
        sales_amount = float(row.sales_amount)
        to_pay = float(row.to_pay)
        sale_qty = int(row.sale_qty)
        ret_qty = int(row.ret_qty)

        if realization <= 0:
            continue

        to_pay_rate = to_pay / realization
        spp_rate = 1 - (sales_amount / realization) if realization > 0 else 0
        total_qty = sale_qty + ret_qty
        buyout_pct = sale_qty / total_qty if total_qty > 0 else 1.0

        # Sanity checks — skip obviously broken data
        if to_pay_rate < 0 or to_pay_rate > 1:
            continue
        if spp_rate < 0 or spp_rate > 0.95:
            continue

        rates_map[row.nm_id] = BdrRates(
            to_pay_rate=round(to_pay_rate, 4),
            spp_rate=round(spp_rate, 4),
            buyout_pct=round(buyout_pct, 4),
        )

    return rates_map


async def get_bdr_rates(db: AsyncSession, project_id: int, lookback_days: int = 7) -> dict[int, BdrRates]:
    """Get BDR coefficients per nm_id from wb_finance_rows.

    Tries lookback_days first, then expands progressively: 14, 30, 90 days.
    Also tries shorter periods (3, 1 day) for fresh data.

    Uses in-memory TTL cache (1h). Empty results are NOT cached.

    Returns: {nm_id: BdrRates}
    """
    import time

    now = time.monotonic()
    cached = _cache.get(project_id)
    if cached and (now - cached[0]) < _CACHE_TTL and cached[1]:
        return cached[1]

    today = date.today()

    # Try preferred lookback first, then expand progressively
    tried = set()
    for days in [lookback_days, 3, 1, 14, 30, 90]:
        if days in tried:
            continue
        tried.add(days)
        date_from = today - timedelta(days=days)
        rates_map = await _query_bdr_rates(db, project_id, date_from)
        if rates_map:
            logger.info(
                "BDR rates loaded: project=%d, lookback=%d days, articles=%d",
                project_id,
                days,
                len(rates_map),
            )
            _cache[project_id] = (now, rates_map)
            return rates_map

    logger.info("BDR rates: no data for project=%d", project_id)
    return {}


def compute_profit_bdr(
    orders_sum: float,
    orders_count: int,
    adv_sum: float,
    cost_price: float,
    bdr: BdrRates,
    tax_info: dict,
) -> dict:
    """Compute profit using BDR rates instead of flat tariff.

    Formula:
        est_realization = orders_sum * buyout_pct
        price_after_spp = est_realization * (1 - spp_rate)
        to_pay_est = est_realization * to_pay_rate
        cost_total = cost_price * orders_count * buyout_pct
        tax = compute_tax(price_after_spp, tax_info, expenses)
        profit = to_pay_est - cost_total - adv_sum - tax
    """
    buyout = bdr.buyout_pct
    est_real = orders_sum * buyout
    price_after_spp = est_real * (1 - bdr.spp_rate)
    to_pay_est = est_real * bdr.to_pay_rate
    cost_total = cost_price * orders_count * buyout

    # WB expenses for tax deduction (USN D-R)
    wb_expenses = est_real - to_pay_est  # commission + logistics + penalties + storage

    # Tax calculation using same logic as bdr_enrichment.apply_tax
    tax = _compute_tax(price_after_spp, tax_info, wb_expenses, adv_sum, cost_total)

    profit = to_pay_est - cost_total - adv_sum - tax
    margin = (profit / est_real * 100) if est_real > 0 else 0

    return {
        "revenue": round(est_real, 2),
        "profit": round(profit, 2),
        "margin": round(margin, 2),
        "tax": round(tax, 2),
        "commission": round(est_real - to_pay_est, 2),
        "commission_rate": round((1 - bdr.to_pay_rate) * 100, 2),
        "cost_total": round(cost_total, 2),
        "buyout_pct": round(buyout * 100, 2),
        "spp_rate": round(bdr.spp_rate * 100, 2),
        "to_pay_rate": round(bdr.to_pay_rate * 100, 2),
        "has_bdr": True,
    }


def _compute_tax(
    income: float,
    tax_info: dict,
    wb_expenses: float,
    adv_sum: float,
    cost_total: float,
) -> float:
    """Compute tax from income (price_after_spp).

    Same formula as bdr_enrichment.apply_tax but returns just the number.
    """
    usn_rate = tax_info.get("usn_rate", 0) / 100
    nds_rate = tax_info.get("nds_rate", 0) / 100
    regime = tax_info.get("tax_regime", "usn_income")
    cost_as_expense = tax_info.get("cost_as_expense", False)

    # НДС
    nds_sum = income * nds_rate / (1 + nds_rate) if nds_rate > 0 else 0
    tax_base = income - nds_sum

    if regime == "usn_income_expense_vat":
        expenses = abs(wb_expenses) + adv_sum
        if cost_as_expense:
            expenses += cost_total
        tax_base = income - nds_sum - expenses

    usn_sum = max(tax_base * usn_rate, 0)
    return nds_sum + usn_sum
