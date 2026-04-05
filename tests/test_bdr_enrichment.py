"""
Tests for bdr_enrichment — pure computation functions (no DB required).

Covers: apply_tax, apply_tax_article, enrich_article, compute_abc.
"""


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: apply_tax (summary-level)
# ═══════════════════════════════════════════════════════════════════════════════

from backend.services.bdr_enrichment import (
    apply_tax,
    apply_tax_article,
    compute_abc,
    enrich_article,
)


class TestApplyTax:
    """Test summary-level tax calculation."""

    def _base_summary(self) -> dict:
        return {
            "sales_amount": 1000000,
            "logistics": -50000,
            "storage": -10000,
            "commission": -150000,
            "penalties": -5000,
            "deductions": -30000,
            "to_pay": 700000,
            "ad_deduction": -20000,
            "loan_deduction": -5000,
        }

    def test_usn_income_no_nds(self):
        """USN 'Доходы' without NDS: tax = sales * usn_rate."""
        s = self._base_summary()
        tax_info = {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}
        apply_tax(s, tax_info, total_adv=40000, total_cost=100000)

        assert s["tax_nds"] == 0
        assert s["tax_usn"] == 60000.0  # 1_000_000 * 0.06
        assert s["tax_total"] == 60000.0

    def test_usn_income_with_nds(self):
        """USN 'Доходы' with NDS 5%: nds = income * 5% / 1.05."""
        s = self._base_summary()
        tax_info = {"usn_rate": 6, "nds_rate": 5, "tax_regime": "usn_income", "cost_as_expense": False}
        apply_tax(s, tax_info, total_adv=0, total_cost=0)

        expected_nds = round(1000000 * 0.05 / 1.05, 2)
        assert s["tax_nds"] == expected_nds
        tax_base = 1000000 - expected_nds
        expected_usn = round(tax_base * 0.06, 2)
        assert s["tax_usn"] == expected_usn

    def test_usn_income_expense_vat(self):
        """USN 'Доходы - Расходы' with NDS: expenses deducted from base."""
        s = self._base_summary()
        tax_info = {"usn_rate": 15, "nds_rate": 5, "tax_regime": "usn_income_expense_vat", "cost_as_expense": False}
        apply_tax(s, tax_info, total_adv=40000, total_cost=100000)

        _ = round(1000000 * 0.05 / 1.05, 2)
        expenses = 50000 + 10000 + 150000 + 5000 + 30000 + 40000
        assert s["expenses_total"] == round(expenses, 2)

    def test_usn_income_expense_with_cost(self):
        """When cost_as_expense=True, cost is added to expenses."""
        s = self._base_summary()
        tax_info = {"usn_rate": 15, "nds_rate": 0, "tax_regime": "usn_income_expense_vat", "cost_as_expense": True}
        apply_tax(s, tax_info, total_adv=40000, total_cost=100000)

        expenses_expected = 50000 + 10000 + 150000 + 5000 + 30000 + 40000 + 100000
        assert s["expenses_total"] == round(expenses_expected, 2)

    def test_zero_income_no_tax(self):
        """Zero income -> zero tax."""
        s = {"sales_amount": 0, "to_pay": 0, "ad_deduction": 0, "loan_deduction": 0}
        tax_info = {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}
        apply_tax(s, tax_info, total_adv=0, total_cost=0)

        assert s["tax_total"] == 0
        assert s["tax_nds"] == 0
        assert s["tax_usn"] == 0

    def test_profit_calculation(self):
        """Profit = to_pay + ad_ded + loan_ded - adv - cost - tax."""
        s = self._base_summary()
        tax_info = {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}
        apply_tax(s, tax_info, total_adv=40000, total_cost=100000)

        expected_profit = round(700000 + (-20000) + (-5000) - 40000 - 100000 - 60000, 2)
        assert s["profit"] == expected_profit


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: apply_tax_article (per-article)
# ═══════════════════════════════════════════════════════════════════════════════


