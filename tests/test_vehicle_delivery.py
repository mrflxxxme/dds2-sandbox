"""
Tests for supply_chain/vehicle_delivery.py — Vehicle CRUD, status management,
overview, items management.
"""

import uuid
from decimal import Decimal

import pytest

from backend.models.enums import FactoryOrderStatus, VehicleStatus
from backend.schemas.supply_chain import (
    AddItemsToVehicleRequest,
    AddItemToVehicle,
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
    get_factory_order,
    split_to_vehicles,
)
from backend.services.supply_chain.vehicle_delivery import (
    add_items_to_vehicle,
    apply_price_resync,
    clear_all_vehicle_items,
    create_vehicle,
    delete_vehicle,
    get_available_items,
    get_supply_chain_overview,
    get_vehicle,
    list_vehicles,
    preview_price_resync,
    remove_item_from_vehicle,
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

    @pytest.mark.asyncio
    async def test_change_target_warehouse_syncs_inbound_receipt(self, db_session, project):
        """Смена target_warehouse_id у DISPATCHED-машины тащит за собой
        warehouse_id привязанной приёмки (EXPECTED). Иначе приёмка теряется
        для нового склада (инцидент 2026-05-07: V-0001 → IN-76).
        """
        from sqlalchemy import select

        from backend.models.warehouse import InboundReceipt, InboundStatus
        from backend.services.warehouse_crud import create_warehouse

        wh_old = await create_warehouse(db_session, project.id, {"name": f"old-{_uid()}", "warehouse_type": "EXTERNAL"})
        wh_new = await create_warehouse(
            db_session, project.id, {"name": f"new-{_uid()}", "warehouse_type": "FULFILLMENT"}
        )

        order = await _create_factory_order_with_items(db_session, project.id, [(f"BC-WS-{_uid()}", 10)])
        item_id = order.items[0].id
        order_no = f"V-WS-{_uid()}"
        await create_vehicle(db_session, project.id, VehicleCreate(order_no=order_no, rate_cny=Decimal("12.5")))
        await split_to_vehicles(
            db_session,
            project.id,
            order.id,
            SplitToVehiclesRequest(
                assignments=[
                    SplitItem(factory_order_item_id=item_id, qty=10, vehicle_order_no=order_no),
                ]
            ),
        )
        await update_vehicle_status(db_session, project.id, order_no, VehicleStatusUpdate(status=VehicleStatus.SHIPPED))
        await update_vehicle_status(
            db_session, project.id, order_no, VehicleStatusUpdate(status=VehicleStatus.CUSTOMS, dt_number="DT-1")
        )
        await update_vehicle(
            db_session, project.id, order_no, VehicleUpdate(invoice_no="INV-1", target_warehouse_id=wh_old.id)
        )
        result = await update_vehicle_status(
            db_session, project.id, order_no, VehicleStatusUpdate(status=VehicleStatus.DISPATCHED)
        )
        receipt_id = result["inbound_receipt_id"]

        receipt_before = (
            await db_session.execute(select(InboundReceipt).where(InboundReceipt.id == receipt_id))
        ).scalar_one()
        assert receipt_before.warehouse_id == wh_old.id
        assert receipt_before.status == InboundStatus.EXPECTED

        await update_vehicle(db_session, project.id, order_no, VehicleUpdate(target_warehouse_id=wh_new.id))

        receipt_after = (
            await db_session.execute(select(InboundReceipt).where(InboundReceipt.id == receipt_id))
        ).scalar_one()
        assert (
            receipt_after.warehouse_id == wh_new.id
        ), "warehouse_id приёмки должен синхронизироваться при смене target_warehouse_id"

    @pytest.mark.asyncio
    async def test_change_target_warehouse_blocked_when_receipt_accepted(self, db_session, project):
        """Если приёмка уже ACCEPTED (товар оприходован) — смена склада запрещена."""
        from backend.models.warehouse import InboundReceipt, InboundStatus
        from backend.services.warehouse_crud import create_warehouse

        wh_old = await create_warehouse(db_session, project.id, {"name": f"old-{_uid()}", "warehouse_type": "EXTERNAL"})
        wh_new = await create_warehouse(
            db_session, project.id, {"name": f"new-{_uid()}", "warehouse_type": "FULFILLMENT"}
        )

        order = await _create_factory_order_with_items(db_session, project.id, [(f"BC-WSA-{_uid()}", 5)])
        item_id = order.items[0].id
        order_no = f"V-WSA-{_uid()}"
        await create_vehicle(db_session, project.id, VehicleCreate(order_no=order_no, rate_cny=Decimal("12.5")))
        await split_to_vehicles(
            db_session,
            project.id,
            order.id,
            SplitToVehiclesRequest(
                assignments=[
                    SplitItem(factory_order_item_id=item_id, qty=5, vehicle_order_no=order_no),
                ]
            ),
        )
        await update_vehicle_status(db_session, project.id, order_no, VehicleStatusUpdate(status=VehicleStatus.SHIPPED))
        await update_vehicle_status(
            db_session, project.id, order_no, VehicleStatusUpdate(status=VehicleStatus.CUSTOMS, dt_number="DT-2")
        )
        await update_vehicle(
            db_session, project.id, order_no, VehicleUpdate(invoice_no="INV-2", target_warehouse_id=wh_old.id)
        )
        result = await update_vehicle_status(
            db_session, project.id, order_no, VehicleStatusUpdate(status=VehicleStatus.DISPATCHED)
        )
        receipt_id = result["inbound_receipt_id"]

        from sqlalchemy import update as sql_update

        await db_session.execute(
            sql_update(InboundReceipt).where(InboundReceipt.id == receipt_id).values(status=InboundStatus.ACCEPTED)
        )
        await db_session.commit()

        with pytest.raises(ValueError, match="уже принята"):
            await update_vehicle(db_session, project.id, order_no, VehicleUpdate(target_warehouse_id=wh_new.id))


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

    @pytest.mark.asyncio
    async def test_available_items_excludes_soft_deleted(self, db_session, project):
        order = await _create_factory_order_with_items(
            db_session, project.id, [("BC-AVD-LIVE", 100), ("BC-AVD-DEAD", 200)]
        )
        dead_item = next(i for i in order.items if i.barcode == "BC-AVD-DEAD")
        dead_item.soft_delete()
        await db_session.commit()

        available = await get_available_items(db_session, project.id)
        found = [g for g in available if g["order_id"] == order.id]
        assert len(found) == 1
        barcodes = {it["barcode"] for it in found[0]["items"]}
        assert barcodes == {"BC-AVD-LIVE"}


# ═══════════════════════════════════════════════════════════════════════════════
# Auto-transition of FactoryOrder.status from vehicle-side mutations
# ═══════════════════════════════════════════════════════════════════════════════


class TestFactoryOrderAutoStatus:
    """Verify FactoryOrder.status reacts to add/remove/clear/delete from vehicle side."""

    @pytest.mark.asyncio
    async def test_add_items_to_vehicle_transitions_to_distributed(self, db_session, project):
        order = await _create_factory_order_with_items(db_session, project.id, [("BC-AS1", 50)])
        assert order.status == FactoryOrderStatus.FORMING.value
        item_id = order.items[0].id

        vehicle = await create_vehicle(db_session, project.id, VehicleCreate(order_no=f"V-{_uid()}"))
        await add_items_to_vehicle(
            db_session,
            project.id,
            vehicle.order_no,
            AddItemsToVehicleRequest(items=[AddItemToVehicle(factory_order_item_id=item_id, qty=50)]),
        )

        refreshed = await get_factory_order(db_session, project.id, order.id)
        assert refreshed.status == FactoryOrderStatus.DISTRIBUTED.value

    @pytest.mark.asyncio
    async def test_add_partial_items_stays_in_forming(self, db_session, project):
        order = await _create_factory_order_with_items(db_session, project.id, [("BC-AS2", 100)])
        item_id = order.items[0].id

        vehicle = await create_vehicle(db_session, project.id, VehicleCreate(order_no=f"V-{_uid()}"))
        await add_items_to_vehicle(
            db_session,
            project.id,
            vehicle.order_no,
            AddItemsToVehicleRequest(items=[AddItemToVehicle(factory_order_item_id=item_id, qty=40)]),
        )

        refreshed = await get_factory_order(db_session, project.id, order.id)
        assert refreshed.status == FactoryOrderStatus.FORMING.value

    @pytest.mark.asyncio
    async def test_remove_item_from_vehicle_reverts_to_forming(self, db_session, project):
        order = await _create_factory_order_with_items(db_session, project.id, [("BC-AS3", 30)])
        item_id = order.items[0].id
        vehicle_no = f"V-{_uid()}"

        await split_to_vehicles(
            db_session,
            project.id,
            order.id,
            SplitToVehiclesRequest(
                assignments=[SplitItem(factory_order_item_id=item_id, qty=30, vehicle_order_no=vehicle_no)]
            ),
        )
        refreshed = await get_factory_order(db_session, project.id, order.id)
        assert refreshed.status == FactoryOrderStatus.DISTRIBUTED.value

        # Remove the single CostOrderItem from the vehicle → should roll back to FORMING
        vehicle = await get_vehicle(db_session, project.id, vehicle_no)
        cost_item_id = vehicle.items[0].id
        await remove_item_from_vehicle(db_session, project.id, vehicle_no, cost_item_id)

        refreshed = await get_factory_order(db_session, project.id, order.id)
        assert refreshed.status == FactoryOrderStatus.FORMING.value

    @pytest.mark.asyncio
    async def test_clear_all_vehicle_items_reverts_to_forming(self, db_session, project):
        order = await _create_factory_order_with_items(db_session, project.id, [("BC-AS4", 20)])
        item_id = order.items[0].id
        vehicle_no = f"V-{_uid()}"

        await split_to_vehicles(
            db_session,
            project.id,
            order.id,
            SplitToVehiclesRequest(
                assignments=[SplitItem(factory_order_item_id=item_id, qty=20, vehicle_order_no=vehicle_no)]
            ),
        )
        assert (
            await get_factory_order(db_session, project.id, order.id)
        ).status == FactoryOrderStatus.DISTRIBUTED.value

        await clear_all_vehicle_items(db_session, project.id, vehicle_no)

        refreshed = await get_factory_order(db_session, project.id, order.id)
        assert refreshed.status == FactoryOrderStatus.FORMING.value

    @pytest.mark.asyncio
    async def test_delete_vehicle_reverts_to_forming(self, db_session, project):
        order = await _create_factory_order_with_items(db_session, project.id, [("BC-AS5", 15)])
        item_id = order.items[0].id
        vehicle_no = f"V-{_uid()}"

        await split_to_vehicles(
            db_session,
            project.id,
            order.id,
            SplitToVehiclesRequest(
                assignments=[SplitItem(factory_order_item_id=item_id, qty=15, vehicle_order_no=vehicle_no)]
            ),
        )
        assert (
            await get_factory_order(db_session, project.id, order.id)
        ).status == FactoryOrderStatus.DISTRIBUTED.value

        await delete_vehicle(db_session, project.id, vehicle_no)

        refreshed = await get_factory_order(db_session, project.id, order.id)
        assert refreshed.status == FactoryOrderStatus.FORMING.value

    @pytest.mark.asyncio
    async def test_closed_order_status_not_modified(self, db_session, project):
        """CLOSED is a final status — auto-transition must never roll it back."""
        order = await _create_factory_order_with_items(db_session, project.id, [("BC-AS6", 10)])
        item_id = order.items[0].id
        vehicle_no = f"V-{_uid()}"

        await split_to_vehicles(
            db_session,
            project.id,
            order.id,
            SplitToVehiclesRequest(
                assignments=[SplitItem(factory_order_item_id=item_id, qty=10, vehicle_order_no=vehicle_no)]
            ),
        )

        # Force CLOSED manually
        fo = await get_factory_order(db_session, project.id, order.id)
        fo.status = FactoryOrderStatus.CLOSED.value
        await db_session.commit()

        # Now remove the item from the vehicle — CLOSED must remain
        vehicle = await get_vehicle(db_session, project.id, vehicle_no)
        await remove_item_from_vehicle(db_session, project.id, vehicle_no, vehicle.items[0].id)

        refreshed = await get_factory_order(db_session, project.id, order.id)
        assert refreshed.status == FactoryOrderStatus.CLOSED.value


class TestPriceResync:
    """Resync CostOrderItem.price_cny from linked FactoryOrderItem.price_cny."""

    @pytest.mark.asyncio
    async def test_preview_detects_price_diff(self, db_session, project):
        order = await _create_factory_order_with_items(db_session, project.id, [("BC-RS1", 100)])
        item_id = order.items[0].id
        vehicle = await create_vehicle(db_session, project.id, VehicleCreate(order_no=f"V-{_uid()}"))
        await add_items_to_vehicle(
            db_session,
            project.id,
            vehicle.order_no,
            AddItemsToVehicleRequest(items=[AddItemToVehicle(factory_order_item_id=item_id, qty=100)]),
        )

        # Change factory order item price after assignment
        order.items[0].price_cny = Decimal("25.5")
        await db_session.commit()

        preview = await preview_price_resync(db_session, project.id, vehicle.order_no)
        assert preview.total_items == 1
        assert preview.changed_items == 1
        assert preview.items[0].old_price_cny == Decimal("10")
        assert preview.items[0].new_price_cny == Decimal("25.5")
        # Delta: (25.5 - 10) * 100 = 1550
        assert preview.items[0].delta_sum_cny == Decimal("1550")
        assert preview.sum_delta_cny == Decimal("1550")

    @pytest.mark.asyncio
    async def test_preview_empty_when_prices_match(self, db_session, project):
        order = await _create_factory_order_with_items(db_session, project.id, [("BC-RS2", 50)])
        item_id = order.items[0].id
        vehicle = await create_vehicle(db_session, project.id, VehicleCreate(order_no=f"V-{_uid()}"))
        await add_items_to_vehicle(
            db_session,
            project.id,
            vehicle.order_no,
            AddItemsToVehicleRequest(items=[AddItemToVehicle(factory_order_item_id=item_id, qty=50)]),
        )

        preview = await preview_price_resync(db_session, project.id, vehicle.order_no)
        assert preview.changed_items == 0
        assert preview.sum_delta_cny == Decimal("0")

    @pytest.mark.asyncio
    async def test_apply_updates_price_and_returns_count(self, db_session, project):
        order = await _create_factory_order_with_items(db_session, project.id, [("BC-RS3", 40)])
        item_id = order.items[0].id
        vehicle = await create_vehicle(db_session, project.id, VehicleCreate(order_no=f"V-{_uid()}"))
        await add_items_to_vehicle(
            db_session,
            project.id,
            vehicle.order_no,
            AddItemsToVehicleRequest(items=[AddItemToVehicle(factory_order_item_id=item_id, qty=40)]),
        )

        order.items[0].price_cny = Decimal("33")
        await db_session.commit()

        result = await apply_price_resync(db_session, project.id, vehicle.order_no)
        assert result["applied"] == 1

        refreshed = await get_vehicle(db_session, project.id, vehicle.order_no)
        assert refreshed.items[0].price_cny == Decimal("33")

    @pytest.mark.asyncio
    async def test_apply_noop_when_no_changes(self, db_session, project):
        order = await _create_factory_order_with_items(db_session, project.id, [("BC-RS4", 10)])
        item_id = order.items[0].id
        vehicle = await create_vehicle(db_session, project.id, VehicleCreate(order_no=f"V-{_uid()}"))
        await add_items_to_vehicle(
            db_session,
            project.id,
            vehicle.order_no,
            AddItemsToVehicleRequest(items=[AddItemToVehicle(factory_order_item_id=item_id, qty=10)]),
        )

        result = await apply_price_resync(db_session, project.id, vehicle.order_no)
        assert result["applied"] == 0

    @pytest.mark.asyncio
    async def test_preview_unknown_vehicle_raises(self, db_session, project):
        with pytest.raises(ValueError):
            await preview_price_resync(db_session, project.id, "V-DOES-NOT-EXIST")


# ═══════════════════════════════════════════════════════════════════════════════
# cost_summary excludes soft-deleted items (V-0017 inflated-себестоимость bug)
# ═══════════════════════════════════════════════════════════════════════════════


class TestVehicleCostSummaryExcludesSoftDeleted:
    @pytest.mark.asyncio
    async def test_cost_summary_excludes_soft_deleted_items(self, db_session, project):
        """Page-load cost summary (Товар/Доставка/Пошлина/НДС/Итого) must exclude
        soft-deleted items. Re-upload/replace soft-deletes old rows but keeps their
        stored cost_rub/total_rub; counting them inflated Товар/Итого while the ¥ total
        (active-only) stayed correct — the V-0017 symptom (12.9M ₽ vs 423k ¥)."""
        from backend.models import CostOrder, CostOrderItem

        order_no = f"V-{_uid()}"
        db_session.add(
            CostOrder(
                order_no=order_no,
                project_id=project.id,
                country="CHINA",
                status=VehicleStatus.CUSTOMS,
                rate_cny=Decimal("10.8"),
                rate_usd=Decimal("74"),
                rate_eur=Decimal("84"),
                delivery_cost_cny=Decimal("0"),
                delivery_cost_usd=Decimal("0"),
            )
        )

        def _item(barcode, qty, price, cost, total, *, deleted=False):
            it = CostOrderItem(
                project_id=project.id,
                order_no=order_no,
                barcode=barcode,
                qty=qty,
                price_cny=Decimal(price),
                cost_rub=Decimal(cost),
                delivery_rub=Decimal("0"),
                duty_rub=Decimal("0"),
                vat_rub=Decimal("0"),
                util_rub=Decimal("0"),
                total_rub=Decimal(total),
            )
            if deleted:
                it.soft_delete()
            return it

        # 2 active rows: goods 50¥×2 + 50¥×3 = 250¥ ; cost 540×2 + 540×3 = 2700₽
        db_session.add(_item("BC-A", 2, "50", "540", "570"))
        db_session.add(_item("BC-B", 3, "50", "540", "570"))
        # soft-deleted leftover from an old re-upload at 10× the price — must NOT count
        db_session.add(_item("BC-A", 2, "500", "5400", "5700", deleted=True))
        await db_session.commit()

        vehicle = await get_vehicle(db_session, project.id, order_no)
        assert vehicle.total_cny == Decimal("250")  # ¥ total: active-only (already correct)
        cs = vehicle.cost_summary
        assert cs is not None
        # Active-only: the 5400×2 / 5700×2 dead row must be excluded.
        assert cs.total_cost_rub == Decimal("2700")
        assert cs.total_rub == Decimal("2850")
