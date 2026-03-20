"""
Tests for _linear_regression_trend — pure computation function.
No DB required — tests linear regression trend % calculation.
"""

import pytest

from backend.services.funnel.product_trends import _linear_regression_trend

# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _linear_regression_trend
# ═══════════════════════════════════════════════════════════════════════════════


class TestLinearRegressionTrend:
    """Verify linear regression trend calculation."""

    def test_empty_list(self):
        """Empty input → 0."""
        assert _linear_regression_trend([]) == 0.0

    def test_single_value(self):
        """Single value → 0 (need >= 2 for regression)."""
        assert _linear_regression_trend([100]) == 0.0

    def test_constant_values(self):
        """All same values → 0% trend (no slope)."""
        assert _linear_regression_trend([10, 10, 10, 10]) == 0.0

    def test_increasing_values(self):
        """Strictly increasing → positive trend."""
        result = _linear_regression_trend([10, 20, 30, 40, 50])
        assert result > 0

    def test_decreasing_values(self):
        """Strictly decreasing → negative trend."""
        result = _linear_regression_trend([50, 40, 30, 20, 10])
        assert result < 0

    def test_symmetric_increase_decrease(self):
        """Increasing and decreasing by same slope → same magnitude."""
        up = _linear_regression_trend([10, 20, 30, 40, 50])
        down = _linear_regression_trend([50, 40, 30, 20, 10])
        assert abs(up + down) < 0.1  # Should be symmetric

    def test_two_values_doubling(self):
        """[10, 20] → trend should be positive ~50%."""
        result = _linear_regression_trend([10, 20])
        # mean = 15, predicted_last = 20, (20-15)/15*100 = 33.3%
        assert result == pytest.approx(33.3, abs=0.1)

    def test_linear_10_20_30(self):
        """[10, 20, 30] → perfect linear increase."""
        result = _linear_regression_trend([10, 20, 30])
        # mean = 20, predicted_last = 30, (30-20)/20*100 = 50%
        assert result == 50.0

    def test_all_zeros(self):
        """All zeros → 0 (mean is zero, safe return)."""
        assert _linear_regression_trend([0, 0, 0, 0]) == 0.0

    def test_near_zero_mean(self):
        """Values close to zero but not exactly → 0 (below threshold)."""
        assert _linear_regression_trend([0.0000000001, 0.0000000002]) == 0.0

    def test_negative_values(self):
        """Negative values → still computes valid trend."""
        result = _linear_regression_trend([-30, -20, -10])
        # Increasing (becoming less negative) → positive trend
        # mean = -20, predicted = -10, (-10 - (-20)) / |-20| * 100 = 50%
        assert result == 50.0

    def test_mixed_positive_negative(self):
        """Mix of positive and negative values."""
        result = _linear_regression_trend([-10, 0, 10])
        # mean = 0 → near zero mean → returns 0
        assert result == 0.0

    def test_returns_rounded(self):
        """Result should be rounded to 1 decimal."""
        result = _linear_regression_trend([1, 2, 3, 4, 5, 6, 7])
        # Should be a float rounded to 1 decimal
        assert result == round(result, 1)

    def test_large_values(self):
        """Large values should work without overflow."""
        result = _linear_regression_trend([1000000, 2000000, 3000000])
        assert result == 50.0  # Same as [10, 20, 30] proportionally

    def test_noisy_uptrend(self):
        """Noisy upward trend → positive trend."""
        result = _linear_regression_trend([10, 15, 12, 18, 16, 22, 20, 25])
        assert result > 0

    def test_noisy_downtrend(self):
        """Noisy downward trend → negative trend."""
        result = _linear_regression_trend([25, 20, 22, 16, 18, 12, 15, 10])
        assert result < 0

    def test_spike_at_end(self):
        """Flat then spike → positive trend."""
        result = _linear_regression_trend([10, 10, 10, 10, 10, 50])
        assert result > 0

    def test_drop_at_end(self):
        """Flat then drop → negative trend."""
        result = _linear_regression_trend([50, 50, 50, 50, 50, 10])
        assert result < 0

    def test_two_equal_values(self):
        """Two equal values → 0% trend."""
        assert _linear_regression_trend([42, 42]) == 0.0

    def test_float_precision(self):
        """Floating point values should be handled correctly."""
        result = _linear_regression_trend([1.5, 2.5, 3.5, 4.5])
        # Perfect linear → predicted_last = 4.5, mean = 3.0
        # (4.5 - 3.0) / 3.0 * 100 = 50%
        assert result == 50.0
