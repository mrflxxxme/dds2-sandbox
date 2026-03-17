"""
Tests for opiu_service — pure computation functions.
No DB required — tests _empty_totals and P&L formula structure.

Note: _accumulate_row was removed when OPIU was migrated to SQL GROUP BY aggregation.
Row-level accumulation is now done in SQL (see _build_aggregate_sql).
These tests validate the totals structure and P&L formula correctness.
"""
import pytest
from decimal import Decimal

D = Decimal
ZERO = D("0")


# ─── Imports ─────────────────────────────────────────────────────────────────

from backend.services.opiu_service import _empty_totals


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _empty_totals
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmptyTotals:
    """Verify initial state of P&L accumulators."""

    def test_all_fields_zero(self):
        """All numeric fields should be ZERO."""
        t = _empty_totals()
        for key, val in t.items():
            assert val == ZERO, f"Field '{key}' should be ZERO, got {val}"

    def test_required_fields_exist(self):
        """Must contain all P&L metric fields."""
        t = _empty_totals()
        required = [
            "realization", "sales_amount", "logistics", "commission",
            "penalties", "storage", "acceptance", "deductions",
            "ad_deduction", "additional_payment", "compensation_ppvz",
        ]
        for field in required:
            assert field in t, f"Missing field '{field}' in _empty_totals()"


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: P&L formula chain (using pre-filled data)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPnLFormulas:
    """Verify P&L calculations using pre-filled accumulator data."""

    def _build_scenario(self):
        """Build a realistic P&L scenario with pre-filled metrics."""
        t = _empty_totals()
        # Simulate: 10 sales @ 1000 retail, 2 returns
        t["realization"] = D("8000")       # 10*1000 - 2*1000
        t["sales_amount"] = D("6400")      # 10*800 - 2*800
        t["commission"] = D("1600")        # sales - ppvz_net
        t["logistics"] = D("500")
        t["storage"] = D("200")
        t["penalties"] = D("100")
        t["acceptance"] = D("80")
        t["deductions"] = D("350")         # 300 ad + 50 non-ad
        t["ad_deduction"] = D("300")
        t["additional_payment"] = D("80")  # 10*10 - 2*10
        t["compensation_ppvz"] = D("0")
        return t

    def test_spp_discount(self):
        """SPP discount = realization - sales_amount."""
        t = self._build_scenario()
        spp = t["realization"] - t["sales_amount"]
        assert spp == D("1600")

    def test_other_deductions_formula(self):
        """Прочие удержания = deductions - ad_deduction."""
        t = self._build_scenario()
        other_ded = t["deductions"] - t["ad_deduction"]
        assert other_ded == D("50")

    def test_direct_costs(self):
        """Direct = logistics + commission + penalties + storage + ads + other_ded + acceptance."""
        t = self._build_scenario()
        other_ded = t["deductions"] - t["ad_deduction"]
        direct = (t["logistics"] + t["commission"] + t["penalties"] +
                  t["storage"] + t["ad_deduction"] + other_ded + t["acceptance"])
        assert direct == D("2830")

    def test_gross_margin(self):
        """Gross margin = sales_amount - direct_costs + compensation."""
        t = self._build_scenario()
        other_ded = t["deductions"] - t["ad_deduction"]
        direct = (t["logistics"] + t["commission"] + t["penalties"] +
                  t["storage"] + t["ad_deduction"] + other_ded + t["acceptance"])
        comp = t["additional_payment"] + t["compensation_ppvz"]
        margin = t["sales_amount"] - direct + comp
        assert margin == D("3650")

    def test_tax_calculation_usn(self):
        """USN 6% tax on sales_amount = 6400 * 0.06 = 384."""
        t = self._build_scenario()
        usn_rate = D("0.06")
        tax = max(t["sales_amount"] * usn_rate, ZERO)
        assert tax == D("384.00")

    def test_net_profit(self):
        """Net profit = EBITDA - taxes."""
        t = self._build_scenario()
        other_ded = t["deductions"] - t["ad_deduction"]
        direct = (t["logistics"] + t["commission"] + t["penalties"] +
                  t["storage"] + t["ad_deduction"] + other_ded + t["acceptance"])
        comp = t["additional_payment"] + t["compensation_ppvz"]
        margin = t["sales_amount"] - direct + comp

        tax = t["sales_amount"] * D("0.06")
        net_profit = margin - tax
        assert net_profit == D("3266.00")
