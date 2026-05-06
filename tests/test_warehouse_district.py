# ruff: noqa: RUF001, RUF002, RUF003
"""Sanity-тесты маппинга WAREHOUSE_TO_DISTRICT и okrug_to_district."""

import pytest

from backend.services.warehouse_district import (
    DISTRICT_ABROAD,
    DISTRICT_CENTRAL,
    DISTRICT_FAR_EAST_SIBERIA,
    DISTRICT_LABELS,
    DISTRICT_NORTHWEST,
    DISTRICT_ORDER,
    DISTRICT_SOUTH_CAUCASUS,
    DISTRICT_UNKNOWN,
    DISTRICT_URAL,
    DISTRICT_VOLGA,
    OKRUG_NAME_TO_KEY,
    WAREHOUSE_LOGISTICS_COEF,
    WAREHOUSE_TO_DISTRICT,
    okrug_to_district,
    warehouse_to_district,
)


class TestWarehouseToDistrict:
    @pytest.mark.parametrize(
        ("warehouse", "expected"),
        [
            ("Коледино", DISTRICT_CENTRAL),
            ("Электросталь", DISTRICT_CENTRAL),
            ("Подольск 3", DISTRICT_CENTRAL),
            ("Голицыно СГТ", DISTRICT_CENTRAL),
            ("Шушары: Питание", DISTRICT_NORTHWEST),
            ("СПБ Шушары", DISTRICT_NORTHWEST),
            ("Краснодар", DISTRICT_SOUTH_CAUCASUS),
            ("Невинномысск", DISTRICT_SOUTH_CAUCASUS),
            ("Казань", DISTRICT_VOLGA),
            ("Сарапул", DISTRICT_VOLGA),
            ("Екатеринбург - Перспективная 14", DISTRICT_URAL),
            ("Челябинск СГТ", DISTRICT_URAL),
            ("Хабаровск", DISTRICT_FAR_EAST_SIBERIA),
            ("Новосибирск", DISTRICT_FAR_EAST_SIBERIA),
            ("Минск", DISTRICT_ABROAD),
            ("Атакент", DISTRICT_ABROAD),
            ("Ташкент 2", DISTRICT_ABROAD),
        ],
    )
    def test_known_warehouses(self, warehouse: str, expected: str) -> None:
        assert warehouse_to_district(warehouse) == expected

    def test_unknown_warehouse(self) -> None:
        assert warehouse_to_district("Несуществующий склад") == DISTRICT_UNKNOWN

    def test_none_or_empty(self) -> None:
        assert warehouse_to_district(None) == DISTRICT_UNKNOWN
        assert warehouse_to_district("") == DISTRICT_UNKNOWN

    def test_strip_whitespace(self) -> None:
        assert warehouse_to_district("  Коледино  ") == DISTRICT_CENTRAL


class TestOkrugToDistrict:
    @pytest.mark.parametrize(
        ("okrug", "country", "expected"),
        [
            ("Центральный федеральный округ", "Россия", DISTRICT_CENTRAL),
            ("Приволжский федеральный округ", "Россия", DISTRICT_VOLGA),
            ("Уральский федеральный округ", "Россия", DISTRICT_URAL),
            ("Северо-Западный федеральный округ", "Россия", DISTRICT_NORTHWEST),
            ("Южный федеральный округ", "Россия", DISTRICT_SOUTH_CAUCASUS),
            ("Северо-Кавказский федеральный округ", "Россия", DISTRICT_SOUTH_CAUCASUS),
            ("Сибирский федеральный округ", "Россия", DISTRICT_FAR_EAST_SIBERIA),
            ("Дальневосточный федеральный округ", "Россия", DISTRICT_FAR_EAST_SIBERIA),
        ],
    )
    def test_okrug_russia(self, okrug: str, country: str, expected: str) -> None:
        assert okrug_to_district(okrug, country) == expected

    def test_country_not_russia_overrides(self) -> None:
        # Заказ доставлен в Беларусь — независимо от okrug → abroad.
        assert okrug_to_district("Минская область", "Беларусь") == DISTRICT_ABROAD
        assert okrug_to_district(None, "Казахстан") == DISTRICT_ABROAD

    def test_unknown_okrug_in_russia(self) -> None:
        assert okrug_to_district("Какой-то округ", "Россия") == DISTRICT_UNKNOWN

    def test_no_okrug_no_country(self) -> None:
        assert okrug_to_district(None, None) == DISTRICT_UNKNOWN
        assert okrug_to_district("", "") == DISTRICT_UNKNOWN


class TestStructure:
    def test_all_districts_have_labels(self) -> None:
        for d in DISTRICT_ORDER:
            assert d in DISTRICT_LABELS, f"missing label for {d}"

    def test_all_warehouses_map_to_known_district(self) -> None:
        valid = set(DISTRICT_ORDER) | {DISTRICT_UNKNOWN}
        for w, d in WAREHOUSE_TO_DISTRICT.items():
            assert d in valid, f"warehouse {w!r} → unknown district key {d!r}"

    def test_okrug_keys_subset_of_districts(self) -> None:
        valid = set(DISTRICT_ORDER)
        for okrug, key in OKRUG_NAME_TO_KEY.items():
            assert key in valid, f"okrug {okrug!r} → unknown key {key!r}"

    def test_logistics_coef_keys_match_warehouses(self) -> None:
        # Не строгое равенство (могут быть склады без коэффициента),
        # но все ключи WAREHOUSE_LOGISTICS_COEF должны быть в WAREHOUSE_TO_DISTRICT.
        extra = set(WAREHOUSE_LOGISTICS_COEF) - set(WAREHOUSE_TO_DISTRICT)
        assert not extra, f"warehouses in coef but not in district map: {extra}"
