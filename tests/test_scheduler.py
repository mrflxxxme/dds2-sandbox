"""
Tests for backend/scheduler.py — Background WB funnel sync scheduler.

Tests internal logic with mocked DB and WB API. Does NOT start the actual scheduler.
"""

from datetime import UTC, date, timedelta
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

    with (
        patch("backend.scheduler.helpers.AsyncSessionLocal", return_value=mock_session),
        patch("backend.scheduler.helpers.get_failed_dates", return_value=set()),
    ):
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

    with (
        patch("backend.scheduler.helpers.AsyncSessionLocal", return_value=mock_session),
        patch("backend.scheduler.helpers.get_failed_dates", return_value={poisoned_date}),
    ):
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


# ─── Тированные FF-джобы (_add_fulfillment_jobs) ─────────────────────────────


def _ff_jobs(monkeypatch, *, include_default, fast="", fast_min=10, slow="", slow_min=30, default_min=60):
    """Зарегистрировать FF-джобы на mock-scheduler, вернуть {job_id: kwargs}."""
    import backend.scheduler as sched_mod
    from backend.config import settings as cfg

    monkeypatch.setattr(cfg, "FULFILLMENT_SYNC_FAST_WAREHOUSE_IDS", fast)
    monkeypatch.setattr(cfg, "FULFILLMENT_SYNC_FAST_INTERVAL_MINUTES", fast_min)
    monkeypatch.setattr(cfg, "FULFILLMENT_SYNC_SLOW_WAREHOUSE_IDS", slow)
    monkeypatch.setattr(cfg, "FULFILLMENT_SYNC_SLOW_INTERVAL_MINUTES", slow_min)
    monkeypatch.setattr(cfg, "FULFILLMENT_SYNC_INTERVAL_MINUTES", default_min)

    mock_sched = MagicMock()
    sched_mod._add_fulfillment_jobs(mock_sched, include_default=include_default)
    return {c.kwargs["id"]: c.kwargs for c in mock_sched.add_job.call_args_list}


def test_ff_jobs_local_no_default(monkeypatch):
    """Локалка (include_default=False): FAST+SLOW, без DEFAULT; пересечение
    SLOW с FAST исключается."""
    jobs = _ff_jobs(monkeypatch, include_default=False, fast="5, 1", slow="1, 2")
    assert set(jobs) == {"fulfillment_sync_fast", "fulfillment_sync_slow"}
    assert jobs["fulfillment_sync_fast"]["kwargs"] == {"warehouse_ids": [5, 1]}
    assert jobs["fulfillment_sync_fast"]["trigger"].interval == timedelta(minutes=10)
    # 1 уже в FAST → SLOW остаётся [2]
    assert jobs["fulfillment_sync_slow"]["kwargs"] == {"warehouse_ids": [2]}
    assert jobs["fulfillment_sync_slow"]["trigger"].interval == timedelta(minutes=30)


def test_ff_jobs_prod_with_default(monkeypatch):
    """Прод (include_default=True): FAST+SLOW+DEFAULT; DEFAULT исключает
    приоритетные склады (чтобы не синкать дважды)."""
    jobs = _ff_jobs(monkeypatch, include_default=True, fast="5,1", slow="2", default_min=60)
    assert set(jobs) == {"fulfillment_sync_fast", "fulfillment_sync_slow", "fulfillment_sync"}
    assert jobs["fulfillment_sync"]["kwargs"] == {"exclude_warehouse_ids": [5, 1, 2]}
    assert jobs["fulfillment_sync"]["trigger"].interval == timedelta(minutes=60)


def test_ff_jobs_prod_empty_syncs_all(monkeypatch):
    """Пустые FAST/SLOW + include_default=True → один DEFAULT для ВСЕХ
    (exclude=None) = прежнее «раз в час»."""
    jobs = _ff_jobs(monkeypatch, include_default=True, fast="", slow="")
    assert set(jobs) == {"fulfillment_sync"}
    assert jobs["fulfillment_sync"]["kwargs"] == {"exclude_warehouse_ids": None}


def test_start_scheduler_ff_only_path(monkeypatch):
    """SCHEDULER_ENABLED=false + FULFILLMENT_SYNC_ENABLED=true → FF-only
    scheduler стартует, регистрирует FAST-джобу, БЕЗ DEFAULT-контура."""
    import backend.scheduler as sched_mod
    from backend.config import settings as cfg

    monkeypatch.setattr(cfg, "SCHEDULER_ENABLED", False)
    monkeypatch.setattr(cfg, "FULFILLMENT_SYNC_ENABLED", True)
    monkeypatch.setattr(cfg, "FULFILLMENT_SYNC_FAST_WAREHOUSE_IDS", "5,1")
    monkeypatch.setattr(cfg, "FULFILLMENT_SYNC_SLOW_WAREHOUSE_IDS", "")

    mock_sched = MagicMock()
    original = sched_mod._scheduler
    try:
        with patch.object(sched_mod, "AsyncIOScheduler", return_value=mock_sched):
            sched_mod.start_scheduler()
        ids = {c.kwargs["id"] for c in mock_sched.add_job.call_args_list}
        assert ids == {"fulfillment_sync_fast"}  # без DEFAULT и без SLOW
        mock_sched.start.assert_called_once()
    finally:
        sched_mod._scheduler = original


def test_start_scheduler_disabled_without_ff_starts_nothing(monkeypatch):
    """SCHEDULER_ENABLED=false + FULFILLMENT_SYNC_ENABLED=false → ничего не стартует."""
    import backend.scheduler as sched_mod
    from backend.config import settings as cfg

    monkeypatch.setattr(cfg, "SCHEDULER_ENABLED", False)
    monkeypatch.setattr(cfg, "FULFILLMENT_SYNC_ENABLED", False)

    original = sched_mod._scheduler
    try:
        sched_mod._scheduler = None
        with patch.object(sched_mod, "AsyncIOScheduler") as mock_cls:
            sched_mod.start_scheduler()
        mock_cls.assert_not_called()
        assert sched_mod._scheduler is None
    finally:
        sched_mod._scheduler = original


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
    from datetime import datetime

    import backend.scheduler as sched_mod

    mock_job = MagicMock()
    mock_job.id = "fast_backfill"
    mock_job.name = "Fast backfill"
    mock_job.next_run_time = datetime(2026, 3, 6, 12, 0, tzinfo=UTC)

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
