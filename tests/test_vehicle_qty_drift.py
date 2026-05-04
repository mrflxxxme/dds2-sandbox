"""
Tests for sc17 — Vehicle qty drift confirmation.

Covers:
- update_vehicle_item with mode=strict / mode=extend_plan
- _adjust_assigned_qty edge cases (clamp, no-op, mix-group)
- _enrich_vehicle qty_drift / fo_qty / fo_assigned exposure
- Router: 422 with structured detail on FactoryQtyExceeded
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.models.enums import FactoryOrderStatus
from backend.models.supply_chain import FactoryOrderHistory, FactoryOrderItem
from backend.schemas.supply_chain import (
    AddItemsToVehicleRequest,
    AddItemToVehicle,
    FactoryOrderCreate,
    FactoryOrderItemCreate,
    MixGroupItemInput,
    SplitItem,
    SplitToVehiclesRequest,
    VehicleCreate,
)
from backend.services.supply_chain.factory_orders import (
    FactoryQtyExceeded,
    create_factory_order,
    get_factory_order,
    set_mix_group,
    split_to_vehicles,
)
from backend.services.supply_chain.vehicle_delivery import (
    add_items_to_vehicle,
    create_vehicle,
    get_vehicle,
    update_vehicle_item,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _create_factory_order_with_items(db_session, project_id, barcodes_qtys):
    """Helper: create a factory order with items. barcodes_qtys: list of (barcode, qty)."""
    items = [FactoryOrderItemCreate(barcode=bc, qty=q, price_cny=Decimal("10")) for bc, q in barcodes_qtys]
    data = FactoryOrderCreate(
        order_number=f"FO-DR-{_uid()}",
        factory_name="Drift Test Factory",
        items=items,
    )
    return await create_factory_order(db_session, project_id, data)


async def _setup_vehicle_with_one_item(db_session, project_id, fo_qty: int, assigned_qty: int):
    """Build a factory order with single item, then a vehicle that already has assigned_qty.

    Returns (factory_order, vehicle, cost_item_id, factory_order_item_id).
    """
    order = await _create_factory_order_with_items(db_session, project_id, [(f"BC-{_uid()}", fo_qty)])
    foi_id = order.items[0].id
    vehicle_no = f"V-{_uid()}"
    await create_vehicle(db_session, project_id, VehicleCreate(order_no=vehicle_no))
    await split_to_vehicles(
        db_session,
        project_id,
        order.id,
        SplitToVehiclesRequest(
            assignments=[SplitItem(factory_order_item_id=foi_id, qty=assigned_qty, vehicle_order_no=vehicle_no)]
        ),
    )
    vehicle = await get_vehicle(db_session, project_id, vehicle_no)
    cost_item_id = vehicle.items[0].id
    return order, vehicle, cost_item_id, foi_id


# ═══════════════════════════════════════════════════════════════════════════════
# 1-7: _adjust_assigned_qty / update_vehicle_item core behavior
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_strict_pass_within_plan(db_session, project):
    """delta=4, available=4 → ok, fo.assigned_qty +=4"""
    # fo_qty=10, assigned=6 (one cost item with qty=6) → available=4
    _order, _vehicle, cost_item_id, foi_id = await _setup_vehicle_with_one_item(
        db_session, project.id, fo_qty=10, assigned_qty=6
    )

    # Bump to qty=10 → delta=+4, fits exactly
    result = await update_vehicle_item(
        db_session,
        project.id,
        _vehicle.order_no,
        cost_item_id,
        new_qty=10,
        mode="strict",
        user_name="tester",
    )
    assert result["ok"] is True
    assert result["extended_by"] == 0

    fo_item = (await db_session.execute(select(FactoryOrderItem).where(FactoryOrderItem.id == foi_id))).scalar_one()
    await db_session.refresh(fo_item)
    assert fo_item.assigned_qty == 10
    assert fo_item.qty == 10  # plan unchanged


@pytest.mark.asyncio
async def test_strict_fail_exceeds_plan(db_session, project):
    """delta=8 with available=4 in strict mode → FactoryQtyExceeded with full detail."""
    _order, _vehicle, cost_item_id, foi_id = await _setup_vehicle_with_one_item(
        db_session, project.id, fo_qty=10, assigned_qty=6
    )

    with pytest.raises(FactoryQtyExceeded) as exc_info:
        await update_vehicle_item(
            db_session,
            project.id,
            _vehicle.order_no,
            cost_item_id,
            new_qty=14,  # delta=+8, available=4
            mode="strict",
            user_name="tester",
        )

    detail = exc_info.value.detail
    expected_keys = {
        "error",
        "fo_id",
        "fo_number",
        "foi_id",
        "barcode",
        "subject",
        "fo_qty",
        "fo_assigned",
        "available",
        "attempted_delta",
        "overflow",
        "in_mix_group",
        "mix_group_id",
    }
    assert set(detail.keys()) == expected_keys
    assert detail["error"] == "exceeds_factory_qty"
    assert detail["foi_id"] == foi_id
    assert detail["fo_qty"] == 10
    assert detail["fo_assigned"] == 6
    assert detail["available"] == 4
    assert detail["attempted_delta"] == 8
    assert detail["overflow"] == 4
    assert detail["in_mix_group"] is False
    assert detail["mix_group_id"] is None
    assert detail["fo_number"] is not None  # has order_number


@pytest.mark.asyncio
async def test_extend_plan_with_zero_available(db_session, project):
    """delta=8 with available=0 + extend_plan → fo.qty +=8, history record."""
    # fo_qty=6, assigned=6 → available=0
    _order, _vehicle, cost_item_id, foi_id = await _setup_vehicle_with_one_item(
        db_session, project.id, fo_qty=6, assigned_qty=6
    )

    result = await update_vehicle_item(
        db_session,
        project.id,
        _vehicle.order_no,
        cost_item_id,
        new_qty=14,  # delta=+8, available=0 → extend by 8
        mode="extend_plan",
        user_name="tester",
    )
    assert result["ok"] is True
    assert result["extended_by"] == 8

    fo_item = (await db_session.execute(select(FactoryOrderItem).where(FactoryOrderItem.id == foi_id))).scalar_one()
    await db_session.refresh(fo_item)
    assert fo_item.qty == 14
    assert fo_item.assigned_qty == 14

    # History has qty_extended_from_vehicle entry
    history = (
        (
            await db_session.execute(
                select(FactoryOrderHistory).where(
                    FactoryOrderHistory.event_type == "qty_extended_from_vehicle",
                    FactoryOrderHistory.factory_order_id == _order.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(history) == 1


@pytest.mark.asyncio
async def test_extend_plan_with_partial_available(db_session, project):
    """delta=8 with available=4 + extend_plan → fo.qty bumped only by 4 (not 8)."""
    _order, _vehicle, cost_item_id, foi_id = await _setup_vehicle_with_one_item(
        db_session, project.id, fo_qty=10, assigned_qty=6
    )

    result = await update_vehicle_item(
        db_session,
        project.id,
        _vehicle.order_no,
        cost_item_id,
        new_qty=14,  # delta=+8, available=4 → extend by 4
        mode="extend_plan",
        user_name="tester",
    )
    assert result["ok"] is True
    assert result["extended_by"] == 4

    fo_item = (await db_session.execute(select(FactoryOrderItem).where(FactoryOrderItem.id == foi_id))).scalar_one()
    await db_session.refresh(fo_item)
    assert fo_item.qty == 14  # 10 + 4
    assert fo_item.assigned_qty == 14


@pytest.mark.asyncio
async def test_decrement_clamp(db_session, project):
    """delta=-12 при assigned=10 → clamp to 0 (не отрицательное)."""
    # fo_qty=20, assigned=10 (cost_item.qty=10)
    _order, _vehicle, _cost_item_id, foi_id = await _setup_vehicle_with_one_item(
        db_session, project.id, fo_qty=20, assigned_qty=10
    )

    # Reduce qty to 0 → delta=-10. But we want delta=-12: directly reduce qty in DB
    # via _adjust_assigned_qty. update_vehicle_item with new_qty=0 → delta=-10.
    # For -12 delta we test via _adjust_assigned_qty directly.
    from backend.services.supply_chain.factory_orders import _adjust_assigned_qty

    fo_item = (await db_session.execute(select(FactoryOrderItem).where(FactoryOrderItem.id == foi_id))).scalar_one()
    result = await _adjust_assigned_qty(db_session, fo_item, delta=-12, mode="strict", user_name=None)
    assert result.new_assigned_qty == 0
    assert result.extended_by == 0


@pytest.mark.asyncio
async def test_noop_zero_delta(db_session, project):
    """new_qty == old_qty → returns {noop: True}, ничего не меняет."""
    _order, _vehicle, cost_item_id, foi_id = await _setup_vehicle_with_one_item(
        db_session, project.id, fo_qty=10, assigned_qty=6
    )

    # update with same qty as current
    result = await update_vehicle_item(
        db_session,
        project.id,
        _vehicle.order_no,
        cost_item_id,
        new_qty=6,  # same as current
        mode="strict",
        user_name="tester",
    )
    assert result["ok"] is True
    assert result.get("noop") is True

    # Verify nothing changed
    fo_item = (await db_session.execute(select(FactoryOrderItem).where(FactoryOrderItem.id == foi_id))).scalar_one()
    await db_session.refresh(fo_item)
    assert fo_item.qty == 10
    assert fo_item.assigned_qty == 6


@pytest.mark.asyncio
async def test_mix_group_extends_only_current_foi(db_session, project):
    """mix-group из 3 позиций — extend_plan на одной не меняет qty других."""
    # Create a factory order with 3 items, group them into a mix-box
    items_data = [
        FactoryOrderItemCreate(barcode=f"BC-MX1-{_uid()}", qty=10, price_cny=Decimal("10"), pcs_per_box=2),
        FactoryOrderItemCreate(barcode=f"BC-MX2-{_uid()}", qty=10, price_cny=Decimal("10"), pcs_per_box=2),
        FactoryOrderItemCreate(barcode=f"BC-MX3-{_uid()}", qty=10, price_cny=Decimal("10"), pcs_per_box=2),
    ]
    order = await create_factory_order(
        db_session,
        project.id,
        FactoryOrderCreate(order_number=f"FO-MX-{_uid()}", items=items_data),
    )
    item_ids = [i.id for i in order.items]

    # Form mix group
    await set_mix_group(
        db_session,
        project.id,
        order.id,
        items=[MixGroupItemInput(id=iid, pcs_per_box=2) for iid in item_ids],
        box_size="20x20x20",
    )

    # Add items to a vehicle (each with qty=8)
    vehicle_no = f"V-MX-{_uid()}"
    await create_vehicle(db_session, project.id, VehicleCreate(order_no=vehicle_no))
    await add_items_to_vehicle(
        db_session,
        project.id,
        vehicle_no,
        AddItemsToVehicleRequest(items=[AddItemToVehicle(factory_order_item_id=iid, qty=8) for iid in item_ids]),
    )

    # Pick the first cost item, bump qty=8 → 14 with extend_plan (delta=+6, available=2)
    vehicle = await get_vehicle(db_session, project.id, vehicle_no)
    target_ci = next(ci for ci in vehicle.items if ci.factory_order_item_id == item_ids[0])

    await update_vehicle_item(
        db_session,
        project.id,
        vehicle_no,
        target_ci.id,
        new_qty=14,
        mode="extend_plan",
        user_name="mixer",
    )

    # First FOI: qty bumped from 10 to 10 + (delta=6 - available=2) = 14
    refreshed = (
        (await db_session.execute(select(FactoryOrderItem).where(FactoryOrderItem.id.in_(item_ids)))).scalars().all()
    )
    fo_qty_by_id = {fi.id: fi.qty for fi in refreshed}
    assert fo_qty_by_id[item_ids[0]] == 14
    assert fo_qty_by_id[item_ids[1]] == 10
    assert fo_qty_by_id[item_ids[2]] == 10


# ═══════════════════════════════════════════════════════════════════════════════
# 8-9: Idempotency + history
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_idempotency_double_call(db_session, project):
    """Повтор PATCH с тем же qty → no-op, нет лишних history записей."""
    _order, _vehicle, cost_item_id, _foi_id = await _setup_vehicle_with_one_item(
        db_session, project.id, fo_qty=10, assigned_qty=6
    )

    # First PATCH (real change): qty 6 → 10, no extend (available=4 fits exactly)
    await update_vehicle_item(
        db_session,
        project.id,
        _vehicle.order_no,
        cost_item_id,
        new_qty=10,
        mode="strict",
        user_name="tester",
    )

    history_before_count = len(
        (await db_session.execute(select(FactoryOrderHistory).where(FactoryOrderHistory.factory_order_id == _order.id)))
        .scalars()
        .all()
    )

    # Second PATCH with same qty → no-op
    result = await update_vehicle_item(
        db_session,
        project.id,
        _vehicle.order_no,
        cost_item_id,
        new_qty=10,
        mode="strict",
        user_name="tester",
    )
    assert result.get("noop") is True

    history_after_count = len(
        (await db_session.execute(select(FactoryOrderHistory).where(FactoryOrderHistory.factory_order_id == _order.id)))
        .scalars()
        .all()
    )
    assert history_after_count == history_before_count


@pytest.mark.asyncio
async def test_history_record_on_extend(db_session, project):
    """event_type='qty_extended_from_vehicle', changed_by, changed_at, details — формат."""
    _order, _vehicle, cost_item_id, _foi_id = await _setup_vehicle_with_one_item(
        db_session, project.id, fo_qty=6, assigned_qty=6
    )

    await update_vehicle_item(
        db_session,
        project.id,
        _vehicle.order_no,
        cost_item_id,
        new_qty=14,  # +8 over plan
        mode="extend_plan",
        user_name="extender_user",
    )

    history = (
        (
            await db_session.execute(
                select(FactoryOrderHistory).where(
                    FactoryOrderHistory.event_type == "qty_extended_from_vehicle",
                    FactoryOrderHistory.factory_order_id == _order.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(history) == 1
    entry = history[0]
    assert entry.event_type == "qty_extended_from_vehicle"
    assert entry.changed_by == "extender_user"
    assert entry.changed_at is not None
    # changed_at should be UTC (tzinfo set)
    assert entry.changed_at.tzinfo is not None
    # Details mentions extended count and barcode
    assert "+8" in (entry.details or "")
    assert _vehicle.order_no in (entry.details or "")


# ═══════════════════════════════════════════════════════════════════════════════
# 10-11: enriched schema + status refresh
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_qty_drift_in_enriched_schema(db_session, project):
    """В БД есть рассинхрон → GET /vehicles/{order_no} возвращает qty_drift > 0 в items."""
    # Setup: fo_qty=10, single cost_item with qty=10. Then artificially bump cost_item.qty to 15
    # WITHOUT going through the service → simulates pre-existing drift in DB.
    _order, _vehicle, cost_item_id, _foi_id = await _setup_vehicle_with_one_item(
        db_session, project.id, fo_qty=10, assigned_qty=10
    )

    # Directly bump CostOrderItem.qty in DB (simulating sc15-era drift)
    from backend.models.cost import CostOrderItem

    cost_item = (await db_session.execute(select(CostOrderItem).where(CostOrderItem.id == cost_item_id))).scalar_one()
    cost_item.qty = 15
    await db_session.commit()

    # Enriched read should show qty_drift = 15 - max(0, 10 - (15-15)) = 15 - 10 = 5
    vehicle = await get_vehicle(db_session, project.id, _vehicle.order_no)
    item = next(ci for ci in vehicle.items if ci.id == cost_item_id)
    assert item.qty_drift == 5
    assert item.fo_qty == 10
    assert item.fo_assigned == 10


@pytest.mark.asyncio
async def test_refresh_factory_order_statuses_called_after_extend(db_session, project):
    """После extend_plan заказ может перейти в DISTRIBUTED статус."""
    # Setup: fo_qty=6, assigned=4 (cost_item.qty=4) → status FORMING
    _order, _vehicle, cost_item_id, _foi_id = await _setup_vehicle_with_one_item(
        db_session, project.id, fo_qty=6, assigned_qty=4
    )
    refreshed = await get_factory_order(db_session, project.id, _order.id)
    assert refreshed.status == FactoryOrderStatus.FORMING.value

    # Extend plan: cost_item.qty 4 → 12, delta=+8 (available=2 → extend by 6)
    # After: fo.qty=12, fo.assigned_qty=12 → DISTRIBUTED
    await update_vehicle_item(
        db_session,
        project.id,
        _vehicle.order_no,
        cost_item_id,
        new_qty=12,
        mode="extend_plan",
        user_name="tester",
    )

    refreshed = await get_factory_order(db_session, project.id, _order.id)
    assert refreshed.status == FactoryOrderStatus.DISTRIBUTED.value


# ═══════════════════════════════════════════════════════════════════════════════
# 12: Router 422
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_router_returns_422_with_structured_detail(client, auth_headers):
    """PATCH с превышением и mode=strict → 422 с полным detail."""
    # Create project, factory order with one item, and a vehicle assigned to it.
    resp = await client.post("/api/v1/projects", json={"name": "Drift Router Test"}, headers=auth_headers)
    project = resp.json()
    headers = {**auth_headers, "X-Project-Id": str(project["id"])}

    # Factory order
    resp = await client.post(
        "/api/v1/supply-chain/factory-orders",
        json={
            "order_number": f"FO-RT-{_uid()}",
            "items": [{"barcode": "BC-RT-1", "qty": 10, "price_cny": "10"}],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    fo = resp.json()
    foi_id = fo["items"][0]["id"]
    fo_id = fo["id"]

    # Vehicle
    vno = f"V-RT-{_uid()}"
    resp = await client.post(
        "/api/v1/supply-chain/vehicles",
        json={"order_no": vno, "container_type": "truck1"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text

    # Split: assign 6 of 10 to vehicle
    resp = await client.post(
        f"/api/v1/supply-chain/factory-orders/{fo_id}/split-to-vehicles",
        json={"assignments": [{"factory_order_item_id": foi_id, "qty": 6, "vehicle_order_no": vno}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    # Find cost_item id via GET
    resp = await client.get(f"/api/v1/supply-chain/vehicles/{vno}", headers=headers)
    assert resp.status_code == 200, resp.text
    vehicle = resp.json()
    cost_item_id = vehicle["items"][0]["id"]

    # PATCH with delta=+8 (qty 6 → 14), available=4, mode=strict → 422
    resp = await client.patch(
        f"/api/v1/supply-chain/vehicles/{vno}/items/{cost_item_id}",
        json={"qty": 14, "mode": "strict"},
        headers=headers,
    )
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    body = resp.json()
    # DDS unified error handler (backend/exceptions.py) wraps structured HTTPException
    # detail as {"error": {"code": "VALIDATION_ERROR", "message": "exceeds_factory_qty",
    # "payload": {...}, ...}}. Frontend reads error.payload directly.
    err = body.get("error", body)
    detail = err.get("payload")
    assert detail is not None, f"expected error.payload to contain detail, got: {body}"
    assert err["message"] == "exceeds_factory_qty"
    assert isinstance(detail, dict)
    assert detail["error"] == "exceeds_factory_qty"
    assert detail["foi_id"] == foi_id
    assert detail["fo_qty"] == 10
    assert detail["fo_assigned"] == 6
    assert detail["available"] == 4
    assert detail["attempted_delta"] == 8
    assert detail["overflow"] == 4
    assert detail["in_mix_group"] is False
    assert detail["mix_group_id"] is None
    assert "fo_number" in detail
    assert "barcode" in detail
