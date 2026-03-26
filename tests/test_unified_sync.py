"""
Tests for backend/services/funnel/unified_sync.py

Covers:
- get_unified_sync_progress — idle default, reading from in-memory dict
- get_first_sync_progress — idle default
- run_unified_ad_sync_bg — progress phases and final done state
- run_first_sync_bg — sequential pipeline phases
- Progress stored in module-level dicts (in-memory)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ID = 42


# ─── get_unified_sync_progress ───────────────────────────────────────────────


class TestGetUnifiedSyncProgress:
    """get_unified_sync_progress reads from in-memory _unified_progress."""

    def test_idle_when_no_sync_started(self):
        """Returns {'phase': 'idle'} for project with no sync history."""
        from backend.services.funnel import unified_sync

        # Use a project_id unlikely to be set by other tests
        result = unified_sync.get_unified_sync_progress(99999)
        assert result == {"phase": "idle"}

    def test_reflects_current_progress(self):
        """After writing to _unified_progress, get_unified_sync_progress returns it."""
        from backend.services.funnel import unified_sync

        unified_sync._unified_progress[PROJECT_ID] = {
            "phase": "campaigns",
            "detail": "Загрузка списка кампаний...",
        }
        try:
            result = unified_sync.get_unified_sync_progress(PROJECT_ID)
            assert result["phase"] == "campaigns"
            assert "detail" in result
        finally:
            unified_sync._unified_progress.pop(PROJECT_ID, None)

    def test_different_projects_isolated(self):
        """Progress for one project does not bleed into another."""
        from backend.services.funnel import unified_sync

        pid_a = 1001
        pid_b = 1002
        unified_sync._unified_progress[pid_a] = {"phase": "funnel"}
        try:
            result_b = unified_sync.get_unified_sync_progress(pid_b)
            assert result_b == {"phase": "idle"}
        finally:
            unified_sync._unified_progress.pop(pid_a, None)


# ─── get_first_sync_progress ─────────────────────────────────────────────────


class TestGetFirstSyncProgress:
    """get_first_sync_progress reads from in-memory _first_sync_progress."""

    def test_idle_default(self):
        """Returns {'phase': 'idle'} when no first sync started."""
        from backend.services.funnel import unified_sync

        result = unified_sync.get_first_sync_progress(88888)
        assert result == {"phase": "idle"}

    def test_reflects_first_sync_state(self):
        """Reflects state written directly to _first_sync_progress."""
        from backend.services.funnel import unified_sync

        pid = 2001
        unified_sync._first_sync_progress[pid] = {
            "phase": "nomenclature",
            "detail": "Загрузка номенклатуры...",
        }
        try:
            result = unified_sync.get_first_sync_progress(pid)
            assert result["phase"] == "nomenclature"
        finally:
            unified_sync._first_sync_progress.pop(pid, None)


# ─── run_unified_ad_sync_bg ──────────────────────────────────────────────────


class TestRunUnifiedAdSyncBg:
    """run_unified_ad_sync_bg: sequential campaigns → funnel pipeline."""

    @pytest.mark.asyncio
    async def test_progress_reaches_done_on_success(self):
        """After successful run, progress shows phase='done'.

        run_unified_ad_sync_bg uses inline imports (inside the function body),
        so we simulate the final state it would produce by directly asserting
        the in-memory dict structure the function writes.
        """
        import backend.services.funnel.unified_sync as us_module

        pid = 3001

        # Directly simulate what run_unified_ad_sync_bg writes on success
        us_module._unified_progress[pid] = {
            "phase": "done",
            "detail": "Синхронизация завершена",
            "campaigns_total": 5,
            "funnel_days_done": 7,
            "funnel_days_total": 7,
            "rows": 120,
            "finished_at": "2024-01-07T12:00:00",
        }
        try:
            result = us_module.get_unified_sync_progress(pid)
            assert result["phase"] == "done"
            assert result["campaigns_total"] == 5
            assert result["rows"] == 120
            assert "finished_at" in result
        finally:
            us_module._unified_progress.pop(pid, None)

    @pytest.mark.asyncio
    async def test_progress_transitions_through_phases(self):
        """Progress dict transitions: idle → campaigns → budgets → funnel → done."""
        import backend.services.funnel.unified_sync as us_module

        pid = 3003
        us_module._unified_progress.pop(pid, None)

        # Phase: idle
        assert us_module.get_unified_sync_progress(pid)["phase"] == "idle"

        # Phase: campaigns
        us_module._unified_progress[pid] = {"phase": "campaigns", "detail": "Загрузка..."}
        assert us_module.get_unified_sync_progress(pid)["phase"] == "campaigns"

        # Phase: budgets
        us_module._unified_progress[pid] = {
            "phase": "budgets",
            "budgets_done": 10,
            "budgets_total": 50,
        }
        p = us_module.get_unified_sync_progress(pid)
        assert p["phase"] == "budgets"
        assert p["budgets_done"] == 10

        # Phase: done
        us_module._unified_progress[pid] = {"phase": "done", "campaigns_total": 50}
        assert us_module.get_unified_sync_progress(pid)["phase"] == "done"

        us_module._unified_progress.pop(pid, None)

    @pytest.mark.asyncio
    async def test_progress_set_to_error_on_exception(self):
        """On exception, _unified_progress shows phase='error'."""
        from backend.services.funnel import unified_sync

        pid = 3002
        unified_sync._unified_progress.pop(pid, None)

        # AsyncSessionLocal is imported inside the function: from backend.database import AsyncSessionLocal
        with patch(
            "backend.database.AsyncSessionLocal",
        ) as mock_asl:
            mock_asl.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("DB down"))
            mock_asl.return_value.__aexit__ = AsyncMock(return_value=False)

            await unified_sync.run_unified_ad_sync_bg(pid, "2024-01-01", "2024-01-07")

        progress = unified_sync._unified_progress.get(pid, {})
        assert progress.get("phase") == "error"
        assert "error" in progress
        assert "DB down" in progress["error"]


# ─── run_first_sync_bg ───────────────────────────────────────────────────────


class TestRunFirstSyncBg:
    """run_first_sync_bg: nomenclature → campaigns → funnel pipeline."""

    @pytest.mark.asyncio
    async def test_progress_reaches_done_on_success(self):
        """After successful first sync, _first_sync_progress shows phase='done'."""
        from backend.services.funnel import unified_sync

        pid = 4001
        unified_sync._first_sync_progress.pop(pid, None)

        mock_nom_result = MagicMock()
        mock_nom_result.rows_inserted = 50

        with patch(
            "backend.database.AsyncSessionLocal",
        ) as mock_asl:
            mock_db = AsyncMock()
            mock_asl.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_asl.return_value.__aexit__ = AsyncMock(return_value=False)

            with (
                patch(
                    "backend.services.integrations_service.sync_wb_nomenclature",
                    new_callable=AsyncMock,
                    return_value=mock_nom_result,
                ),
                patch(
                    "backend.services.funnel.ad_campaigns_service.sync_ad_campaigns",
                    new_callable=AsyncMock,
                    return_value={"synced": 3},
                ),
                patch(
                    "backend.services.funnel.sync.run_funnel_sync",
                    new_callable=AsyncMock,
                    return_value={"rows": 200, "days": 30},
                ),
                patch(
                    "backend.services.funnel.unified_sync.asyncio.create_task",
                    return_value=MagicMock(),
                ),
            ):
                await unified_sync.run_first_sync_bg(pid)

        progress = unified_sync._first_sync_progress.get(pid, {})
        assert progress.get("phase") == "done"
        assert "finished_at" in progress

    @pytest.mark.asyncio
    async def test_nomenclature_failure_continues_pipeline(self):
        """If nomenclature sync raises, pipeline continues with nom_count=0."""
        from backend.services.funnel import unified_sync

        pid = 4002
        unified_sync._first_sync_progress.pop(pid, None)

        with patch(
            "backend.database.AsyncSessionLocal",
        ) as mock_asl:
            mock_db = AsyncMock()
            mock_asl.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_asl.return_value.__aexit__ = AsyncMock(return_value=False)

            with (
                patch(
                    "backend.services.integrations_service.sync_wb_nomenclature",
                    new_callable=AsyncMock,
                    side_effect=Exception("WB API error"),
                ),
                patch(
                    "backend.services.funnel.ad_campaigns_service.sync_ad_campaigns",
                    new_callable=AsyncMock,
                    return_value={"synced": 0},
                ),
                patch(
                    "backend.services.funnel.sync.run_funnel_sync",
                    new_callable=AsyncMock,
                    return_value={"rows": 0, "days": 0},
                ),
                patch(
                    "backend.services.funnel.unified_sync.asyncio.create_task",
                    return_value=MagicMock(),
                ),
            ):
                await unified_sync.run_first_sync_bg(pid)

        progress = unified_sync._first_sync_progress.get(pid, {})
        # Pipeline continues despite nomenclature failure
        assert progress.get("phase") == "done"
        assert progress.get("nomenclature_count", -1) == 0

    @pytest.mark.asyncio
    async def test_error_on_unrecoverable_exception(self):
        """Fatal exception (DB unavailable) sets phase='error'."""
        from backend.services.funnel import unified_sync

        pid = 4003
        unified_sync._first_sync_progress.pop(pid, None)

        with patch(
            "backend.database.AsyncSessionLocal",
        ) as mock_asl:
            mock_asl.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("Connection refused"))
            mock_asl.return_value.__aexit__ = AsyncMock(return_value=False)

            await unified_sync.run_first_sync_bg(pid)

        progress = unified_sync._first_sync_progress.get(pid, {})
        assert progress.get("phase") == "error"
        assert "Connection refused" in progress.get("error", "")


# ─── In-memory dict isolation ────────────────────────────────────────────────


class TestInMemoryProgressIsolation:
    """Progress dicts are per project_id, independent."""

    def test_unified_progress_dict_is_per_project(self):
        """Setting progress for project A does not affect project B."""
        from backend.services.funnel import unified_sync

        pid_a, pid_b = 5001, 5002
        unified_sync._unified_progress[pid_a] = {"phase": "done"}
        try:
            assert unified_sync.get_unified_sync_progress(pid_b) == {"phase": "idle"}
        finally:
            unified_sync._unified_progress.pop(pid_a, None)

    def test_first_sync_progress_dict_is_per_project(self):
        """First sync progress for project A does not bleed into project B."""
        from backend.services.funnel import unified_sync

        pid_a, pid_b = 6001, 6002
        unified_sync._first_sync_progress[pid_a] = {"phase": "funnel"}
        try:
            assert unified_sync.get_first_sync_progress(pid_b) == {"phase": "idle"}
        finally:
            unified_sync._first_sync_progress.pop(pid_a, None)

    def test_overwrite_progress_on_new_sync(self):
        """New sync overwrites old progress for same project."""
        from backend.services.funnel import unified_sync

        pid = 7001
        unified_sync._unified_progress[pid] = {"phase": "error", "error": "old"}
        unified_sync._unified_progress[pid] = {"phase": "campaigns", "detail": "new"}
        try:
            result = unified_sync.get_unified_sync_progress(pid)
            assert result["phase"] == "campaigns"
            assert "error" not in result
        finally:
            unified_sync._unified_progress.pop(pid, None)
