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
        # Реклама уже вычтена из to_pay через deductions_total (ad_deduction)
        # Кредиты тоже в deductions_total, но они НЕ операционный расход
        # Добавляем обратно loan_deduction, т.к. это финансовая операция
        + float(summary.get("loan_deduction", 0))
        - float(total_cost)
        - total_tax,
        2,
    )


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

    # Profit per article: to_pay - cost - tax
    # Реклама уже в to_pay через deductions_total, кредиты добавляем обратно
    art["profit"] = round(
        float(art.get("to_pay", 0))
        + float(art.get("loan_deduction", 0))
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
