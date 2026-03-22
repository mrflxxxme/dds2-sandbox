"""
Tests for stock_analytics_service — pure computation helpers.
No DB required — tests compute_days_left, compute_trend_pct,
classify_traffic_light, build_traffic_light_counts.
"""

import pytest

from backend.services.stock_analytics_service import (
    build_traffic_light_counts,
    classify_traffic_light,
    compute_days_left,
    compute_trend_pct,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Tests: compute_days_left
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeDaysLeft:
    """Verify days left calculation."""

    def test_normal_case(self):
        """100 stocks / 10 avg daily = 10 days."""
        assert compute_days_left(100, 10) == 10

    def test_fractional_result(self):
        """150 stocks / 7 avg daily = 21 days (int division)."""
        assert compute_days_left(150, 7) == 21

    def test_zero_avg_daily_with_stock(self):
        """Zero sales but stock exists → 999 days (infinite, green)."""
        assert compute_days_left(100, 0) == 999

    def test_zero_avg_daily_no_stock(self):
        """Zero sales and zero stock → 0 days."""
        assert compute_days_left(0, 0) == 0

    def test_negative_avg_daily(self):
        """Negative avg with stock → 999 days."""
        assert compute_days_left(100, -5) == 999

    def test_zero_stocks(self):
        """Zero stock → 0 days."""
        assert compute_days_left(0, 10) == 0

    def test_negative_stocks(self):
        """Negative stock → 0 days."""
        assert compute_days_left(-10, 5) == 0

    def test_both_zero(self):
        """Both zero → 0 days."""
        assert compute_days_left(0, 0) == 0

    def test_large_stock_low_demand(self):
        """1000 stocks / 0.5 avg daily = 2000 days."""
        assert compute_days_left(1000, 0.5) == 2000

    def test_very_small_avg(self):
        """10 stocks / 0.01 avg daily = 1000 days."""
        assert compute_days_left(10, 0.01) == 1000


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: compute_trend_pct
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeTrendPct:
    """Verify trend percentage calculation."""

    def test_positive_trend(self):
        """15 vs 10 = +50%."""
        assert compute_trend_pct(15, 10) == 50.0

    def test_negative_trend(self):
        """5 vs 10 = -50%."""
        assert compute_trend_pct(5, 10) == -50.0

    def test_no_change(self):
        """10 vs 10 = 0%."""
        assert compute_trend_pct(10, 10) == 0.0

    def test_zero_prev(self):
        """Previous period zero → 0% (no division by zero)."""
        assert compute_trend_pct(10, 0) == 0.0

    def test_negative_prev(self):
        """Negative previous → 0%."""
        assert compute_trend_pct(10, -5) == 0.0

    def test_both_zero(self):
        """Both zero → 0%."""
        assert compute_trend_pct(0, 0) == 0.0

    def test_rounding(self):
        """Result should be rounded to 1 decimal."""
        # 7/3 ≈ 2.333, prev=3 → ((2.333 - 3) / 3) * 100 = -22.2%
        result = compute_trend_pct(7 / 3, 3)
        assert result == pytest.approx(-22.2, abs=0.1)

    def test_double_growth(self):
        """20 vs 10 = 100% growth."""
        assert compute_trend_pct(20, 10) == 100.0


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: classify_traffic_light
# ═══════════════════════════════════════════════════════════════════════════════


class TestClassifyTrafficLight:
    """Verify traffic light classification boundaries."""

    def test_zero_days_red(self):
        assert classify_traffic_light(0) == "red"

    def test_6_days_red(self):
        assert classify_traffic_light(6) == "red"

    def test_7_days_orange(self):
        """Boundary: 7 → orange."""
        assert classify_traffic_light(7) == "orange"

    def test_14_days_orange(self):
        """Boundary: 14 → orange."""
        assert classify_traffic_light(14) == "orange"

    def test_15_days_yellow(self):
        """Boundary: 15 → yellow."""
        assert classify_traffic_light(15) == "yellow"

    def test_29_days_yellow(self):
        """Boundary: 29 → yellow."""
        assert classify_traffic_light(29) == "yellow"

    def test_30_days_green(self):
        """Boundary: 30 → green."""
        assert classify_traffic_light(30) == "green"

    def test_100_days_green(self):
        assert classify_traffic_light(100) == "green"


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: build_traffic_light_counts
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildTrafficLightCounts:
    """Verify traffic light counting."""

    def test_empty_list(self):
        result = build_traffic_light_counts([])
        assert result == {"red": 0, "orange": 0, "yellow": 0, "green": 0}

    def test_all_red(self):
        articles = [{"traffic_light": "red"}, {"traffic_light": "red"}]
        result = build_traffic_light_counts(articles)
        assert result["red"] == 2
        assert result["orange"] == 0

    def test_mixed(self):
        articles = [
            {"traffic_light": "red"},
            {"traffic_light": "orange"},
            {"traffic_light": "yellow"},
            {"traffic_light": "green"},
            {"traffic_light": "green"},
        ]
        result = build_traffic_light_counts(articles)
        assert result == {"red": 1, "orange": 1, "yellow": 1, "green": 2}

    def test_missing_traffic_light_defaults_green(self):
        """Articles without traffic_light key default to green."""
        articles = [{"other_key": "val"}, {"traffic_light": "red"}]
        result = build_traffic_light_counts(articles)
        assert result["green"] == 1
        assert result["red"] == 1

    def test_single_article(self):
        articles = [{"traffic_light": "orange"}]
        result = build_traffic_light_counts(articles)
        assert result["orange"] == 1
        assert sum(result.values()) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Integration-style: verify full pipeline with mock data
# ═══════════════════════════════════════════════════════════════════════════════


class TestStockAnalyticsPipeline:
    """End-to-end pipeline test using pure functions only (no DB)."""

    def _build_article(self, nm_id: int, vendor_code: str, stocks: int, avg_daily: float, prev_avg: float):
        """Build an article dict as get_stock_analytics would."""
        days_left = compute_days_left(stocks, avg_daily)
        trend_pct = compute_trend_pct(avg_daily, prev_avg)
        traffic = classify_traffic_light(days_left)
        return {
            "nm_id": nm_id,
            "vendor_code": vendor_code,
            "stocks_wb": stocks,
            "avg_daily": avg_daily,
            "days_left": days_left,
            "trend_pct": trend_pct,
            "traffic_light": traffic,
            "orders_30d": int(avg_daily * 30),
        }

    def test_critical_stock_detection(self):
        """Article with only 3 days left should be red."""
        a = self._build_article(1, "ART-001", 15, 5.0, 4.0)
        assert a["days_left"] == 3
        assert a["traffic_light"] == "red"

    def test_healthy_stock(self):
        """Article with 50 days left should be green."""
        a = self._build_article(2, "ART-002", 500, 10.0, 10.0)
        assert a["days_left"] == 50
        assert a["traffic_light"] == "green"

    def test_no_sales_no_stock(self):
        """Zero stock, zero sales → red (0 days)."""
        a = self._build_article(3, "ART-003", 0, 0.0, 0.0)
        assert a["days_left"] == 0
        assert a["traffic_light"] == "red"

    def test_growing_trend(self):
        """Sales growing 50% → positive trend."""
        a = self._build_article(4, "ART-004", 200, 15.0, 10.0)
        assert a["trend_pct"] == 50.0

    def test_declining_trend(self):
        """Sales declining → negative trend."""
        a = self._build_article(5, "ART-005", 200, 5.0, 10.0)
        assert a["trend_pct"] == -50.0

    def test_traffic_light_counts_pipeline(self):
        """Build multiple articles and count traffic light."""
        articles = [
            self._build_article(1, "A1", 3, 1.0, 1.0),  # 3 days → red
            self._build_article(2, "A2", 10, 1.0, 1.0),  # 10 days → orange
            self._build_article(3, "A3", 20, 1.0, 1.0),  # 20 days → yellow
            self._build_article(4, "A4", 50, 1.0, 1.0),  # 50 days → green
            self._build_article(5, "A5", 100, 1.0, 1.0),  # 100 days → green
        ]
        counts = build_traffic_light_counts(articles)
        assert counts == {"red": 1, "orange": 1, "yellow": 1, "green": 2}

    def test_sort_by_days_left(self):
        """Articles should be sortable by days_left ascending."""
        articles = [
            self._build_article(1, "A1", 100, 1.0, 1.0),  # 100 days
            self._build_article(2, "A2", 3, 1.0, 1.0),  # 3 days
            self._build_article(3, "A3", 20, 1.0, 1.0),  # 20 days
        ]
        articles.sort(key=lambda a: a["days_left"])
        assert [a["nm_id"] for a in articles] == [2, 3, 1]

    def test_most_critical_determination(self):
        """Most critical is the article with lowest days_left."""
        articles = [
            self._build_article(1, "SAFE", 100, 1.0, 1.0),
            self._build_article(2, "CRITICAL", 2, 1.0, 1.0),
            self._build_article(3, "WARNING", 10, 1.0, 1.0),
        ]
        articles.sort(key=lambda a: a["days_left"])
        critical = [a for a in articles if a["traffic_light"] == "red"]
        most_critical = critical[0] if critical else None
        assert most_critical is not None
        assert most_critical["vendor_code"] == "CRITICAL"
        assert most_critical["days_left"] == 2
