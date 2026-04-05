"""
Tests for wb_cancel_sync — aggregate cancel stats from WB orders.
Mocks WB API and DB operations.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# sync_cancel_stats — aggregation logic
# ═══════════════════════════════════════════════════════════════════════════════


class TestSyncCancelStatsAggregation:
    """Test the order aggregation logic without DB.

    We mock _get_wb_key and WBApiClient.get_orders to test
    the aggregation and upsert logic.
    """

    @pytest.mark.asyncio
    async def test_empty_orders_returns_empty(self):
        """No orders from WB returns empty status."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        with (
            patch("backend.services.wb_cancel_sync._get_wb_key", return_value=(MagicMock(), "fake-key")),
            patch("backend.integrations.wb_api.WBApiClient") as MockClient,
        ):
            instance = MockClient.return_value
            instance.get_orders = AsyncMock(return_value=[])

            from backend.services.wb_cancel_sync import sync_cancel_stats

            result = await sync_cancel_stats(mock_db, project_id=1, date_from=date(2024, 1, 1))

        assert result["status"] == "empty"
        assert result["total_orders"] == 0

    @pytest.mark.asyncio
    async def test_aggregates_correctly(self):
        """Orders aggregated by (nm_id, date) with correct cancel counts."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        orders = [
            {"nmId": 100, "date": "2024-01-15T10:00:00", "isCancel": False},
            {"nmId": 100, "date": "2024-01-15T11:00:00", "isCancel": True},
            {"nmId": 100, "date": "2024-01-15T12:00:00", "isCancel": False},
            {"nmId": 200, "date": "2024-01-15T08:00:00", "isCancel": False},
            {"nmId": 200, "date": "2024-01-16T08:00:00", "isCancel": True},
        ]

        with (
            patch("backend.services.wb_cancel_sync._get_wb_key", return_value=(MagicMock(), "fake-key")),
            patch("backend.integrations.wb_api.WBApiClient") as MockClient,
        ):
            instance = MockClient.return_value
            instance.get_orders = AsyncMock(return_value=orders)

            from backend.services.wb_cancel_sync import sync_cancel_stats

            result = await sync_cancel_stats(mock_db, project_id=1, date_from=date(2024, 1, 1))

        assert result["status"] == "ok"
        assert result["total_orders"] == 5
        assert result["total_cancelled"] == 2
        assert result["days_synced"] == 3  # (100, Jan15), (200, Jan15), (200, Jan16)

    @pytest.mark.asyncio
    async def test_skips_orders_without_nm_id(self):
        """Orders without nmId are skipped."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        orders = [
            {"nmId": None, "date": "2024-01-15T10:00:00", "isCancel": False},
            {"date": "2024-01-15T10:00:00", "isCancel": False},  # no nmId key
            {"nmId": 100, "date": "2024-01-15T10:00:00", "isCancel": False},
        ]

        with (
            patch("backend.services.wb_cancel_sync._get_wb_key", return_value=(MagicMock(), "fake-key")),
            patch("backend.integrations.wb_api.WBApiClient") as MockClient,
        ):
            instance = MockClient.return_value
            instance.get_orders = AsyncMock(return_value=orders)

            from backend.services.wb_cancel_sync import sync_cancel_stats

            result = await sync_cancel_stats(mock_db, project_id=1, date_from=date(2024, 1, 1))

        assert result["total_orders"] == 1  # only the one with nmId=100

    @pytest.mark.asyncio
    async def test_skips_bad_dates(self):
        """Orders with unparseable dates are skipped."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        orders = [
            {"nmId": 100, "date": "invalid-date", "isCancel": False},
            {"nmId": 100, "date": "", "isCancel": False},
            {"nmId": 100, "date": "2024-01-15T10:00:00", "isCancel": False},
        ]

        with (
            patch("backend.services.wb_cancel_sync._get_wb_key", return_value=(MagicMock(), "fake-key")),
            patch("backend.integrations.wb_api.WBApiClient") as MockClient,
        ):
            instance = MockClient.return_value
            instance.get_orders = AsyncMock(return_value=orders)

            from backend.services.wb_cancel_sync import sync_cancel_stats

            result = await sync_cancel_stats(mock_db, project_id=1, date_from=date(2024, 1, 1))

        assert result["total_orders"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Project isolation
# ═══════════════════════════════════════════════════════════════════════════════


class TestCancelSyncProjectIsolation:
    """Verify that sync_cancel_stats uses project_id from args."""

    @pytest.mark.asyncio
    async def test_uses_project_id_for_key_lookup(self):
        """_get_wb_key is called with the correct project_id."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        with (
            patch("backend.services.wb_cancel_sync._get_wb_key", return_value=(MagicMock(), "key")) as mock_get_key,
            patch("backend.integrations.wb_api.WBApiClient") as MockClient,
        ):
            instance = MockClient.return_value
            instance.get_orders = AsyncMock(return_value=[])

            from backend.services.wb_cancel_sync import sync_cancel_stats

            await sync_cancel_stats(mock_db, project_id=42, date_from=date(2024, 1, 1))

        mock_get_key.assert_called_once_with(mock_db, 42)
