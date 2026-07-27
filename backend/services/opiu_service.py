"""
Service: opiu — ОПИУ (P&L) report from WB finance data.

Monthly breakdown with hierarchical P&L rows.
Reuses loaders from bdr_loaders.py.

Architecture:
- SQL queries & row builders → opiu_helpers.py
- Orchestration → this file

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
    load_tax_settings_by_month,
)
from backend.services.cost.valuation import (
    DEFAULT_METHOD,
    compute_project_valuation,
    slice_window,
)
from backend.services.opiu_helpers import (
    OPEX_TYPE_LABELS,
    build_aggregate_sql,
    build_article_nm_ids_sql,
    build_brand_nm_ids_sql,
    build_brands_sql,
    build_cost_qty_sql,
    build_opex_by_type_sql,
    build_row,
    empty_totals,
)
from backend.services.settings_service import get_valuation_method
from backend.services.wb_bdr_helpers import cost_with_fallback

logger = logging.getLogger("dds.opiu")

D = Decimal
ZERO = D("0")

# Keep old private names as aliases for backwards compatibility
_build_aggregate_sql = build_aggregate_sql
_build_brands_sql = build_brands_sql
_build_cost_qty_sql = build_cost_qty_sql
_empty_totals = empty_totals


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
        _esc = article.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params["article_like"] = f"%{_esc}%"

    # ── 1. SQL aggregation — returns ~3 rows instead of 634K ──
    result = await db.execute(text(build_aggregate_sql(brand, article)), params)
    agg_rows = result.mappings().all()

    # ── 2. Load enrichment data (small queries) ──
    # Налог: настройки на каждый месяц диапазона — месячные колонки считаются
    # ставками СВОЕГО месяца (раньше весь диапазон шёл по первому месяцу).
    tax_by_month = await load_tax_settings_by_month(db, project_id, date_from, date_to)
    tax_info = next(iter(tax_by_month.values()))  # настройки первого месяца (строки/ответ)

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
    total_data = empty_totals()

    for row in agg_rows:
        mk = row["month_key"]
        if mk is None:
            continue
        months_set.add(mk)

        d = empty_totals()
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
            monthly_data[mk] = empty_totals()

    # ── 5. Load brands ──
    brands_result = await db.execute(
        text(build_brands_sql()),
        {
            "project_id": project_id,
            "date_from": date_from,
            "date_to": date_to,
        },
    )
    all_brands = sorted(r[0] for r in brands_result)

    # ── 6. Ads per month (from WbFunnelDaily, filtered by brand nm_ids) ──
    ads_by_month = await load_ads_monthly(db, project_id, date_from, date_to)

    # When brand or article is set, only count ads for matching nm_ids
    filter_nm_ids: set[int] | None = None
    if article:
        nm_result = await db.execute(text(build_article_nm_ids_sql()), params)
        filter_nm_ids = {r[0] for r in nm_result}
    elif brand:
        nm_result = await db.execute(text(build_brand_nm_ids_sql()), params)
        filter_nm_ids = {r[0] for r in nm_result}

    monthly_ads: dict[str, float] = {}
    for mk in months_set:
        month_ads = ads_by_month.get(mk, {})
        if filter_nm_ids is not None:
            monthly_ads[mk] = sum(v for nm, v in month_ads.items() if nm in filter_nm_ids)
        else:
            monthly_ads[mk] = sum(month_ads.values())

    # ── 7. Cost per month (need per-article qty) ──
    # COGS source depends on the project's valuation method. lifetime_avg keeps
    # the legacy global-average path bit-for-bit (zero regression). fifo/moving_avg
    # source per-month COGS from the path-dependent valuation engine, falling back
    # to the manual override map for SKUs the engine doesn't cover.
    method = await get_valuation_method(db, project_id)
    cost_overrides = await load_cost_overrides(db, project_id)

    win: dict[str, dict] = {}
    # cost_map грузится при ЛЮБОМ методе — страховка для SKU, по которым движок
    # дал 0 (партии не сматчились по ключу / нетто-ноль месяца): не списываем
    # ноль при известной средней закупочной (зеркало фикса в wb_bdr_service).
    cost_map = await load_avg_costs(db, project_id)
    if method != DEFAULT_METHOD:
        valuation = await compute_project_valuation(db, project_id)
        win = slice_window(valuation, date_from, date_to, method)

    cost_result = await db.execute(text(build_cost_qty_sql(brand, article)), params)
    monthly_cost: dict[str, float] = {mk: 0.0 for mk in months_set}
    for row in cost_result:  # type: ignore[assignment]
        mk = row.month_key
        sa_name = row.sa_name or ""
        nm_id = row.nm_id or 0
        qty = int(row.total_qty or 0)
        sku = sa_name.lower() if sa_name else ""
        m_engine = win.get(sku, {}).get("monthly", {}).get(mk) if method != DEFAULT_METHOD else None
        if m_engine is not None:
            # Engine per-unit cost for this month × the window's net units (qty).
            # Both are net of returns, so a FULL month gives the month's exact COGS
            # (qty == engine month qty); a SUB-MONTH window is prorated instead of
            # being charged the whole month — the engine buckets COGS by month.
            m_qty = int(m_engine["qty"])
            eff = (float(m_engine["cogs"]) / m_qty) if m_qty else 0.0
            # Движок дал 0 (партии не сматчились / нетто-ноль месяца) → фолбэк
            # на среднюю закупочную/override, не списываем ноль.
            eff = cost_with_fallback(eff, sku, nm_id, cost_map, cost_overrides)
            cost_amount = eff * qty
        else:
            # lifetime_avg path, or override fallback for SKUs missing from valuation.
            # cost_map keyed by lowercased article_seller; sa_name from WB may differ in case
            cost_price = cost_map.get(sku, 0) if sku else 0
            if cost_price == 0 and nm_id in cost_overrides:
                cost_price = cost_overrides[nm_id]
            cost_amount = cost_price * qty
        if mk in monthly_cost:
            monthly_cost[mk] += cost_amount
        else:
            monthly_cost[mk] = cost_amount
    total_cost_val = sum(monthly_cost.values())

    # ── 7b. Operating expenses by counterparty type (bank transactions) ──
    # Транзакции не привязаны к бренду/артикулу → разбивку по типам контрагентов
    # считаем только для общего вида проекта; при фильтре по бренду/артикулу opex = 0.
    opex_by_type: dict[str, dict[str, float]] = {}
    if not brand and not article:
        opex_result = await db.execute(
            text(build_opex_by_type_sql()),
            {"project_id": project_id, "date_from": date_from, "date_to": date_to},
        )
        for opex_row in opex_result:
            opex_by_type.setdefault(opex_row.primary_type, {})[opex_row.month_key] = float(
                opex_row.expense_sum or 0
            )

    # ── 8. Build P&L rows ──
    months_sorted = sorted(months_set, reverse=True)

    # ── 7c. Проценты и комиссии по займам ──
    # Стоимость денег — операционный расход, но к бренду/артикулу не относится,
    # поэтому при фильтре её не показываем (как и прочий opex выше). Считается по
    # КАЛЕНДАРНЫМ месяцам по дням — та же база, что на дашборде займов.
    loan_cost: dict[str, dict[str, float]] = {}
    if not brand and not article and months_sorted:
        from backend.services.loan_analytics import cost_by_month_keys

        raw = await cost_by_month_keys(db, project_id, months_sorted)
        loan_cost = {
            mk: {"interest": float(v.get("interest", 0)), "fee": float(v.get("fee", 0))}
            for mk, v in raw.items()
        }

    pnl_result: dict = _build_pnl_result(
        monthly_data,
        total_data,
        months_sorted,
        all_brands,
        tax_info,
        monthly_ads,
        monthly_cost,
        total_cost_val,
        date_from,
        date_to,
        tax_by_month=tax_by_month,
        opex_by_type=opex_by_type,
        loan_cost=loan_cost,
    )
    return pnl_result


def _build_pnl_result(
    monthly_data,
    total_data,
    months_sorted,
    all_brands,
    tax_info,
    monthly_ads,
    monthly_cost,
    total_cost_val,
    date_from,
    date_to,
    tax_by_month=None,
    opex_by_type=None,
    loan_cost=None,
):
    """Build the final P&L rows and return the result dict.

    ``tax_by_month`` — настройки налога на каждый месяц ('YYYY-MM' → dict);
    None (старые вызовы) → все месяцы по настройкам первого (tax_info)."""

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

    # ── Операционные расходы: разбивка по типам контрагентов ──
    # opex_by_type: {primary_type: {month_key: amount}}. Дети сортируются по
    # убыванию суммы за период; родитель = их сумма.
    opex_by_type = opex_by_type or {}
    opex_child_defs = []  # (total, key, label, monthly)
    for ptype, month_amounts in opex_by_type.items():
        child_m = {mk: month_amounts.get(mk, 0.0) for mk in months_sorted}
        child_t = sum(child_m.values())
        if child_t == 0:
            continue
        label = OPEX_TYPE_LABELS.get(ptype, ptype)
        opex_child_defs.append((child_t, f"opex_{ptype.lower()}", label, child_m))
    opex_child_defs.sort(key=lambda x: x[0], reverse=True)

    # Проценты по займам — такой же операционный расход, но источник другой:
    # не банковские платежи, а НАЧИСЛЕНИЕ по дням (проценты платят рвано, а то и
    # не платят вовсе — по кассе расход месяца был бы нулевым при живом долге).
    loan_cost = loan_cost or {}
    loan_int_m = {mk: float(loan_cost.get(mk, {}).get("interest", 0.0)) for mk in months_sorted}
    loan_fee_m = {mk: float(loan_cost.get(mk, {}).get("fee", 0.0)) for mk in months_sorted}
    loan_m = {mk: loan_int_m[mk] + loan_fee_m[mk] for mk in months_sorted}
    loan_int_t = sum(loan_int_m.values())
    loan_fee_t = sum(loan_fee_m.values())
    loan_t = loan_int_t + loan_fee_t

    ops_m = {mk: sum(c[3][mk] for c in opex_child_defs) + loan_m[mk] for mk in months_sorted}
    ops_t = sum(c[0] for c in opex_child_defs) + loan_t

    ebitda_m = {mk: margin_m[mk] - ops_m[mk] for mk in months_sorted}
    ebitda_t = margin_t - ops_t

    # Настройки на месяц: смена ставки/режима внутри диапазона учитывается
    # помесячно; tax_by_month=None (старые вызовы) → настройки первого месяца.
    months_tax = tax_by_month or {}
    uniform = all(v == tax_info for v in months_tax.values()) if months_tax else True

    def _info_for(mk):
        return months_tax.get(mk, tax_info)

    def _calc_tax(income, expenses, info):
        usn_rate = info.get("usn_rate", 0) / 100
        nds_rate = info.get("nds_rate", 0) / 100
        nds = income * nds_rate / (1 + nds_rate) if nds_rate > 0 else 0
        base = income - nds
        if info.get("tax_regime", "usn_income") == "usn_income_expense_vat":
            base = base - expenses
        usn = max(base * usn_rate, 0)
        return nds + usn

    def _tax_expenses_for(log, stor, comm, pen, ded, adv, cost_val, info):
        exp = abs(log) + abs(stor) + abs(comm) + abs(pen) + abs(ded) + abs(adv)
        if info.get("cost_as_expense", False):
            exp += abs(cost_val)
        return exp

    tax_exp_m = {
        mk: _tax_expenses_for(
            log_m[mk], stor_m[mk], comm_m[mk], pen_m[mk], ded_m[mk], adv_m[mk], cost_m[mk],
            _info_for(mk),
        )
        for mk in months_sorted
    }
    tax_exp_t = _tax_expenses_for(log_t, stor_t, comm_t, pen_t, ded_t, adv_t, cost_t, tax_info)

    tax_m = {mk: _calc_tax(sales_m[mk], tax_exp_m[mk], _info_for(mk)) for mk in months_sorted}
    # Одинаковые настройки → прежний агрегатный расчёт (бит-в-бит); смешанные →
    # итог обязан быть суммой месяцев, каждый по своей ставке.
    tax_t = _calc_tax(sales_t, tax_exp_t, tax_info) if uniform else sum(tax_m.values())

    net_m = {mk: ebitda_m[mk] - tax_m[mk] for mk in months_sorted}
    net_t = ebitda_t - tax_t

    def _r(key, label, level, bold, vm, vt, bm=None, bt=None, expandable=False, parent_key=None):
        return build_row(key, label, level, bold, vm, vt, months_sorted, bm, bt, expandable, parent_key)

    opex_child_rows = [
        _r(child_key, label, 1, False, child_m, child_t, real_m, real_t, parent_key="operating_expenses")
        for child_t, child_key, label, child_m in opex_child_defs
    ]
    if loan_t:
        # Комиссии раскрываем отдельной строкой только когда они есть: у частных
        # займов их нет вовсе, и пустая строка «Комиссии» лишь путала бы.
        has_fee = bool(loan_fee_t)
        opex_child_rows.append(
            _r(
                "opex_loan_interest",
                "Проценты по займам",
                1,
                False,
                loan_m,
                loan_t,
                real_m,
                real_t,
                expandable=has_fee,
                parent_key="operating_expenses",
            )
        )
        if has_fee:
            opex_child_rows += [
                _r("loan_interest", "Проценты", 2, False, loan_int_m, loan_int_t,
                   real_m, real_t, parent_key="opex_loan_interest"),
                _r("loan_fees", "Комиссии банков", 2, False, loan_fee_m, loan_fee_t,
                   real_m, real_t, parent_key="opex_loan_interest"),
            ]

    rows = [
        _r("realization", "Реализация", 0, True, real_m, real_t),
        _r("spp_discount", "Скидка за счет МП", 1, False, spp_m, spp_t, real_m, real_t),
        _r("sales_amount", "Фактические продажи", 0, True, sales_m, sales_t, real_m, real_t),
        _r("direct_costs", "Прямые расходы", 0, True, direct_m, direct_t, real_m, real_t, expandable=True),
        _r("cost_of_sales", "Себестоимость продаж", 1, False, cost_m, cost_t, real_m, real_t, expandable=True),
        _r("cost_price", "Себестоимость", 2, False, cost_m, cost_t, real_m, real_t),
        _r("logistics", "Логистика", 1, False, log_m, log_t, real_m, real_t),
        _r("commission", "Комиссия", 1, False, comm_m, comm_t, real_m, real_t),
        _r("penalties", "Штрафы", 1, False, pen_m, pen_t, real_m, real_t),
        _r("storage", "Хранение", 1, False, stor_m, stor_t, real_m, real_t),
        _r("advertising", "Внутренняя реклама", 1, False, adv_m, adv_t, real_m, real_t),
        _r("deductions", "Прочие удержания", 1, False, ded_m, ded_t, real_m, real_t),
        _r("acceptance", "Платная приёмка", 1, False, acc_m, acc_t, real_m, real_t),
        _r("compensation", "Компенсация", 0, False, comp_total_m, comp_total_t, real_m, real_t, expandable=True),
        _r("comp_bonus", "Баллы за скидки", 1, False, comp_bonus_m, comp_bonus_t, real_m, real_t),
        _r(
            "comp_voluntary",
            "Добровольная компенсация при возврате",
            1,
            False,
            comp_vol_m,
            comp_vol_t,
            real_m,
            real_t,
        ),
        _r("gross_margin", "Валовая маржа", 0, True, margin_m, margin_t, real_m, real_t),
        _r(
            "operating_expenses",
            "Операционные расходы",
            0,
            False,
            ops_m,
            ops_t,
            real_m,
            real_t,
            expandable=bool(opex_child_rows),
        ),
        *opex_child_rows,
        _r("ebitda", "Операционная прибыль (EBITDA)", 0, True, ebitda_m, ebitda_t, real_m, real_t),
        _r("taxes", "Налоги (кроме зарплатных)", 0, True, tax_m, tax_t, real_m, real_t, expandable=True),
        _r("net_profit", "Чистая прибыль", 0, True, net_m, net_t, real_m, real_t),
    ]

    return {
        "period": {"date_from": str(date_from), "date_to": str(date_to)},
        "year_label": str(date_from.year),
        "months": months_sorted,
        "rows": rows,
        "brands": all_brands,
        "tax_info": tax_info,
    }
