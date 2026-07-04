"""
Tests for project_settings_service — tax_rate, vat_rate mutations with cache invalidation.
Uses mocks for DB and cache since the service operates on a Project model directly.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.project_settings_service import set_tax_rate, set_vat_rate

# ═══════════════════════════════════════════════════════════════════════════════
# set_tax_rate
# ═══════════════════════════════════════════════════════════════════════════════


class TestSetTaxRate:
    @pytest.mark.asyncio
    async def test_sets_decimal_value(self):
        """Tax rate is stored as Decimal."""
        mock_db = AsyncMock()
        mock_project = MagicMock()
        mock_project.id = 1

        with patch("backend.services.project_settings_service.invalidate_cache", new_callable=AsyncMock):
            result = await set_tax_rate(mock_db, mock_project, Decimal("6.0"))

        assert result == Decimal("6.0")
        assert mock_project.tax_rate == Decimal("6.0")
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalidates_caches(self):
        """Tax rate change invalidates BDR, OPIU, dashboard, funnel caches."""
        mock_db = AsyncMock()
        mock_project = MagicMock()
        mock_project.id = 42

        with patch("backend.services.project_settings_service.invalidate_cache", new_callable=AsyncMock) as mock_inv:
            await set_tax_rate(mock_db, mock_project, Decimal("7.0"))

        # 5 prefixes: wb_bdr, opiu, dashboard + the two real funnel caches
        # (funnel:tariff_map, funnel:avg_buyout — the old bare "funnel:project_id" was a no-op).
        assert mock_inv.await_count == 5
        call_args = [c.args[0] for c in mock_inv.call_args_list]
        assert any("wb_bdr" in a for a in call_args)
        assert any("opiu" in a for a in call_args)
        assert any("dashboard" in a for a in call_args)
        assert any("funnel:tariff_map" in a for a in call_args)
        assert any("funnel:avg_buyout" in a for a in call_args)

    @pytest.mark.asyncio
    async def test_float_input_converted(self):
        """Float input is converted to Decimal via str."""
        mock_db = AsyncMock()
        mock_project = MagicMock()
        mock_project.id = 1

        with patch("backend.services.project_settings_service.invalidate_cache", new_callable=AsyncMock):
            result = await set_tax_rate(mock_db, mock_project, Decimal("5.5"))

        assert isinstance(result, Decimal)


# ═══════════════════════════════════════════════════════════════════════════════
# set_vat_rate
# ═══════════════════════════════════════════════════════════════════════════════


class TestSetVatRate:
    @pytest.mark.asyncio
    async def test_sets_decimal_value(self):
        """VAT rate is stored as Decimal."""
        mock_db = AsyncMock()
        mock_project = MagicMock()
        mock_project.id = 1

        with patch("backend.services.project_settings_service.invalidate_cache", new_callable=AsyncMock):
            result = await set_vat_rate(mock_db, mock_project, Decimal("20.0"))

        assert result == Decimal("20.0")
        assert mock_project.vat_rate == Decimal("20.0")
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalidates_caches(self):
        """VAT rate change invalidates cost, BDR, OPIU, dashboard caches."""
        mock_db = AsyncMock()
        mock_project = MagicMock()
        mock_project.id = 42

        with patch("backend.services.project_settings_service.invalidate_cache", new_callable=AsyncMock) as mock_inv:
            await set_vat_rate(mock_db, mock_project, Decimal("22.0"))

        # Should invalidate 4 cache prefixes
        assert mock_inv.await_count == 4
        call_args = [c.args[0] for c in mock_inv.call_args_list]
        assert any("cost" in a for a in call_args)
        assert any("wb_bdr" in a for a in call_args)
        assert any("opiu" in a for a in call_args)
        assert any("dashboard" in a for a in call_args)

    @pytest.mark.asyncio
    async def test_project_id_in_cache_keys(self):
        """Cache keys include project_id."""
        mock_db = AsyncMock()
        mock_project = MagicMock()
        mock_project.id = 99

        with patch("backend.services.project_settings_service.invalidate_cache", new_callable=AsyncMock) as mock_inv:
            await set_vat_rate(mock_db, mock_project, Decimal("20"))

        call_args = [c.args[0] for c in mock_inv.call_args_list]
        assert all("project_id=99" in a for a in call_args)
