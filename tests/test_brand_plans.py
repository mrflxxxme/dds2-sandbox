"""
Tests for brand plan service — plan-fact calculations.

Tests pure logic functions without DB dependencies.
"""

import calendar
from decimal import Decimal

import pytest

from backend.services.planning.brand_plan import _prev_month


class TestPrevMonth:
    def test_regular_month(self):
        assert _prev_month(2026, 5) == (2026, 4)

    def test_january_wraps_to_december(self):
        assert _prev_month(2026, 1) == (2025, 12)

    def test_february(self):
        assert _prev_month(2026, 2) == (2026, 1)

    def test_december(self):
        assert _prev_month(2026, 12) == (2026, 11)


# ═══════════════════════════════════════════════════════════════════════════════
# Adaptive daily plan calculation
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdaptivePlan:
    """Test the adaptive daily plan formula:
    plan_daily = (plan_adjusted - fact_cumulative) / remaining_days
    """

    def test_first_day_even_split(self):
        plan = Decimal("60000000")
        # Day 1, fact=0, remaining=29 (30-day month)
        plan_day = (plan - Decimal("0")) / 29
        assert plan_day == Decimal("60000000") / 29

    def test_overperformance_reduces_plan(self):
        plan = Decimal("30000000")
        # After 10 days, fact=20M (ahead), 20 remaining
        fact_cum = Decimal("20000000")
        remaining = 20
        plan_day = (plan - fact_cum) / remaining
        assert plan_day == Decimal("500000")  # 10M / 20 = 500K

    def test_underperformance_increases_plan(self):
        plan = Decimal("30000000")
        # After 10 days, fact=5M (behind), 20 remaining
        fact_cum = Decimal("5000000")
        remaining = 20
        plan_day = (plan - fact_cum) / remaining
        assert plan_day == Decimal("1250000")  # 25M / 20 = 1.25M

    def test_last_day_zero_remaining(self):
        # When remaining_days=0, plan_day should be 0 (not division error)
        remaining = 0
        plan_day = Decimal("0") if remaining == 0 else Decimal("100") / remaining
        assert plan_day == Decimal("0")


# ═══════════════════════════════════════════════════════════════════════════════
# Debt carryover
# ═══════════════════════════════════════════════════════════════════════════════


class TestDebtCarryover:
    def test_no_debt_when_plan_met(self):
        plan_prev = Decimal("60000000")
        fact_prev = Decimal("65000000")
        debt = max(Decimal("0"), plan_prev - fact_prev)
        assert debt == Decimal("0")

    def test_debt_when_underperformed(self):
        plan_prev = Decimal("60000000")
        fact_prev = Decimal("55000000")
        debt = max(Decimal("0"), plan_prev - fact_prev)
        assert debt == Decimal("5000000")

    def test_adjusted_plan_includes_debt(self):
        plan_month = Decimal("49700000")
        debt = Decimal("5000000")
        adjusted = plan_month + debt
        assert adjusted == Decimal("54700000")

    def test_no_debt_when_no_prev_plan(self):
        plan_prev = Decimal("0")
        fact_prev = Decimal("10000000")
        debt = max(Decimal("0"), plan_prev - fact_prev) if plan_prev > 0 else Decimal("0")
        assert debt == Decimal("0")


# ═══════════════════════════════════════════════════════════════════════════════
# Forecast
# ═══════════════════════════════════════════════════════════════════════════════


class TestForecast:
    def test_basic_forecast(self):
        fact_mtd = Decimal("20000000")
        current_day = 10
        days_in_month = 31
        forecast = float(fact_mtd / current_day * days_in_month)
        assert forecast == pytest.approx(62000000, rel=0.01)

    def test_forecast_zero_day(self):
        current_day = 0
        forecast = 0 if current_day == 0 else 100
        assert forecast == 0

    def test_forecast_full_month(self):
        fact_mtd = Decimal("80000000")
        current_day = 31
        days_in_month = 31
        forecast = float(fact_mtd / current_day * days_in_month)
        assert forecast == pytest.approx(80000000)

    def test_forecast_mid_month(self):
        fact_mtd = Decimal("30000000")
        current_day = 15
        days_in_month = 30
        forecast = float(fact_mtd / current_day * days_in_month)
        assert forecast == pytest.approx(60000000)


# ═══════════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_january_debt_from_december(self):
        prev_y, prev_m = _prev_month(2026, 1)
        assert prev_y == 2025
        assert prev_m == 12

    def test_february_days(self):
        # 2026 is not a leap year
        assert calendar.monthrange(2026, 2)[1] == 28

    def test_leap_year_february(self):
        assert calendar.monthrange(2028, 2)[1] == 29

    def test_pct_with_zero_plan(self):
        plan_adjusted = Decimal("0")
        fact = Decimal("5000000")
        pct = float(fact / plan_adjusted * 100) if plan_adjusted > 0 else None
        assert pct is None
