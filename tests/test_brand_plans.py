"""
Tests for brand plan service — plan-fact calculations.

Tests pure logic functions without DB dependencies.
"""

import calendar
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from backend.services.planning.brand_plan import (
    _get_last_fact_date,
    _prev_month,
    get_plan_fact_brands,
)


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


# ═══════════════════════════════════════════════════════════════════════════════
# Forecast uses last_fact_day, not today.day (regression)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Bug: the forecast denominator was `today.day`. In UTC containers or when WB
# data lagged a day, this divided fact_mtd by a day with no data, producing an
# off-by-one forecast (fact * 30 / 11 instead of fact * 30 / 10).
#
# Fix: current_day is now driven by MAX(COALESCE(sale_dt, rr_dt)) from
# wb_finance_rows — the latest day with actual data.


async def _insert_wb_row(db, project_id: int, brand: str, sale_dt: date, rrd_id: int, price: int):
    """Insert a minimal wb_finance_rows row for a sale on a given date."""
    await db.execute(
        text(
            """
            INSERT INTO wb_finance_rows (
                project_id, realizationreport_id, rrd_id,
                date_from, date_to, sa_name, nm_id, brand_name,
                sale_dt, rr_dt, doc_type_name, quantity,
                retail_price_withdisc_rub, retail_amount, ppvz_for_pay,
                ppvz_sales_commission, delivery_rub, penalty, additional_payment,
                storage_fee, acceptance, deduction, ppvz_reward,
                rebill_logistic_cost, ppvz_vw, ppvz_vw_nds, synced_at
            ) VALUES (
                :pid, 0, :rrd,
                :sale_dt, :sale_dt, 'ART-1', 1, :brand,
                :sale_dt, :sale_dt, 'Продажа', 1,
                :price, :price, :price,
                0, 0, 0, 0,
                0, 0, 0, 0,
                0, 0, 0, NOW()
            )
            """
        ),
        {"pid": project_id, "rrd": rrd_id, "sale_dt": sale_dt, "brand": brand, "price": price},
    )


class TestLastFactDateHelper:
    """_get_last_fact_date returns the last day with actual WB data, or None."""

    @pytest.mark.asyncio
    async def test_returns_max_date_within_range(self, db_session, project):
        month_start = date(2026, 4, 1)
        month_end = date(2026, 4, 30)
        await _insert_wb_row(db_session, project.id, "Brand-A", date(2026, 4, 5), 10001, 1000)
        await _insert_wb_row(db_session, project.id, "Brand-A", date(2026, 4, 10), 10002, 1000)
        await _insert_wb_row(db_session, project.id, "Brand-A", date(2026, 4, 7), 10003, 1000)
        await db_session.commit()

        last = await _get_last_fact_date(db_session, project.id, month_start, month_end)
        assert last == date(2026, 4, 10)

    @pytest.mark.asyncio
    async def test_returns_none_when_no_facts(self, db_session, project):
        last = await _get_last_fact_date(db_session, project.id, date(2026, 4, 1), date(2026, 4, 30))
        assert last is None

    @pytest.mark.asyncio
    async def test_respects_project_id_isolation(self, db_session, project, other_project):
        await _insert_wb_row(db_session, other_project.id, "Brand-X", date(2026, 4, 15), 20001, 1000)
        await db_session.commit()

        last = await _get_last_fact_date(db_session, project.id, date(2026, 4, 1), date(2026, 4, 30))
        assert last is None


class TestForecastCurrentDayFromData:
    """get_plan_fact_brands anchors current_day on last WB data date, not today.day.

    The key guarantee: even if today.day == 11, if data only reaches day 10,
    forecast must be fact * 30 / 10, not fact * 30 / 11.
    """

    @pytest.mark.asyncio
    async def test_current_day_from_last_fact_not_today(self, db_session, project, monkeypatch):
        # Pretend "today" is 2026-04-11 so we hit the current-month branch
        class _FakeDate(date):
            @classmethod
            def today(cls):
                return date(2026, 4, 11)

        monkeypatch.setattr("backend.services.planning.brand_plan.date", _FakeDate)

        # Plan: 30M for April
        await db_session.execute(
            text(
                "INSERT INTO brand_plans (project_id, brand, year, month, plan_amount, created_at) "
                "VALUES (:pid, 'Brand-L', 2026, 4, 30000000, NOW())"
            ),
            {"pid": project.id},
        )
        # Facts total 10M, with last fact on the 10th (one day before "today")
        await _insert_wb_row(db_session, project.id, "Brand-L", date(2026, 4, 3), 30001, 4000000)
        await _insert_wb_row(db_session, project.id, "Brand-L", date(2026, 4, 10), 30002, 6000000)
        await db_session.commit()

        rows = await get_plan_fact_brands(db_session, project.id, 2026, 4)
        assert len(rows) == 1
        row = rows[0]

        # Last fact day is the 10th, NOT today.day == 11
        assert row["current_day"] == 10
        assert row["fact_mtd"] == pytest.approx(10_000_000)
        # Forecast = 10M * 30 / 10 = 30M (not 10M * 30 / 11 ~= 27.27M)
        assert row["forecast"] == pytest.approx(30_000_000, rel=1e-6)

    @pytest.mark.asyncio
    async def test_no_data_yields_zero_current_day(self, db_session, project, monkeypatch):
        class _FakeDate(date):
            @classmethod
            def today(cls):
                return date(2026, 4, 11)

        monkeypatch.setattr("backend.services.planning.brand_plan.date", _FakeDate)

        await db_session.execute(
            text(
                "INSERT INTO brand_plans (project_id, brand, year, month, plan_amount, created_at) "
                "VALUES (:pid, 'Brand-Z', 2026, 4, 50000000, NOW())"
            ),
            {"pid": project.id},
        )
        await db_session.commit()

        rows = await get_plan_fact_brands(db_session, project.id, 2026, 4)
        assert len(rows) == 1
        assert rows[0]["current_day"] == 0
        assert rows[0]["forecast"] == 0
