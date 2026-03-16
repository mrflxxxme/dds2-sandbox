"""
Service: opiu — ОПИУ (P&L) report from WB finance data.

Monthly breakdown with hierarchical P&L rows.
Optimized: SQL aggregation instead of loading all rows into memory.
"""

import logging
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, case, or_, literal_column, cast, String
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
    Uses SQL aggregation — no full row loading into memory.
    """
    # ── 1. SQL aggregation per month ──
    # month_key expression: extract year-month from rr_dt (or date_from as fallback)
    month_expr = func.to_char(
        func.coalesce(WbFinanceRow.rr_dt, WbFinanceRow.date_from),
        literal_column("'YYYY-MM'"),
    )

    is_sale = WbFinanceRow.doc_type_name == "Продажа"
    is_return = WbFinanceRow.doc_type_name == "Возврат"

    # Sign: +1 for sales, -1 for returns, 0 for other
    sign = case(
        (is_sale, 1),
        (is_return, -1),
        else_=0,
    )

    # Filters
    filters = [
        WbFinanceRow.project_id == project_id,
        or_(
            WbFinanceRow.rr_dt.between(date_from, date_to),
            (WbFinanceRow.rr_dt.is_(None)) & (WbFinanceRow.date_from >= date_from) & (WbFinanceRow.date_to <= date_to),
        ),
    ]
    if brand:
        filters.append(WbFinanceRow.brand_name == brand)
    if article:
        filters.append(WbFinanceRow.sa_name.ilike(f"%{article}%"))

    # Main aggregation query
    q = select(
        month_expr.label("month_key"),
        # Реализация (retail_price_withdisc_rub * sign, only sales/returns)
        func.coalesce(func.sum(
            case((is_sale | is_return, sign * func.coalesce(WbFinanceRow.retail_price_withdisc_rub, 0)), else_=0)
        ), 0).label("realization"),
        # Фактические продажи (retail_amount * sign, only sales/returns)
        func.coalesce(func.sum(
            case((is_sale | is_return, sign * func.coalesce(WbFinanceRow.retail_amount, 0)), else_=0)
        ), 0).label("sales_amount"),
        # ppvz_for_pay for sales
        func.coalesce(func.sum(
            case((is_sale, func.coalesce(WbFinanceRow.ppvz_for_pay, 0)), else_=0)
        ), 0).label("ppvz_for_pay_sale"),
        # ppvz_for_pay for returns
        func.coalesce(func.sum(
            case((is_return, func.coalesce(WbFinanceRow.ppvz_for_pay, 0)), else_=0)
        ), 0).label("ppvz_for_pay_ret"),
        # ppvz_for_pay for voluntary compensation (sale + specific oper_name)
        func.coalesce(func.sum(
            case(
                (is_sale & (WbFinanceRow.supplier_oper_name == "Добровольная компенсация при возврате"),
                 func.coalesce(WbFinanceRow.ppvz_for_pay, 0)),
                else_=0,
            )
        ), 0).label("comp_ppvz"),
        # Логистика (non sale/return only)
        func.coalesce(func.sum(
            case((~is_sale & ~is_return, func.coalesce(WbFinanceRow.delivery_rub, 0)), else_=0)
        ), 0).label("logistics"),
        # Штрафы
        func.coalesce(func.sum(
            case((~is_sale & ~is_return, func.coalesce(WbFinanceRow.penalty, 0)), else_=0)
        ), 0).label("penalties"),
        # Хранение
        func.coalesce(func.sum(
            case((~is_sale & ~is_return, func.coalesce(WbFinanceRow.storage_fee, 0)), else_=0)
        ), 0).label("storage"),
        # Приёмка
        func.coalesce(func.sum(
            case((~is_sale & ~is_return, func.coalesce(WbFinanceRow.acceptance, 0)), else_=0)
        ), 0).label("acceptance"),
        # Удержания (all)
        func.coalesce(func.sum(
            case((~is_sale & ~is_return, func.coalesce(WbFinanceRow.deduction, 0)), else_=0)
        ), 0).label("deductions"),
        # Ad deductions (Продвижение/Медиа в bonus_type_name)
        func.coalesce(func.sum(
            case(
                (~is_sale & ~is_return & (
                    WbFinanceRow.bonus_type_name.ilike("%Продвижение%") |
                    WbFinanceRow.bonus_type_name.ilike("%Медиа%")
                ), func.coalesce(WbFinanceRow.deduction, 0)),
                else_=0,
            )
        ), 0).label("ad_deduction"),
        # Loan deductions
        func.coalesce(func.sum(
            case(
                (~is_sale & ~is_return & (
                    WbFinanceRow.bonus_type_name.ilike("%кредит%") |
                    WbFinanceRow.bonus_type_name.ilike("%заём%")
                ), func.coalesce(WbFinanceRow.deduction, 0)),
                else_=0,
            )
        ), 0).label("loan_deduction"),
        # Additional payment (bonus points, sign-weighted)
        func.coalesce(func.sum(
            case((is_sale | is_return, sign * func.coalesce(WbFinanceRow.additional_payment, 0)), else_=0)
        ), 0).label("additional_payment"),
        # Sale quantities for cost calculation
        func.coalesce(func.sum(
            case(
                (is_sale & (WbFinanceRow.supplier_oper_name.in_(["Продажа", ""])),
                 func.coalesce(WbFinanceRow.quantity, 0)),
                else_=0,
            )
        ), 0).label("sale_qty"),
    ).where(*filters).group_by(month_expr)

    result = await db.execute(q)
    monthly_rows = result.all()

    # ── 2. Also get per-article sale quantities for cost calculation ──
    article_cost_q = select(
        WbFinanceRow.sa_name,
        WbFinanceRow.nm_id,
        month_expr.label("month_key"),
        func.sum(func.coalesce(WbFinanceRow.quantity, 0)).label("qty"),
    ).where(
        *filters,
        WbFinanceRow.doc_type_name == "Продажа",
        WbFinanceRow.supplier_oper_name.in_(["Продажа", ""]),
    ).group_by(WbFinanceRow.sa_name, WbFinanceRow.nm_id, month_expr)

    article_result = await db.execute(article_cost_q)
    article_rows = article_result.all()

    # ── 3. Get all brands for filter dropdown ──
    brands_q = select(func.distinct(WbFinanceRow.brand_name)).where(
        WbFinanceRow.project_id == project_id,
        WbFinanceRow.brand_name.isnot(None),
        WbFinanceRow.brand_name != "",
        or_(
            WbFinanceRow.rr_dt.between(date_from, date_to),
            (WbFinanceRow.rr_dt.is_(None)) & (WbFinanceRow.date_from >= date_from) & (WbFinanceRow.date_to <= date_to),
        ),
    )
    brands_result = await db.execute(brands_q)
    all_brands = sorted([r[0] for r in brands_result if r[0]])

    # ── 4. Load enrichment data ──
    cost_map = await load_avg_costs(db, project_id)
    cost_overrides = await load_cost_overrides(db, project_id)
    tax_info = await load_tax_settings(db, project_id, date_from, date_to)

    # ── 5. Build monthly data from SQL results ──
    months_set: set[str] = set()
    current = date_from.replace(day=1)
    while current <= date_to:
        months_set.add(current.strftime("%Y-%m"))
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    monthly_data: dict[str, dict] = {}
    for row in monthly_rows:
        mk = row.month_key
        months_set.add(mk)
        monthly_data[mk] = {
            "realization": float(row.realization or 0),
            "sales_amount": float(row.sales_amount or 0),
            "ppvz_for_pay_sale": float(row.ppvz_for_pay_sale or 0),
            "ppvz_for_pay_ret": float(row.ppvz_for_pay_ret or 0),
            "comp_ppvz": float(row.comp_ppvz or 0),
            "logistics": float(row.logistics or 0),
            "penalties": float(row.penalties or 0),
            "storage": float(row.storage or 0),
            "acceptance": float(row.acceptance or 0),
            "deductions": float(row.deductions or 0),
            "ad_deduction": float(row.ad_deduction or 0),
            "loan_deduction": float(row.loan_deduction or 0),
            "additional_payment": float(row.additional_payment or 0),
        }

    # Fill missing months with zeros
    for mk in months_set:
        if mk not in monthly_data:
            monthly_data[mk] = {k: 0.0 for k in [
                "realization", "sales_amount", "ppvz_for_pay_sale", "ppvz_for_pay_ret",
                "comp_ppvz", "logistics", "penalties", "storage", "acceptance",
                "deductions", "ad_deduction", "loan_deduction", "additional_payment",
            ]}

    # ── 6. Calculate costs per month from article quantities ──
    article_nm: dict[str, int] = {}
    monthly_article_qty: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for row in article_rows:
        sa_name = row.sa_name or ""
        nm_id = row.nm_id or 0
        mk = row.month_key
        qty = int(row.qty or 0)
        if sa_name and nm_id:
            article_nm[sa_name] = nm_id
        monthly_article_qty[mk][sa_name] += qty

    monthly_cost: dict[str, float] = {}
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

    # ── 7. Calculate ads per month ──
    monthly_ads: dict[str, float] = {}
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

    # ── 8. Compute totals ──
    def _sum_monthly(field):
        return sum(monthly_data[mk].get(field, 0) for mk in months_set)

    total_data = {k: _sum_monthly(k) for k in monthly_data.get(next(iter(months_set), ""), {}).keys()} if months_set else {}

    total_cost_val = sum(monthly_cost.values())
    total_ads = sum(monthly_ads.values())

    # ── 9. Build P&L rows ──
    months_sorted = sorted(months_set, reverse=True)

    def _pct(val, base):
        if not base or base == 0:
            return 0
        return round(val / base * 100, 2)

    def _build_row(key, label, level, bold, values_monthly, values_total,
                   base_monthly=None, base_total=None, expandable=False):
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
            "key": key, "label": label, "level": level, "bold": bold,
            "expandable": expandable,
            "total": round(float(values_total), 2), "total_pct": total_pct,
            "monthly": monthly, "monthly_pct": monthly_pct,
        }

    def _m(field):
        return {mk: monthly_data[mk].get(field, 0) for mk in months_sorted}

    def _t(field):
        return total_data.get(field, 0)

    # Metrics
    real_m, real_t = _m("realization"), _t("realization")
    sales_m, sales_t = _m("sales_amount"), _t("sales_amount")
    spp_m = {mk: monthly_data[mk]["realization"] - monthly_data[mk]["sales_amount"] for mk in months_sorted}
    spp_t = _t("realization") - _t("sales_amount")
    log_m, log_t = _m("logistics"), _t("logistics")

    # Commission = sales_amount - (ppvz_net - comp)
    comm_m = {}
    for mk in months_sorted:
        d = monthly_data[mk]
        ppvz_net = d["ppvz_for_pay_sale"] - d["ppvz_for_pay_ret"]
        comp = d["comp_ppvz"]
        comm_m[mk] = d["sales_amount"] - (ppvz_net - comp)
    comm_t = _t("sales_amount") - (_t("ppvz_for_pay_sale") - _t("ppvz_for_pay_ret") - _t("comp_ppvz"))

    pen_m, pen_t = _m("penalties"), _t("penalties")
    stor_m, stor_t = _m("storage"), _t("storage")
    acc_m, acc_t = _m("acceptance"), _t("acceptance")

    # Deductions minus ads and loans
    ded_m = {mk: monthly_data[mk]["deductions"] - monthly_data[mk]["ad_deduction"] - monthly_data[mk]["loan_deduction"]
             for mk in months_sorted}
    ded_t = _t("deductions") - _t("ad_deduction") - _t("loan_deduction")

    adv_m = _m("ad_deduction")
    adv_t = _t("ad_deduction")

    cost_m = {mk: monthly_cost.get(mk, 0) for mk in months_sorted}
    cost_t = total_cost_val

    # Direct costs
    direct_m = {mk: cost_m[mk] + log_m[mk] + comm_m[mk] + pen_m[mk] +
                    stor_m[mk] + adv_m[mk] + ded_m[mk] + acc_m[mk]
                for mk in months_sorted}
    direct_t = cost_t + log_t + comm_t + pen_t + stor_t + adv_t + ded_t + acc_t

    # Compensation
    comp_bonus_m, comp_bonus_t = _m("additional_payment"), _t("additional_payment")
    comp_vol_m = {mk: monthly_data[mk]["comp_ppvz"] for mk in months_sorted}
    comp_vol_t = _t("comp_ppvz")
    comp_total_m = {mk: comp_bonus_m[mk] + comp_vol_m[mk] for mk in months_sorted}
    comp_total_t = comp_bonus_t + comp_vol_t

    # Gross margin
    margin_m = {mk: sales_m[mk] - direct_m[mk] + comp_total_m[mk] for mk in months_sorted}
    margin_t = sales_t - direct_t + comp_total_t

    # Operating expenses (from DDS, not OPIU)
    ops_m = {mk: 0 for mk in months_sorted}
    ops_t = 0

    # EBITDA
    ebitda_m = {mk: margin_m[mk] - ops_m[mk] for mk in months_sorted}
    ebitda_t = margin_t - ops_t

    # Taxes
    usn_rate = tax_info.get("usn_rate", 0) / 100
    nds_rate = tax_info.get("nds_rate", 0) / 100

    def _calc_tax(income):
        nds = income * nds_rate / (1 + nds_rate) if nds_rate > 0 else 0
        base = income - nds
        usn = max(base * usn_rate, 0)
        return nds + usn

    tax_m = {mk: _calc_tax(sales_m[mk]) for mk in months_sorted}
    tax_t = _calc_tax(sales_t)

    # Net profit
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

    return {
        "period": {"date_from": str(date_from), "date_to": str(date_to)},
        "year_label": str(date_from.year),
        "months": months_sorted,
        "rows": rows,
        "brands": all_brands,
        "tax_info": tax_info,
    }
