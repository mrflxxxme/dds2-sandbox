"""
Service: opiu — ОПИУ (P&L) report from WB finance data.

Monthly breakdown with hierarchical P&L rows.
Reuses loaders from bdr_loaders.py.

Optimized: SQL-level aggregation instead of loading all rows into Python.
"""

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.services.bdr_loaders import (
    load_ads_monthly,
    load_avg_costs,
    load_cost_overrides,
    load_tax_settings,
)

logger = logging.getLogger("dds.opiu")

D = Decimal
ZERO = D("0")


# ─── SQL aggregation query ────────────────────────────────────────────────────

_DATE_FILTER = """(
    (rr_dt BETWEEN :date_from AND :date_to)
    OR (rr_dt IS NULL AND date_from >= :date_from AND date_to <= :date_to)
  )"""

_SELECT_COLS = """
    COALESCE(to_char(rr_dt, 'YYYY-MM'), to_char(date_from, 'YYYY-MM')) AS month_key,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_price_withdisc_rub WHEN doc_type_name = 'Возврат' THEN -retail_price_withdisc_rub ELSE 0 END), 0) AS realization,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_amount WHEN doc_type_name = 'Возврат' THEN -retail_amount ELSE 0 END), 0) AS sales_amount,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN ppvz_for_pay ELSE 0 END), 0) AS ppvz_for_pay_sale,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN ppvz_for_pay ELSE 0 END), 0) AS ppvz_for_pay_ret,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' AND supplier_oper_name = 'Добровольная компенсация при возврате' THEN ppvz_for_pay ELSE 0 END), 0) AS comp_ppvz,
    COALESCE(SUM(delivery_rub), 0) AS logistics,
    COALESCE(SUM(penalty), 0) AS penalties,
    COALESCE(SUM(storage_fee), 0) AS storage,
    COALESCE(SUM(acceptance), 0) AS acceptance_total,
    COALESCE(SUM(deduction), 0) AS deductions,
    COALESCE(SUM(CASE WHEN deduction != 0 AND (bonus_type_name LIKE '%%Продвижение%%' OR bonus_type_name LIKE '%%Медиа%%') THEN deduction ELSE 0 END), 0) AS ad_deduction,
    COALESCE(SUM(CASE WHEN deduction != 0 AND (LOWER(bonus_type_name) LIKE '%%кредит%%' OR LOWER(bonus_type_name) LIKE '%%заём%%') THEN deduction ELSE 0 END), 0) AS loan_deduction,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN additional_payment WHEN doc_type_name = 'Возврат' THEN -additional_payment ELSE 0 END), 0) AS additional_payment
"""


def _build_aggregate_sql(brand: str | None, article: str | None) -> str:
    where = f"project_id = :project_id AND {_DATE_FILTER}"
    if brand:
        where += " AND brand_name = :brand"
    if article:
        where += " AND LOWER(sa_name) LIKE :article_like"
    return f"SELECT {_SELECT_COLS} FROM wb_finance_rows WHERE {where} GROUP BY month_key ORDER BY month_key"  # noqa: S608


def _build_brands_sql() -> str:
    return f"""SELECT DISTINCT brand_name FROM wb_finance_rows
        WHERE project_id = :project_id AND {_DATE_FILTER}
        AND brand_name IS NOT NULL AND brand_name != ''
        ORDER BY brand_name"""  # noqa: S608


def _build_cost_qty_sql(brand: str | None, article: str | None) -> str:
    """Net qty per article/month: sale_qty - ret_qty (same as BDR)."""
    where = f"""project_id = :project_id
        AND doc_type_name IN ('Продажа', 'Возврат')
        AND supplier_oper_name IN ('Продажа', 'Возврат')
        AND {_DATE_FILTER}"""
    if brand:
        where += " AND brand_name = :brand"
    if article:
        where += " AND LOWER(sa_name) LIKE :article_like"
    return f"""SELECT
        COALESCE(to_char(rr_dt, 'YYYY-MM'), to_char(date_from, 'YYYY-MM')) AS month_key,
        sa_name, nm_id,
        COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN quantity ELSE 0 END), 0)
        - COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN quantity ELSE 0 END), 0) AS total_qty
        FROM wb_finance_rows WHERE {where}
        GROUP BY month_key, sa_name, nm_id"""


