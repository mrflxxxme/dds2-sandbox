"""
Tests for backend/scheduler.py — Background WB funnel sync scheduler.

Tests internal logic with mocked DB and WB API. Does NOT start the actual scheduler.
"""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── _get_missing_dates ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_missing_dates_finds_gaps():
    """_get_missing_dates should return dates with NO funnel data."""
    today = date.today()
    # Simulate: only yesterday has data → all other days in range are missing
    mock_existing = {today - timedelta(days=1)}

    mock_result = MagicMock()
    mock_result.__iter__ = lambda self: iter([(d,) for d in mock_existing])

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.scheduler.helpers.AsyncSessionLocal", return_value=mock_session), \
         patch("backend.scheduler.helpers.get_failed_dates", return_value=set()):
        from backend.scheduler.helpers import get_missing_dates
        missing = await get_missing_dates(project_id=1, lookback_days=5)

    # Should have 4 missing dates (lookback 5 days, minus yesterday which has data, minus today)
    assert len(missing) == 4
    # Should be sorted oldest first
    assert missing == sorted(missing)
    # Yesterday should NOT be in missing
    yesterday_str = (today - timedelta(days=1)).isoformat()
    assert yesterday_str not in missing


@pytest.mark.asyncio
async def test_get_missing_dates_skips_poisoned():
    """_get_missing_dates should skip dates that failed too many times."""
    today = date.today()
    # No existing data at all
    mock_result = MagicMock()
    mock_result.__iter__ = lambda self: iter([])

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    # Mark 2 days ago as poisoned
    poisoned_date = (today - timedelta(days=2)).isoformat()

    with patch("backend.scheduler.helpers.AsyncSessionLocal", return_value=mock_session), \
         patch("backend.scheduler.helpers.get_failed_dates", return_value={poisoned_date}):
        from backend.scheduler.helpers import get_missing_dates
        missing = await get_missing_dates(project_id=1, lookback_days=5)

    # Poisoned date should be excluded
    assert poisoned_date not in missing


# ─── _get_sync_project_ids ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_sync_project_ids_with_global_key():
    """With a global WB key (project_id IS NULL), should return ALL project IDs."""
    # First call: check for global key → returns id=1
    # Second call: get all projects → returns [1, 2, 3]
    call_count = 0

    async def mock_execute(query):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            # Global key exists
            result.scalar.return_value = 1
        else:
            # All project IDs
            result.__iter__ = lambda self: iter([(1,), (2,), (3,)])
        return result

    mock_session = AsyncMock()
    mock_session.execute = mock_execute
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.scheduler.helpers.AsyncSessionLocal", return_value=mock_session):
        from backend.scheduler.helpers import get_sync_project_ids
        pids = await get_sync_project_ids()

    assert pids == [1, 2, 3]


@pytest.mark.asyncio
async def test_get_sync_project_ids_without_global_key():
    """Without global key, should return only projects with their own WB keys."""
    call_count = 0

    async def mock_execute(query):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            # No global key
            result.scalar.return_value = None
        else:
            # Only project 2 has WB key
            result.__iter__ = lambda self: iter([(2,)])
        return result

    mock_session = AsyncMock()
    mock_session.execute = mock_execute
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.scheduler.helpers.AsyncSessionLocal", return_value=mock_session):
        from backend.scheduler.helpers import get_sync_project_ids
        pids = await get_sync_project_ids()

    assert pids == [2]


# ─── get_scheduler_info ──────────────────────────────────────────────────────

def test_get_scheduler_info_when_not_running():
    """get_scheduler_info returns running=False when scheduler is None."""
    import backend.scheduler as sched_mod
    original = sched_mod._scheduler
    try:
        sched_mod._scheduler = None
        info = sched_mod.get_scheduler_info()
        assert info == {"running": False, "jobs": []}
    finally:
        sched_mod._scheduler = original


def test_get_scheduler_info_when_running():
    """get_scheduler_info returns job list when scheduler is running."""
    import backend.scheduler as sched_mod
    from datetime import datetime, timezone

    mock_job = MagicMock()
    mock_job.id = "fast_backfill"
    mock_job.name = "Fast backfill"
    mock_job.next_run_time = datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc)

    mock_scheduler = MagicMock()
    mock_scheduler.running = True
    mock_scheduler.get_jobs.return_value = [mock_job]

    original = sched_mod._scheduler
    try:
        sched_mod._scheduler = mock_scheduler
        info = sched_mod.get_scheduler_info()
        assert info["running"] is True
        assert len(info["jobs"]) == 1
        assert info["jobs"][0]["id"] == "fast_backfill"
    finally:
        sched_mod._scheduler = original
