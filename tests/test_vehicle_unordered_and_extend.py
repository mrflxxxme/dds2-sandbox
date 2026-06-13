"""
Tests for the «наполнение машины» расхождения-flows (vehicle-fill diff modal):

- bulk add with mode=extend_plan grows the original order line (корзина «!»);
- add_unordered_items_to_vehicle: create new order / attach to existing, then book
  onto the vehicle (корзина «✗»), including the post-shipment branch.
"""

import uuid
from decimal import Decimal

import pydantic
import pytest
from sqlalchemy import select

from backend.models.cost import CostOrder, CostOrderItem
from backend.models.enums import FactoryOrderStatus, VehicleStatus
from backend.models.supply_chain import FactoryOrder, FactoryOrderHistory
from backend.schemas.supply_chain import (
    AddItemsToVehicleRequest,
    AddItemToVehicle,
    AddUnorderedItemsRequest,
    FactoryOrderCreate,
    FactoryOrderItemCreate,
    UnorderedItem,
    VehicleCreate,
)
from backend.services.supply_chain.factory_orders import create_factory_order, get_factory_order
from backend.services.supply_chain.vehicle_delivery import (
    add_items_to_vehicle,
    add_unordered_items_to_vehicle,
    create_vehicle,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _order_with_item(db_session, project_id, barcode, qty):
    fo = await create_factory_order(
        db_session,
        project_id,
        FactoryOrderCreate(
            order_number=f"FO-UX-{_uid()}",
            items=[FactoryOrderItemCreate(barcode=barcode, qty=qty, price_cny=Decimal("10"))],
        ),
    )
    return fo, fo.items[0].id


async def _set_vehicle_status(db_session, project_id, order_no, status):
    co = (
        await db_session.execute(
            select(CostOrder).where(CostOrder.order_no == order_no, CostOrder.project_id == project_id)
        )
    ).scalar_one()
    co.status = status
    await db_session.commit()


async def _vehicle_items(db_session, project_id, order_no):
    rows = (
        (
            await db_session.execute(
                select(CostOrderItem).where(
                    CostOrderItem.order_no == order_no,
                    CostOrderItem.project_id == project_id,
                    CostOrderItem.is_deleted == False,  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )
    return {r.barcode: r for r in rows}


# ─── Goal 1: «!» bucket — extend_plan on the bulk add path ────────────────────


@pytest.mark.asyncio
async def test_bulk_add_extend_plan_grows_order_line(db_session, project):
    fo, item_id = await _order_with_item(db_session, project.id, "BC-EXT1", 50)
    vehicle = await create_vehicle(db_session, project.id, VehicleCreate(order_no=f"V-{_uid()}"))

    # ask for 80 while only 50 ordered → plan auto-grows to 80
    await add_items_to_vehicle(
        db_session,
        project.id,
        vehicle.order_no,
        AddItemsToVehicleRequest(items=[AddItemToVehicle(factory_order_item_id=item_id, qty=80, mode="extend_plan")]),
        user_name="Tester",
    )

    refreshed = await get_factory_order(db_session, project.id, fo.id)
    foi = refreshed.items[0]
    assert foi.qty == 80
    assert foi.assigned_qty == 80

    hist = (
        (
            await db_session.execute(
                select(FactoryOrderHistory).where(
                    FactoryOrderHistory.factory_order_id == fo.id,
                    FactoryOrderHistory.event_type == "qty_extended_from_vehicle",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(hist) == 1

    veh_items = await _vehicle_items(db_session, project.id, vehicle.order_no)
    assert veh_items["BC-EXT1"].qty == 80


@pytest.mark.asyncio
async def test_bulk_add_strict_exceed_still_raises(db_session, project):
    """Default strict mode keeps the old behaviour (no silent plan growth)."""
    fo, item_id = await _order_with_item(db_session, project.id, "BC-EXT2", 50)
    vehicle = await create_vehicle(db_session, project.id, VehicleCreate(order_no=f"V-{_uid()}"))
    with pytest.raises(ValueError, match="remaining"):
        await add_items_to_vehicle(
            db_session,
            project.id,
            vehicle.order_no,
            AddItemsToVehicleRequest(items=[AddItemToVehicle(factory_order_item_id=item_id, qty=80)]),
        )


# ─── Goal 2: «✗» bucket — create new order / attach to existing ───────────────


@pytest.mark.asyncio
async def test_unordered_new_order_forming(db_session, project):
    vehicle = await create_vehicle(db_session, project.id, VehicleCreate(order_no=f"V-{_uid()}"))
    result = await add_unordered_items_to_vehicle(
        db_session,
        project.id,
        vehicle.order_no,
        AddUnorderedItemsRequest(
            target="new_order",
            items=[
                UnorderedItem(barcode="BC-NEW1", qty=12, price_cny=Decimal("31"), box_size="60x40x50", pcs_per_box=40),
                UnorderedItem(barcode="BC-NEW2", qty=8, price_cny=Decimal("44.5")),
            ],
        ),
        user_name="Tester",
    )
    assert result["ok"] is True
    assert result["added"] == 2

    fo = await get_factory_order(db_session, project.id, result["factory_order_id"])
    assert fo is not None
    assert fo.order_number.startswith(f"{vehicle.order_no}-Д")
    by_bc = {i.barcode: i for i in fo.items}
    assert by_bc["BC-NEW1"].qty == 12 and by_bc["BC-NEW1"].assigned_qty == 12
    assert by_bc["BC-NEW1"].box_size == "60x40x50" and by_bc["BC-NEW1"].pcs_per_box == 40
    assert by_bc["BC-NEW1"].price_cny == Decimal("31")

    veh_items = await _vehicle_items(db_session, project.id, vehicle.order_no)
    assert veh_items["BC-NEW1"].qty == 12 and veh_items["BC-NEW2"].qty == 8
    assert veh_items["BC-NEW1"].factory_order_item_id == by_bc["BC-NEW1"].id


@pytest.mark.asyncio
async def test_unordered_new_order_autonumber_increments(db_session, project):
    vehicle = await create_vehicle(db_session, project.id, VehicleCreate(order_no=f"V-{_uid()}"))
    r1 = await add_unordered_items_to_vehicle(
        db_session,
        project.id,
        vehicle.order_no,
        AddUnorderedItemsRequest(
            target="new_order", items=[UnorderedItem(barcode="BC-A", qty=1, price_cny=Decimal("1"))]
        ),
    )
    r2 = await add_unordered_items_to_vehicle(
        db_session,
        project.id,
        vehicle.order_no,
        AddUnorderedItemsRequest(
            target="new_order", items=[UnorderedItem(barcode="BC-B", qty=1, price_cny=Decimal("1"))]
        ),
    )
    fo1 = await get_factory_order(db_session, project.id, r1["factory_order_id"])
    fo2 = await get_factory_order(db_session, project.id, r2["factory_order_id"])
    assert fo1.order_number == f"{vehicle.order_no}-Д1"
    assert fo2.order_number == f"{vehicle.order_no}-Д2"


@pytest.mark.asyncio
async def test_unordered_existing_order_merges_and_books(db_session, project):
    fo, _item_id = await _order_with_item(db_session, project.id, "BC-MERGE", 10)
    vehicle = await create_vehicle(db_session, project.id, VehicleCreate(order_no=f"V-{_uid()}"))
    result = await add_unordered_items_to_vehicle(
        db_session,
        project.id,
        vehicle.order_no,
        AddUnorderedItemsRequest(
            target="existing_order",
            factory_order_id=fo.id,
            # incoming price differs — must NOT overwrite the negotiated order-line price
            items=[UnorderedItem(barcode="BC-MERGE", qty=5, price_cny=Decimal("99"))],
        ),
    )
    assert result["factory_order_id"] == fo.id

    refreshed = await get_factory_order(db_session, project.id, fo.id)
    foi = refreshed.items[0]
    assert foi.qty == 15  # merged 10 + 5
    assert foi.assigned_qty == 5  # only the freshly-added 5 booked onto the vehicle
    assert foi.price_cny == Decimal("10")  # existing price preserved, not overwritten by 99

    veh_items = await _vehicle_items(db_session, project.id, vehicle.order_no)
    assert veh_items["BC-MERGE"].qty == 5


@pytest.mark.asyncio
async def test_unordered_existing_order_status_guard(db_session, project):
    fo, _item_id = await _order_with_item(db_session, project.id, "BC-CLOSED", 10)
    order_obj = (await db_session.execute(select(FactoryOrder).where(FactoryOrder.id == fo.id))).scalar_one()
    order_obj.status = FactoryOrderStatus.CLOSED.value
    await db_session.commit()

    vehicle = await create_vehicle(db_session, project.id, VehicleCreate(order_no=f"V-{_uid()}"))
    with pytest.raises(ValueError, match="статус"):
        await add_unordered_items_to_vehicle(
            db_session,
            project.id,
            vehicle.order_no,
            AddUnorderedItemsRequest(
                target="existing_order",
                factory_order_id=fo.id,
                items=[UnorderedItem(barcode="BC-CLOSED", qty=5, price_cny=Decimal("10"))],
            ),
        )


@pytest.mark.asyncio
async def test_unordered_existing_requires_factory_order_id():
    with pytest.raises(pydantic.ValidationError):
        AddUnorderedItemsRequest(target="existing_order", items=[UnorderedItem(barcode="X", qty=1)])


@pytest.mark.asyncio
async def test_unordered_new_order_post_shipment(db_session, project):
    vehicle = await create_vehicle(db_session, project.id, VehicleCreate(order_no=f"V-{_uid()}"))
    await _set_vehicle_status(db_session, project.id, vehicle.order_no, VehicleStatus.SHIPPED)

    result = await add_unordered_items_to_vehicle(
        db_session,
        project.id,
        vehicle.order_no,
        AddUnorderedItemsRequest(
            target="new_order",
            items=[UnorderedItem(barcode="BC-PS-NEW", qty=7, price_cny=Decimal("9"))],
        ),
    )
    assert result["ok"] is True

    veh_items = await _vehicle_items(db_session, project.id, vehicle.order_no)
    ci = veh_items["BC-PS-NEW"]
    assert ci.qty == 7
    assert ci.added_after_ship is True