@cached(prefix="reports:opiu", ttl=3600)
async def get_opiu(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
    brand: str | None = None,
    article: str | None = None,
) -> dict:
    """
    Build ОПИУ (P&L) from locally cached WB finance data.
    Returns monthly breakdown + total with hierarchical P&L rows.
    Uses SQL-level aggregation for performance (handles 1M+ rows).
    """
    params: dict = {
        "project_id": project_id,
        "date_from": date_from,
        "date_to": date_to,
    }
    if brand:
        params["brand"] = brand
    if article:
        params["article_like"] = f"%{article.lower()}%"

    # ── 1. SQL aggregation — returns ~3 rows instead of 634K ──
    result = await db.execute(text(_build_aggregate_sql(brand, article)), params)
    agg_rows = result.mappings().all()

    # ── 2. Load enrichment data (small queries) ──
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

    # ── 4. Parse aggregated results into monthly_data ──
    monthly_data: dict[str, dict] = {}
    total_data = _empty_totals()

    for row in agg_rows:
        mk = row["month_key"]
        if mk is None:
            continue
        months_set.add(mk)

        d = _empty_totals()
        d["realization"] = D(str(row["realization"]))
        d["sales_amount"] = D(str(row["sales_amount"]))
        d["logistics"] = D(str(row["logistics"]))
        d["penalties"] = D(str(row["penalties"]))
        d["storage"] = D(str(row["storage"]))
        d["acceptance"] = D(str(row["acceptance_total"]))
        d["deductions"] = D(str(row["deductions"]))
        d["ad_deduction"] = D(str(row["ad_deduction"]))
        d["loan_deduction"] = D(str(row["loan_deduction"]))
        d["additional_payment"] = D(str(row["additional_payment"]))
        d["compensation_ppvz"] = D(str(row["comp_ppvz"]))
        d["_ppvz_for_pay_sale"] = D(str(row["ppvz_for_pay_sale"]))
        d["_ppvz_for_pay_ret"] = D(str(row["ppvz_for_pay_ret"]))
        d["_comp_ppvz"] = D(str(row["comp_ppvz"]))

        # Commission = sales_amount - (ppvz_net - compensation)
        ppvz_net = d["_ppvz_for_pay_sale"] - d["_ppvz_for_pay_ret"]
        d["commission"] = d["sales_amount"] - (ppvz_net - d["_comp_ppvz"])

        monthly_data[mk] = d

        # Accumulate totals
        for key in total_data:
            total_data[key] += d[key]

    # Recalculate total commission
    ppvz_net = total_data["_ppvz_for_pay_sale"] - total_data["_ppvz_for_pay_ret"]
    total_data["commission"] = total_data["sales_amount"] - (ppvz_net - total_data["_comp_ppvz"])

    # Fill missing months
    for mk in months_set:
        if mk not in monthly_data:
            monthly_data[mk] = _empty_totals()

    # ── 5. Load brands ──
    brands_result = await db.execute(
        text(_build_brands_sql()),
        {
            "project_id": project_id,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
    all_brands = sorted(r[0] for r in brands_result)

    # ── 6. Ads per month (from WbFunnelDaily, filtered by brand nm_ids) ──
    ads_by_month = await load_ads_monthly(db, project_id, date_from, date_to)

    # When brand is set, only count ads for nm_ids belonging to this brand
    brand_nm_ids: set[int] | None = None
    if brand:
        nm_result = await db.execute(
            text(
                f"SELECT DISTINCT nm_id FROM wb_finance_rows"  # noqa: S608
                f" WHERE project_id = :project_id AND {_DATE_FILTER}"
                f" AND brand_name = :brand AND nm_id IS NOT NULL AND nm_id != 0"
            ),
            params,
        )
        brand_nm_ids = {r[0] for r in nm_result}

    monthly_ads: dict[str, float] = {}
    for mk in months_set:
        month_ads = ads_by_month.get(mk, {})
        if brand_nm_ids is not None:
            monthly_ads[mk] = sum(v for nm, v in month_ads.items() if nm in brand_nm_ids)
        else:
            monthly_ads[mk] = sum(month_ads.values())

    # ── 7. Cost per month (need per-article qty) ──
    cost_map = await load_avg_costs(db, project_id)
    cost_overrides = await load_cost_overrides(db, project_id)

    cost_result = await db.execute(text(_build_cost_qty_sql(brand, article)), params)
    monthly_cost: dict[str, float] = {mk: 0.0 for mk in months_set}
    for row in cost_result:
        mk = row.month_key
        sa_name = row.sa_name or ""
        nm_id = row.nm_id or 0
        qty = int(row.total_qty or 0)
        cost_price = cost_map.get(sa_name, 0)
        if cost_price == 0 and nm_id in cost_overrides:
            cost_price = cost_overrides[nm_id]
        if mk in monthly_cost:
            monthly_cost[mk] += cost_price * qty
        else:
            monthly_cost[mk] = cost_price * qty
    total_cost_val = sum(monthly_cost.values())

    # ── 8. Build P&L rows ──
    months_sorted = sorted(months_set, reverse=True)

    def _pct(val, base):
        if not base or base == 0:
            return 0
        return round(val / base * 100, 2)

    def _build_row(
        key, label, level, bold, values_monthly, values_total, base_monthly=None, base_total=None, expandable=False
    ):
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

    def _metric_monthly(field):
        return {mk: float(monthly_data[mk].get(field, 0)) for mk in months_sorted}

    def _metric_total(field):
        return float(total_data.get(field, 0))

    # ── Metrics ──
    real_m = _metric_monthly("realization")
    real_t = _metric_total("realization")

    spp_m = {
        mk: float(monthly_data[mk]["realization"]) - float(monthly_data[mk]["sales_amount"]) for mk in months_sorted
    }
    spp_t = float(total_data["realization"]) - float(total_data["sales_amount"])

    sales_m = _metric_monthly("sales_amount")
    sales_t = _metric_total("sales_amount")

    log_m = _metric_monthly("logistics")
    log_t = _metric_total("logistics")

    comm_m = _metric_monthly("commission")
    comm_t = _metric_total("commission")

    pen_m = _metric_monthly("penalties")
    pen_t = _metric_total("penalties")

    stor_m = _metric_monthly("storage")
    stor_t = _metric_total("storage")

    ded_m = {
        mk: float(monthly_data[mk]["deductions"])
        - float(monthly_data[mk]["ad_deduction"])
        - float(monthly_data[mk].get("loan_deduction", 0))
        for mk in months_sorted
    }
    ded_t = (
        float(total_data["deductions"]) - float(total_data["ad_deduction"]) - float(total_data.get("loan_deduction", 0))
    )

    acc_m = _metric_monthly("acceptance")
    acc_t = _metric_total("acceptance")

    cost_m = {mk: monthly_cost.get(mk, 0) for mk in months_sorted}
    cost_t = total_cost_val

    adv_m = {mk: monthly_ads.get(mk, 0) for mk in months_sorted}
    adv_t = sum(monthly_ads.values())

    direct_m = {
        mk: cost_m[mk] + log_m[mk] + comm_m[mk] + pen_m[mk] + stor_m[mk] + adv_m[mk] + ded_m[mk] + acc_m[mk]
        for mk in months_sorted
    }
    direct_t = cost_t + log_t + comm_t + pen_t + stor_t + adv_t + ded_t + acc_t

    comp_bonus_m = _metric_monthly("additional_payment")
    comp_bonus_t = _metric_total("additional_payment")
    comp_vol_m = _metric_monthly("compensation_ppvz")
    comp_vol_t = _metric_total("compensation_ppvz")

    comp_total_m = {mk: comp_bonus_m[mk] + comp_vol_m[mk] for mk in months_sorted}
    comp_total_t = comp_bonus_t + comp_vol_t

    margin_m = {mk: sales_m[mk] - direct_m[mk] + comp_total_m[mk] for mk in months_sorted}
    margin_t = sales_t - direct_t + comp_total_t

    ops_m = {mk: 0 for mk in months_sorted}
    ops_t = 0

    ebitda_m = {mk: margin_m[mk] - ops_m[mk] for mk in months_sorted}
    ebitda_t = margin_t - ops_t

    usn_rate = tax_info.get("usn_rate", 0) / 100
    nds_rate = tax_info.get("nds_rate", 0) / 100
    regime = tax_info.get("tax_regime", "usn_income")
    cost_as_expense = tax_info.get("cost_as_expense", False)

    def _calc_tax(income, expenses=0):
        nds = income * nds_rate / (1 + nds_rate) if nds_rate > 0 else 0
        base = income - nds
        if regime == "usn_income_expense_vat":
            base = base - expenses
        usn = max(base * usn_rate, 0)
        return nds + usn

    def _tax_expenses_for(log, stor, comm, pen, ded, adv, cost_val):
        """Tax-deductible expenses for income-expense USN regime."""
        exp = abs(log) + abs(stor) + abs(comm) + abs(pen) + abs(ded) + abs(adv)
        if cost_as_expense:
            exp += abs(cost_val)
        return exp

    tax_exp_m = {
        mk: _tax_expenses_for(log_m[mk], stor_m[mk], comm_m[mk], pen_m[mk], ded_m[mk], adv_m[mk], cost_m[mk])
        for mk in months_sorted
    }
    tax_exp_t = _tax_expenses_for(log_t, stor_t, comm_t, pen_t, ded_t, adv_t, cost_t)

    tax_m = {mk: _calc_tax(sales_m[mk], tax_exp_m[mk]) for mk in months_sorted}
    tax_t = _calc_tax(sales_t, tax_exp_t)

    net_m = {mk: ebitda_m[mk] - tax_m[mk] for mk in months_sorted}
    net_t = ebitda_t - tax_t

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
        _build_row(
            "compensation", "Компенсация", 0, False, comp_total_m, comp_total_t, real_m, real_t, expandable=True
        ),
        _build_row("comp_bonus", "Баллы за скидки", 1, False, comp_bonus_m, comp_bonus_t, real_m, real_t),
        _build_row(
            "comp_voluntary", "Добровольная компенсация при возврате", 1, False, comp_vol_m, comp_vol_t, real_m, real_t
        ),
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
        "loan_deduction": ZERO,
        "additional_payment": ZERO,
        "compensation_ppvz": ZERO,
        "_ppvz_for_pay_sale": ZERO,
        "_ppvz_for_pay_ret": ZERO,
        "_comp_ppvz": ZERO,
    }
