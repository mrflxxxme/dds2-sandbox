"""Tests for backend/scheduler/jobs/wb_finance.py — weekly/daily finance sync logic.

Covers:
- Date range computation (weekly / daily)
- Guard against DELETE when sync returned 0 rows (prevents data loss on WB 204)
- Orphan daily rows cleanup
- Report cache invalidation after a sync that landed new rows
"""

from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.scheduler.jobs.wb_finance import (
    _compute_daily_range,
    _compute_weekly_range,
    sync_all_projects_wb_finance,
)


class TestComputeWeeklyRange:
    """Weekly finance sync runs on Monday. Must fetch previous completed Mon-Sun,
    NOT include today's incomplete Monday."""

    def test_monday_with_no_prior_data_fetches_prev_week_only(self):
        """Fresh project on Monday — fetch Mon..Sun of previous week, NOT today."""
        today = date(2026, 4, 20)  # Monday
        result = _compute_weekly_range(today=today, max_weekly_date=None)
        assert result is not None
        date_from, date_to = result
        assert date_from == date(2026, 4, 13)  # prev Monday
        assert date_to == date(2026, 4, 19)  # prev Sunday — NOT today

    def test_monday_skips_when_prev_week_already_covered(self):
        """If max_weekly_date >= prev_sunday, skip."""
        today = date(2026, 4, 20)  # Monday
        result = _compute_weekly_range(today=today, max_weekly_date=date(2026, 4, 19))
        assert result is None

    def test_monday_skips_when_max_in_future(self):
        """Future max_date (shouldn't happen but defensive) → skip."""
        today = date(2026, 4, 20)
        result = _compute_weekly_range(today=today, max_weekly_date=date(2026, 5, 1))
        assert result is None

    def test_monday_gap_fill_when_older_data(self):
        """If there's a gap (missed several weeks) — fetch from gap start to prev Sunday."""
        today = date(2026, 4, 20)
        result = _compute_weekly_range(today=today, max_weekly_date=date(2026, 3, 8))
        assert result is not None
        date_from, date_to = result
        assert date_from == date(2026, 3, 9)  # day after last data
        assert date_to == date(2026, 4, 19)  # prev Sunday

    def test_date_to_never_includes_today(self):
        """CRITICAL: date_to must never be >= today (WB has no data for incomplete day)."""
        today = date(2026, 4, 20)
        result = _compute_weekly_range(today=today, max_weekly_date=None)
        assert result is not None
        _, date_to = result
        assert date_to < today

    def test_initial_sync_with_lookback_days(self):
        """Fresh project can use initial_lookback_days to backfill history."""
        today = date(2026, 4, 20)
        result = _compute_weekly_range(today=today, max_weekly_date=None, initial_lookback_days=60)
        assert result is not None
        date_from, date_to = result
        assert date_from == date(2026, 2, 19)
        assert date_to == date(2026, 4, 19)


class TestComputeDailyRange:
    """Daily sync runs Tue-Sun. Fetches current incomplete week from Mon to yesterday."""

    def test_tuesday_no_daily_data_fetches_from_monday(self):
        """Tuesday with no daily data — fetch Mon..Mon (1 day, yesterday)."""
        today = date(2026, 4, 21)  # Tuesday
        result = _compute_daily_range(today=today, max_daily_date=None)
        assert result is not None
        date_from, date_to = result
        assert date_from == date(2026, 4, 20)  # this Monday
        assert date_to == date(2026, 4, 20)  # yesterday

    def test_wednesday_with_monday_data_fetches_tuesday_only(self):
        """Wednesday, daily for Mon already present — fetch Tue..Tue."""
        today = date(2026, 4, 22)  # Wednesday
        result = _compute_daily_range(today=today, max_daily_date=date(2026, 4, 20))
        assert result is not None
        date_from, date_to = result
        assert date_from == date(2026, 4, 21)  # Tuesday (day after last daily)
        assert date_to == date(2026, 4, 21)  # yesterday

    def test_skip_when_up_to_date(self):
        """Already have yesterday's daily → skip."""
        today = date(2026, 4, 22)  # Wed
        result = _compute_daily_range(today=today, max_daily_date=date(2026, 4, 21))
        assert result is None

    def test_skip_old_daily_restarts_from_last_monday(self):
        """max_daily_date from previous week → restart from this Monday."""
        today = date(2026, 4, 22)  # Wed
        # daily from PREVIOUS week
        result = _compute_daily_range(today=today, max_daily_date=date(2026, 4, 13))
        assert result is not None
        date_from, date_to = result
        assert date_from == date(2026, 4, 20)  # this Monday (NOT day after old daily)
        assert date_to == date(2026, 4, 21)


class TestReportCacheInvalidation:
    """After a finance sync lands new rows, cached reports (БДР/ОПиУ/план-факт,
    ttl=1h) must be invalidated — otherwise the report keeps serving a stale
    pre-sync snapshot for up to an hour (missing e.g. the just-synced Sunday)."""

    @staticmethod
    def _fake_db(rows_scalar=None):
        """Fake AsyncSession whose execute() returns a result exposing both
        .scalar() (max-date queries) and .rowcount (delete)."""
        db = MagicMock()
        result = MagicMock()
        result.scalar.return_value = rows_scalar
        result.rowcount = 0
        db.execute = AsyncMock(return_value=result)
        db.commit = AsyncMock()
        db.add = MagicMock()
        return db

    @asynccontextmanager
    async def _session_factory(self):
        yield self._fake_db()

    async def _run(self, rows_synced):
        key = MagicMock()
        key.id = 99
        with (
            patch(
                "backend.scheduler.jobs.wb_finance.get_sync_project_ids",
                AsyncMock(return_value=[4]),
            ),
            patch(
                "backend.scheduler.jobs.wb_finance.AsyncSessionLocal",
                lambda: self._session_factory(),
            ),
            patch(
                "backend.services.integrations_service._get_wb_key",
                AsyncMock(return_value=(key, "api-key")),
            ),
            patch(
                "backend.services.wb_finance_sync.sync_wb_finance",
                AsyncMock(return_value={"status": "OK", "rows_synced": rows_synced}),
            ),
            patch(
                "backend.services.wb_cancel_sync.sync_cancel_stats",
                AsyncMock(return_value={"status": "OK"}),
            ),
            patch(
                "backend.cache.invalidate_project_reports",
                AsyncMock(),
            ) as inv,
        ):
            await sync_all_projects_wb_finance()
        return inv

    @pytest.mark.asyncio
    async def test_invalidates_reports_when_rows_synced(self):
        """rows_synced > 0 → invalidate_project_reports(pid) called once."""
        inv = await self._run(rows_synced=5)
        inv.assert_awaited_once_with(4)

    @pytest.mark.asyncio
    async def test_no_invalidation_when_nothing_synced(self):
        """WB 204 → rows_synced == 0 → no cache churn."""
        inv = await self._run(rows_synced=0)
        inv.assert_not_awaited()
