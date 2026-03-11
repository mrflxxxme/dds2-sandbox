"""
Service: opiu — ОПИУ (P&L) report from WB finance data.

Monthly breakdown with hierarchical P&L rows.
Reuses loaders from bdr_loaders.py.
"""

import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.wb_finance import WbFinanceRow
from backend.cache import cached
from backend.services.bdr_loaders import (
    load_ads,
    load_avg_costs,
    load_cost_overrides,
    load_tax_settings,
)

logger = logging.getLogger("dds.opiu")

D = Decimal
ZERO = D("0")


@cached(prefix="reports:opiu", ttl=3600)
async def get_opiu(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
    brand: Optional[str] = None,
    article: Optional[str] = None,
) -> dict:
    """
    Build ОПИУ (P&L) from locally cached WB finance data.
    Returns monthly breakdown + total with hierarchical P&L rows.
    """
    # ── 1. Load finance rows ──
    q = select(WbFinanceRow).where(
        WbFinanceRow.project_id == project_id,
        or_(
            WbFinanceRow.rr_dt.between(date_from, date_to),
            (WbFinanceRow.rr_dt.is_(None)) & (WbFinanceRow.date_from >= date_from) & (WbFinanceRow.date_to <= date_to),
        ),
    )
    result = await db.execute(q)
    raw_rows = result.scalars().all()

    # ── 2. Load enrichment data ──
    ads_map = await load_ads(db, project_id, date_from, date_to)
    cost_map = await load_avg_costs(db, project_id)
    cost_overrides = await load_cost_overrides(db, project_id)
    tax_info = await load_tax_settings(db, project_id, date_from, date_to)

    # ── 3. Determine months in range ──
    months_set: set[str] = set()
    current = date_from.replace(day=1)
    while current <= date_to:
        months_set.add(current.strftime("%Y-%m"))
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    # ── 4. Aggregate per month ──
    # Per-month accumulators: month_key -> totals dict
    monthly_data: dict[str, dict] = {}
    for mk in months_set:
        monthly_data[mk] = _empty_totals()
    total_data = _empty_totals()

    # Track per-article nm_id for cost/ads lookup
    article_nm: dict[str, int] = {}
    # Per-month per-article qty for cost calculation
    monthly_article_qty: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total_article_qty: dict[str, int] = defaultdict(int)
    all_brands: set[str] = set()

    for row in raw_rows:
        brand_name = row.brand_name or ""
        sa_name = row.sa_name or ""
        nm_id = row.nm_id or 0
        doc_type = row.doc_type_name or ""
        oper_name = row.supplier_oper_name or ""

        if brand_name:
            all_brands.add(brand_name)

        # Filters
        if brand and brand_name != brand:
            continue
        if article and article.lower() not in sa_name.lower():
            continue

        # Determine month
        if row.rr_dt:
            month_key = row.rr_dt.strftime("%Y-%m")
        elif row.date_from:
            month_key = row.date_from.strftime("%Y-%m")
        else:
            continue

        if month_key not in monthly_data:
            monthly_data[month_key] = _empty_totals()
            months_set.add(month_key)

        # Track nm_id for article
        if sa_name and nm_id:
            article_nm[sa_name] = nm_id

        # Track sale quantities for cost calculation
        if doc_type == "Продажа" and oper_name in ("Продажа", ""):
            qty = int(row.quantity or 0)
            monthly_article_qty[month_key][sa_name] += qty
            total_article_qty[sa_name] += qty

        # Accumulate
        _accumulate_row(monthly_data[month_key], row, doc_type, oper_name)
        _accumulate_row(total_data, row, doc_type, oper_name)

    # ── 5. Calculate ads per month (from WbFunnelDaily, aggregated) ──
    # ads_map is nm_id -> total_ads for the whole period
    # We need monthly ads — load per month
    monthly_ads: dict[str, float] = {}
    total_ads = 0.0
    for mk in months_set:
        y, m = mk.split("-")
        m_from = date(int(y), int(m), 1)
        if int(m) == 12:
            m_to = date(int(y) + 1, 1, 1)
        else:
            m_to = date(int(y), int(m) + 1, 1)
        from datetime import timedelta
        m_to = m_to - timedelta(days=1)
        m_ads = await load_ads(db, project_id, m_from, m_to)
        monthly_ads[mk] = sum(m_ads.values())
    total_ads = sum(monthly_ads.values())

    # ── 6. Calculate cost per month ──
    monthly_cost: dict[str, float] = {}
    total_cost_val = 0.0
    for mk in months_set:
        month_cost = 0.0
        for sa_name, qty in monthly_article_qty.get(mk, {}).items():
            cost_price = cost_map.get(sa_name, 0)
            if cost_price == 0:
                nm = article_nm.get(sa_name, 0)
                if nm in cost_overrides:
                    cost_price = cost_overrides[nm]
            month_cost += cost_price * qty
        monthly_cost[mk] = month_cost
    total_cost_val = sum(monthly_cost.values())

    # ── 7. Build P&L rows ──
    months_sorted = sorted(months_set, reverse=True)

    def _pct(val, base):
        """Calculate % of base."""
        if not base or base == 0:
            return 0
        return round(val / base * 100, 2)

    def _build_row(key, label, level, bold, values_monthly, values_total,
                   base_monthly=None, base_total=None, expandable=False):
        """Build a single P&L row with monthly + total."""
        total_pct = _pct(values_total, base_total) if base_total else None

        monthly = {}
        monthly_pct = {}
        for mk in months_sorted:
            monthly[mk] = round(float(values_monthly.get(mk, 0)), 2)
            if base_monthly and mk in base_monthly:
                monthly_pct[mk] = _pct(values_monthly.get(mk, 0), base_monthly.get(mk, 0))
            else:
                monthly_pct[mk] = None

        return {
            "key": key,
            "label": label,
            "level": level,
            "bold": bold,
            "expandable": expandable,
            "total": round(float(values_total), 2),
            "total_pct": total_pct,
            "monthly": monthly,
            "monthly_pct": monthly_pct,
        }

    # Extract metrics per month
    def _metric_monthly(field):
        return {mk: float(monthly_data[mk].get(field, 0)) for mk in months_sorted}

    def _metric_total(field):
        return float(total_data.get(field, 0))

    # ── Реализация ──
    real_m = _metric_monthly("realization")
    real_t = _metric_total("realization")

    # ── Скидка за счет МП ──
    spp_m = {mk: float(monthly_data[mk]["realization"]) - float(monthly_data[mk]["sales_amount"])
             for mk in months_sorted}
    spp_t = float(total_data["realization"]) - float(total_data["sales_amount"])

    # ── Фактические продажи ──
    sales_m = _metric_monthly("sales_amount")
    sales_t = _metric_total("sales_amount")

    # ── Логистика ──
    log_m = _metric_monthly("logistics")
    log_t = _metric_total("logistics")

    # ── Комиссия ──
    comm_m = _metric_monthly("commission")
    comm_t = _metric_total("commission")

    # ── Штрафы ──
    pen_m = _metric_monthly("penalties")
    pen_t = _metric_total("penalties")

    # ── Хранение ──
    stor_m = _metric_monthly("storage")
    stor_t = _metric_total("storage")

    # ── Прочие удержания (without ad deductions to avoid double-counting) ──
    ded_m = {mk: float(monthly_data[mk]["deductions"]) - float(monthly_data[mk]["ad_deduction"])
             for mk in months_sorted}
    ded_t = float(total_data["deductions"]) - float(total_data["ad_deduction"])

    # ── Платная приёмка ──
    acc_m = _metric_monthly("acceptance")
    acc_t = _metric_total("acceptance")

    # ── Себестоимость ──
    cost_m = {mk: monthly_cost.get(mk, 0) for mk in months_sorted}
    cost_t = total_cost_val

    # ── Реклама (из finance deductions: Продвижение/Медиа) ──
    adv_m = _metric_monthly("ad_deduction")
    adv_t = _metric_total("ad_deduction")

    # ── Прямые расходы (сумма) ──
    direct_m = {mk: cost_m[mk] + log_m[mk] + comm_m[mk] + pen_m[mk] +
                    stor_m[mk] + adv_m[mk] + ded_m[mk] + acc_m[mk]
                for mk in months_sorted}
    direct_t = cost_t + log_t + comm_t + pen_t + stor_t + adv_t + ded_t + acc_t

    # ── Компенсация ──
    comp_bonus_m = _metric_monthly("additional_payment")
    comp_bonus_t = _metric_total("additional_payment")
    comp_vol_m = _metric_monthly("compensation_ppvz")
    comp_vol_t = _metric_total("compensation_ppvz")

    comp_total_m = {mk: comp_bonus_m[mk] + comp_vol_m[mk] for mk in months_sorted}
    comp_total_t = comp_bonus_t + comp_vol_t

    # ── Валовая маржа ──
    margin_m = {mk: sales_m[mk] - direct_m[mk] + comp_total_m[mk] for mk in months_sorted}
    margin_t = sales_t - direct_t + comp_total_t

    # ── Операционные расходы = 0 (рассчитываются в ДДС-отчёте, не в ОПИУ) ──
    ops_m = {mk: 0 for mk in months_sorted}
    ops_t = 0

    # ── EBITDA ──
    ebitda_m = {mk: margin_m[mk] - ops_m[mk] for mk in months_sorted}
    ebitda_t = margin_t - ops_t

    # ── Налоги ──
    usn_rate = tax_info.get("usn_rate", 0) / 100
    nds_rate = tax_info.get("nds_rate", 0) / 100

    def _calc_tax(income):
        nds = income * nds_rate / (1 + nds_rate) if nds_rate > 0 else 0
        base = income - nds
        usn = max(base * usn_rate, 0)
        return nds + usn

    tax_m = {mk: _calc_tax(sales_m[mk]) for mk in months_sorted}
    tax_t = _calc_tax(sales_t)

    # ── Чистая прибыль ──
    net_m = {mk: ebitda_m[mk] - tax_m[mk] for mk in months_sorted}
    net_t = ebitda_t - tax_t

    # ── Build P&L rows array ──
    rows = [
        _build_row("realization", "Реализация", 0, True, real_m, real_t),
        _build_row("spp_discount", "Скидка за счет МП", 1, False, spp_m, spp_t, real_m, real_t),
        _build_row("sales_amount", "Фактические продажи", 0, True, sales_m, sales_t, real_m, real_t),
        _build_row("direct_costs", "Прямые расходы", 0, True, direct_m, direct_t, real_m, real_t, expandable=True),
        _build_row("cost_of_sales", "Себестоимость продаж", 1, False, cost_m, cost_t, real_m, real_t, expandable=True),
        _build_row("cost_price", "Себестоимость", 2, False, cost_m, cost_t, real_m, real_t),
        _build_row("logistics", "Логистика", 1, False, log_m, log_t, real_m, real_t),
        _build_row("commission", "Комиссия", 1, False, comm_m, comm_t, real_m, real_t),
        _build_row("penalties", "Штрафы", 1, False, pen_m, pen_t, real_m, real_t),
        _build_row("storage", "Хранение", 1, False, stor_m, stor_t, real_m, real_t),
        _build_row("advertising", "Внутренняя реклама", 1, False, adv_m, adv_t, real_m, real_t),
        _build_row("deductions", "Прочие удержания", 1, False, ded_m, ded_t, real_m, real_t),
        _build_row("acceptance", "Платная приёмка", 1, False, acc_m, acc_t, real_m, real_t),
        _build_row("compensation", "Компенсация", 0, False, comp_total_m, comp_total_t, real_m, real_t, expandable=True),
        _build_row("comp_bonus", "Баллы за скидки", 1, False, comp_bonus_m, comp_bonus_t, real_m, real_t),
        _build_row("comp_voluntary", "Добровольная компенсация при возврате", 1, False, comp_vol_m, comp_vol_t, real_m, real_t),
        _build_row("gross_margin", "Валовая маржа", 0, True, margin_m, margin_t, real_m, real_t),
        _build_row("operating_expenses", "Операционные расходы", 0, False, ops_m, ops_t, real_m, real_t),
        _build_row("ebitda", "Операционная прибыль (EBITDA)", 0, True, ebitda_m, ebitda_t, real_m, real_t),
        _build_row("taxes", "Налоги (кроме зарплатных)", 0, True, tax_m, tax_t, real_m, real_t, expandable=True),
        _build_row("net_profit", "Чистая прибыль", 0, True, net_m, net_t, real_m, real_t),
    ]

    # Year label
    year_label = str(date_from.year)

    return {
        "period": {"date_from": str(date_from), "date_to": str(date_to)},
        "year_label": year_label,
        "months": months_sorted,
        "rows": rows,
        "brands": sorted(all_brands),
        "tax_info": tax_info,
    }


