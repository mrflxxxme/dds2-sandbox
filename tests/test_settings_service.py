"""
Tests for settings_service — project-level key-value settings.
Uses DB fixtures from conftest.py (project, other_project, db_session).
"""

import pytest

from backend.services.settings_service import (
    get_all_warehouses,
    get_excluded_warehouses,
    get_pallet_boxes_by_size,
    get_pallet_category_compat,
    get_preorder_allowed_warehouses,
    get_setting,
    set_excluded_warehouses,
    set_pallet_boxes_by_size,
    set_pallet_category_compat,
    set_preorder_allowed_warehouses,
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
# Preorder-allowed warehouses (whitelist предзаявок без лимита)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPreorderAllowedWarehouses:
    @pytest.mark.asyncio
    async def test_empty_by_default(self, db_session, project):
        """No preorder-allowed warehouses by default (всё блокируется без лимита)."""
        result = await get_preorder_allowed_warehouses(db_session, project.id)
        assert result == []

    @pytest.mark.asyncio
    async def test_set_and_get(self, db_session, project):
        """Set whitelist and read back."""
        valid = await set_preorder_allowed_warehouses(db_session, project.id, ["Электросталь", "Коледино"])
        assert "Электросталь" in valid
        result = await get_preorder_allowed_warehouses(db_session, project.id)
        assert "Коледино" in result

    @pytest.mark.asyncio
    async def test_parens_and_blank_normalized(self, db_session, project):
        """Parens stripped, blank/whitespace dropped."""
        result = await set_preorder_allowed_warehouses(
            db_session, project.id, ["Краснодар (Тихорецкая)", "", "   ", "Тула"]
        )
        assert result == ["Краснодар", "Тула"]

    @pytest.mark.asyncio
    async def test_independent_from_excluded(self, db_session, project):
        """Whitelist и excluded — разные ключи, не пересекаются."""
        await set_excluded_warehouses(db_session, project.id, ["Новосибирск"])
        await set_preorder_allowed_warehouses(db_session, project.id, ["Электросталь"])
        assert await get_excluded_warehouses(db_session, project.id) == ["Новосибирск"]
        assert await get_preorder_allowed_warehouses(db_session, project.id) == ["Электросталь"]

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Whitelist is project-specific."""
        await set_preorder_allowed_warehouses(db_session, project.id, ["Электросталь"])
        other = await get_preorder_allowed_warehouses(db_session, other_project.id)
        assert other == []


# ═══════════════════════════════════════════════════════════════════════════════
# Pallet boxes-per-size override (ручная норма паллеты по размеру коробки)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPalletBoxesBySize:
    @pytest.mark.asyncio
    async def test_empty_by_default(self, db_session, project):
        assert await get_pallet_boxes_by_size(db_session, project.id) == {}

    @pytest.mark.asyncio
    async def test_set_and_get_with_canon(self, db_session, project):
        """Ключи канонизируются: «60×40×50» / «60X40X50» → «60x40x50»."""
        result = await set_pallet_boxes_by_size(db_session, project.id, {"60×40×50": 16, "60X40X50": 18})
        # Оба написания → один канонический ключ (последнее значение побеждает).
        assert result == {"60x40x50": 18}
        assert await get_pallet_boxes_by_size(db_session, project.id) == {"60x40x50": 18}

    @pytest.mark.asyncio
    async def test_canon_merges_typo_and_separators(self, db_session, project):
        """Канон по числам: «60x40xc40» (опечатка) и «60*40*40» → один ключ «60x40x40»."""
        result = await set_pallet_boxes_by_size(db_session, project.id, {"60x40xc40": 12, "60*40*40": 15})
        assert result == {"60x40x40": 15}  # один ключ, последнее значение

    @pytest.mark.asyncio
    async def test_invalid_values_dropped(self, db_session, project):
        """Значения ≤0 / нечисловые / пустой ключ отбрасываются."""
        result = await set_pallet_boxes_by_size(
            db_session, project.id, {"60x40x50": 16, "40x30x20": 0, "30x20x10": -5, "": 10},
        )
        assert result == {"60x40x50": 16}

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        await set_pallet_boxes_by_size(db_session, project.id, {"60x40x50": 16})
        assert await get_pallet_boxes_by_size(db_session, other_project.id) == {}


# ═══════════════════════════════════════════════════════════════════════════════
# Pallet category compat (правила совместимости категорий на паллете)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPalletCategoryCompat:
    @pytest.mark.asyncio
    async def test_default_disabled_empty(self, db_session, project):
        """Ключ не задан → дефолт: выключено, групп нет."""
        result = await get_pallet_category_compat(db_session, project.id)
        assert result == {"enabled": False, "groups": []}

    @pytest.mark.asyncio
    async def test_set_and_get_roundtrip(self, db_session, project):
        """set → get возвращает ту же форму {enabled, groups[{name, categories}]}."""
        result = await set_pallet_category_compat(
            db_session,
            project.id,
            True,
            [
                {"name": "Мягкое", "categories": ["Коврики", "Полки"]},
                {"name": None, "categories": ["Держатели"]},
            ],
        )
        assert result == {
            "enabled": True,
            "groups": [
                {"name": "Мягкое", "categories": ["Коврики", "Полки"]},
                {"name": None, "categories": ["Держатели"]},
            ],
        }
        assert await get_pallet_category_compat(db_session, project.id) == result

    @pytest.mark.asyncio
    async def test_normalization_trim_and_drop_empty(self, db_session, project):
        """Категории trim'ятся, пустые выкидываются, пустые группы выкидываются."""
        result = await set_pallet_category_compat(
            db_session,
            project.id,
            True,
            [
                {"name": "  Группа  ", "categories": ["  Коврики  ", "", "   "]},
                {"name": "Пустая", "categories": ["", "  "]},
                {"name": None, "categories": []},
            ],
        )
        assert result["groups"] == [{"name": "Группа", "categories": ["Коврики"]}]

    @pytest.mark.asyncio
    async def test_duplicate_across_groups_raises(self, db_session, project):
        """Одна категория в двух группах → ValueError (русский текст)."""
        with pytest.raises(ValueError, match="двух группах"):
            await set_pallet_category_compat(
                db_session,
                project.id,
                True,
                [
                    {"name": None, "categories": ["Коврики", "Полки"]},
                    {"name": None, "categories": ["Держатели", "Коврики"]},
                ],
            )

    @pytest.mark.asyncio
    async def test_duplicate_within_group_raises(self, db_session, project):
        """Дубль категории внутри одной группы → ValueError. Дубль ловится и после trim."""
        with pytest.raises(ValueError, match="повторяется"):
            await set_pallet_category_compat(
                db_session,
                project.id,
                True,
                [{"name": None, "categories": ["Коврики", "  Коврики  "]}],
            )

    @pytest.mark.asyncio
    async def test_broken_json_returns_default(self, db_session, project):
        """Битый JSON в БД → дефолт, не исключение."""
        await set_setting(db_session, project.id, "pallet_category_compat", "{not-json[[")
        result = await get_pallet_category_compat(db_session, project.id)
        assert result == {"enabled": False, "groups": []}

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        await set_pallet_category_compat(
            db_session, project.id, True, [{"name": None, "categories": ["Коврики"]}]
        )
        assert await get_pallet_category_compat(db_session, other_project.id) == {
            "enabled": False,
            "groups": [],
        }


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
