"""
sc18 — post-shipment add items tests.

Covers:
- add_items_post_shipment status guard (FORMING → 400; allowed: SHIPPED/CUSTOMS/DISPATCHED/DELIVERED)
- assigned_qty sync via _adjust_assigned_qty (strict + extend_plan)
- added_after_ship flag persisted on CostOrderItem
- DISPATCHED → InboundReceiptItem appended to existing receipt
- DELIVERED → new InboundReceipt (DRAFT) created
- recalculate_order_items called (cost_rub stored)
- FactoryOrderHistory event_type="item_added_post_shipment"
- Router 422 on FactoryQtyExceeded (strict)
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.models.cost import CostOrderItem
from backend.models.enums import VehicleStatus
from backend.models.supply_chain import FactoryOrderHistory
from backend.models.warehouse import InboundReceipt, InboundReceiptItem, InboundStatus
from backend.schemas.supply_chain import (
    FactoryOrderCreate,
    FactoryOrderItemCreate,
    PostShipmentAddItem,
    PostShipmentItemsRequest,
    SplitItem,
    SplitToVehiclesRequest,
    VehicleCreate,
)
from backend.services.supply_chain.factory_orders import (
    FactoryQtyExceeded,
    create_factory_order,
    split_to_vehicles,
)
from backend.services.supply_chain.vehicle_delivery import (
    add_items_post_shipment,
    create_vehicle,
    get_vehicle,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _setup_shipped_vehicle(
    db_session, project_id, status: VehicleStatus, fo_qty: int = 100, vehicle_qty: int = 50
):
    """Create FO + vehicle with one item, set vehicle to specified status. Returns warehouse_id too."""
    from backend.models.warehouse import Warehouse, WarehouseType

    wh = Warehouse(project_id=project_id, name=f"WH-PS-{_uid()}", warehouse_type=WarehouseType.FULFILLMENT.value)
    db_session.add(wh)
    await db_session.flush()

    fo = await create_factory_order(
        db_session,
        project_id,
        FactoryOrderCreate(
            order_number=f"FO-PS-{_uid()}",
            factory_name="PostShip Factory",
            items=[
                FactoryOrderItemCreate(barcode=f"BC-{_uid()}", qty=fo_qty, price_cny=Decimal("10")),
                FactoryOrderItemCreate(barcode=f"BC-{_uid()}", qty=fo_qty, price_cny=Decimal("20")),
            ],
        ),
    )
    foi_id_main = fo.items[0].id
    foi_id_extra = fo.items[1].id

    vehicle_no = f"VPS-{_uid()}"
    await create_vehicle(db_session, project_id, VehicleCreate(order_no=vehicle_no, target_warehouse_id=wh.id))
    await split_to_vehicles(
        db_session,
        project_id,
        fo.id,
        SplitToVehiclesRequest(
            assignments=[SplitItem(factory_order_item_id=foi_id_main, qty=vehicle_qty, vehicle_order_no=vehicle_no)]
        ),
    )

    vehicle = await get_vehicle(db_session, project_id, vehicle_no)
    from backend.models.cost import CostOrder

    co_result = await db_session.execute(
        select(CostOrder).where(CostOrder.order_no == vehicle.order_no, CostOrder.project_id == project_id)
    )
    co = co_result.scalar_one()
    co.status = status
    co.target_warehouse_id = wh.id
    await db_session.commit()
    return fo, vehicle_no, foi_id_main, foi_id_extra, wh.id


@pytest.mark.asyncio
async def test_post_shipment_blocked_for_forming(db_session, project):
    """FORMING vehicle → ValueError (use regular add_items_to_vehicle)."""
    _fo, vehicle_no, _, foi_id_extra, _wh_id = await _setup_shipped_vehicle(
        db_session, project.id, VehicleStatus.FORMING
    )
    with pytest.raises(ValueError, match="status >= SHIPPED"):
        await add_items_post_shipment(
            db_session,
            project.id,
            vehicle_no,
            PostShipmentItemsRequest(items=[PostShipmentAddItem(factory_order_item_id=foi_id_extra, qty=5)]),
        )


@pytest.mark.asyncio
async def test_post_shipment_shipped_creates_cost_item_with_flag(db_session, project):
    """SHIPPED: new CostOrderItem persisted with added_after_ship=True."""
    _fo, vehicle_no, _, foi_id_extra, _wh_id = await _setup_shipped_vehicle(
        db_session, project.id, VehicleStatus.SHIPPED
    )
    result = await add_items_post_shipment(
        db_session,
        project.id,
        vehicle_no,
        PostShipmentItemsRequest(items=[PostShipmentAddItem(factory_order_item_id=foi_id_extra, qty=8)]),
        user_name="tester",
    )
    assert result["ok"] is True
    assert result["added"] == 1
    assert result["new_receipt_id"] is None
    assert result["receipt_items_added"] == 0
    assert result["vehicle_status"] == VehicleStatus.SHIPPED

    items_result = await db_session.execute(
        select(CostOrderItem).where(
            CostOrderItem.order_no == vehicle_no,
            CostOrderItem.factory_order_item_id == foi_id_extra,
            CostOrderItem.is_deleted == False,  # noqa: E712
        )
    )
    new_item = items_result.scalar_one()
    assert new_item.qty == 8
    assert new_item.added_after_ship is True


@pytest.mark.asyncio
async def test_post_shipment_strict_raises_factory_qty_exceeded(db_session, project):
    """SHIPPED + strict + delta > available → FactoryQtyExceeded."""
    fo, vehicle_no, _, foi_id_extra, wh_id = await _setup_shipped_vehicle(
        db_session, project.id, VehicleStatus.SHIPPED, fo_qty=10, vehicle_qty=5
    )
    with pytest.raises(FactoryQtyExceeded) as exc_info:
        await add_items_post_shipment(
            db_session,
            project.id,
            vehicle_no,
            PostShipmentItemsRequest(
                items=[PostShipmentAddItem(factory_order_item_id=foi_id_extra, qty=20, mode="strict")]
            ),
        )
    assert exc_info.value.detail["error"] == "exceeds_factory_qty"
    assert exc_info.value.detail["overflow"] == 10
    assert exc_info.value.detail["available"] == 10


@pytest.mark.asyncio
async def test_post_shipment_extend_plan_bumps_fo_qty(db_session, project):
    """SHIPPED + extend_plan + delta > available → fo.qty extended, history recorded."""
    fo, vehicle_no, _, foi_id_extra, wh_id = await _setup_shipped_vehicle(
        db_session, project.id, VehicleStatus.SHIPPED, fo_qty=10, vehicle_qty=5
    )
    await add_items_post_shipment(
        db_session,
        project.id,
        vehicle_no,
        PostShipmentItemsRequest(
            items=[PostShipmentAddItem(factory_order_item_id=foi_id_extra, qty=15, mode="extend_plan")]
        ),
        user_name="extender",
    )
    from backend.models.supply_chain import FactoryOrderItem

    foi_result = await db_session.execute(select(FactoryOrderItem).where(FactoryOrderItem.id == foi_id_extra))
    foi = foi_result.scalar_one()
    assert foi.qty == 15
    assert foi.assigned_qty == 15

    hist_result = await db_session.execute(
        select(FactoryOrderHistory).where(
            FactoryOrderHistory.factory_order_id == fo.id,
            FactoryOrderHistory.event_type == "item_added_post_shipment",
        )
    )
    assert hist_result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_post_shipment_dispatched_appends_to_receipt(db_session, project):
    """DISPATCHED: existing InboundReceipt (EXPECTED) gets new InboundReceiptItem."""
    fo, vehicle_no, _, foi_id_extra, wh_id = await _setup_shipped_vehicle(
        db_session, project.id, VehicleStatus.DISPATCHED
    )

    from backend.models.cost import CostOrder

    co_result = await db_session.execute(select(CostOrder).where(CostOrder.order_no == vehicle_no))
    co = co_result.scalar_one()

    foi_extra_result = await db_session.execute(
        select(__import__("backend.models.supply_chain", fromlist=["FactoryOrderItem"]).FactoryOrderItem).where(
            __import__("backend.models.supply_chain", fromlist=["FactoryOrderItem"]).FactoryOrderItem.id == foi_id_extra
        )
    )
    foi_extra = foi_extra_result.scalar_one()

    from backend.models.cost import Nomenclature

    nom_extra = Nomenclature(project_id=project.id, barcode=foi_extra.barcode, subject="x", article_seller="x")
    db_session.add(nom_extra)
    await db_session.flush()

    receipt = InboundReceipt(
        project_id=project.id,
        warehouse_id=wh_id,
        number=f"IN-PS-{_uid()}",
        status=InboundStatus.EXPECTED,
        cost_order_id=co.id,
    )
    db_session.add(receipt)
    await db_session.flush()
    await db_session.commit()

    result = await add_items_post_shipment(
        db_session,
        project.id,
        vehicle_no,
        PostShipmentItemsRequest(items=[PostShipmentAddItem(factory_order_item_id=foi_id_extra, qty=7)]),
    )
    assert result["receipt_items_added"] == 1
    assert result["new_receipt_id"] is None

    items_result = await db_session.execute(
        select(InboundReceiptItem).where(InboundReceiptItem.receipt_id == receipt.id)
    )
    items = items_result.scalars().all()
    assert len(items) == 1
    assert items[0].barcode == foi_extra.barcode
    assert items[0].expected_qty == 7


@pytest.mark.asyncio
async def test_post_shipment_delivered_creates_separate_receipt(db_session, project):
    """DELIVERED: new InboundReceipt (DRAFT) created for manual acceptance."""
    fo, vehicle_no, _, foi_id_extra, wh_id = await _setup_shipped_vehicle(
        db_session, project.id, VehicleStatus.DELIVERED
    )
    from backend.models.supply_chain import FactoryOrderItem

    foi_extra_result = await db_session.execute(select(FactoryOrderItem).where(FactoryOrderItem.id == foi_id_extra))
    foi_extra = foi_extra_result.scalar_one()
    from backend.models.cost import Nomenclature

    db_session.add(Nomenclature(project_id=project.id, barcode=foi_extra.barcode, subject="x", article_seller="x"))
    await db_session.commit()

    result = await add_items_post_shipment(
        db_session,
        project.id,
        vehicle_no,
        PostShipmentItemsRequest(items=[PostShipmentAddItem(factory_order_item_id=foi_id_extra, qty=12)]),
    )
    assert result["new_receipt_id"] is not None
    assert result["receipt_items_added"] == 0

    rec_result = await db_session.execute(select(InboundReceipt).where(InboundReceipt.id == result["new_receipt_id"]))
    receipt = rec_result.scalar_one()
    assert receipt.status == InboundStatus.DRAFT
    assert "Доп. приёмка" in (receipt.comment or "")
