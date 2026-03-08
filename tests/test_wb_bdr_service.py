"""
Tests for wb_bdr_service — pure computation functions.
No DB required — tests _compute_metrics, _apply_tax, _enrich_article, _compute_abc.
"""
import pytest
from decimal import Decimal

D = Decimal
ZERO = D("0")


# ─── Imports ─────────────────────────────────────────────────────────────────

from backend.services.wb_bdr_service import (
    _empty_totals,
    _compute_metrics,
    _serialize,
)
from backend.services.bdr_enrichment import (
    apply_tax as _apply_tax,
    apply_tax_article as _apply_tax_article,
    enrich_article as _enrich_article,
    compute_abc as _compute_abc,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_sale(**overrides) -> dict:
    t = _empty_totals()
    for k, v in overrides.items():
        t[k] = D(str(v))
    return t


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _compute_metrics
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeMetrics:
    """Verify BDR metric formulas against known inputs."""

    def test_empty_buckets(self):
        """All-zero input → all-zero output."""
        result = _compute_metrics(_empty_totals(), _empty_totals(), _empty_totals())
        assert result["realization"] == ZERO
        assert result["sales_amount"] == ZERO
        assert result["to_pay"] == ZERO
        assert result["sale_qty"] == 0
        assert result["ret_qty"] == 0

    def test_basic_sale(self):
        """Single sale → realization, commission, to_pay."""
        sale = _make_sale(
            retail_price_withdisc_rub=1000,
            retail_amount=800,
            ppvz_for_pay=600,
            product_qty=5,
        )
        ret = _empty_totals()
        other = _make_sale(delivery_rub=100, storage_fee=50)

        result = _compute_metrics(sale, ret, other)

        assert result["realization"] == D("1000")
        assert result["sales_amount"] == D("800")
        assert result["sale_qty"] == 5
        assert result["ret_qty"] == 0
        # commission = sales_amount - (ppvz_net - compensation)
        assert result["commission"] == D("800") - D("600")  # 200
        # to_pay = ppvz_net - logistics - penalties - storage - deductions - acceptance
        assert result["to_pay"] == D("600") - D("100") - D("0") - D("50") - D("0") - D("0")

    def test_sale_with_returns(self):
        """Sales + returns → net values."""
        sale = _make_sale(
            retail_price_withdisc_rub=1000,
            retail_amount=800,
            ppvz_for_pay=600,
            product_qty=10,
        )
        ret = _make_sale(
            retail_price_withdisc_rub=200,
            retail_amount=160,
            ppvz_for_pay=120,
            product_qty=2,
        )
        other = _empty_totals()

        result = _compute_metrics(sale, ret, other)

        assert result["realization"] == D("800")  # 1000 - 200
        assert result["sales_amount"] == D("640")  # 800 - 160
        assert result["sale_qty"] == 8  # net = gross(10) - returns(2)
        assert result["ret_qty"] == 2
        assert result["returns_amount"] == D("200")

    def test_buyout_pct(self):
        """Buyout % = sale_qty / (sale_qty + ret_qty) * 100."""
        sale = _make_sale(product_qty=80)
        ret = _make_sale(product_qty=20)
        other = _empty_totals()

        result = _compute_metrics(sale, ret, other)
        assert result["buyout_pct"] == D("80")  # 80/(80+20)*100

    def test_avg_sale_price(self):
        """avg_sale_price = sales_amount / net_qty."""
        sale = _make_sale(retail_amount=5000, product_qty=10)
        ret = _make_sale(retail_amount=500, product_qty=2)
        other = _empty_totals()

        result = _compute_metrics(sale, ret, other)
        net_qty = 10 - 2  # 8
        expected_avg = D("4500") / D("8")
        assert result["avg_sale_price"] == expected_avg


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _apply_tax
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplyTax:
    """Tax calculation on summary."""

    def test_usn_income_no_nds(self):
        """USN 6% without NDS."""
        summary = {"sales_amount": 100000, "to_pay": 50000}
        tax_info = {"tax_regime": "usn_income", "usn_rate": 6, "nds_rate": 0, "cost_as_expense": False}

        _apply_tax(summary, tax_info, D("5000"), D("10000"))

        # tax_base = 100000 - 0 = 100000
        # usn = 100000 * 0.06 = 6000
        assert summary["tax_nds"] == 0
        assert summary["tax_usn"] == 6000
        assert summary["tax_total"] == 6000
        # profit = to_pay - adv - cost - tax = 50000 - 5000 - 10000 - 6000 = 29000
        assert summary["profit"] == 29000

    def test_usn_income_expense_with_nds(self):
        """USN 15% Д-Р with NDS 5%."""
        summary = {
            "sales_amount": 100000,
            "to_pay": 50000,
            "logistics": -10000,
            "storage": -3000,
            "commission": 20000,
            "penalties": -500,
            "deductions": -1000,
        }
        tax_info = {"tax_regime": "usn_income_expense_vat", "usn_rate": 15, "nds_rate": 5, "cost_as_expense": True}

        _apply_tax(summary, tax_info, D("5000"), D("10000"))

        # NDS = 100000 * 0.05 / 1.05 ≈ 4761.90
        expected_nds = round(100000 * 0.05 / 1.05, 2)
        assert summary["tax_nds"] == expected_nds

        # Expenses = |logistics| + |storage| + |commission| + |penalties| + |deductions| + adv + cost
        expenses = 10000 + 3000 + 20000 + 500 + 1000 + 5000 + 10000
        assert summary["expenses_total"] == round(expenses, 2)

    def test_zero_tax(self):
        """Zero rates → zero tax, profit = to_pay - adv - cost."""
        summary = {"sales_amount": 50000, "to_pay": 30000}
        tax_info = {"tax_regime": "usn_income", "usn_rate": 0, "nds_rate": 0, "cost_as_expense": False}

        _apply_tax(summary, tax_info, D("2000"), D("5000"))

        assert summary["tax_total"] == 0
        assert summary["profit"] == 30000 - 2000 - 5000  # 23000


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _apply_tax_article
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplyTaxArticle:
    """Per-article tax."""

    def test_basic_article_tax(self):
        art = {
            "sales_amount": 10000,
            "to_pay": 5000,
            "adv_sum": 500,
            "cost_total": 1000,
            "logistics": -800,
            "storage": -200,
            "commission": 2000,
            "penalties": 0,
            "deductions": 0,
        }
        tax_info = {"tax_regime": "usn_income", "usn_rate": 6, "nds_rate": 0, "cost_as_expense": False}

        _apply_tax_article(art, tax_info)

        assert art["tax_usn"] == round(10000 * 0.06, 2)
        assert art["profit"] == round(5000 - 500 - 1000 - 600, 2)  # to_pay - adv - cost - tax


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _enrich_article
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnrichArticle:
    """Computed fields: margin, ROI, DRR, turnover, GMROI, capitalization."""

    def _base_article(self):
        return {
            "sale_qty": 100,
            "realization": 50000,
            "sales_amount": 45000,
            "profit": 10000,
            "cost_total": 20000,
            "cost_price": 200,
            "adv_sum": 3000,
            "avg_retail_price": 500,
            "stocks_wb": 200,
            "orders_sum": 60000,
            "orders_count": 120,
            "ret_qty": 10,
        }

    def test_margin(self):
        art = self._base_article()
        _enrich_article(art, 100000, 90000, 7)
        # margin = profit / realization * 100 = 10000/50000*100 = 20%
        assert art["margin_pct"] == 20.0

    def test_roi(self):
        art = self._base_article()
        _enrich_article(art, 100000, 90000, 7)
        # ROI = profit / cost_total * 100 = 10000/20000*100 = 50%
        assert art["roi"] == 50.0

    def test_drr(self):
        art = self._base_article()
        _enrich_article(art, 100000, 90000, 7)
        # DRR = adv / sales_amount * 100 = 3000/45000*100 ≈ 6.67%
        assert art["drr"] == round(3000 / 45000 * 100, 2)

    def test_drr_orders(self):
        art = self._base_article()
        _enrich_article(art, 100000, 90000, 7)
        # DRR orders = adv / orders_sum * 100 = 3000/60000*100 = 5%
        assert art["drr_orders"] == 5.0

    def test_turnover_days(self):
        art = self._base_article()
        _enrich_article(art, 100000, 90000, 7)
        # daily_sales = 100/7 ≈ 14.28
        # turnover = stocks_wb / daily_sales = 200/14.28 ≈ 14.0
        expected = round(200 / (100 / 7), 2)
        assert art["turnover_days"] == expected

    def test_capitalization(self):
        art = self._base_article()
        _enrich_article(art, 100000, 90000, 7)
        assert art["cap_cost"] == round(200 * 200, 2)  # stocks * cost_price
        assert art["cap_retail"] == round(200 * 500, 2)  # stocks * avg_retail

    def test_gmroi(self):
        art = self._base_article()
        _enrich_article(art, 100000, 90000, 7)
        cap_cost = 200 * 200  # 40000
        expected = round(10000 / cap_cost * 100, 2)
        assert art["gmroi"] == expected

    def test_revenue_share(self):
        art = self._base_article()
        _enrich_article(art, 100000, 90000, 7)
        # revenue_share = realization / total_real * 100 = 50000/100000*100 = 50%
        assert art["revenue_share"] == 50.0

    def test_avg_profit_per_item(self):
        art = self._base_article()
        _enrich_article(art, 100000, 90000, 7)
        # avg = profit / sale_qty = 10000/100 = 100
        assert art["avg_profit_per_item"] == 100.0

    def test_zero_sales_no_division_error(self):
        """Zero quantities → no ZeroDivisionError."""
        art = {
            "sale_qty": 0,
            "realization": 0,
            "sales_amount": 0,
            "profit": 0,
            "cost_total": 0,
            "cost_price": 0,
            "adv_sum": 0,
            "avg_retail_price": 0,
            "stocks_wb": 0,
            "orders_sum": 0,
            "orders_count": 0,
            "ret_qty": 0,
        }
        # Should not raise
        _enrich_article(art, 0, 0, 7)
        assert art["margin_pct"] == 0
        assert art["roi"] == 0
        assert art["drr"] == 0
        assert art["turnover_days"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _compute_abc
# ═══════════════════════════════════════════════════════════════════════════════

class TestComputeABC:
    """ABC analysis classification."""

    def test_three_articles(self):
        """Article with 80% profit = A, next 15% = B, rest = C."""
        articles = [
            {"profit": 80, "realization": 80},
            {"profit": 15, "realization": 15},
            {"profit": 5, "realization": 5},
        ]
        _compute_abc(articles)

        # Total profit = 100; cumulative: 80=80% → A, 95=95% → B, 100=100% → C
        assert articles[0]["abc_profit"] == "A"
        assert articles[1]["abc_profit"] == "B"
        assert articles[2]["abc_profit"] == "C"

    def test_all_zero_profit(self):
        """All zero profit → all C."""
        articles = [{"profit": 0, "realization": 0}, {"profit": 0, "realization": 0}]
        _compute_abc(articles)
        assert all(a["abc_profit"] == "C" for a in articles)
        assert all(a["abc_revenue"] == "C" for a in articles)

    def test_single_article(self):
        """Single article → A for both (it's 100% of everything)."""
        articles = [{"profit": 100, "realization": 500}]
        _compute_abc(articles)
        assert articles[0]["abc_profit"] == "A"
        assert articles[0]["abc_revenue"] == "A"

    def test_negative_profit_handled(self):
        """Negative profits → treated as 0 in cumulative, classified as C."""
        articles = [
            {"profit": 100, "realization": 100},
            {"profit": -20, "realization": 20},
        ]
        _compute_abc(articles)
        # Total positive profit = 100; art[0]=100%, art[1]=0 contribution → both boundaries
        assert articles[0]["abc_profit"] == "A"
        assert articles[1]["abc_profit"] == "C"  # negative → 0 contribution → beyond 95%


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _serialize
# ═══════════════════════════════════════════════════════════════════════════════

class TestSerialize:
    """Decimal → float conversion."""

    def test_decimal_to_float(self):
        d = {"a": D("1.23"), "b": "text", "c": 42}
        result = _serialize(d)
        assert result["a"] == 1.23
        assert isinstance(result["a"], float)
        assert result["b"] == "text"
        assert result["c"] == 42
