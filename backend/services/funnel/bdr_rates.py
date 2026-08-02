# ruff: noqa: RUF001
"""
BDR rates — per-article DAILY coefficients from wb_finance_rows for funnel profit.

Provides 3 coefficients per (nm_id, date):
- to_pay_rate: what % of realization WB actually pays out
- spp_rate: SPP discount % (1 - sales_amount / realization)
- buyout_pct: % of orders that are actually bought (not returned)

Daily rates are used to match funnel data precisely:
- Funnel day 18.03 → BDR rates from rr_dt = 18.03
- If no data for that day → fallback to article-level average
"""

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("dds.funnel.bdr_rates")

# In-memory cache: project_id -> (monotonic_ts, daily_map, avg_map)
_cache: dict[int, tuple[float, dict, dict]] = {}
_CACHE_TTL = 3600  # 1 hour


@dataclass
class BdrRates:
    """Per-article BDR coefficients."""

    to_pay_rate: float  # to_pay / realization (e.g. 0.598 = 59.8%)
    spp_rate: float  # 1 - sales_amount / realization (e.g. 0.331 = 33.1%)
    buyout_pct: float  # sale_qty / (sale_qty + ret_qty + cancel_qty) (e.g. 0.882 = 88.2%)
    # Эквайринг / реализация. Уже сидит внутри to_pay — это разбор расхода WB,
    # а не добавка к нему. Ноль, если отчёт заливали до появления поля.
    acq_rate: float = 0.0


class BdrRatesLookup:
    """Wrapper for daily + avg BDR rates with automatic fallback.

    Usage:
        lookup = BdrRatesLookup(daily_map, avg_map)
        bdr = lookup.get(nm_id, row_date)  # daily if exists, else avg
        bdr = lookup.get(nm_id)            # avg only
    """

    def __init__(
        self,
        daily_map: dict[tuple[int, date], BdrRates],
        avg_map: dict[int, BdrRates],
    ):
        self.daily = daily_map
        self.avg = avg_map

    def get(self, nm_id: int, d: date | None = None) -> BdrRates | None:
        """Look up rates: daily first (if date provided), then avg fallback."""
        if d is not None:
            daily = self.daily.get((nm_id, d))
            if daily:
                return daily
        return self.avg.get(nm_id)

    def __bool__(self) -> bool:
        return bool(self.daily) or bool(self.avg)


# ── Daily rates SQL (GROUP BY nm_id, rr_dt) ─────────────────────────────────

_BDR_RATES_DAILY_SQL = text("""
SELECT
  nm_id,
  rr_dt,

  -- Realization (net: sales - returns, до СПП)
  COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_price_withdisc_rub ELSE 0 END), 0) -
  COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN retail_price_withdisc_rub ELSE 0 END), 0)
    AS realization,

  -- Sales amount (net, после СПП)
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

  -- Эквайринг (комиссия за организацию платежей) — внутри ppvz_for_pay
  COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN acquiring_fee ELSE 0 END), 0) -
  COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN acquiring_fee ELSE 0 END), 0)
    AS acquiring,

  -- Quantities
  COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа'
      AND supplier_oper_name IN ('Продажа', 'Возврат')
      THEN quantity ELSE 0 END), 0) AS sale_qty,
  COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат'
      AND supplier_oper_name IN ('Продажа', 'Возврат')
      THEN quantity ELSE 0 END), 0) AS ret_qty

FROM wb_finance_rows
WHERE project_id = :project_id
  AND rr_dt >= :date_from
GROUP BY nm_id, rr_dt
HAVING
  (COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_price_withdisc_rub ELSE 0 END), 0) -
   COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN retail_price_withdisc_rub ELSE 0 END), 0)) > 0
""")


def _build_rates(
    realization: float,
    sales_amount: float,
    to_pay: float,
    sale_qty: int,
    ret_qty: int,
    cancel_qty: int = 0,
    acquiring: float = 0.0,
) -> BdrRates | None:
    """Build BdrRates from raw aggregates, with sanity clamping."""
    if realization <= 0:
        return None

    to_pay_rate = max(to_pay / realization, 0)
    if to_pay_rate > 1:
        to_pay_rate = 1.0

    spp_rate = max(1 - (sales_amount / realization), 0)
    if spp_rate > 0.95:
        spp_rate = 0.95

    total_qty = sale_qty + ret_qty + cancel_qty
    buyout_pct = sale_qty / total_qty if total_qty > 0 else 1.0

    return BdrRates(
        to_pay_rate=round(to_pay_rate, 4),
        spp_rate=round(spp_rate, 4),
        buyout_pct=round(buyout_pct, 4),
        acq_rate=round(max(acquiring / realization, 0), 4),
    )


async def _load_cancel_map(
    db: AsyncSession,
    project_id: int,
    date_from: date,
) -> dict[tuple[int, date], int]:
    """Load cancelled order counts per (nm_id, dt) from wb_order_cancel_daily."""
    result = await db.execute(
        text("""
            SELECT nm_id, dt, cancelled_orders
            FROM wb_order_cancel_daily
            WHERE project_id = :project_id AND dt >= :date_from
        """),
        {"project_id": project_id, "date_from": date_from},
    )
    return {(row.nm_id, row.dt): row.cancelled_orders for row in result.fetchall()}


