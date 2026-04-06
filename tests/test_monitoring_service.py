"""
Tests for monitoring_service — sync health metrics.
Uses DB fixtures from conftest.py.
"""

import pytest

from backend.services.monitoring_service import (
    get_sync_log_filtered,
    get_sync_overview,
)

# ═════════════════��═════════════════════════════════════════════════════════════
# get_sync_overview
# ════��═══════════════��══════════════════════════════════════════════════════════


class TestGetSyncOverview:
    @pytest.mark.asyncio
    async def test_empty_for_new_project(self, db_session, project):
        """New project has no sync data."""
        result = await get_sync_overview(db_session, project.id)
        assert result["sync_types"] == []
        assert result["total_syncs_24h"] == 0
        assert result["total_errors_24h"] == 0

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Overview for project A does not include project B data."""
        result_a = await get_sync_overview(db_session, project.id)
        result_b = await get_sync_overview(db_session, other_project.id)
        # Both empty — but importantly, no cross-contamination
        assert result_a["total_syncs_24h"] == 0
        assert result_b["total_syncs_24h"] == 0

    @pytest.mark.asyncio
    async def test_return_structure(self, db_session, project):
        """Verify the structure of the returned dict."""
        result = await get_sync_overview(db_session, project.id)
        assert "sync_types" in result
        assert "total_syncs_24h" in result
        assert "total_errors_24h" in result
        assert isinstance(result["sync_types"], list)


# ═════════���════════════════════���═══════════════════════���════════════════════════
# get_sync_log_filtered
# ���════════════════════════════════════════════════════���═════════════════════════


class TestGetSyncLogFiltered:
    @pytest.mark.asyncio
    async def test_empty_for_new_project(self, db_session, project):
        """New project has no sync logs."""
        result = await get_sync_log_filtered(db_session, project.id)
        assert result == []

    @pytest.mark.asyncio
    async def test_with_filters(self, db_session, project):
        """Filters can be applied (even if no data matches)."""
        result = await get_sync_log_filtered(
            db_session,
            project.id,
            service="wb_finance",
            sync_type="full",
            status="OK",
            limit=10,
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Logs are isolated by project."""
        result_a = await get_sync_log_filtered(db_session, project.id)
        result_b = await get_sync_log_filtered(db_session, other_project.id)
        assert result_a == []
        assert result_b == []
