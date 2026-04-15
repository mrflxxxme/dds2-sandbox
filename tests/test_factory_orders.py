"""
Tests for supply_chain/factory_orders.py — Factory orders CRUD + split to vehicles.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.schemas.supply_chain import (
    FactoryOrderCreate,
    FactoryOrderItemCreate,
    FactoryOrderUpdate,
    SplitItem,
    SplitToVehiclesRequest,
)
from backend.services.supply_chain.factory_orders import (
    add_items,
    create_factory_order,
    delete_factory_order,
    get_factory_order,
    get_factory_orders,
    split_to_vehicles,
    update_factory_order,
    update_factory_order_status,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ═══════════════════════════════════════════════════════════════════════════════
# create_factory_order
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateFactoryOrder:
    @pytest.mark.asyncio
    async def test_create_without_items(self, db_session: AsyncSession, project):
        data = FactoryOrderCreate(
            order_number=f"FO-{_uid()}",
            factory_name="Test Factory",
            note="test note",
        )
        order = await create_factory_order(db_session, project.id, data)
        assert order.id is not None
        assert order.project_id == project.id
        assert order.factory_name == "Test Factory"
        assert order.is_deleted is False

    @pytest.mark.asyncio
    async def test_create_with_items(self, db_session: AsyncSession, project):
        data = FactoryOrderCreate(
            order_number=f"FO-{_uid()}",
            items=[
                FactoryOrderItemCreate(barcode="BC-001", qty=100, price_cny=Decimal("15.50")),
                FactoryOrderItemCreate(barcode="BC-002", qty=200, price_cny=Decimal("25.00")),
            ],
        )
        order = await create_factory_order(db_session, project.id, data)
        assert len(order.items) == 2
        assert order.items[0].qty == 100
        assert order.items[0].assigned_qty == 0


# ═══════════════════════════════════════════════════════════════════════════════
# get_factory_orders (list)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetFactoryOrders:
    @pytest.mark.asyncio
    async def test_list_empty(self, db_session, project):
        orders = await get_factory_orders(db_session, project.id)
        assert isinstance(orders, list)

    @pytest.mark.asyncio
    async def test_list_returns_created(self, db_session, project):
        data = FactoryOrderCreate(order_number=f"FO-{_uid()}")
        await create_factory_order(db_session, project.id, data)
        orders = await get_factory_orders(db_session, project.id)
        assert len(orders) >= 1

    @pytest.mark.asyncio
    async def test_list_excludes_deleted(self, db_session, project):
        data = FactoryOrderCreate(order_number=f"FO-DEL-{_uid()}")
        order = await create_factory_order(db_session, project.id, data)
        await delete_factory_order(db_session, project.id, order.id)
        orders = await get_factory_orders(db_session, project.id)
        ids = [o.id for o in orders]
        assert order.id not in ids

    @pytest.mark.asyncio
    async def test_list_project_isolation(self, db_session, project, other_project):
        data = FactoryOrderCreate(order_number=f"FO-ISO-{_uid()}")
        order = await create_factory_order(db_session, project.id, data)
        orders = await get_factory_orders(db_session, other_project.id)
        ids = [o.id for o in orders]
        assert order.id not in ids


# ═══════════════════════════════════════════════════════════════════════════════
# get_factory_order (single)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetFactoryOrder:
    @pytest.mark.asyncio
    async def test_get_found(self, db_session, project):
        data = FactoryOrderCreate(order_number=f"FO-{_uid()}")
        order = await create_factory_order(db_session, project.id, data)
        found = await get_factory_order(db_session, project.id, order.id)
        assert found is not None
        assert found.id == order.id

    @pytest.mark.asyncio
    async def test_get_not_found(self, db_session, project):
        found = await get_factory_order(db_session, project.id, 999999)
        assert found is None

    @pytest.mark.asyncio
    async def test_get_project_isolation(self, db_session, project, other_project):
        data = FactoryOrderCreate(order_number=f"FO-{_uid()}")
        order = await create_factory_order(db_session, project.id, data)
        found = await get_factory_order(db_session, other_project.id, order.id)
        assert found is None


# ═══════════════════════════════════════════════════════════════════════════════
# update_factory_order
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateFactoryOrder:
    @pytest.mark.asyncio
    async def test_update_factory_name(self, db_session, project):
        data = FactoryOrderCreate(order_number=f"FO-{_uid()}", factory_name="Old")
        order = await create_factory_order(db_session, project.id, data)
        updated = await update_factory_order(
            db_session,
            project.id,
            order.id,
            FactoryOrderUpdate(factory_name="New"),
        )
        assert updated is not None
        assert updated.factory_name == "New"

    @pytest.mark.asyncio
    async def test_update_not_found(self, db_session, project):
        result = await update_factory_order(
            db_session,
            project.id,
            999999,
            FactoryOrderUpdate(factory_name="X"),
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# delete_factory_order (soft delete)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeleteFactoryOrder:
    @pytest.mark.asyncio
    async def test_soft_delete(self, db_session, project):
        data = FactoryOrderCreate(order_number=f"FO-{_uid()}")
        order = await create_factory_order(db_session, project.id, data)
        result = await delete_factory_order(db_session, project.id, order.id)
        assert result is True
        found = await get_factory_order(db_session, project.id, order.id)
        assert found is None

    @pytest.mark.asyncio
    async def test_delete_not_found(self, db_session, project):
        result = await delete_factory_order(db_session, project.id, 999999)
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# add_items
# ═══════════════════════════════════════════════════════════════════════════════


class TestAddItems:
    @pytest.mark.asyncio
    async def test_add_items(self, db_session, project):
        data = FactoryOrderCreate(order_number=f"FO-{_uid()}")
        order = await create_factory_order(db_session, project.id, data)

        items = await add_items(
            db_session,
            project.id,
            order.id,
            [FactoryOrderItemCreate(barcode="BC-A1", qty=50, price_cny=Decimal("10.00"))],
        )
        assert len(items) == 1
        assert items[0].qty == 50

    @pytest.mark.asyncio
    async def test_add_items_order_not_found(self, db_session, project):
        with pytest.raises(ValueError, match="not found"):
            await add_items(
                db_session,
                project.id,
                999999,
                [FactoryOrderItemCreate(barcode="X", qty=1, price_cny=Decimal("1"))],
            )


# ═══════════════════════════════════════════════════════════════════════════════
# split_to_vehicles
# ═══════════════════════════════════════════════════════════════════════════════


class TestSplitToVehicles:
    @pytest.mark.asyncio
    async def test_split_happy_path(self, db_session, project):
        data = FactoryOrderCreate(
            order_number=f"FO-{_uid()}",
            items=[
                FactoryOrderItemCreate(barcode="BC-SP1", qty=100, price_cny=Decimal("10")),
            ],
        )
        order = await create_factory_order(db_session, project.id, data)
        item_id = order.items[0].id

        result = await split_to_vehicles(
            db_session,
            project.id,
            order.id,
            SplitToVehiclesRequest(
                assignments=[
                    SplitItem(factory_order_item_id=item_id, qty=60, vehicle_order_no=f"V-{_uid()}"),
                    SplitItem(factory_order_item_id=item_id, qty=40, vehicle_order_no=f"V-{_uid()}"),
                ]
            ),
        )
        assert result["ok"] is True
        assert result["created_cost_items"] == 2

        # Verify assigned_qty updated
        refreshed = await get_factory_order(db_session, project.id, order.id)
        assert refreshed.items[0].assigned_qty == 100

    @pytest.mark.asyncio
    async def test_split_exceeds_qty(self, db_session, project):
        data = FactoryOrderCreate(
            order_number=f"FO-{_uid()}",
            items=[
                FactoryOrderItemCreate(barcode="BC-EX", qty=10, price_cny=Decimal("5")),
            ],
        )
        order = await create_factory_order(db_session, project.id, data)
        item_id = order.items[0].id

        with pytest.raises(ValueError, match="Cannot assign"):
            await split_to_vehicles(
                db_session,
                project.id,
                order.id,
                SplitToVehiclesRequest(
                    assignments=[
                        SplitItem(factory_order_item_id=item_id, qty=15, vehicle_order_no=f"V-{_uid()}"),
                    ]
                ),
            )

    @pytest.mark.asyncio
    async def test_split_order_not_found(self, db_session, project):
        with pytest.raises(ValueError, match="not found"):
            await split_to_vehicles(
                db_session,
                project.id,
                999999,
                SplitToVehiclesRequest(assignments=[]),
            )


# ═══════════════════════════════════════════════════════════════════════════════
# update_factory_order_status — READY removal
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateFactoryOrderStatus:
    @pytest.mark.asyncio
    async def test_rejects_ready_status(self, db_session, project):
        """READY was removed from FactoryOrderStatus — it must no longer be accepted."""
        data = FactoryOrderCreate(order_number=f"FO-{_uid()}", factory_name="X")
        order = await create_factory_order(db_session, project.id, data)

        with pytest.raises(ValueError, match="READY"):
            await update_factory_order_status(db_session, project.id, order.id, "READY")

    @pytest.mark.asyncio
    async def test_accepts_valid_statuses(self, db_session, project):
        data = FactoryOrderCreate(order_number=f"FO-{_uid()}", factory_name="X")
        order = await create_factory_order(db_session, project.id, data)

        updated = await update_factory_order_status(db_session, project.id, order.id, "DISTRIBUTED")
        assert updated is not None
        assert updated.status == "DISTRIBUTED"