async def _query_bdr_rates_daily(
    db: AsyncSession,
    project_id: int,
    date_from: date,
) -> tuple[dict[tuple[int, date], BdrRates], dict[int, BdrRates]]:
    """Query daily BDR rates. Returns (daily_map, avg_map).

    daily_map: {(nm_id, rr_dt): BdrRates} — exact rates per day
    avg_map:   {nm_id: BdrRates}           — weighted avg as fallback
    """
    result = await db.execute(
        _BDR_RATES_DAILY_SQL,
        {"project_id": project_id, "date_from": date_from},
    )
    rows = result.fetchall()

    # Load cancel stats for buyout adjustment
    cancel_map = await _load_cancel_map(db, project_id, date_from)

    daily_map: dict[tuple[int, date], BdrRates] = {}

    # Accumulators for weighted average per nm_id
    acc: dict[int, dict] = {}  # nm_id -> {realization, sales_amount, to_pay, sale_qty, ret_qty, cancel_qty}

    for row in rows:
        realization = float(row.realization)
        sales_amount = float(row.sales_amount)
        to_pay = float(row.to_pay)
        acquiring = float(row.acquiring or 0)
        sale_qty = int(row.sale_qty)
        ret_qty = int(row.ret_qty)
        nm_id = row.nm_id
        rr_dt = row.rr_dt
        cancel_qty = cancel_map.get((nm_id, rr_dt), 0)

        rates = _build_rates(realization, sales_amount, to_pay, sale_qty, ret_qty, cancel_qty, acquiring)
        if rates:
            daily_map[(nm_id, rr_dt)] = rates

        # Accumulate for avg
        if nm_id not in acc:
            acc[nm_id] = {
                "realization": 0,
                "sales_amount": 0,
                "to_pay": 0,
                "sale_qty": 0,
                "ret_qty": 0,
                "cancel_qty": 0,
                "acquiring": 0,
            }
        a = acc[nm_id]
        a["realization"] += realization
        a["sales_amount"] += sales_amount
        a["to_pay"] += to_pay
        a["sale_qty"] += sale_qty
        a["ret_qty"] += ret_qty
        a["cancel_qty"] += cancel_qty
        a["acquiring"] += acquiring

    # Build avg map
    avg_map: dict[int, BdrRates] = {}
    for nm_id, a in acc.items():
        rates = _build_rates(
            a["realization"],
            a["sales_amount"],
            a["to_pay"],
            a["sale_qty"],
            a["ret_qty"],
            a["cancel_qty"],
            a["acquiring"],
        )
        if rates:
            avg_map[nm_id] = rates

    return daily_map, avg_map


async def get_bdr_rates(
    db: AsyncSession,
    project_id: int,
    lookback_days: int = 30,
) -> BdrRatesLookup:
    """Get daily BDR coefficients per (nm_id, date) from wb_finance_rows.

    Returns BdrRatesLookup with .get(nm_id, date) method:
    - Tries daily rate for exact (nm_id, date) first
    - Falls back to period-average for nm_id

    Uses in-memory TTL cache (1h).
    """
    now = time.monotonic()
    cached = _cache.get(project_id)
    if cached and (now - cached[0]) < _CACHE_TTL and (cached[1] or cached[2]):
        return BdrRatesLookup(cached[1], cached[2])

    today = date.today()
    date_from = today - timedelta(days=lookback_days)

    daily_map, avg_map = await _query_bdr_rates_daily(db, project_id, date_from)

    if daily_map or avg_map:
        logger.info(
            "BDR rates loaded: project=%d, lookback=%d days, daily_entries=%d, articles=%d",
            project_id,
            lookback_days,
            len(daily_map),
            len(avg_map),
        )
        _cache[project_id] = (now, daily_map, avg_map)
    else:
        logger.info("BDR rates: no data for project=%d", project_id)

    return BdrRatesLookup(daily_map, avg_map)


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

    # WB commission for tax deduction (without SPP discount)
    # SPP already deducted from income (price_after_spp), so expenses = only commission
    wb_commission = price_after_spp - to_pay_est

    # Tax calculation using same logic as bdr_enrichment.apply_tax.
    # НДС отдаём отдельно: в разделе он нужен своей колонкой, а не только внутри налога.
    nds, usn = _compute_tax_parts(price_after_spp, tax_info, wb_commission, adv_sum, cost_total)
    tax = nds + usn

    profit = to_pay_est - cost_total - adv_sum - tax
    margin = (profit / est_real * 100) if est_real > 0 else 0

    return {
        "revenue": round(est_real, 2),
        "profit": round(profit, 2),
        "margin": round(margin, 2),
        "tax": round(tax, 2),
        "nds": round(nds, 2),
        "commission": round(est_real - to_pay_est, 2),
        "acquiring": round(est_real * bdr.acq_rate, 2),
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
    wb_commission: float,
    adv_sum: float,
    cost_total: float,
) -> float:
    """Совокупный налог (НДС + УСН) — прежний контракт для внешних вызовов."""
    nds, usn = _compute_tax_parts(income, tax_info, wb_commission, adv_sum, cost_total)
    return nds + usn


def _compute_tax_parts(
    income: float,
    tax_info: dict,
    wb_commission: float,
    adv_sum: float,
    cost_total: float,
) -> tuple[float, float]:
    """Налог по частям: (НДС, УСН).

    Same formula as bdr_enrichment.apply_tax but returns just the number.
    income = price_after_spp (already without SPP)
    wb_commission = price_after_spp - to_pay_est (only WB commission, no SPP)
    """
    usn_rate = tax_info.get("usn_rate", 0) / 100
    nds_rate = tax_info.get("nds_rate", 0) / 100
    regime = tax_info.get("tax_regime", "usn_income")
    cost_as_expense = tax_info.get("cost_as_expense", False)

    nds_sum = income * nds_rate / (1 + nds_rate) if nds_rate > 0 else 0
    tax_base = income - nds_sum

    if regime == "usn_income_expense_vat":
        expenses = abs(wb_commission) + adv_sum
        if cost_as_expense:
            expenses += cost_total
        tax_base = income - nds_sum - expenses

    usn_sum = max(tax_base * usn_rate, 0)
    return float(nds_sum), float(usn_sum)
