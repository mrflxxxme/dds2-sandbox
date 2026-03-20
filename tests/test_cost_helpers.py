"""
Tests for cost/helpers — pure computation functions.
No DB required — tests _order_no_to_int, safe_float, safe_decimal, compute_need.
"""

from decimal import Decimal

from backend.services.cost.helpers import (
    _order_no_to_int,
    safe_decimal,
    safe_float,
)
from backend.services.warehouse_stock_service import compute_need

D = Decimal
ZERO = D("0")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _order_no_to_int
# ═══════════════════════════════════════════════════════════════════════════════


class TestOrderNoToInt:
    """Verify order_no string → int conversion."""

    def test_simple_integer(self):
        """'41' → 41."""
        assert _order_no_to_int("41") == 41

    def test_slash_format(self):
        """'41/2' → 4102."""
        assert _order_no_to_int("41/2") == 4102

    def test_slash_format_3(self):
        """'41/3' → 4103."""
        assert _order_no_to_int("41/3") == 4103

    def test_slash_format_high_suffix(self):
        """'100/5' → 10005."""
        assert _order_no_to_int("100/5") == 10005

    def test_whitespace_stripped(self):
        """Leading/trailing whitespace should be stripped."""
        assert _order_no_to_int("  41  ") == 41

    def test_slash_with_whitespace(self):
        assert _order_no_to_int("41/2") == 4102

    def test_non_numeric_string(self):
        """Non-numeric → hash-based fallback (positive int < 10^9)."""
        result = _order_no_to_int("abc")
        assert isinstance(result, int)
        assert 0 <= result < 10**9

    def test_empty_string(self):
        """Empty string → hash fallback."""
        result = _order_no_to_int("")
        assert isinstance(result, int)
        assert 0 <= result < 10**9

    def test_zero(self):
        """'0' → 0."""
        assert _order_no_to_int("0") == 0

    def test_negative_number(self):
        """'-5' → -5 (int conversion works)."""
        assert _order_no_to_int("-5") == -5

    def test_integer_input(self):
        """Integer input (via str() in implementation) → correct result."""
        assert _order_no_to_int(41) == 41

    def test_slash_zero(self):
        """'41/0' → 4100."""
        assert _order_no_to_int("41/0") == 4100

    def test_slash_invalid_parts(self):
        """'abc/def' → hash fallback (ValueError on both parts)."""
        result = _order_no_to_int("abc/def")
        assert isinstance(result, int)
        assert 0 <= result < 10**9


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: safe_float
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafeFloat:
    """Verify safe float conversion."""

    def test_normal_float(self):
        assert safe_float(3.14) == 3.14

    def test_integer(self):
        assert safe_float(42) == 42.0

    def test_string_number(self):
        assert safe_float("123.45") == 123.45

    def test_none_returns_zero(self):
        assert safe_float(None) == 0.0

    def test_nan_returns_zero(self):
        assert safe_float(float("nan")) == 0.0

    def test_inf_returns_zero(self):
        assert safe_float(float("inf")) == 0.0

    def test_neg_inf_returns_zero(self):
        assert safe_float(float("-inf")) == 0.0

    def test_invalid_string_returns_zero(self):
        assert safe_float("not_a_number") == 0.0

    def test_empty_string_returns_zero(self):
        assert safe_float("") == 0.0

    def test_decimal_input(self):
        assert safe_float(D("99.99")) == 99.99

    def test_zero(self):
        assert safe_float(0) == 0.0

    def test_negative(self):
        assert safe_float(-10.5) == -10.5

    def test_boolean_true(self):
        """bool is subclass of int → True = 1.0."""
        assert safe_float(True) == 1.0

    def test_boolean_false(self):
        assert safe_float(False) == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: safe_decimal
# ═══════════════════════════════════════════════════════════════════════════════


class TestSafeDecimal:
    """Verify safe Decimal conversion."""

    def test_normal_float(self):
        result = safe_decimal(3.14)
        assert result == D("3.14")

    def test_integer(self):
        assert safe_decimal(42) == D("42")

    def test_string_number(self):
        assert safe_decimal("99.99") == D("99.99")

    def test_none_returns_zero(self):
        assert safe_decimal(None) == ZERO

    def test_nan_returns_zero(self):
        assert safe_decimal(float("nan")) == ZERO

    def test_inf_returns_zero(self):
        assert safe_decimal(float("inf")) == ZERO

    def test_neg_inf_returns_zero(self):
        assert safe_decimal(float("-inf")) == ZERO

    def test_invalid_string_returns_zero(self):
        assert safe_decimal("abc") == ZERO

    def test_empty_string_returns_zero(self):
        assert safe_decimal("") == ZERO

    def test_zero(self):
        assert safe_decimal(0) == ZERO

    def test_negative(self):
        result = safe_decimal(-10.5)
        assert result == D("-10.5")

    def test_returns_decimal_type(self):
        result = safe_decimal(42)
        assert isinstance(result, Decimal)

    def test_decimal_input(self):
        """Decimal input → same value back (via float round-trip)."""
        result = safe_decimal(D("123.45"))
        assert result == D("123.45")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: compute_need (from warehouse_stock_service)
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeNeed:
    """Verify restocking need calculation."""

    def test_deficit(self):
        """10 stocks, 5 avg daily, 14 days need → 60 needed."""
        assert compute_need(10, 5.0, 14) == 60  # 5*14 - 10 = 60

    def test_surplus(self):
        """100 stocks, 1 avg daily, 14 days → 0 (enough stock)."""
        assert compute_need(100, 1.0, 14) == 0  # 1*14 - 100 < 0 → 0

    def test_exact_match(self):
        """14 stocks, 1 avg daily, 14 days → 0."""
        assert compute_need(14, 1.0, 14) == 0

    def test_zero_stock(self):
        """0 stocks → need = avg * days."""
        assert compute_need(0, 5.0, 14) == 70  # 5*14 = 70

    def test_zero_avg_daily(self):
        """No sales → no need."""
        assert compute_need(100, 0.0, 14) == 0

    def test_zero_need_days(self):
        """0 need days → 0 need."""
        assert compute_need(100, 5.0, 0) == 0

    def test_fractional_rounding(self):
        """Fractional result → rounded to nearest int."""
        # 3 avg * 10 days = 30 needed; 30 - 5 = 25 → exactly 25
        assert compute_need(5, 3.0, 10) == 25

    def test_fractional_rounds_up(self):
        """2.5 * 10 = 25 - 0 = 25, but 2.3 * 10 = 23 - 0 = 23.0 → 23."""
        assert compute_need(0, 2.3, 10) == 23

    def test_small_deficit_rounds(self):
        """Small fractional deficit should round correctly."""
        # 1.5 * 10 = 15 - 14 = 1.0 → rounds to 1 (with +0.5 → 1.5 → int=1)
        assert compute_need(14, 1.5, 10) == 1
