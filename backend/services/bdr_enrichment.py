"""
BDR enrichment — tax calculations, computed metrics, ABC analysis.

Pure functions (no DB access). Split from wb_bdr_service.py for maintainability.
"""


# ─── Tax calculation ────────────────────────────────────────────────────────

def apply_tax(summary: dict, tax_info: dict, total_adv, total_cost):
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
        # to_pay уже содержит deductions_total (реклама+кредиты+отзывы)
        # Добавляем обратно рекламу и кредиты — они НЕ операционные расходы
        + float(summary.get("ad_deduction", 0))    # реклама — отдельная статья
        + float(summary.get("loan_deduction", 0))   # кредиты — финансовая операция
        # Вычитаем рекламу из WbFunnelDaily (наш реальный трекинг рекламы)
        - float(total_adv)
        - float(total_cost)
        - total_tax,
        2,
    )


def tax_settings_uniform(tax_by_month: dict[str, dict]) -> bool:
    """True, если во всех месяцах диапазона одинаковые налоговые настройки —
    тогда старый одноставочный путь точен и остаётся бит-в-бит."""
    vals = list(tax_by_month.values())
    return all(v == vals[0] for v in vals[1:]) if vals else True


def _month_tax(income: float, expenses: float, info: dict) -> tuple[float, float]:
    """(НДС, УСН) одного месяца по его настройкам — та же математика, что в apply_tax."""
    usn_rate = info.get("usn_rate", 0) / 100
    nds_rate = info.get("nds_rate", 0) / 100
    nds_sum = income * nds_rate / (1 + nds_rate) if nds_rate > 0 else 0
    tax_base = income - nds_sum
    if info.get("tax_regime", "usn_income") == "usn_income_expense_vat":
        tax_base -= expenses
    return nds_sum, max(tax_base * usn_rate, 0)


def apply_tax_by_month(summary: dict, monthly: dict[str, dict], tax_by_month: dict[str, dict],
                       total_adv, total_cost):
    """Налог сводки для диапазона со СМЕНОЙ ставок: помесячный расчёт, итог = сумма.

    ``monthly``: 'YYYY-MM' → {income, logistics, storage, commission, penalties,
    deductions, adv, cost} — те же компоненты, что apply_tax берёт из summary,
    только в разрезе месяцев. Прибыль пишется той же формулой, что в apply_tax."""
    nds_total = usn_total = expenses_total = 0.0
    has_expense_regime = False
    for mk, m in monthly.items():
        info = tax_by_month.get(mk, {})
        expenses = 0.0
        if info.get("tax_regime", "usn_income") == "usn_income_expense_vat":
            has_expense_regime = True
            expenses = (
                abs(float(m.get("logistics", 0)))
                + abs(float(m.get("storage", 0)))
                + abs(float(m.get("commission", 0)))
                + abs(float(m.get("penalties", 0)))
                + abs(float(m.get("deductions", 0)))
                + float(m.get("adv", 0))
            )
            if info.get("cost_as_expense", False):
                expenses += float(m.get("cost", 0))
            expenses_total += expenses
        nds, usn = _month_tax(float(m.get("income", 0)), expenses, info)
        nds_total += nds
        usn_total += usn

    if has_expense_regime:
        summary["expenses_total"] = round(expenses_total, 2)
    total_tax = nds_total + usn_total
    summary["tax_nds"] = round(nds_total, 2)
    summary["tax_usn"] = round(usn_total, 2)
    summary["tax_total"] = round(total_tax, 2)
    summary["profit"] = round(
        float(summary.get("to_pay", 0))
        + float(summary.get("ad_deduction", 0))
        + float(summary.get("loan_deduction", 0))
        - float(total_adv)
        - float(total_cost)
        - total_tax,
        2,
    )


def blended_tax_info(monthly_income: dict[str, float], tax_by_month: dict[str, dict]) -> dict:
    """Эффективные ставки для построчного налога при смешанных месяцах.

    Строки отчёта не имеют помесячного разреза доходов, поэтому по ним налог
    считается взвешенной по доходам месяца ставкой (НДС blend'ится в доле
    извлечения r/(1+r), затем конвертируется обратно в ставку). Режим и
    cost_as_expense берутся из месяца с максимальным доходом. Это приближение —
    точный помесячный налог считается для «Итого» (apply_tax_by_month)."""
    total_income = sum(v for v in monthly_income.values() if v > 0)
    if total_income <= 0:
        first = next(iter(tax_by_month.values()), None)
        return dict(first) if first else {}
    f_nds = usn_eff = 0.0
    for mk, info in tax_by_month.items():
        w = max(monthly_income.get(mk, 0.0), 0.0) / total_income
        r = info.get("nds_rate", 0) / 100
        f_nds += w * (r / (1 + r) if r > 0 else 0.0)
        usn_eff += w * info.get("usn_rate", 0)
    dominant_mk = max(tax_by_month, key=lambda mk: monthly_income.get(mk, 0.0))
    dominant = tax_by_month[dominant_mk]
    return {
        "tax_regime": dominant.get("tax_regime", "usn_income"),
        "cost_as_expense": dominant.get("cost_as_expense", False),
        "nds_rate": round(f_nds / (1 - f_nds) * 100, 6) if f_nds < 1 else 0,
        "usn_rate": usn_eff,
    }


