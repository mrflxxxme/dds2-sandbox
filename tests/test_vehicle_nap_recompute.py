"""
Root fix: menять наполнение короба в машине → qty пересчитывается автоматически.

Физический якорь — ЧИСЛО КОРОБОВ: qty = коробов × наполнение. Раньше при смене
`pcs_per_box_override` количество залипало на старом значении (108@9 оставалось 108
после правки наполнения на 5), что и порождало фантомный сток на ФФ.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.models.cost import CostOrderItem
from backend.models.supply_chain import FactoryOrderItem
from backend.schemas.supply_chain import (
    FactoryOrderCreate,
    FactoryOrderItemCreate,
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
    create_vehicle,
    get_vehicle,
    update_vehicle_item,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _factory_linked_item(db_session, project_id, *, fo_qty: int, assigned_qty: int) -> tuple[str, int, int]:
    """Vehicle с позицией, привязанной к фабричному заказу (assigned_qty=cost_item.qty)."""
    order = await create_factory_order(
        db_session, project_id,
        FactoryOrderCreate(
            order_number=f"FO-NAP-{_uid()}",
            items=[FactoryOrderItemCreate(barcode=f"BC-{_uid()}", qty=fo_qty, price_cny=Decimal("10"))],
        ),
    )
    foi_id = order.items[0].id
    vno = f"V-{_uid()}"
    await create_vehicle(db_session, project_id, VehicleCreate(order_no=vno))
    await split_to_vehicles(
        db_session, project_id, order.id,
        SplitToVehiclesRequest(
            assignments=[SplitItem(factory_order_item_id=foi_id, qty=assigned_qty, vehicle_order_no=vno)]
        ),
    )
    vehicle = await get_vehicle(db_session, project_id, vno)
    return vno, vehicle.items[0].id, foi_id


async def _bare_vehicle_item(db_session, project_id, *, qty: int, ppb: int) -> tuple[str, int]:
    """Vehicle + один cost-item без привязки к фабричному заказу."""
    vno = f"V-{_uid()}"
    await create_vehicle(db_session, project_id, VehicleCreate(order_no=vno))
    item = CostOrderItem(
        project_id=project_id,
        order_no=vno,
        barcode=f"BC-{_uid()}",
        qty=qty,
        pcs_per_box_override=ppb,
        box_size_override="60x40x50",
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return vno, item.id


@pytest.mark.asyncio
async def test_lower_fill_recomputes_qty_down(db_session, project):
    """108@9 (12 коробов) → наполнение 5 → qty становится 60 (12×5), не залипает на 108."""
    vno, item_id = await _bare_vehicle_item(db_session, project.id, qty=108, ppb=9)

    await update_vehicle_item(
        db_session, project.id, vno, item_id,
        pcs_per_box_override=5, set_pcs_per_box_override=True,
    )

    item = (await db_session.execute(select(CostOrderItem).where(CostOrderItem.id == item_id))).scalar_one()
    await db_session.refresh(item)
    assert item.pcs_per_box_override == 5
    assert item.qty == 60  # 12 коробов × 5, а не залипшие 108


@pytest.mark.asyncio
async def test_higher_fill_recomputes_qty_up(db_session, project):
    """486@9 (54 короба) → наполнение 11 → qty становится 594 (54×11)."""
    vno, item_id = await _bare_vehicle_item(db_session, project.id, qty=486, ppb=9)

    await update_vehicle_item(
        db_session, project.id, vno, item_id,
        pcs_per_box_override=11, set_pcs_per_box_override=True,
    )

    item = (await db_session.execute(select(CostOrderItem).where(CostOrderItem.id == item_id))).scalar_one()
    await db_session.refresh(item)
    assert item.qty == 594


@pytest.mark.asyncio
async def test_explicit_qty_wins_over_recompute(db_session, project):
    """Если qty передан явно вместе с наполнением — берём переданный qty, не пересчитываем."""
    vno, item_id = await _bare_vehicle_item(db_session, project.id, qty=108, ppb=9)

    await update_vehicle_item(
        db_session, project.id, vno, item_id,
        new_qty=100, pcs_per_box_override=5, set_pcs_per_box_override=True,
    )

    item = (await db_session.execute(select(CostOrderItem).where(CostOrderItem.id == item_id))).scalar_one()
    await db_session.refresh(item)
    assert item.qty == 100  # явный qty, не 60


@pytest.mark.asyncio
async def test_non_multiple_qty_not_recomputed(db_session, project):
    """qty не кратен старому наполнению (число коробов неоднозначно) → qty не трогаем."""
    vno, item_id = await _bare_vehicle_item(db_session, project.id, qty=110, ppb=9)  # 110 % 9 != 0

    await update_vehicle_item(
        db_session, project.id, vno, item_id,
        pcs_per_box_override=5, set_pcs_per_box_override=True,
    )

    item = (await db_session.execute(select(CostOrderItem).where(CostOrderItem.id == item_id))).scalar_one()
    await db_session.refresh(item)
    assert item.pcs_per_box_override == 5
    assert item.qty == 110  # не трогаем — число коробов неоднозначно


@pytest.mark.asyncio
async def test_same_fill_is_noop(db_session, project):
    """Наполнение не изменилось → qty не меняется."""
    vno, item_id = await _bare_vehicle_item(db_session, project.id, qty=108, ppb=9)

    await update_vehicle_item(
        db_session, project.id, vno, item_id,
        pcs_per_box_override=9, set_pcs_per_box_override=True,
    )

    item = (await db_session.execute(select(CostOrderItem).where(CostOrderItem.id == item_id))).scalar_one()
    await db_session.refresh(item)
    assert item.qty == 108


@pytest.mark.asyncio
async def test_preexisting_box_detail_blocks_recompute(db_session, project):
    """Ручная поразрядка коробов (box_detail) — источник истины qty → пересчёт не трогаем."""
    vno, item_id = await _bare_vehicle_item(db_session, project.id, qty=27, ppb=9)
    item = (await db_session.execute(select(CostOrderItem).where(CostOrderItem.id == item_id))).scalar_one()
    item.box_detail_override = [9, 9, 9]  # ручная разбивка на 3 короба
    await db_session.commit()

    await update_vehicle_item(
        db_session, project.id, vno, item_id,
        pcs_per_box_override=5, set_pcs_per_box_override=True,
    )

    await db_session.refresh(item)
    assert item.qty == 27  # qty не пересчитан — box_detail главнее


@pytest.mark.asyncio
async def test_factory_linked_lower_fill_relaxes_assigned(db_session, project):
    """Привязка к фабрике: понижение наполнения → qty падает → assigned_qty расслабляется."""
    vno, item_id, foi_id = await _factory_linked_item(db_session, project.id, fo_qty=200, assigned_qty=108)
    # выставляем наполнение 9 (старого override не было → пересчёта нет, qty=108)
    await update_vehicle_item(db_session, project.id, vno, item_id, pcs_per_box_override=9, set_pcs_per_box_override=True)
    # 9 → 5: 108//9×5 = 60, factory delta -48 расслабляет assigned
    await update_vehicle_item(db_session, project.id, vno, item_id, pcs_per_box_override=5, set_pcs_per_box_override=True)

    item = (await db_session.execute(select(CostOrderItem).where(CostOrderItem.id == item_id))).scalar_one()
    await db_session.refresh(item)
    assert item.qty == 60
    fo_item = (await db_session.execute(select(FactoryOrderItem).where(FactoryOrderItem.id == foi_id))).scalar_one()
    await db_session.refresh(fo_item)
    assert fo_item.assigned_qty == 60


@pytest.mark.asyncio
async def test_factory_linked_higher_fill_exceeds_plan_strict(db_session, project):
    """Привязка к фабрике: повышение наполнения выше плана в strict → FactoryQtyExceeded."""
    vno, item_id, _foi_id = await _factory_linked_item(db_session, project.id, fo_qty=200, assigned_qty=108)
    await update_vehicle_item(db_session, project.id, vno, item_id, pcs_per_box_override=9, set_pcs_per_box_override=True)
    # 9 → 20: 108//9×20 = 240 > план 200 (available=92) → strict raises
    with pytest.raises(FactoryQtyExceeded):
        await update_vehicle_item(
            db_session, project.id, vno, item_id,
            pcs_per_box_override=20, set_pcs_per_box_override=True, mode="strict",
        )
