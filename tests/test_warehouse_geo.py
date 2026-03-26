"""
Tests for warehouse_geo module — pure geo-mapping functions.
Tests haversine distance, find_nearest_warehouse, and compute_need for hypothetical mode.
"""

import pytest

from backend.services.stock_analytics_service import compute_need
from backend.services.warehouse_geo import (
    REGION_COORDS,
    SC_PREFIX,
    WAREHOUSE_COORDS,
    find_nearest_warehouse,
    haversine,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Tests: haversine distance
# ═══════════════════════════════════════════════════════════════════════════════


class TestHaversine:
    """Verify Haversine distance formula."""

    def test_same_point(self):
        """Distance to self is 0."""
        assert haversine(55.75, 37.62, 55.75, 37.62) == 0.0

    def test_moscow_to_spb(self):
        """Moscow → Saint Petersburg ≈ 634 km."""
        d = haversine(55.75, 37.62, 59.93, 30.32)
        assert 620 < d < 650

    def test_moscow_to_kazan(self):
        """Moscow → Kazan ≈ 719 km."""
        d = haversine(55.75, 37.62, 55.80, 49.11)
        assert 700 < d < 750

    def test_symmetry(self):
        """haversine(A, B) == haversine(B, A)."""
        d1 = haversine(55.75, 37.62, 59.93, 30.32)
        d2 = haversine(59.93, 30.32, 55.75, 37.62)
        assert d1 == pytest.approx(d2, abs=0.01)

    def test_large_distance(self):
        """Moscow → Vladivostok ≈ 6400 km."""
        d = haversine(55.75, 37.62, 43.11, 131.87)
        assert 6300 < d < 6600


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: find_nearest_warehouse
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindNearestWarehouse:
    """Verify nearest warehouse mapping by region name."""

    def test_moscow_region_maps_to_nearby(self):
        """Московская область → should map to a Moscow-area warehouse."""
        all_whs = list(WAREHOUSE_COORDS.keys())
        result = find_nearest_warehouse("Московская область", all_whs)
        assert result is not None
        # Should be one of the Moscow-area warehouses (within ~50km of Moscow)
        moscow_area = [
            "Коледино",
            "Электросталь",
            "Тула",
            "Белая дача",
            "Истра",
            "Вёшки",
            "Обухово",
            "Белые Столбы",
            "Радумля 1",
            "Пушкино",
        ]
        assert any(kw in result for kw in moscow_area)

    def test_spb_region(self):
        """Санкт-Петербург → should map to Санкт-Петербург (Уткина Заводь)."""
        all_whs = list(WAREHOUSE_COORDS.keys())
        result = find_nearest_warehouse("Санкт-Петербург", all_whs)
        assert result is not None
        assert "Санкт-Петербург" in result

    def test_tatarstan_maps_to_kazan(self):
        """Республика Татарстан → should map to Казань."""
        all_whs = list(WAREHOUSE_COORDS.keys())
        result = find_nearest_warehouse("Республика Татарстан", all_whs)
        assert result is not None
        assert "Казань" in result

    def test_unknown_region_returns_none(self):
        """Unknown region → None."""
        result = find_nearest_warehouse("Антарктида", list(WAREHOUSE_COORDS.keys()))
        assert result is None

    def test_empty_warehouses_returns_none(self):
        """No open warehouses → None."""
        result = find_nearest_warehouse("Москва", [])
        assert result is None

    def test_single_warehouse(self):
        """Single open warehouse → always returns it (if region known)."""
        result = find_nearest_warehouse("Московская область", ["Казань"])
        assert result == "Казань"

    def test_limited_warehouses_filters(self):
        """Only open warehouses should be considered."""
        limited = ["Казань", "Новосибирск"]
        result = find_nearest_warehouse("Московская область", limited)
        # Moscow is closer to Kazan than Новосибирск (Siberia)
        assert result == "Казань"

    def test_novosibirsk_region_maps_correctly(self):
        """Новосибирская область → should map to Новосибирск warehouse."""
        all_whs = list(WAREHOUSE_COORDS.keys())
        result = find_nearest_warehouse("Новосибирская область", all_whs)
        assert result is not None
        assert "Новосибирск" in result

    def test_krasnodar_region(self):
        """Краснодарский край → should map to Краснодар."""
        all_whs = list(WAREHOUSE_COORDS.keys())
        result = find_nearest_warehouse("Краснодарский край", all_whs)
        assert result is not None
        assert "Краснодар" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: COORDS consistency
# ═══════════════════════════════════════════════════════════════════════════════


class TestCoordsConsistency:
    """Verify coordinate data integrity."""

    def test_region_coords_not_empty(self):
        assert len(REGION_COORDS) > 50

    def test_warehouse_coords_not_empty(self):
        assert len(WAREHOUSE_COORDS) > 10

    def test_all_coords_valid_range(self):
        """All latitudes [40..80], longitudes [20..180] for Russia."""
        for name, (lat, lon) in REGION_COORDS.items():
            assert 39 <= lat <= 80, f"Bad lat for {name}: {lat}"
            assert 19 <= lon <= 180, f"Bad lon for {name}: {lon}"

    def test_all_wh_coords_valid_range(self):
        for name, (lat, lon) in WAREHOUSE_COORDS.items():
            assert 39 <= lat <= 80, f"Bad lat for {name}: {lat}"
            assert 19 <= lon <= 180, f"Bad lon for {name}: {lon}"


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: compute_need (used in both actual and hypothetical)
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeNeed:
    """Verify restocking need computation."""

    def test_normal_need(self):
        """avg_daily=10, need_days=14, stock=40 → need=100."""
        result = compute_need(40, 10, 14)
        assert result == 100

    def test_stock_exceeds_need(self):
        """Stock > required → need = 0 (never negative)."""
        result = compute_need(200, 10, 14)
        assert result == 0

    def test_zero_sales(self):
        """No sales → need = 0."""
        result = compute_need(100, 0, 14)
        assert result == 0

    def test_zero_stock(self):
        """Zero stock → need = full required."""
        result = compute_need(0, 10, 14)
        assert result == 140

    def test_fractional_avg(self):
        """avg_daily=2.5, need_days=14 → need=35-stock."""
        result = compute_need(10, 2.5, 14)
        assert result == 25

    def test_large_need_days(self):
        """need_days=30, avg_daily=5, stock=10 → need=140."""
        result = compute_need(10, 5, 30)
        assert result == 140


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: Hypothetical warehouse mapping scenario
# ═══════════════════════════════════════════════════════════════════════════════


class TestHypotheticalScenario:
    """End-to-end scenario: simulate hypothetical warehouse mapping."""

    def test_orders_remapped_to_nearest_warehouse(self):
        """Orders from different regions map to nearest open warehouse."""
        open_whs = ["Коледино", "Казань", "Новосибирск"]

        orders = [
            {"regionName": "Московская область", "nmId": 1001, "quantity": 2},
            {"regionName": "Московская область", "nmId": 1001, "quantity": 1},
            {"regionName": "Республика Татарстан", "nmId": 1001, "quantity": 3},
            {"regionName": "Новосибирская область", "nmId": 1001, "quantity": 1},
        ]

        # Simulate the mapping
        wh_orders: dict[tuple[str, int], int] = {}
        for order in orders:
            region = order["regionName"]
            nm_id = order["nmId"]
            qty = order["quantity"]
            wh = find_nearest_warehouse(region, open_whs)
            if wh:
                key = (wh, nm_id)
                wh_orders[key] = wh_orders.get(key, 0) + qty

        # Moscow orders → Коледино
        assert wh_orders.get(("Коледино", 1001), 0) == 3
        # Tatarstan → Казань
        assert wh_orders.get(("Казань", 1001), 0) == 3
        # Novosibirsk → Новосибирск
        assert wh_orders.get(("Новосибирск", 1001), 0) == 1

    def test_sc_prefix_filters_sorting_centers(self):
        """СЦ warehouses should be filtered by SC_PREFIX."""
        wh_names = ["Коледино", "СЦ Махачкала", "СЦ Ярославль", "Казань"]
        filtered = [w for w in wh_names if not w.startswith(SC_PREFIX)]
        assert filtered == ["Коледино", "Казань"]

    def test_article_with_no_stock_at_warehouse_gets_need(self):
        """In hypothetical mode, an article not stocked at a warehouse still shows need."""
        # Scenario: nm_id=1001 has orders mapped to Казань but no stock there
        stock = 0
        avg_daily = 3.0  # mapped orders / days
        need_days = 14
        need = compute_need(stock, avg_daily, need_days)
        assert need == 42  # 3*14 - 0 = 42

    def test_unknown_region_falls_back_to_actual(self):
        """Orders with unknown region should not map to any warehouse."""
        open_whs = ["Коледино", "Казань"]
        result = find_nearest_warehouse("UnknownRegion123", open_whs)
        assert result is None