def apply_tax_article(art: dict, tax_info: dict):
    """Per-article tax calculation."""
    usn_rate = tax_info.get("usn_rate", 0) / 100
    nds_rate = tax_info.get("nds_rate", 0) / 100
    regime = tax_info.get("tax_regime", "usn_income")
    cost_as_expense = tax_info.get("cost_as_expense", False)

    income = float(art.get("sales_amount", 0))
    nds_sum = income * nds_rate / (1 + nds_rate) if nds_rate > 0 else 0
    tax_base = income - nds_sum

    if regime == "usn_income_expense_vat":
        expenses = (
            abs(float(art.get("logistics", 0)))
            + abs(float(art.get("storage", 0)))
            + abs(float(art.get("commission", 0)))
            + abs(float(art.get("penalties", 0)))
            + abs(float(art.get("deductions", 0)))
            + float(art.get("adv_sum", 0))
        )
        if cost_as_expense:
            expenses += float(art.get("cost_total", 0))
        tax_base = income - nds_sum - expenses

    usn_sum = max(tax_base * usn_rate, 0)
    total_tax = nds_sum + usn_sum

    art["tax_nds"] = round(nds_sum, 2)
    art["tax_usn"] = round(usn_sum, 2)
    art["tax_total"] = round(total_tax, 2)
    art["tax_base"] = round(tax_base, 2)

    # Profit per article: to_pay + ad/loan deductions - adv_sum - cost - tax
    # to_pay содержит deductions_total, добавляем обратно рекламу/кредиты
    art["profit"] = round(
        float(art.get("to_pay", 0))
        + float(art.get("ad_deduction", 0))
        + float(art.get("loan_deduction", 0))
        - float(art.get("adv_sum", 0))
        - float(art.get("cost_total", 0))
        - total_tax,
        2,
    )
    # Profit without operational expenses (same for now)
    art["profit_no_ops"] = art["profit"]


# ─── Enrichment ─────────────────────────────────────────────────────────────

def enrich_article(art: dict, total_real: float, total_sales: float, period_days: int):
    """Add computed columns to article row."""
    sale_qty = art.get("sale_qty", 0)
    realization = float(art.get("realization", 0))
    sales_amount = float(art.get("sales_amount", 0))
    profit = float(art.get("profit", 0))
    cost_total = float(art.get("cost_total", 0))
    cost_price = float(art.get("cost_price", 0))
    adv_sum = float(art.get("adv_sum", 0))
    avg_retail = float(art.get("avg_retail_price", 0))
    stocks_wb = art.get("stocks_wb", 0)
    orders_sum = float(art.get("orders_sum", 0))

    # Avg profit per item
    art["avg_profit_per_item"] = round(profit / sale_qty, 2) if sale_qty > 0 else 0

    # Margin %
    art["margin_pct"] = round(profit / realization * 100, 2) if realization else 0

    # ROI %
    art["roi"] = round(profit / cost_total * 100, 2) if cost_total > 0 else 0

    # Share of total revenue %
    art["revenue_share"] = round(realization / total_real * 100, 2) if total_real else 0

    # DRR % (ad spend / sales)
    art["drr"] = round(adv_sum / sales_amount * 100, 2) if sales_amount else 0

    # DRR by orders %
    art["drr_orders"] = round(adv_sum / orders_sum * 100, 2) if orders_sum > 0 else 0

    # Turnover (days) = stocks / (sale_qty / days)
    daily_sales = sale_qty / period_days if period_days > 0 else 0
    art["turnover_days"] = round(stocks_wb / daily_sales, 2) if daily_sales > 0 else 0

    # Capitalization by cost
    art["cap_cost"] = round(stocks_wb * cost_price, 2)

    # Capitalization by retail
    art["cap_retail"] = round(stocks_wb * avg_retail, 2)

    # GMROI = profit / avg_inventory_cost (simplified: cap_cost for period)
    cap = art["cap_cost"]
    art["gmroi"] = round(profit / cap * 100, 2) if cap > 0 else 0

    # GMROI Year
    art["gmroi_year"] = round(art["gmroi"] * 365 / period_days, 2) if period_days > 0 else 0

    # Clean up temp field
    art.pop("_period_days", None)


# ─── ABC analysis ───────────────────────────────────────────────────────────

def compute_abc(articles: list[dict]):
    """Compute ABC analysis by profit and revenue.

    Standard ABC: items that bring cumulative contribution up to 80% = A,
    next items up to 95% = B, rest = C.
    The item that *crosses* a threshold is still assigned to the lower group.
    """
    # ABC by profit
    total_profit = sum(max(float(a.get("profit", 0)), 0) for a in articles)
    if total_profit > 0:
        sorted_by_profit = sorted(articles, key=lambda x: float(x.get("profit", 0)), reverse=True)
        cumulative = 0
        for a in sorted_by_profit:
            prev_pct = cumulative / total_profit * 100
            cumulative += max(float(a.get("profit", 0)), 0)
            if prev_pct < 80:
                a["abc_profit"] = "A"
            elif prev_pct < 95:
                a["abc_profit"] = "B"
            else:
                a["abc_profit"] = "C"
    else:
        for a in articles:
            a["abc_profit"] = "C"

    # ABC by revenue
    total_rev = sum(max(float(a.get("realization", 0)), 0) for a in articles)
    if total_rev > 0:
        sorted_by_rev = sorted(articles, key=lambda x: float(x.get("realization", 0)), reverse=True)
        cumulative = 0
        for a in sorted_by_rev:
            prev_pct = cumulative / total_rev * 100
            cumulative += max(float(a.get("realization", 0)), 0)
            if prev_pct < 80:
                a["abc_revenue"] = "A"
            elif prev_pct < 95:
                a["abc_revenue"] = "B"
            else:
                a["abc_revenue"] = "C"
    else:
        for a in articles:
            a["abc_revenue"] = "C"