# ─── Aggregation helpers ────────────────────────────────────────────────────

def _empty_totals() -> dict:
    return {
        "realization": ZERO,
        "sales_amount": ZERO,
        "logistics": ZERO,
        "commission": ZERO,
        "penalties": ZERO,
        "storage": ZERO,
        "acceptance": ZERO,
        "deductions": ZERO,
        "ad_deduction": ZERO,
        "additional_payment": ZERO,
        "compensation_ppvz": ZERO,
        # Internal fields for commission calculation
        "_ppvz_for_pay_sale": ZERO,
        "_ppvz_for_pay_ret": ZERO,
        "_comp_ppvz": ZERO,
    }


def _accumulate_row(target: dict, row: WbFinanceRow, doc_type: str, oper_name: str):
    """Accumulate row into P&L totals."""
    is_sale = doc_type == "Продажа"
    is_return = doc_type == "Возврат"
    sign = D("1") if is_sale else D("-1") if is_return else D("0")

    # Реализация (retail_price_withdisc_rub)
    if is_sale or is_return:
        target["realization"] += sign * (row.retail_price_withdisc_rub or ZERO)

    # Фактические продажи (retail_amount)
    if is_sale or is_return:
        target["sales_amount"] += sign * (row.retail_amount or ZERO)

    # Track ppvz_for_pay for commission calculation
    if is_sale:
        target["_ppvz_for_pay_sale"] += row.ppvz_for_pay or ZERO
        if oper_name == "Добровольная компенсация при возврате":
            target["_comp_ppvz"] += row.ppvz_for_pay or ZERO
            target["compensation_ppvz"] += row.ppvz_for_pay or ZERO
    elif is_return:
        target["_ppvz_for_pay_ret"] += row.ppvz_for_pay or ZERO

    # Logistics, penalties, storage, acceptance, deductions — from "other" (non-sale/return)
    if not is_sale and not is_return:
        target["logistics"] += row.delivery_rub or ZERO
        target["penalties"] += row.penalty or ZERO
        target["storage"] += row.storage_fee or ZERO
        target["acceptance"] += row.acceptance or ZERO
        target["deductions"] += row.deduction or ZERO

        # Track ad-related deductions separately (Продвижение/Медиа)
        bonus = row.bonus_type_name or ""
        deduction_val = row.deduction or ZERO
        if deduction_val and bonus:
            if "Продвижение" in bonus or "Медиа" in bonus:
                target["ad_deduction"] += deduction_val

    # Additional payment (bonus points)
    if is_sale or is_return:
        target["additional_payment"] += sign * (row.additional_payment or ZERO)

    # Recompute commission: sales_amount - (ppvz_net - compensation)
    ppvz_net = target["_ppvz_for_pay_sale"] - target["_ppvz_for_pay_ret"]
    comp = target["_comp_ppvz"]
    target["commission"] = target["sales_amount"] - (ppvz_net - comp)

