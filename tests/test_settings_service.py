"""
Tests for settings_service — project-level key-value settings.
Uses DB fixtures from conftest.py (project, other_project, db_session).
"""

import pytest

from backend.services.settings_service import (
    get_all_warehouses,
    get_excluded_warehouses,
    get_setting,
    set_excluded_warehouses,
    set_setting,
)

# ═══════════════════════════════════════════════════════════════════════════════
# get_setting / set_setting
# ═══════════════════════════════════════════════════════════════════════════════


class TestSettingsService:
    @pytest.mark.asyncio
    async def test_get_missing_key_returns_none(self, db_session, project):
        """Non-existent key returns None."""
        val = await get_setting(db_session, project.id, "nonexistent_key")
        assert val is None

    @pytest.mark.asyncio
    async def test_set_and_get(self, db_session, project):
        """Set a key and read it back."""
        await set_setting(db_session, project.id, "test_key", "test_value")
        val = await get_setting(db_session, project.id, "test_key")
        assert val == "test_value"

    @pytest.mark.asyncio
    async def test_upsert_overwrites(self, db_session, project):
        """Setting the same key again overwrites the value."""
        await set_setting(db_session, project.id, "key1", "v1")
        await set_setting(db_session, project.id, "key1", "v2")
        val = await get_setting(db_session, project.id, "key1")
        assert val == "v2"

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Setting in project A is not visible in project B."""
        await set_setting(db_session, project.id, "isolated_key", "secret")
        val = await get_setting(db_session, other_project.id, "isolated_key")
        assert val is None


# ═══════════════════════════════════════════════════════════════════════════════
# Excluded warehouses
# ═══════════════════════════════════════════════════════════════════════════════


class TestExcludedWarehouses:
    @pytest.mark.asyncio
    async def test_empty_by_default(self, db_session, project):
        """No excluded warehouses by default."""
        result = await get_excluded_warehouses(db_session, project.id)
        assert result == []

    @pytest.mark.asyncio
    async def test_set_and_get(self, db_session, project):
        """Set excluded warehouses and read back."""
        all_wh = await get_all_warehouses()
        if not all_wh:
            pytest.skip("No warehouses defined in WAREHOUSE_COORDS")
        wh_name = all_wh[0]["name"]
        valid = await set_excluded_warehouses(db_session, project.id, [wh_name])
        assert wh_name in valid
        result = await get_excluded_warehouses(db_session, project.id)
        assert wh_name in result

    @pytest.mark.asyncio
    async def test_arbitrary_names_accepted(self, db_session, project):
        """Validation against WAREHOUSE_COORDS removed: any non-empty name persists,
        because real WB warehouse names (e.g. «СЦ Барнаул») are not in coords."""
        result = await set_excluded_warehouses(db_session, project.id, ["СЦ Барнаул", "WhateverNew"])
        assert "СЦ Барнаул" in result
        assert "WhateverNew" in result

    @pytest.mark.asyncio
    async def test_empty_and_whitespace_filtered(self, db_session, project):
        """Empty / whitespace-only names are dropped."""
        result = await set_excluded_warehouses(db_session, project.id, ["", "   ", "Москва"])
        assert result == ["Москва"]

    @pytest.mark.asyncio
    async def test_parens_normalized(self, db_session, project):
        """Parenthesized suffixes are stripped (Краснодар (Тихорецкая) -> Краснодар)."""
        result = await set_excluded_warehouses(db_session, project.id, ["Краснодар (Тихорецкая)"])
        assert result == ["Краснодар"]

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Excluded warehouses are project-specific."""
        all_wh = await get_all_warehouses()
        if not all_wh:
            pytest.skip("No warehouses defined in WAREHOUSE_COORDS")
        wh_name = all_wh[0]["name"]
        await set_excluded_warehouses(db_session, project.id, [wh_name])
        other_excluded = await get_excluded_warehouses(db_session, other_project.id)
        assert other_excluded == []


# ═══════════════════════════════════════════════════════════════════════════════
# get_all_warehouses
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetAllWarehouses:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        """Returns list with name/lat/lng/is_sorting_center keys."""
        result = await get_all_warehouses()
        assert isinstance(result, list)
        if result:
            assert "name" in result[0]
            assert "lat" in result[0]
            assert "lng" in result[0]
            assert "is_sorting_center" in result[0]

    @pytest.mark.asyncio
    async def test_sorted_by_name(self):
        """Warehouses are sorted alphabetically."""
        result = await get_all_warehouses()
        names = [w["name"] for w in result]
        assert names == sorted(names)