class TestApplyTaxArticle:
    """Test per-article tax calculation."""

    def _base_article(self) -> dict:
        return {
            "sales_amount": 500000,
            "logistics": -20000,
            "storage": -5000,
            "commission": -75000,
            "penalties": -2000,
            "deductions": -10000,
            "to_pay": 350000,
            "ad_deduction": -8000,
            "loan_deduction": -2000,
            "adv_sum": 15000,
            "cost_total": 50000,
        }

    def test_usn_income(self):
        """Article-level USN income tax."""
        art = self._base_article()
        tax_info = {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}
        apply_tax_article(art, tax_info)

        assert art["tax_nds"] == 0
        assert art["tax_usn"] == round(500000 * 0.06, 2)
        assert art["tax_total"] == round(500000 * 0.06, 2)

    def test_tax_base_stored(self):
        """tax_base field should be populated."""
        art = self._base_article()
        tax_info = {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}
        apply_tax_article(art, tax_info)

        assert "tax_base" in art
        assert art["tax_base"] == 500000.0

    def test_profit_no_ops(self):
        """profit_no_ops should equal profit for now."""
        art = self._base_article()
        tax_info = {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}
        apply_tax_article(art, tax_info)

        assert art["profit_no_ops"] == art["profit"]

    def test_negative_tax_base_zero_usn(self):
        """If tax_base < 0, USN should be 0 (max(..., 0))."""
        art = self._base_article()
        art["sales_amount"] = 10000  # very low income
        tax_info = {"usn_rate": 15, "nds_rate": 5, "tax_regime": "usn_income_expense_vat", "cost_as_expense": True}
        apply_tax_article(art, tax_info)

        assert art["tax_usn"] == 0  # negative base clipped to 0


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: enrich_article
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnrichArticle:
    """Test computed columns for articles."""

    def _base_article(self) -> dict:
        return {
            "sale_qty": 100,
            "realization": 500000.0,
            "sales_amount": 400000.0,
            "profit": 80000.0,
            "cost_total": 200000.0,
            "cost_price": 2000.0,
            "adv_sum": 30000.0,
            "avg_retail_price": 5000.0,
            "stocks_wb": 50,
            "orders_sum": 600000.0,
        }

    def test_avg_profit_per_item(self):
        art = self._base_article()
        enrich_article(art, total_real=1000000, total_sales=800000, period_days=30)
        assert art["avg_profit_per_item"] == 800.0  # 80000 / 100

    def test_margin_pct(self):
        art = self._base_article()
        enrich_article(art, total_real=1000000, total_sales=800000, period_days=30)
        assert art["margin_pct"] == 16.0  # 80000 / 500000 * 100

    def test_roi(self):
        art = self._base_article()
        enrich_article(art, total_real=1000000, total_sales=800000, period_days=30)
        assert art["roi"] == 40.0  # 80000 / 200000 * 100

    def test_revenue_share(self):
        art = self._base_article()
        enrich_article(art, total_real=1000000, total_sales=800000, period_days=30)
        assert art["revenue_share"] == 50.0  # 500000 / 1000000 * 100

    def test_drr(self):
        art = self._base_article()
        enrich_article(art, total_real=1000000, total_sales=800000, period_days=30)
        assert art["drr"] == 7.5  # 30000 / 400000 * 100

    def test_turnover_days(self):
        art = self._base_article()
        enrich_article(art, total_real=1000000, total_sales=800000, period_days=30)
        daily_sales = 100 / 30
        expected = round(50 / daily_sales, 2)
        assert art["turnover_days"] == expected

    def test_cap_cost(self):
        art = self._base_article()
        enrich_article(art, total_real=1000000, total_sales=800000, period_days=30)
        assert art["cap_cost"] == 100000.0  # 50 * 2000

    def test_cap_retail(self):
        art = self._base_article()
        enrich_article(art, total_real=1000000, total_sales=800000, period_days=30)
        assert art["cap_retail"] == 250000.0  # 50 * 5000

    def test_zero_sale_qty(self):
        """Zero sales -> avg_profit_per_item = 0."""
        art = self._base_article()
        art["sale_qty"] = 0
        enrich_article(art, total_real=1000000, total_sales=800000, period_days=30)
        assert art["avg_profit_per_item"] == 0

    def test_zero_realization(self):
        """Zero realization -> margin_pct = 0."""
        art = self._base_article()
        art["realization"] = 0
        enrich_article(art, total_real=1000000, total_sales=800000, period_days=30)
        assert art["margin_pct"] == 0

    def test_zero_cost(self):
        """Zero cost -> roi = 0."""
        art = self._base_article()
        art["cost_total"] = 0
        enrich_article(art, total_real=1000000, total_sales=800000, period_days=30)
        assert art["roi"] == 0

    def test_zero_period_days(self):
        """Zero period_days -> turnover_days = 0."""
        art = self._base_article()
        enrich_article(art, total_real=1000000, total_sales=800000, period_days=0)
        assert art["turnover_days"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: compute_abc
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeABC:
    """Test ABC analysis classification."""

    def test_single_item_gets_A(self):
        """Single item with profit > 0 -> A."""
        articles = [{"profit": 100, "realization": 500}]
        compute_abc(articles)
        assert articles[0]["abc_profit"] == "A"
        assert articles[0]["abc_revenue"] == "A"

    def test_three_items_abc_by_profit(self):
        """Classic ABC split: biggest = A, mid = B, smallest = C."""
        articles = [
            {"profit": 800, "realization": 1000},
            {"profit": 150, "realization": 200},
            {"profit": 50, "realization": 100},
        ]
        compute_abc(articles)
        # 800 is 80% -> A, 150 brings to 95% -> B, 50 -> C
        profit_map = {a["profit"]: a["abc_profit"] for a in articles}
        assert profit_map[800] == "A"
        assert profit_map[150] == "B"
        assert profit_map[50] == "C"

    def test_all_zero_profit_gets_C(self):
        """All zero profit -> all C."""
        articles = [
            {"profit": 0, "realization": 100},
            {"profit": 0, "realization": 200},
        ]
        compute_abc(articles)
        for a in articles:
            assert a["abc_profit"] == "C"

    def test_negative_profit_treated_as_zero(self):
        """Negative profit treated as 0 in cumulative sum."""
        articles = [
            {"profit": 100, "realization": 500},
            {"profit": -50, "realization": 200},
        ]
        compute_abc(articles)
        assert articles[0]["abc_profit"] == "A"
        # negative profit is 0 in sum, so second item is C
        neg_art = next(a for a in articles if a["profit"] == -50)
        assert neg_art["abc_profit"] == "C"

    def test_empty_articles(self):
        """Empty list -> no crash."""
        articles = []
        compute_abc(articles)
        assert articles == []

    def test_abc_by_revenue_independent(self):
        """ABC by revenue is independent from ABC by profit."""
        articles = [
            {"profit": 10, "realization": 9000},  # low profit, high revenue
            {"profit": 900, "realization": 100},  # high profit, low revenue
            {"profit": 90, "realization": 900},
        ]
        compute_abc(articles)
        rev_map = {a["realization"]: a["abc_revenue"] for a in articles}
        prof_map = {a["profit"]: a["abc_profit"] for a in articles}

        # By revenue: 9000 is A, 900 is B, 100 is C
        assert rev_map[9000] == "A"
        assert rev_map[900] == "B"
        assert rev_map[100] == "C"

        # By profit: 900 is A, 90 is B, 10 is C
        assert prof_map[900] == "A"
        assert prof_map[90] == "B"
        assert prof_map[10] == "C"
