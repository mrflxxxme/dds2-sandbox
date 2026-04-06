"""
Tests for stock_forecast_service.py — pure computation helpers (no DB required).
"""

from datetime import date, timedelta

from backend.services.stock_forecast_service import (
    _build_forecast_with_transit,
    build_traffic_light_counts,
    classify_traffic_light,
    compute_days_left,
    compute_trend_pct,
)

# ═══════════════════════════════════════════════════════════════════════════════
# compute_days_left
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeDaysLeft:
    def test_basic(self):
        assert compute_days_left(100, 10.0) == 10

    def test_zero_sales(self):
        """No sales with stock => 999 (infinity stand-in)."""
        assert compute_days_left(50, 0.0) == 999

    def test_zero_sales_zero_stock(self):
        assert compute_days_left(0, 0.0) == 0

    def test_zero_stock_with_sales(self):
        assert compute_days_left(0, 5.0) == 0

    def test_negative_sales(self):
        assert compute_days_left(100, -1.0) == 999

    def test_fractional_sales(self):
        assert compute_days_left(10, 0.5) == 20

    def test_large_stock(self):
        assert compute_days_left(10000, 100.0) == 100


# ═══════════════════════════════════════════════════════════════════════════════
# compute_trend_pct
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeTrendPct:
    def test_increase(self):
        assert compute_trend_pct(15.0, 10.0) == 50.0

    def test_decrease(self):
        assert compute_trend_pct(5.0, 10.0) == -50.0

    def test_no_change(self):
        assert compute_trend_pct(10.0, 10.0) == 0.0

    def test_prev_zero(self):
        assert compute_trend_pct(10.0, 0.0) == 0.0

    def test_both_zero(self):
        assert compute_trend_pct(0.0, 0.0) == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# classify_traffic_light
# ═══════════════════════════════════════════════════════════════════════════════


class TestClassifyTrafficLight:
    def test_red(self):
        assert classify_traffic_light(0) == "red"
        assert classify_traffic_light(6) == "red"

    def test_orange(self):
        assert classify_traffic_light(7) == "orange"
        assert classify_traffic_light(14) == "orange"

    def test_yellow(self):
        assert classify_traffic_light(15) == "yellow"
        assert classify_traffic_light(29) == "yellow"

    def test_green(self):
        assert classify_traffic_light(30) == "green"
        assert classify_traffic_light(999) == "green"


# ═══════════════════════════════════════════════════════════════════════════════
# build_traffic_light_counts
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildTrafficLightCounts:
    def test_empty(self):
        counts = build_traffic_light_counts([])
        assert counts == {"red": 0, "orange": 0, "yellow": 0, "green": 0}

    def test_mixed(self):
        articles = [
            {"traffic_light": "red"},
            {"traffic_light": "red"},
            {"traffic_light": "orange"},
            {"traffic_light": "green"},
        ]
        counts = build_traffic_light_counts(articles)
        assert counts["red"] == 2
        assert counts["orange"] == 1
        assert counts["yellow"] == 0
        assert counts["green"] == 1

    def test_missing_key_defaults_green(self):
        articles = [{"nm_id": 1}]  # no traffic_light key
        counts = build_traffic_light_counts(articles)
        assert counts["green"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# _build_forecast_with_transit
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildForecastWithTransit:
    def test_no_deliveries(self):
        forecast = _build_forecast_with_transit(
            stocks_wb=100, avg_daily=10.0, transit_entries=[], forecast_days=10, today_date=date.today()
        )
        assert len(forecast) == 10
        # Day 0: 100-10=90, Day 1: 90-10=80, ...
        assert forecast[0] == 90
        assert forecast[1] == 80
        assert forecast[9] == 0  # 100-100=0

    def test_with_future_delivery(self):
        today = date.today()
        delivery_date = today + timedelta(days=3)
        forecast = _build_forecast_with_transit(
            stocks_wb=50,
            avg_daily=10.0,
            transit_entries=[{"qty": 100, "delivery_date": delivery_date}],
            forecast_days=10,
            today_date=today,
        )
        # Day 0: 50-10=40, Day 1: 40-10=30, Day 2: 30-10=20
        # Day 3: 20+100-10=110, Day 4: 110-10=100, ...
        assert forecast[0] == 40
        assert forecast[2] == 20
        assert forecast[3] == 110

    def test_past_due_delivery(self):
        today = date.today()
        past_date = today - timedelta(days=2)
        forecast = _build_forecast_with_transit(
            stocks_wb=50,
            avg_daily=10.0,
            transit_entries=[{"qty": 30, "delivery_date": past_date}],
            forecast_days=5,
            today_date=today,
        )
        # Past due: 50+30=80 initial. Day 0: 80-10=70
        assert forecast[0] == 70

    def test_stock_never_goes_negative(self):
        forecast = _build_forecast_with_transit(
            stocks_wb=10, avg_daily=20.0, transit_entries=[], forecast_days=5, today_date=date.today()
        )
        assert all(f >= 0 for f in forecast)

    def test_multiple_deliveries(self):
        today = date.today()
        entries = [
            {"qty": 50, "delivery_date": today + timedelta(days=1)},
            {"qty": 30, "delivery_date": today + timedelta(days=1)},
        ]
        forecast = _build_forecast_with_transit(
            stocks_wb=10, avg_daily=5.0, transit_entries=entries, forecast_days=5, today_date=today
        )
        # Day 0: 10-5=5, Day 1: 5+80-5=80
        assert forecast[0] == 5
        assert forecast[1] == 80
