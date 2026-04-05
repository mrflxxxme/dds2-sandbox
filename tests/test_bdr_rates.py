"""
Tests for bdr_rates — BdrRates dataclass, BdrRatesLookup, _build_rates, compute_profit_bdr.

Pure computation tests (no DB required).
"""

from datetime import date

from backend.services.funnel.bdr_rates import (
    BdrRates,
    BdrRatesLookup,
    _build_rates,
    _compute_tax,
    compute_profit_bdr,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _build_rates
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildRates:
    """Test BdrRates construction from raw aggregates."""

    def test_normal_values(self):
        """Normal scenario: positive realization, sales, to_pay."""
        rates = _build_rates(
            realization=100000,
            sales_amount=70000,
            to_pay=60000,
            sale_qty=80,
            ret_qty=10,
            cancel_qty=10,
        )
        assert rates is not None
        assert rates.to_pay_rate == 0.6  # 60000 / 100000
        assert rates.spp_rate == 0.3  # 1 - 70000/100000
        assert rates.buyout_pct == 0.8  # 80 / (80+10+10)

    def test_zero_realization_returns_none(self):
        """Zero realization -> None (invalid data)."""
        rates = _build_rates(
            realization=0,
            sales_amount=0,
            to_pay=0,
            sale_qty=0,
            ret_qty=0,
        )
        assert rates is None

    def test_negative_realization_returns_none(self):
        """Negative realization -> None."""
        rates = _build_rates(
            realization=-100,
            sales_amount=50,
            to_pay=30,
            sale_qty=5,
            ret_qty=0,
        )
        assert rates is None

    def test_to_pay_rate_clamped_to_1(self):
        """to_pay > realization -> clamped to 1.0."""
        rates = _build_rates(
            realization=1000,
            sales_amount=800,
            to_pay=1500,  # > realization
            sale_qty=10,
            ret_qty=0,
        )
        assert rates is not None
        assert rates.to_pay_rate == 1.0

    def test_spp_rate_clamped_to_095(self):
        """spp_rate > 0.95 -> clamped to 0.95."""
        rates = _build_rates(
            realization=100000,
            sales_amount=1000,  # very low -> spp = 0.99
            to_pay=50000,
            sale_qty=10,
            ret_qty=0,
        )
        assert rates is not None
        assert rates.spp_rate == 0.95

    def test_zero_qty_buyout_default(self):
        """All quantities zero -> buyout = 1.0."""
        rates = _build_rates(
            realization=10000,
            sales_amount=8000,
            to_pay=5000,
            sale_qty=0,
            ret_qty=0,
            cancel_qty=0,
        )
        assert rates is not None
        assert rates.buyout_pct == 1.0

    def test_negative_to_pay_clamped_to_zero(self):
        """Negative to_pay -> to_pay_rate = 0."""
        rates = _build_rates(
            realization=10000,
            sales_amount=8000,
            to_pay=-500,
            sale_qty=10,
            ret_qty=2,
        )
        assert rates is not None
        assert rates.to_pay_rate == 0

    def test_rounding_to_4_decimals(self):
        """Rates should be rounded to 4 decimal places."""
        rates = _build_rates(
            realization=30000,
            sales_amount=21000,
            to_pay=18000,
            sale_qty=7,
            ret_qty=3,
        )
        assert rates is not None
        assert rates.to_pay_rate == 0.6  # 18000/30000 = 0.6
        assert rates.spp_rate == 0.3  # 1 - 21000/30000 = 0.3
        assert rates.buyout_pct == 0.7  # 7/10


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: BdrRatesLookup
# ═══════════════════════════════════════════════════════════════════════════════


class TestBdrRatesLookup:
    """Test lookup with daily + avg fallback."""

    def test_daily_hit(self):
        """Exact (nm_id, date) -> returns daily rate."""
        d = date(2025, 3, 18)
        daily_rates = BdrRates(to_pay_rate=0.6, spp_rate=0.3, buyout_pct=0.85)
        avg_rates = BdrRates(to_pay_rate=0.5, spp_rate=0.25, buyout_pct=0.80)

        lookup = BdrRatesLookup({(123, d): daily_rates}, {123: avg_rates})
        result = lookup.get(123, d)

        assert result is daily_rates

    def test_daily_miss_fallback_to_avg(self):
        """No daily for date -> falls back to avg."""
        d = date(2025, 3, 18)
        avg_rates = BdrRates(to_pay_rate=0.5, spp_rate=0.25, buyout_pct=0.80)

        lookup = BdrRatesLookup({}, {123: avg_rates})
        result = lookup.get(123, d)

        assert result is avg_rates

    def test_no_date_returns_avg(self):
        """No date parameter -> returns avg directly."""
        avg_rates = BdrRates(to_pay_rate=0.5, spp_rate=0.25, buyout_pct=0.80)
        lookup = BdrRatesLookup({}, {123: avg_rates})
        result = lookup.get(123)

        assert result is avg_rates

    def test_unknown_nm_id_returns_none(self):
        """Unknown nm_id -> None."""
        lookup = BdrRatesLookup({}, {})
        result = lookup.get(999)

        assert result is None

    def test_bool_empty(self):
        """Empty lookup -> False."""
        lookup = BdrRatesLookup({}, {})
        assert not lookup

    def test_bool_non_empty_daily(self):
        """Non-empty daily -> True."""
        d = date(2025, 3, 1)
        r = BdrRates(to_pay_rate=0.6, spp_rate=0.3, buyout_pct=0.85)
        lookup = BdrRatesLookup({(1, d): r}, {})
        assert lookup

    def test_bool_non_empty_avg(self):
        """Non-empty avg -> True."""
        r = BdrRates(to_pay_rate=0.6, spp_rate=0.3, buyout_pct=0.85)
        lookup = BdrRatesLookup({}, {1: r})
        assert lookup


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: compute_profit_bdr
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeProfitBdr:
    """Test profit calculation using BDR rates."""

    def _default_bdr(self):
        return BdrRates(to_pay_rate=0.60, spp_rate=0.30, buyout_pct=0.85)

    def _default_tax(self):
        return {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}

    def test_basic_profit(self):
        """Happy path: profit > 0."""
        result = compute_profit_bdr(
            orders_sum=100000,
            orders_count=50,
            adv_sum=5000,
            cost_price=500,
            bdr=self._default_bdr(),
            tax_info=self._default_tax(),
        )
        assert result["has_bdr"] is True
        assert result["revenue"] == round(100000 * 0.85, 2)  # 85000
        assert result["buyout_pct"] == 85.0
        assert result["spp_rate"] == 30.0
        assert result["to_pay_rate"] == 60.0

    def test_cost_total_uses_buyout(self):
        """cost_total = cost_price * orders_count * buyout."""
        result = compute_profit_bdr(
            orders_sum=100000,
            orders_count=50,
            adv_sum=0,
            cost_price=500,
            bdr=self._default_bdr(),
            tax_info=self._default_tax(),
        )
        expected_cost = 500 * 50 * 0.85
        assert result["cost_total"] == round(expected_cost, 2)

    def test_zero_orders(self):
        """Zero orders -> zero everything."""
        result = compute_profit_bdr(
            orders_sum=0,
            orders_count=0,
            adv_sum=0,
            cost_price=500,
            bdr=self._default_bdr(),
            tax_info=self._default_tax(),
        )
        assert result["revenue"] == 0
        assert result["cost_total"] == 0
        assert result["profit"] == 0

    def test_commission_rate(self):
        """Commission rate = (1 - to_pay_rate) * 100."""
        result = compute_profit_bdr(
            orders_sum=100000,
            orders_count=50,
            adv_sum=0,
            cost_price=0,
            bdr=self._default_bdr(),
            tax_info=self._default_tax(),
        )
        assert result["commission_rate"] == 40.0  # (1 - 0.6) * 100

    def test_margin_zero_revenue(self):
        """Zero revenue -> margin = 0."""
        bdr = BdrRates(to_pay_rate=0.6, spp_rate=0.3, buyout_pct=0.0)
        result = compute_profit_bdr(
            orders_sum=100000,
            orders_count=50,
            adv_sum=0,
            cost_price=0,
            bdr=bdr,
            tax_info=self._default_tax(),
        )
        assert result["margin"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _compute_tax (internal helper)
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeTaxInternal:
    """Test internal tax computation function."""

    def test_usn_income_simple(self):
        """USN income: tax = nds + income * usn_rate."""
        tax_info = {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}
        tax = _compute_tax(income=100000, tax_info=tax_info, wb_commission=20000, adv_sum=5000, cost_total=30000)
        assert tax == 6000.0  # 100000 * 0.06

    def test_usn_income_expense_vat_with_cost(self):
        """USN income-expense with cost deduction."""
        tax_info = {"usn_rate": 15, "nds_rate": 5, "tax_regime": "usn_income_expense_vat", "cost_as_expense": True}
        tax = _compute_tax(income=100000, tax_info=tax_info, wb_commission=20000, adv_sum=5000, cost_total=30000)

        nds = 100000 * 0.05 / 1.05
        expenses = 20000 + 5000 + 30000
        tax_base = 100000 - nds - expenses
        usn = max(tax_base * 0.15, 0)
        expected = nds + usn
        assert round(tax, 2) == round(expected, 2)

    def test_zero_rates(self):
        """Zero rates -> zero tax."""
        tax_info = {"usn_rate": 0, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}
        tax = _compute_tax(income=100000, tax_info=tax_info, wb_commission=20000, adv_sum=5000, cost_total=30000)
        assert tax == 0
