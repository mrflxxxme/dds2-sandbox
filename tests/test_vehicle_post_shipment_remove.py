"""
sc19 — post-shipment item removal tests.

Covers:
- DELIVERED → ValueError (запрещено, требуется writeoff)
- SHIPPED/CUSTOMS → restore assigned_qty + recalc, history записывается
- DISPATCHED → + уменьшение expected_qty в существующем InboundReceiptItem
- Mix-group: удаляет всю группу, restore для всех FOI
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.models.cost import CostOrderItem, Nomenclature
from backend.models.enums import VehicleStatus
from backend.models.supply_chain import FactoryOrderHistory, FactoryOrderItem
from backend.models.warehouse import (
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    Warehouse,
    WarehouseType,
)
from backend.schemas.supply_chain import (
    FactoryOrderCreate,
    FactoryOrderItemCreate,
    SplitItem,
    SplitToVehiclesRequest,
    VehicleCreate,
)
from backend.services.supply_chain.factory_orders import (
    create_factory_order,
    split_to_vehicles,
)
from backend.services.supply_chain.vehicle_delivery import (
    create_vehicle,
    get_vehicle,
    remove_item_from_vehicle,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _setup_vehicle(db_session, project_id, status: VehicleStatus, fo_qty: int = 100, vehicle_qty: int = 50):
    """Build FO + vehicle with one item linked, set status, return ids + warehouse."""
    wh = Warehouse(project_id=project_id, name=f"WH-R-{_uid()}", warehouse_type=WarehouseType.FULFILLMENT.value)
    db_session.add(wh)
    await db_session.flush()

    fo = await create_factory_order(
        db_session,
        project_id,
        FactoryOrderCreate(
            order_number=f"FO-R-{_uid()}",
            factory_name="Remove Test Factory",
            items=[FactoryOrderItemCreate(barcode=f"BC-{_uid()}", qty=fo_qty, price_cny=Decimal("10"))],
        ),
    )
    foi_id = fo.items[0].id

    vehicle_no = f"VR-{_uid()}"
    await create_vehicle(db_session, project_id, VehicleCreate(order_no=vehicle_no, target_warehouse_id=wh.id))
    await split_to_vehicles(
        db_session,
        project_id,
        fo.id,
        SplitToVehiclesRequest(
            assignments=[SplitItem(factory_order_item_id=foi_id, qty=vehicle_qty, vehicle_order_no=vehicle_no)]
        ),
    )

    vehicle = await get_vehicle(db_session, project_id, vehicle_no)
    cost_item_id = vehicle.items[0].id

    from backend.models.cost import CostOrder

    co_result = await db_session.execute(
        select(CostOrder).where(CostOrder.order_no == vehicle_no, CostOrder.project_id == project_id)
    )
    co = co_result.scalar_one()
    co.status = status
    co.target_warehouse_id = wh.id
    await db_session.commit()
    return fo, vehicle_no, cost_item_id, foi_id, wh.id, co.id


@pytest.mark.asyncio
async def test_remove_delivered_raises_value_error(db_session, project):
    """DELIVERED → запрещено (нужен writeoff/return)."""
    _fo, vehicle_no, cost_item_id, _foi, _wh, _co_id = await _setup_vehicle(
        db_session, project.id, VehicleStatus.DELIVERED
    )
    with pytest.raises(ValueError, match="запрещено"):
        await remove_item_from_vehicle(db_session, project.id, vehicle_no, cost_item_id)


@pytest.mark.asyncio
async def test_remove_shipped_restores_assigned_qty(db_session, project):
    """SHIPPED → cost_item soft-deleted, fo_item.assigned_qty уменьшается, history записан."""
    fo, vehicle_no, cost_item_id, foi_id, _wh, _co_id = await _setup_vehicle(
        db_session, project.id, VehicleStatus.SHIPPED, fo_qty=100, vehicle_qty=30
    )

    foi_before = (await db_session.execute(select(FactoryOrderItem).where(FactoryOrderItem.id == foi_id))).scalar_one()
    assert foi_before.assigned_qty == 30

    result = await remove_item_from_vehicle(db_session, project.id, vehicle_no, cost_item_id, user_name="tester")
    assert result["ok"] is True
    assert result.get("receipt_items_affected", 0) == 0

    await db_session.refresh(foi_before)
    assert foi_before.assigned_qty == 0

    ci = (await db_session.execute(select(CostOrderItem).where(CostOrderItem.id == cost_item_id))).scalar_one()
    assert ci.is_deleted is True

    hist = (
        await db_session.execute(
            select(FactoryOrderHistory).where(
                FactoryOrderHistory.factory_order_id == fo.id,
                FactoryOrderHistory.event_type == "item_removed_post_shipment",
            )
        )
    ).scalar_one_or_none()
    assert hist is not None


@pytest.mark.asyncio
async def test_remove_dispatched_decrements_receipt_item(db_session, project):
    """DISPATCHED → линкованная EXPECTED-приёмка пересобирается из активных
    cost_items: убрали единственную позицию → строка приёмки по этому баркоду
    удаляется (чище, чем 0-qty призрак)."""
    _fo, vehicle_no, cost_item_id, _foi, wh_id, co_id = await _setup_vehicle(
        db_session, project.id, VehicleStatus.DISPATCHED, fo_qty=100, vehicle_qty=20
    )

    cost = (await db_session.execute(select(CostOrderItem).where(CostOrderItem.id == cost_item_id))).scalar_one()
    db_session.add(Nomenclature(project_id=project.id, barcode=cost.barcode, subject="x", article_seller="x"))
    await db_session.flush()

    receipt = InboundReceipt(
        project_id=project.id,
        warehouse_id=wh_id,
        number=f"IN-R-{_uid()}",
        status=InboundStatus.EXPECTED,
        cost_order_id=co_id,
    )
    db_session.add(receipt)
    await db_session.flush()
    db_session.add(
        InboundReceiptItem(
            project_id=project.id,
            receipt_id=receipt.id,
            nomenclature_id=(await db_session.execute(select(Nomenclature).where(Nomenclature.barcode == cost.barcode)))
            .scalar_one()
            .id,
            barcode=cost.barcode,
            expected_qty=20,
            actual_qty=0,
        )
    )
    await db_session.commit()

    result = await remove_item_from_vehicle(db_session, project.id, vehicle_no, cost_item_id)
    assert result["receipt_items_affected"] == 1

    # Reconcile rebuilt the receipt from active cost items — the removed barcode
    # has none left, so its line is dropped entirely.
    ri = (
        await db_session.execute(select(InboundReceiptItem).where(InboundReceiptItem.receipt_id == receipt.id))
    ).scalar_one_or_none()
    assert ri is None


@pytest.mark.asyncio
async def test_remove_forming_does_not_record_post_shipment_history(db_session, project):
    """FORMING → удаление работает как раньше, БЕЗ history item_removed_post_shipment."""
    fo, vehicle_no, cost_item_id, _foi, _wh, _co_id = await _setup_vehicle(
        db_session, project.id, VehicleStatus.FORMING, fo_qty=100, vehicle_qty=10
    )
    await remove_item_from_vehicle(db_session, project.id, vehicle_no, cost_item_id, user_name="tester")
    hist = (
        await db_session.execute(
            select(FactoryOrderHistory).where(
                FactoryOrderHistory.factory_order_id == fo.id,
                FactoryOrderHistory.event_type == "item_removed_post_shipment",
            )
        )
    ).scalar_one_or_none()
    assert hist is None
