"""
Tests for supply_chain/vehicle_delivery.py — Vehicle CRUD, status management,
overview, items management.
"""

import uuid
from decimal import Decimal

import pytest

from backend.models.enums import VehicleStatus
from backend.schemas.supply_chain import (
    FactoryOrderCreate,
    FactoryOrderItemCreate,
    SplitItem,
    SplitToVehiclesRequest,
    VehicleCreate,
    VehicleStatusUpdate,
    VehicleUpdate,
)
from backend.services.supply_chain.factory_orders import (
    create_factory_order,
    split_to_vehicles,
)
from backend.services.supply_chain.vehicle_delivery import (
    create_vehicle,
    get_available_items,
    get_supply_chain_overview,
    get_vehicle,
    list_vehicles,
    update_vehicle,
    update_vehicle_status,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _create_factory_order_with_items(db_session, project_id, barcodes_qtys):
    """Helper: create a factory order with items. barcodes_qtys: list of (barcode, qty)."""
    items = [FactoryOrderItemCreate(barcode=bc, qty=q, price_cny=Decimal("10")) for bc, q in barcodes_qtys]
    data = FactoryOrderCreate(
        order_number=f"FO-VD-{_uid()}",
        factory_name="Test Factory",
        items=items,
    )
    return await create_factory_order(db_session, project_id, data)


# ═══════════════════════════════════════════════════════════════════════════════
# create_vehicle
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateVehicle:
    @pytest.mark.asyncio
    async def test_create_vehicle_truck(self, db_session, project):
        data = VehicleCreate(order_no=f"V-{_uid()}", container_type="truck1")
        vehicle = await create_vehicle(db_session, project.id, data)
        assert vehicle.order_no == data.order_no
        assert vehicle.status == VehicleStatus.FORMING
        assert vehicle.transport_type == "AUTO"
        assert vehicle.container_type == "truck1"

    @pytest.mark.asyncio
    async def test_create_vehicle_container(self, db_session, project):
        data = VehicleCreate(
            order_no=f"V-{_uid()}",
            container_type="40ft",
            delivery_cost_cny=Decimal("15000"),
            rate_cny=Decimal("12.5"),
        )
        vehicle = await create_vehicle(db_session, project.id, data)
        assert vehicle.transport_type == "CONTAINER"
        assert vehicle.container_type == "40ft"

    @pytest.mark.asyncio
    async def test_create_vehicle_with_all_fields(self, db_session, project):
        data = VehicleCreate(
            order_no=f"V-{_uid()}",
            container_type="20ft",
            delivery_cost_cny=Decimal("10000"),
            rate_cny=Decimal("12.5"),
            rate_usd=Decimal("92"),
            rate_eur=Decimal("98"),
            ship_date=None,
            invoice_no="INV-001",
        )
        vehicle = await create_vehicle(db_session, project.id, data)
        assert vehicle.invoice_no == "INV-001"


# ═══════════════════════════════════════════════════════════════════════════════
# get_vehicle / list_vehicles
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetVehicle:
    @pytest.mark.asyncio
    async def test_get_found(self, db_session, project):
        order_no = f"V-{_uid()}"
        await create_vehicle(db_session, project.id, VehicleCreate(order_no=order_no))
        found = await get_vehicle(db_session, project.id, order_no)
        assert found is not None
        assert found.order_no == order_no

    @pytest.mark.asyncio
    async def test_get_not_found(self, db_session, project):
        found = await get_vehicle(db_session, project.id, "NONEXISTENT")
        assert found is None

    @pytest.mark.asyncio
    async def test_get_project_isolation(self, db_session, project, other_project):
        order_no = f"V-{_uid()}"
        await create_vehicle(db_session, project.id, VehicleCreate(order_no=order_no))
        found = await get_vehicle(db_session, other_project.id, order_no)
        assert found is None


class TestListVehicles:
    @pytest.mark.asyncio
    async def test_list_returns_vehicles(self, db_session, project):
        order_no = f"V-{_uid()}"
        await create_vehicle(db_session, project.id, VehicleCreate(order_no=order_no))
        vehicles = await list_vehicles(db_session, project.id)
        order_nos = [v.order_no for v in vehicles]
        assert order_no in order_nos


# ═══════════════════════════════════════════════════════════════════════════════
# update_vehicle
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateVehicle:
    @pytest.mark.asyncio
    async def test_update_in_forming(self, db_session, project):
        order_no = f"V-{_uid()}"
        await create_vehicle(db_session, project.id, VehicleCreate(order_no=order_no))
        updated = await update_vehicle(
            db_session,
            project.id,
            order_no,
            VehicleUpdate(invoice_no="INV-UPD"),
        )
        assert updated is not None
        assert updated.invoice_no == "INV-UPD"

    @pytest.mark.asyncio
    async def test_update_not_found(self, db_session, project):
        result = await update_vehicle(
            db_session,
            project.id,
            "NONEXISTENT",
            VehicleUpdate(note="test"),
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# update_vehicle_status — FORMING → SHIPPED → CUSTOMS → DELIVERED
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateVehicleStatus:
    @pytest.mark.asyncio
    async def test_forming_to_shipped(self, db_session, project):
        """FORMING -> SHIPPED with items should succeed."""
        order = await _create_factory_order_with_items(db_session, project.id, [("BC-SH1", 50)])
        item_id = order.items[0].id

        order_no = f"V-SH-{_uid()}"
        await create_vehicle(
            db_session,
            project.id,
            VehicleCreate(order_no=order_no, rate_cny=Decimal("12.5")),
        )
        await split_to_vehicles(
            db_session,
            project.id,
            order.id,
            SplitToVehiclesRequest(
                assignments=[
                    SplitItem(factory_order_item_id=item_id, qty=50, vehicle_order_no=order_no),
                ]
            ),
        )

        result = await update_vehicle_status(
            db_session,
            project.id,
            order_no,
            VehicleStatusUpdate(status=VehicleStatus.SHIPPED),
        )
        assert result["ok"] is True
        assert result["status"] == VehicleStatus.SHIPPED

    @pytest.mark.asyncio
    async def test_ship_empty_vehicle_fails(self, db_session, project):
        order_no = f"V-EMPTY-{_uid()}"
        await create_vehicle(db_session, project.id, VehicleCreate(order_no=order_no))
        with pytest.raises(ValueError, match="пуст"):
            await update_vehicle_status(
                db_session,
                project.id,
                order_no,
                VehicleStatusUpdate(status=VehicleStatus.SHIPPED),
            )

    @pytest.mark.asyncio
    async def test_invalid_transition(self, db_session, project):
        order_no = f"V-INV-{_uid()}"
        await create_vehicle(db_session, project.id, VehicleCreate(order_no=order_no))
        with pytest.raises(ValueError, match="Нельзя перейти"):
            await update_vehicle_status(
                db_session,
                project.id,
                order_no,
                VehicleStatusUpdate(status=VehicleStatus.CUSTOMS),
            )

    @pytest.mark.asyncio
    async def test_vehicle_not_found(self, db_session, project):
        with pytest.raises(ValueError, match="not found"):
            await update_vehicle_status(
                db_session,
                project.id,
                "NONEXISTENT",
                VehicleStatusUpdate(status=VehicleStatus.SHIPPED),
            )


# ═══════════════════════════════════════════════════════════════════════════════
# get_supply_chain_overview
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetSupplyChainOverview:
    @pytest.mark.asyncio
    async def test_overview_empty(self, db_session, project):
        overview = await get_supply_chain_overview(db_session, project.id)
        assert overview["total_factory_orders"] == 0
        assert overview["total_vehicles"] == 0
        assert overview["total_items"] == 0
        assert overview["total_amount_cny"] == 0
        assert isinstance(overview["vehicles_by_status"], dict)

    @pytest.mark.asyncio
    async def test_overview_counts_factory_orders(self, db_session, project):
        await _create_factory_order_with_items(db_session, project.id, [("BC-OV1", 100)])
        overview = await get_supply_chain_overview(db_session, project.id)
        assert overview["total_factory_orders"] >= 1

    @pytest.mark.asyncio
    async def test_overview_project_isolation(self, db_session, project, other_project):
        await _create_factory_order_with_items(db_session, project.id, [("BC-OVI", 100)])
        overview = await get_supply_chain_overview(db_session, other_project.id)
        assert overview["total_factory_orders"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# get_available_items
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetAvailableItems:
    @pytest.mark.asyncio
    async def test_available_items_full_unassigned(self, db_session, project):
        order = await _create_factory_order_with_items(db_session, project.id, [("BC-AV1", 100)])
        available = await get_available_items(db_session, project.id)
        found = [g for g in available if g["order_id"] == order.id]
        assert len(found) == 1
        assert found[0]["items"][0]["remaining_qty"] == 100

    @pytest.mark.asyncio
    async def test_available_items_partially_assigned(self, db_session, project):
        order = await _create_factory_order_with_items(db_session, project.id, [("BC-AV2", 100)])
        item_id = order.items[0].id

        await split_to_vehicles(
            db_session,
            project.id,
            order.id,
            SplitToVehiclesRequest(
                assignments=[
                    SplitItem(factory_order_item_id=item_id, qty=60, vehicle_order_no=f"V-{_uid()}"),
                ]
            ),
        )

        available = await get_available_items(db_session, project.id)
        found = [g for g in available if g["order_id"] == order.id]
        assert found[0]["items"][0]["remaining_qty"] == 40
