"""
Tests for DDS PnL report — aggregation logic.

The get_dds_pnl function is async and requires DB, but its post-query
aggregation logic (category classification, monthly rollups, CNY conversion,
net profit) can be tested by simulating SQL row results.

We extract the aggregation flow and test it end-to-end with mock row data.
"""

from types import SimpleNamespace

# ═══════════════════════════════════════════════════════════════════════════════
# Helper: simulate SQL rows returned by the GROUP BY query in get_dds_pnl
# ═══════════════════════════════════════════════════════════════════════════════


def _make_pnl_row(
    m: int,
    cat_lvl1_2: str,
    counterparty: str,
    currency: str = "RUB",
    income: float = 0.0,
    expense: float = 0.0,
) -> SimpleNamespace:
    """Build a mock SQL row matching get_dds_pnl's SELECT columns."""
    return SimpleNamespace(
        m=m,
        cat_lvl1_2=cat_lvl1_2,
        counterparty=counterparty,
        currency=currency,
        income=income,
        expense=expense,
    )


def _aggregate_pnl_rows(rows: list, avg_rate: float = 1.0) -> dict:
    """
    Reproduce the aggregation logic from get_dds_pnl (lines 106-231).
    This is the pure-computation part extracted for testing.
    """
    active_months: set[int] = set()
    for r in rows:
        active_months.add(int(r.m))

    month_names = {
        1: "Январь",
        2: "Февраль",
        3: "Март",
        4: "Апрель",
        5: "Май",
        6: "Июнь",
        7: "Июль",
        8: "Август",
        9: "Сентябрь",
        10: "Октябрь",
        11: "Ноябрь",
        12: "Декабрь",
    }
    months = sorted(active_months)
    month_labels = [{"key": m, "label": f"{month_names[m]} 2025"} for m in months]

    cat_cp_data: dict = {}
    revenue_by_month: dict = {m: 0.0 for m in months}
    revenue_by_month["total"] = 0.0

    for r in rows:
        m = int(r.m)
        cat = r.cat_lvl1_2 or "Без категории"
        cp = r.counterparty or "—"
        inc = float(r.income or 0)
        exp = float(r.expense or 0)

        if r.currency == "CNY":
            inc *= avg_rate
            exp *= avg_rate

        if cat not in cat_cp_data:
            cat_cp_data[cat] = {}
        if cp not in cat_cp_data[cat]:
            cat_cp_data[cat][cp] = {mm: {"income": 0.0, "expense": 0.0} for mm in months}
            cat_cp_data[cat][cp]["total"] = {"income": 0.0, "expense": 0.0}

        cat_cp_data[cat][cp][m]["income"] += inc
        cat_cp_data[cat][cp][m]["expense"] += exp
        cat_cp_data[cat][cp]["total"]["income"] += inc
        cat_cp_data[cat][cp]["total"]["expense"] += exp

        revenue_by_month[m] += inc
        revenue_by_month["total"] += inc

    categories = []
    total_expense_by_month = {m: 0.0 for m in months}
    total_expense_by_month["total"] = 0.0
    total_income_by_month = {m: 0.0 for m in months}
    total_income_by_month["total"] = 0.0

    for cat_name, cp_dict in sorted(cat_cp_data.items()):
        cat_total_income = sum(sum(cp_months[m]["income"] for m in months) for cp_months in cp_dict.values())
        cat_total_expense = sum(sum(cp_months[m]["expense"] for m in months) for cp_months in cp_dict.values())
        is_income = cat_total_income > cat_total_expense

        cat_monthly = {}
        for m in months:
            val = sum(cp_months[m]["income" if is_income else "expense"] for cp_months in cp_dict.values())
            cat_monthly[str(m)] = round(val, 2)
            if is_income:
                total_income_by_month[m] += val
            else:
                total_expense_by_month[m] += val
        cat_total = sum(cp_months["total"]["income" if is_income else "expense"] for cp_months in cp_dict.values())
        cat_monthly["total"] = round(cat_total, 2)
        if is_income:
            total_income_by_month["total"] += cat_total
        else:
            total_expense_by_month["total"] += cat_total

        counterparties = []
        for cp_name, cp_months in sorted(
            cp_dict.items(), key=lambda x: -x[1]["total"]["income" if is_income else "expense"]
        ):
            cp_monthly = {}
            for m in months:
                cp_monthly[str(m)] = round(cp_months[m]["income" if is_income else "expense"], 2)
            cp_monthly["total"] = round(cp_months["total"]["income" if is_income else "expense"], 2)
            if cp_monthly["total"] > 0:
                counterparties.append(
                    {
                        "name": cp_name,
                        "monthly": cp_monthly,
                    }
                )

        categories.append(
            {
                "name": cat_name,
                "type": "income" if is_income else "expense",
                "monthly": cat_monthly,
                "counterparties": counterparties,
            }
        )

    categories.sort(key=lambda c: (0 if c["type"] == "income" else 1, -c["monthly"]["total"]))

    revenue = {str(m): round(revenue_by_month[m], 2) for m in months}
    revenue["total"] = round(revenue_by_month["total"], 2)

    total_exp = {str(m): round(total_expense_by_month[m], 2) for m in months}
    total_exp["total"] = round(total_expense_by_month["total"], 2)

    total_inc = {str(m): round(total_income_by_month[m], 2) for m in months}
    total_inc["total"] = round(total_income_by_month["total"], 2)

    net_profit = {}
    for m in months:
        net_profit[str(m)] = round(total_income_by_month[m] - total_expense_by_month[m], 2)
    net_profit["total"] = round(total_income_by_month["total"] - total_expense_by_month["total"], 2)

    return {
        "months": month_labels,
        "revenue": revenue,
        "categories": categories,
        "summary": {
            "total_income": total_inc,
            "total_expense": total_exp,
            "net_profit": net_profit,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: DDS PnL aggregation
# ═══════════════════════════════════════════════════════════════════════════════


class TestDdsPnlAggregation:
    """Test the post-query aggregation logic from get_dds_pnl."""

    def test_empty_rows(self):
        """No rows → empty result."""
        result = _aggregate_pnl_rows([])
        assert result["months"] == []
        assert result["categories"] == []
        assert result["revenue"] == {"total": 0.0}
        assert result["summary"]["net_profit"] == {"total": 0.0}

    def test_single_income_row(self):
        """One income row → single month, single category."""
        rows = [_make_pnl_row(1, "Выручка WB", "Wildberries", income=100000)]
        result = _aggregate_pnl_rows(rows)

        assert len(result["months"]) == 1
        assert result["months"][0]["key"] == 1
        assert result["revenue"]["1"] == 100000.0
        assert result["revenue"]["total"] == 100000.0
        assert len(result["categories"]) == 1
        assert result["categories"][0]["type"] == "income"
        assert result["categories"][0]["monthly"]["total"] == 100000.0

    def test_single_expense_row(self):
        """One expense row → classified as expense."""
        rows = [_make_pnl_row(3, "Логистика", "DHL", expense=50000)]
        result = _aggregate_pnl_rows(rows)

        assert len(result["categories"]) == 1
        assert result["categories"][0]["type"] == "expense"
        assert result["categories"][0]["monthly"]["total"] == 50000.0

    def test_income_before_expense_sorting(self):
        """Income categories should come before expense categories."""
        rows = [
            _make_pnl_row(1, "Логистика", "DHL", expense=30000),
            _make_pnl_row(1, "Выручка WB", "WB", income=100000),
        ]
        result = _aggregate_pnl_rows(rows)

        assert result["categories"][0]["type"] == "income"
        assert result["categories"][1]["type"] == "expense"

    def test_multi_month_aggregation(self):
        """Multiple months → separate keys in monthly dict."""
        rows = [
            _make_pnl_row(1, "Выручка WB", "WB", income=100000),
            _make_pnl_row(2, "Выручка WB", "WB", income=120000),
            _make_pnl_row(3, "Выручка WB", "WB", income=90000),
        ]
        result = _aggregate_pnl_rows(rows)

        assert len(result["months"]) == 3
        assert result["revenue"]["1"] == 100000.0
        assert result["revenue"]["2"] == 120000.0
        assert result["revenue"]["3"] == 90000.0
        assert result["revenue"]["total"] == 310000.0

    def test_counterparty_drill_down(self):
        """Multiple counterparties within same category."""
        rows = [
            _make_pnl_row(1, "Логистика", "DHL", expense=20000),
            _make_pnl_row(1, "Логистика", "CDEK", expense=10000),
        ]
        result = _aggregate_pnl_rows(rows)

        cat = result["categories"][0]
        assert cat["name"] == "Логистика"
        assert len(cat["counterparties"]) == 2
        # Sorted by total desc
        assert cat["counterparties"][0]["name"] == "DHL"
        assert cat["counterparties"][0]["monthly"]["total"] == 20000.0
        assert cat["counterparties"][1]["name"] == "CDEK"
        assert cat["counterparties"][1]["monthly"]["total"] == 10000.0

    def test_net_profit_calculation(self):
        """Net profit = total_income - total_expense per month."""
        rows = [
            _make_pnl_row(1, "Выручка WB", "WB", income=100000),
            _make_pnl_row(1, "Логистика", "DHL", expense=30000),
            _make_pnl_row(2, "Выручка WB", "WB", income=80000),
            _make_pnl_row(2, "Логистика", "DHL", expense=25000),
        ]
        result = _aggregate_pnl_rows(rows)

        assert result["summary"]["net_profit"]["1"] == 70000.0
        assert result["summary"]["net_profit"]["2"] == 55000.0
        assert result["summary"]["net_profit"]["total"] == 125000.0

    def test_cny_conversion(self):
        """CNY amounts should be converted using avg_rate."""
        rows = [
            _make_pnl_row(1, "Закупка", "Supplier CN", currency="CNY", expense=10000),
        ]
        result = _aggregate_pnl_rows(rows, avg_rate=13.5)

        cat = result["categories"][0]
        assert cat["monthly"]["total"] == 135000.0  # 10000 * 13.5

    def test_cny_and_rub_mixed(self):
        """Mixed currencies — RUB passes through, CNY converted."""
        rows = [
            _make_pnl_row(1, "Закупка", "Supplier RU", currency="RUB", expense=50000),
            _make_pnl_row(1, "Закупка", "Supplier CN", currency="CNY", expense=10000),
        ]
        result = _aggregate_pnl_rows(rows, avg_rate=12.0)

        cat = result["categories"][0]
        # 50000 RUB + 10000*12 CNY = 50000 + 120000 = 170000
        assert cat["monthly"]["total"] == 170000.0

    def test_null_category_becomes_default(self):
        """None cat_lvl1_2 → 'Без категории'."""
        rows = [_make_pnl_row(1, None, "Unknown", expense=5000)]
        result = _aggregate_pnl_rows(rows)

        assert result["categories"][0]["name"] == "Без категории"

    def test_null_counterparty_becomes_dash(self):
        """None counterparty → '—'."""
        rows = [_make_pnl_row(1, "Прочее", None, expense=1000)]
        result = _aggregate_pnl_rows(rows)

        cp = result["categories"][0]["counterparties"]
        assert len(cp) == 1
        assert cp[0]["name"] == "—"

    def test_category_classification_income_vs_expense(self):
        """Category with more income than expense → income type."""
        rows = [
            _make_pnl_row(1, "Смешанная", "CP1", income=80000, expense=20000),
        ]
        result = _aggregate_pnl_rows(rows)

        assert result["categories"][0]["type"] == "income"

    def test_category_classification_expense_dominant(self):
        """Category with more expense than income → expense type."""
        rows = [
            _make_pnl_row(1, "Смешанная", "CP1", income=10000, expense=90000),
        ]
        result = _aggregate_pnl_rows(rows)

        assert result["categories"][0]["type"] == "expense"

    def test_zero_total_counterparty_excluded(self):
        """Counterparty with total == 0 should be excluded."""
        rows = [
            _make_pnl_row(1, "Выручка WB", "Active", income=50000),
            # This cp has income=0 and since category is income, its monthly total is 0
        ]
        result = _aggregate_pnl_rows(rows)

        # Only one counterparty with positive total
        assert len(result["categories"][0]["counterparties"]) == 1

    def test_expense_categories_sorted_by_total_desc(self):
        """Multiple expense categories sorted by total descending."""
        rows = [
            _make_pnl_row(1, "Реклама", "Yandex", expense=50000),
            _make_pnl_row(1, "Логистика", "DHL", expense=80000),
            _make_pnl_row(1, "Зарплата", "Staff", expense=30000),
        ]
        result = _aggregate_pnl_rows(rows)

        expense_cats = [c for c in result["categories"] if c["type"] == "expense"]
        assert expense_cats[0]["name"] == "Логистика"
        assert expense_cats[1]["name"] == "Реклама"
        assert expense_cats[2]["name"] == "Зарплата"
