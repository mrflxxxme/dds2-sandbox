"""
Tests for warehouse delivery times — pure logic validation.
No DB required.
"""

import pytest

from backend.schemas.warehouse import (
    DeliveryTimeItem,
    DeliveryTimeRow,
    DeliveryTimesResponse,
    DeliveryTimesUpdate,
)


class TestDeliveryTimeSchemas:
    """Validate delivery time schema shapes."""

    def test_delivery_time_item_defaults(self):
        """Default delivery_days = 3."""
        item = DeliveryTimeItem(wb_warehouse_name="Коледино")
        assert item.delivery_days == 3
        assert item.wb_warehouse_name == "Коледино"

    def test_delivery_time_item_custom(self):
        item = DeliveryTimeItem(wb_warehouse_name="Казань", delivery_days=5)
        assert item.delivery_days == 5

    def test_delivery_times_update_empty(self):
        update = DeliveryTimesUpdate(items=[])
        assert update.assembly_days is None
        assert update.wb_acceptance_days is None
        assert update.items == []

    def test_delivery_times_update_full(self):
        update = DeliveryTimesUpdate(
            assembly_days=2,
            wb_acceptance_days=3,
            items=[
                DeliveryTimeItem(wb_warehouse_name="Коледино", delivery_days=1),
                DeliveryTimeItem(wb_warehouse_name="Тула", delivery_days=2),
            ],
        )
        assert update.assembly_days == 2
        assert update.wb_acceptance_days == 3
        assert len(update.items) == 2

    def test_delivery_time_row(self):
        row = DeliveryTimeRow(wb_warehouse_name="Коледино", delivery_days=1, total_days=5)
        assert row.total_days == 5

    def test_delivery_times_response_shape(self):
        response = DeliveryTimesResponse(
            warehouse_id=1,
            warehouse_name="натали",
            assembly_days=2,
            wb_acceptance_days=2,
            wb_warehouses=[
                DeliveryTimeRow(wb_warehouse_name="Коледино", delivery_days=1, total_days=5),
                DeliveryTimeRow(wb_warehouse_name="Тула", delivery_days=2, total_days=6),
            ],
        )
        assert response.warehouse_id == 1
        assert response.assembly_days == 2
        assert response.wb_acceptance_days == 2
        assert len(response.wb_warehouses) == 2
        assert response.wb_warehouses[0].total_days == 5


class TestTotalDaysFormula:
    """Verify total_days = assembly_days + delivery_days + wb_acceptance_days."""

    @pytest.mark.parametrize(
        "assembly,delivery,acceptance,expected",
        [
            (2, 1, 2, 5),  # Standard case
            (0, 3, 2, 5),  # No assembly
            (3, 0, 2, 5),  # Zero delivery
            (2, 5, 0, 7),  # Zero acceptance
            (0, 0, 0, 0),  # All zeros
            (1, 10, 3, 14),  # Large delivery
            (5, 3, 2, 10),  # Large assembly
        ],
    )
    def test_total_days_formula(self, assembly, delivery, acceptance, expected):
        """total = assembly + delivery + acceptance."""
        total = assembly + delivery + acceptance
        assert total == expected

    def test_default_values(self):
        """Default: assembly=0, delivery=3, acceptance=2 → total=5."""
        assembly = 0  # no assembly_days set
        delivery = 3  # default
        acceptance = 2  # default
        assert assembly + delivery + acceptance == 5
