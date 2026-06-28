"""
Tests for Assembly Request module — service layer.

Covers:
1. Create with valid data -> status PENDING, items saved
2. Create with non-existent barcode -> ValueError
3. Create for non-FULFILLMENT warehouse -> ValueError
4. Create with already-linked FBO supply -> ValueError
5. GET list -> filters by project_id (other project -> empty)
6. GET list -> deleted records not returned
7. Edit items in IN_PROGRESS status -> ValueError
8. Full lifecycle: PENDING -> IN_PROGRESS -> READY -> VEHICLE_ASSIGNED -> SHIPPED
9. Skip status (PENDING -> READY directly) -> ValueError
10. Ship -> stock decreased, OutboundShipment created, FBO supply linked
11. Cancel SHIPPED -> stock restored, OutboundShipment soft-deleted
12. Bulk assign vehicle -> all get vehicle_info, status VEHICLE_ASSIGNED
13. Ship with insufficient stock -> ValueError with deficit details
"""

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text

from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.warehouse import InboundReceipt, OutboundShipment, WarehouseStock
from backend.models.wb_fbo import WbFboSupply, WbSupplyStatus
from backend.schemas.assembly import (
    AssemblyItemCreate,
    AssemblyRequestCreate,
    AssemblyRequestResponse,
    AssemblyRequestUpdate,
    AssignVehicle,
)
from backend.services.assembly_service import (
    _build_response,
    assign_vehicle,
    assign_vehicle_bulk,
    cancel_request,
    close_request,
    create_assembly_request,
    deliver_request,
    get_assembly_attempts,
    get_assembly_request,
    get_logistics_analytics,
    list_assembly_requests,
    mark_ready,
    prefetch_list_maps,
    reopen_for_reship,
    return_to_warehouse,
    ship_request,
    start_assembly,
    update_assembly_request,
)
from backend.utils.time import utcnow

# PROJECT_ID / OTHER_PROJECT_ID are assigned per-test by the `setup_test_data`
# fixture from conftest's sequence-allocated `project` / `other_project`. Never
# hardcode a project id: a fixed value is a landmine — projects_id_seq on the
# local dev DB eventually climbs to it and auto-id INSERTs collide on projects_pkey.
PROJECT_ID = 0
OTHER_PROJECT_ID = 0
TEST_BARCODE_1 = "TEST_BC_ASM_001"
TEST_BARCODE_2 = "TEST_BC_ASM_002"


@pytest_asyncio.fixture(autouse=True)
async def setup_test_data(db_session, project, other_project):
    """Allocate fresh projects and seed assembly test fixtures.

    `project` / `other_project` (conftest) have sequence-allocated ids — no
    hardcoded project id to collide with projects_id_seq as it climbs on the
    local dev DB. Each test gets a clean project, so no cross-test cleanup needed.
    """
    global PROJECT_ID, OTHER_PROJECT_ID
    PROJECT_ID = project.id
    OTHER_PROJECT_ID = other_project.id

    # Ensure FULFILLMENT warehouse
    wh_result = await db_session.execute(
        text(
            "SELECT id FROM warehouses WHERE project_id = :pid AND warehouse_type = 'FULFILLMENT' AND (is_deleted = false OR is_deleted IS NULL) LIMIT 1"
        ),
        {"pid": PROJECT_ID},
    )
    wh_id = wh_result.scalar()
    if wh_id is None:
        await db_session.execute(
            text(
                "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, is_deleted, created_at, updated_at) "
                "VALUES (:pid, 'Test Fulfillment WH', 'FULFILLMENT', 1, true, false, NOW(), NOW()) RETURNING id"
            ),
            {"pid": PROJECT_ID},
        )
        await db_session.commit()
        wh_result = await db_session.execute(
            text("SELECT id FROM warehouses WHERE project_id = :pid AND warehouse_type = 'FULFILLMENT' LIMIT 1"),
            {"pid": PROJECT_ID},
        )
        wh_id = wh_result.scalar()

    # Ensure nomenclature entries for test barcodes
    for barcode in [TEST_BARCODE_1, TEST_BARCODE_2]:
        nom_result = await db_session.execute(
            text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
            {"pid": PROJECT_ID, "bc": barcode},
        )
        if nom_result.scalar() is None:
            await db_session.execute(
                text("INSERT INTO nomenclature (project_id, barcode, updated_at) VALUES (:pid, :bc, NOW())"),
                {"pid": PROJECT_ID, "bc": barcode},
            )
    await db_session.commit()

    # Create ACTIVE FBO supply (no linked assembly request)
    fbo = WbFboSupply(
        project_id=PROJECT_ID,
        wb_supply_id="ASM-FBO-TEST-1",
        wb_status=WbSupplyStatus.ACTIVE,
        name="ASM_TEST FBO Supply",
        warehouse_name="Электросталь",
        created_at_wb=datetime(2026, 3, 20),
    )
    db_session.add(fbo)
    await db_session.flush()

    # Add FBO supply items (for refresh_from_fbo tests)
    from backend.models.wb_fbo import WbFboSupplyItem

    fbo_item = WbFboSupplyItem(
        project_id=PROJECT_ID,
        supply_id=fbo.id,
        wb_order_id="ORD-ASM-1",
        barcode=TEST_BARCODE_1,
        quantity=10,
    )
    db_session.add(fbo_item)
    await db_session.commit()

    # Seed warehouse stock for test barcodes
    nom1_result = await db_session.execute(
        text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
        {"pid": PROJECT_ID, "bc": TEST_BARCODE_1},
    )
    nom1_id = nom1_result.scalar()

    nom2_result = await db_session.execute(
        text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
        {"pid": PROJECT_ID, "bc": TEST_BARCODE_2},
    )
    nom2_id = nom2_result.scalar()

    for nom_id, barcode in [(nom1_id, TEST_BARCODE_1), (nom2_id, TEST_BARCODE_2)]:
        await db_session.execute(
            text(
                "INSERT INTO warehouse_stock (project_id, warehouse_id, nomenclature_id, barcode, quantity, in_transit, updated_at) "
                "VALUES (:pid, :wid, :nid, :bc, 100, 0, NOW())"
            ),
            {"pid": PROJECT_ID, "wid": wh_id, "nid": nom_id, "bc": barcode},
        )
    await db_session.commit()

    yield


async def _get_fulfillment_wh_id(db_session) -> int:
    result = await db_session.execute(
        text(
            "SELECT id FROM warehouses WHERE project_id = :pid AND warehouse_type = 'FULFILLMENT' AND (is_deleted = false OR is_deleted IS NULL) LIMIT 1"
        ),
        {"pid": PROJECT_ID},
    )
    return result.scalar()


async def _get_fbo_supply_id(db_session) -> int:
    result = await db_session.execute(
        text("SELECT id FROM wb_fbo_supplies WHERE project_id = :pid AND wb_supply_id = 'ASM-FBO-TEST-1'"),
        {"pid": PROJECT_ID},
    )
    return result.scalar()


async def _create_test_request(db_session) -> AssemblyRequest:
    """Helper: create a valid assembly request."""
    wh_id = await _get_fulfillment_wh_id(db_session)
    fbo_id = await _get_fbo_supply_id(db_session)
    payload = AssemblyRequestCreate(
        warehouse_id=wh_id,
        wb_fbo_supply_id=fbo_id,
        pallets_count=2,
        pallet_weight_kg=Decimal("150.00"),
        items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=5)],
    )
    return await create_assembly_request(db_session, PROJECT_ID, payload)


async def _create_second_fulfillment_wh(db_session, stock_for: list[str] | None = None) -> int:
    """Helper: create a 2nd FULFILLMENT warehouse; optionally seed stock for barcodes."""
    await db_session.execute(
        text(
            "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, is_deleted, created_at, updated_at) "
            "VALUES (:pid, 'Test Fulfillment WH 2', 'FULFILLMENT', 3, true, false, NOW(), NOW())"
        ),
        {"pid": PROJECT_ID},
    )
    await db_session.commit()
    wh2_id = (
        await db_session.execute(
            text("SELECT id FROM warehouses WHERE project_id = :pid AND name = 'Test Fulfillment WH 2' LIMIT 1"),
            {"pid": PROJECT_ID},
        )
    ).scalar()
    for barcode in stock_for or []:
        nom_id = (
            await db_session.execute(
                text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
                {"pid": PROJECT_ID, "bc": barcode},
            )
        ).scalar()
        await db_session.execute(
            text(
                "INSERT INTO warehouse_stock (project_id, warehouse_id, nomenclature_id, barcode, quantity, in_transit, updated_at) "
                "VALUES (:pid, :wid, :nid, :bc, 100, 0, NOW())"
            ),
            {"pid": PROJECT_ID, "wid": wh2_id, "nid": nom_id, "bc": barcode},
        )
    await db_session.commit()
    return wh2_id


# --- Tests ------------------------------------------------------------------


@pytest.mark.asyncio
class TestCreateAssemblyRequest:
    """Tests 1-4: Create assembly request."""

    async def test_create_valid(self, db_session):
        """1. Create with valid data -> status IN_PROGRESS (PENDING removed), items saved."""
        req = await _create_test_request(db_session)
        assert req.status == AssemblyStatus.IN_PROGRESS
        assert req.number.startswith("ASM-")

        # Reload to check items
        loaded = await get_assembly_request(db_session, PROJECT_ID, req.id)
        assert loaded is not None
        assert len(loaded.items) == 1
        assert loaded.items[0].barcode == TEST_BARCODE_1
        assert loaded.items[0].quantity == 5

    async def test_create_nonexistent_barcode(self, db_session):
        """2. Create with non-existent barcode -> ValueError."""
        wh_id = await _get_fulfillment_wh_id(db_session)
        fbo_id = await _get_fbo_supply_id(db_session)
        payload = AssemblyRequestCreate(
            warehouse_id=wh_id,
            wb_fbo_supply_id=fbo_id,
            pallets_count=1,
            pallet_weight_kg=Decimal("100.00"),
            items=[AssemblyItemCreate(barcode="NONEXISTENT_BARCODE", quantity=1)],
        )
        with pytest.raises(ValueError, match="Barcode not found"):
            await create_assembly_request(db_session, PROJECT_ID, payload)

    async def test_create_non_fulfillment_warehouse(self, db_session):
        """3. Create for non-FULFILLMENT warehouse -> ValueError."""
        # Create a TRANSIT warehouse
        await db_session.execute(
            text(
                "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, is_deleted, created_at, updated_at) "
                "VALUES (:pid, 'Transit WH', 'TRANSIT', 2, true, false, NOW(), NOW())"
            ),
            {"pid": PROJECT_ID},
        )
        await db_session.commit()
        wh_result = await db_session.execute(
            text("SELECT id FROM warehouses WHERE project_id = :pid AND warehouse_type = 'TRANSIT' LIMIT 1"),
            {"pid": PROJECT_ID},
        )
        transit_wh_id = wh_result.scalar()
        fbo_id = await _get_fbo_supply_id(db_session)

        payload = AssemblyRequestCreate(
            warehouse_id=transit_wh_id,
            wb_fbo_supply_id=fbo_id,
            pallets_count=1,
            pallet_weight_kg=Decimal("100.00"),
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=1)],
        )
        with pytest.raises(ValueError, match="FULFILLMENT"):
            await create_assembly_request(db_session, PROJECT_ID, payload)

    async def test_create_already_linked_fbo_same_warehouse(self, db_session):
        """4. Create 2nd request for same FBO supply FROM THE SAME warehouse -> ValueError.

        Совместная поставка разрешает несколько сборок на поставку, но только с
        РАЗНЫХ складов-источников; повтор с того же склада по-прежнему запрещён.
        """
        # First create a request
        await _create_test_request(db_session)

        # Try to create another for the same FBO supply, same source warehouse
        wh_id = await _get_fulfillment_wh_id(db_session)
        fbo_id = await _get_fbo_supply_id(db_session)
        payload = AssemblyRequestCreate(
            warehouse_id=wh_id,
            wb_fbo_supply_id=fbo_id,
            pallets_count=1,
            pallet_weight_kg=Decimal("100.00"),
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=1)],
        )
        with pytest.raises(ValueError, match="уже есть активная сборка"):
            await create_assembly_request(db_session, PROJECT_ID, payload)

    async def test_create_allows_accepted_fbo_supply(self, db_session):
        """Regression: ACCEPTED FBO supply must be linkable for retroactive entry.

        Users reported they could not create an assembly request for a supply
        that WB had already accepted (e.g. WB supply #38412181). Late onboarding
        and backfilling historic records need this path open.
        """
        # Flip the seeded supply to ACCEPTED and expire identity map so the
        # service re-reads the row instead of serving the fixture's cached object
        await db_session.execute(
            text(
                "UPDATE wb_fbo_supplies SET wb_status = 'ACCEPTED' "
                "WHERE project_id = :pid AND wb_supply_id = 'ASM-FBO-TEST-1'"
            ),
            {"pid": PROJECT_ID},
        )
        await db_session.commit()
        db_session.expire_all()

        wh_id = await _get_fulfillment_wh_id(db_session)
        fbo_id = await _get_fbo_supply_id(db_session)
        payload = AssemblyRequestCreate(
            warehouse_id=wh_id,
            wb_fbo_supply_id=fbo_id,
            pallets_count=2,
            pallet_weight_kg=Decimal("150.00"),
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=5)],
        )
        req = await create_assembly_request(db_session, PROJECT_ID, payload)
        assert req.status == AssemblyStatus.IN_PROGRESS
        assert req.wb_fbo_supply_id == fbo_id

    async def test_create_rejects_cancelled_fbo_supply(self, db_session):
        """CANCELLED FBO supply still cannot be linked — linking a cancelled
        supply would create a zombie assembly request."""
        await db_session.execute(
            text(
                "UPDATE wb_fbo_supplies SET wb_status = 'CANCELLED' "
                "WHERE project_id = :pid AND wb_supply_id = 'ASM-FBO-TEST-1'"
            ),
            {"pid": PROJECT_ID},
        )
        await db_session.commit()
        db_session.expire_all()

        wh_id = await _get_fulfillment_wh_id(db_session)
        fbo_id = await _get_fbo_supply_id(db_session)
        payload = AssemblyRequestCreate(
            warehouse_id=wh_id,
            wb_fbo_supply_id=fbo_id,
            pallets_count=1,
            pallet_weight_kg=Decimal("100.00"),
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=1)],
        )
        with pytest.raises(ValueError, match="ACTIVE, ON_DELIVERY, IN_PROGRESS or ACCEPTED"):
            await create_assembly_request(db_session, PROJECT_ID, payload)


@pytest.mark.asyncio
class TestListAssemblyRequests:
    """Tests 5-6: List assembly requests."""

    async def test_list_filters_by_project(self, db_session):
        """5. GET list -> filters by project_id (other project -> empty)."""
        await _create_test_request(db_session)

        items, total = await list_assembly_requests(db_session, OTHER_PROJECT_ID)
        assert total == 0
        assert items == []

        items, total = await list_assembly_requests(db_session, PROJECT_ID)
        assert total >= 1

    async def test_list_excludes_deleted(self, db_session):
        """6. GET list -> deleted records not returned."""
        req = await _create_test_request(db_session)

        # Soft-delete
        req.soft_delete()
        await db_session.commit()

        items, _total = await list_assembly_requests(db_session, PROJECT_ID)
        found_ids = [r.id for r in items]
        assert req.id not in found_ids

    async def test_list_filters_by_counterparty(self, db_session):
        """GET list?counterparty_id -> only requests linked to that carrier.

        Powers the «Доставки» tab on the carrier counterparty card.
        """
        from backend.models.counterparty import Counterparty

        carrier = Counterparty(project_id=PROJECT_ID, name="ИП Перевозчик Тест", primary_type="CARRIER")
        other = Counterparty(project_id=PROJECT_ID, name="ИП Другой Тест", primary_type="CARRIER")
        db_session.add_all([carrier, other])
        await db_session.flush()

        req = await _create_test_request(db_session)
        req.counterparty_id = carrier.id
        await db_session.commit()

        items, total = await list_assembly_requests(db_session, PROJECT_ID, counterparty_id=carrier.id)
        assert total >= 1
        assert all(r.counterparty_id == carrier.id for r in items)
        assert req.id in [r.id for r in items]

        # A different carrier sees nothing of this request.
        other_items, _ = await list_assembly_requests(db_session, PROJECT_ID, counterparty_id=other.id)
        assert req.id not in [r.id for r in other_items]


@pytest.mark.asyncio
class TestUpdateAssemblyRequest:
    """Test 7: Edit items in non-PENDING status."""

    async def test_edit_items_in_progress_allowed(self, db_session):
        """7. Edit items in IN_PROGRESS status -> allowed (until READY)."""
        req = await _create_test_request(db_session)
        await start_assembly(db_session, PROJECT_ID, req.id)

        payload = AssemblyRequestUpdate(
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_2, quantity=3)],
        )
        updated = await update_assembly_request(db_session, PROJECT_ID, req.id, payload)
        assert len(updated.items) == 1
        assert updated.items[0].barcode == TEST_BARCODE_2

    async def test_edit_items_in_ready_allowed(self, db_session):
        """7b. Edit items in READY status -> allowed (нужно править факт под WB)."""
        req = await _create_test_request(db_session)
        await start_assembly(db_session, PROJECT_ID, req.id)
        await mark_ready(db_session, PROJECT_ID, req.id)

        payload = AssemblyRequestUpdate(
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_2, quantity=3)],
        )
        updated = await update_assembly_request(db_session, PROJECT_ID, req.id, payload)
        assert len(updated.items) == 1
        assert updated.items[0].barcode == TEST_BARCODE_2


@pytest.mark.asyncio
class TestChangeWarehouse:
    """Смена склада-источника заявки через update."""

    async def test_change_warehouse_in_progress_allowed(self, db_session):
        """IN_PROGRESS + достаточный сток на новом складе → склад меняется."""
        req = await _create_test_request(db_session)
        wh2_id = await _create_second_fulfillment_wh(db_session, stock_for=[TEST_BARCODE_1])

        updated = await update_assembly_request(
            db_session, PROJECT_ID, req.id, AssemblyRequestUpdate(warehouse_id=wh2_id)
        )
        assert updated.warehouse_id == wh2_id

    async def test_change_warehouse_vehicle_assigned_skips_stock_check(self, db_session):
        """VEHICLE_ASSIGNED: смена склада разрешена даже без стока (факт подгоняют под WB)."""
        req = await _create_test_request(db_session)
        await mark_ready(db_session, PROJECT_ID, req.id)
        await assign_vehicle(
            db_session,
            PROJECT_ID,
            req.id,
            AssignVehicle(
                vehicle_info="Truck WH-MOVE",
                vehicle_brand="GAZ",
                driver_phone="+79991234567",
                pickup_date="2026-03-22",
                pickup_time_slot="08:00-12:00",
                pickup_cost=1,
                delivery_date="2026-03-23",
            ),
        )
        wh2_id = await _create_second_fulfillment_wh(db_session, stock_for=[])  # без стока

        updated = await update_assembly_request(
            db_session, PROJECT_ID, req.id, AssemblyRequestUpdate(warehouse_id=wh2_id)
        )
        assert updated.warehouse_id == wh2_id

    async def test_change_warehouse_nonexistent_rejected(self, db_session):
        req = await _create_test_request(db_session)
        with pytest.raises(ValueError, match="Warehouse not found"):
            await update_assembly_request(db_session, PROJECT_ID, req.id, AssemblyRequestUpdate(warehouse_id=99999999))

    async def test_change_warehouse_other_project_rejected(self, db_session):
        """Склад чужого проекта → not found (изоляция по project_id)."""
        req = await _create_test_request(db_session)
        await db_session.execute(
            text(
                "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, is_deleted, created_at, updated_at) "
                "VALUES (:pid, 'Foreign WH', 'FULFILLMENT', 7, true, false, NOW(), NOW())"
            ),
            {"pid": OTHER_PROJECT_ID},
        )
        await db_session.commit()
        foreign_id = (
            await db_session.execute(
                text("SELECT id FROM warehouses WHERE project_id = :pid AND name = 'Foreign WH' LIMIT 1"),
                {"pid": OTHER_PROJECT_ID},
            )
        ).scalar()
        with pytest.raises(ValueError, match="Warehouse not found"):
            await update_assembly_request(
                db_session, PROJECT_ID, req.id, AssemblyRequestUpdate(warehouse_id=foreign_id)
            )

    async def test_change_warehouse_non_fulfillment_rejected(self, db_session):
        req = await _create_test_request(db_session)
        await db_session.execute(
            text(
                "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, is_deleted, created_at, updated_at) "
                "VALUES (:pid, 'Transit WH X', 'TRANSIT', 5, true, false, NOW(), NOW())"
            ),
            {"pid": PROJECT_ID},
        )
        await db_session.commit()
        transit_id = (
            await db_session.execute(
                text("SELECT id FROM warehouses WHERE project_id = :pid AND name = 'Transit WH X' LIMIT 1"),
                {"pid": PROJECT_ID},
            )
        ).scalar()
        with pytest.raises(ValueError, match="FULFILLMENT"):
            await update_assembly_request(
                db_session, PROJECT_ID, req.id, AssemblyRequestUpdate(warehouse_id=transit_id)
            )

    async def test_change_warehouse_deficit_rejected(self, db_session):
        """IN_PROGRESS + новый склад без стока → дефицит → ValueError."""
        req = await _create_test_request(db_session)
        wh2_id = await _create_second_fulfillment_wh(db_session, stock_for=[])
        with pytest.raises(ValueError):
            await update_assembly_request(db_session, PROJECT_ID, req.id, AssemblyRequestUpdate(warehouse_id=wh2_id))

    async def test_change_warehouse_blocked_when_shipped(self, db_session):
        """Закрытый статус (SHIPPED) → смена склада запрещена."""
        req = await _create_test_request(db_session)
        req_id = req.id  # захватываем ДО expire_all (иначе sync lazy-load в async)
        wh2_id = await _create_second_fulfillment_wh(db_session, stock_for=[TEST_BARCODE_1])
        await db_session.execute(
            text("UPDATE assembly_requests SET status = 'SHIPPED' WHERE id = :rid"),
            {"rid": req_id},
        )
        await db_session.commit()
        db_session.expire_all()  # чтобы сервис перечитал SHIPPED, а не стейл из identity map
        with pytest.raises(ValueError, match="status"):
            await update_assembly_request(db_session, PROJECT_ID, req_id, AssemblyRequestUpdate(warehouse_id=wh2_id))


@pytest.mark.asyncio
class TestRefreshFromFbo:
    """Refresh items from linked WbFboSupply.

    Сценарий: WB принял меньше заявленного — нужно подтянуть accepted_qty
    в позиции заявки, чтобы остатки сошлись и можно было отгрузить.
    """

    async def test_refresh_uses_accepted_qty_when_supply_accepted(self, db_session):
        """ACCEPTED supply: quantity=10, accepted_qty=7 → позиция = 7."""
        from backend.models.wb_fbo import WbFboSupplyItem
        from backend.services.assembly.analytics import refresh_from_fbo

        req = await _create_test_request(db_session)  # позиция: 5
        # Помечаем supply как ACCEPTED и проставляем accepted_qty=7
        fbo_id = await _get_fbo_supply_id(db_session)
        supply = (await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == fbo_id))).scalar_one()
        supply.wb_status = WbSupplyStatus.ACCEPTED
        item = (
            await db_session.execute(select(WbFboSupplyItem).where(WbFboSupplyItem.supply_id == fbo_id))
        ).scalar_one()
        item.quantity = 10
        item.accepted_qty = 7
        await db_session.commit()

        result = await refresh_from_fbo(db_session, PROJECT_ID, req.id)
        assert result.changed == 1
        assert len(result.items) == 1
        assert result.items[0].quantity == 7  # фактически принято, не заявлено

    async def test_refresh_uses_quantity_when_supply_not_accepted(self, db_session):
        """ACTIVE supply: используем заявленное (quantity), не accepted_qty."""
        from backend.models.wb_fbo import WbFboSupplyItem
        from backend.services.assembly.analytics import refresh_from_fbo

        req = await _create_test_request(db_session)  # позиция: 5
        # Supply остаётся ACTIVE, но accepted_qty=0 (поставка ещё не принята)
        fbo_id = await _get_fbo_supply_id(db_session)
        item = (
            await db_session.execute(select(WbFboSupplyItem).where(WbFboSupplyItem.supply_id == fbo_id))
        ).scalar_one()
        item.quantity = 10
        item.accepted_qty = 0
        await db_session.commit()

        result = await refresh_from_fbo(db_session, PROJECT_ID, req.id)
        assert result.changed == 1
        assert result.items[0].quantity == 10  # заявлено WB

    async def test_refresh_removes_zero_accepted_items(self, db_session):
        """ACCEPTED supply: accepted_qty=0 → позиция удаляется."""
        from backend.models.wb_fbo import WbFboSupplyItem
        from backend.services.assembly.analytics import refresh_from_fbo

        req = await _create_test_request(db_session)
        fbo_id = await _get_fbo_supply_id(db_session)
        supply = (await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == fbo_id))).scalar_one()
        supply.wb_status = WbSupplyStatus.ACCEPTED
        item = (
            await db_session.execute(select(WbFboSupplyItem).where(WbFboSupplyItem.supply_id == fbo_id))
        ).scalar_one()
        item.quantity = 10
        item.accepted_qty = 0  # WB не принял ни одной
        await db_session.commit()

        result = await refresh_from_fbo(db_session, PROJECT_ID, req.id)
        assert result.removed == 1
        assert len(result.items) == 0

    async def test_refresh_pulls_goods_and_adds_new_barcode(self, db_session):
        """Кнопка «Из FBO» форс-перетягивает goods из WB: новый ШК, появившийся в
        кабинете после создания поставки, попадает в заявку (а не теряется в
        устаревшем зеркале WbFboSupplyItem)."""
        from unittest.mock import AsyncMock

        from backend.services.assembly.analytics import refresh_from_fbo

        req = await _create_test_request(db_session)  # позиция: TEST_BARCODE_1 = 5
        # wb_supply_id должен быть числовым (в проде так и есть) — иначе int() в
        # enrich-хелпере падает и WB-вызов тихо пропускается.
        fbo_id = await _get_fbo_supply_id(db_session)
        supply = (await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == fbo_id))).scalar_one()
        supply.wb_supply_id = "40012237"
        await db_session.commit()

        # WB-состав вырос: TEST_BARCODE_1 → 10 и новый TEST_BARCODE_2 = 7.
        mock_client = AsyncMock()
        mock_client.get_fbw_supply_detail.return_value = {
            "warehouseName": "Электросталь",
            "quantity": 17,
            "statusID": 1,
        }
        mock_client.get_fbw_supply_goods.return_value = [
            {"barcode": TEST_BARCODE_1, "vendorCode": "ART1", "nmID": 1, "quantity": 10, "acceptedQuantity": 0},
            {"barcode": TEST_BARCODE_2, "vendorCode": "ART2", "nmID": 2, "quantity": 7, "acceptedQuantity": 0},
        ]

        result = await refresh_from_fbo(db_session, PROJECT_ID, req.id, api_client=mock_client)

        assert mock_client.get_fbw_supply_goods.called, "goods обязаны перетянуться из WB"
        assert result.added == 1  # TEST_BARCODE_2 добавлен
        assert result.changed == 1  # TEST_BARCODE_1: 5 → 10
        assert result.skipped == []
        assert TEST_BARCODE_2 in {i.barcode for i in result.items}

    async def test_refresh_skips_barcode_absent_in_nomenclature(self, db_session):
        """Goods содержит ШК, которого нет в номенклатуре → не валим рефреш (был бы
        HTTP 400), а копим в skipped."""
        from unittest.mock import AsyncMock

        from backend.services.assembly.analytics import refresh_from_fbo

        req = await _create_test_request(db_session)  # позиция: TEST_BARCODE_1 = 5
        fbo_id = await _get_fbo_supply_id(db_session)
        supply = (await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == fbo_id))).scalar_one()
        supply.wb_supply_id = "40012237"
        await db_session.commit()

        mock_client = AsyncMock()
        mock_client.get_fbw_supply_detail.return_value = {"warehouseName": "Электросталь", "quantity": 5, "statusID": 1}
        mock_client.get_fbw_supply_goods.return_value = [
            {"barcode": "UNRESOLVABLE_BC_ZZZ", "vendorCode": "X", "nmID": 9, "quantity": 5, "acceptedQuantity": 0},
        ]

        # Не должно бросать ValueError
        result = await refresh_from_fbo(db_session, PROJECT_ID, req.id, api_client=mock_client)

        assert "UNRESOLVABLE_BC_ZZZ" in result.skipped
        assert result.added == 0
        assert result.removed == 1  # TEST_BARCODE_1 ушёл из состава WB


@pytest.mark.asyncio
class TestAutoRefreshActiveAssembliesFromFbo:
    """Фоновый авто-рефреш состава активных сборок из FBO (раз в час из планировщика).

    Зеркалит ручную «Из FBO», но без клика — чтобы «Расхождение наполнения»
    обновлялось само.
    """

    async def test_autorefresh_updates_active_assembly_composition(self, db_session):
        """Активная сборка с привязкой к FBO авто-обновляется из свежих goods WB."""
        from unittest.mock import AsyncMock, patch

        from backend.services.assembly.analytics import refresh_active_assemblies_from_fbo

        req = await _create_test_request(db_session)  # IN_PROGRESS, TEST_BARCODE_1 = 5
        # wb_supply_id числовой — иначе int() в enrich-хелпере пропустит WB-вызов.
        fbo_id = await _get_fbo_supply_id(db_session)
        supply = (await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == fbo_id))).scalar_one()
        supply.wb_supply_id = "40012237"
        await db_session.commit()

        # WB-состав изменился: TEST_BARCODE_1 → 10 и новый TEST_BARCODE_2 = 7.
        mock_client = AsyncMock()
        mock_client.get_fbw_supply_detail.return_value = {"warehouseName": "Электросталь", "quantity": 17, "statusID": 1}
        mock_client.get_fbw_supply_goods.return_value = [
            {"barcode": TEST_BARCODE_1, "vendorCode": "ART1", "nmID": 1, "quantity": 10, "acceptedQuantity": 0},
            {"barcode": TEST_BARCODE_2, "vendorCode": "ART2", "nmID": 2, "quantity": 7, "acceptedQuantity": 0},
        ]

        # Глушим троттл-паузу (11s между detail/goods) — без неё тест мгновенный.
        with patch("backend.services.assembly.crud.asyncio.sleep", new=AsyncMock()):
            result = await refresh_active_assemblies_from_fbo(db_session, PROJECT_ID, api_client=mock_client)

        assert result["processed"] >= 1
        assert result["refreshed"] >= 1
        assert result["supplies"] == 1  # дедуп по поставке
        assert mock_client.get_fbw_supply_goods.called, "фон обязан перетянуть goods из WB"
        # goods для одной поставки тянутся ровно один раз (а не на каждую сборку).
        assert mock_client.get_fbw_supply_goods.call_count == 1

        loaded = await get_assembly_request(db_session, PROJECT_ID, req.id)
        qty_by_bc = {i.barcode: i.quantity for i in loaded.items}
        assert qty_by_bc.get(TEST_BARCODE_1) == 10  # 5 → 10
        assert qty_by_bc.get(TEST_BARCODE_2) == 7  # добавлен

    async def test_autorefresh_skips_shipped(self, db_session):
        """SHIPPED-сборки (refresh для них запрещён) в авто-рефреш не попадают."""
        from unittest.mock import AsyncMock

        from backend.services.assembly.analytics import refresh_active_assemblies_from_fbo

        req = await _create_test_request(db_session)
        await mark_ready(db_session, PROJECT_ID, req.id)
        await assign_vehicle(
            db_session,
            PROJECT_ID,
            req.id,
            AssignVehicle(
                vehicle_info="Truck X",
                vehicle_brand="GAZ",
                driver_phone="+79991234567",
                pickup_date="2026-03-22",
                pickup_time_slot="08:00-12:00",
                pickup_cost=1,
                delivery_date="2026-03-23",
            ),
        )
        await ship_request(db_session, PROJECT_ID, req.id)

        mock_client = AsyncMock()
        result = await refresh_active_assemblies_from_fbo(db_session, PROJECT_ID, api_client=mock_client)

        assert result["processed"] == 0  # активных сборок с привязкой нет
        assert not mock_client.get_fbw_supply_goods.called


@pytest.mark.asyncio
class TestLifecycle:
    """Test 8-9: Full lifecycle and invalid transitions."""

    async def test_full_lifecycle(self, db_session):
        """8. Full lifecycle: IN_PROGRESS -> READY -> VEHICLE_ASSIGNED -> SHIPPED.
        (PENDING removed: новые заявки создаются сразу в IN_PROGRESS.)
        """
        req = await _create_test_request(db_session)
        assert req.status == AssemblyStatus.IN_PROGRESS

        req = await mark_ready(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.READY
        assert req.actual_ready_date == date.today()

        payload = AssignVehicle(
            vehicle_info="Truck ABC-123",
            vehicle_brand="GAZ-330",
            driver_phone="+79991234567",
            pickup_date="2026-03-22",
            pickup_time_slot="08:00-12:00",
            pickup_cost=15000,
            delivery_date="2026-03-23",
        )
        req = await assign_vehicle(db_session, PROJECT_ID, req.id, payload)
        assert req.status == AssemblyStatus.VEHICLE_ASSIGNED
        assert req.vehicle_info == "Truck ABC-123"
        assert req.vehicle_assigned_at is not None

        req = await ship_request(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.SHIPPED
        assert req.shipped_at is not None
        assert req.outbound_shipment_id is not None

    async def test_skip_status_raises(self, db_session):
        """9. Skip status (IN_PROGRESS -> VEHICLE_ASSIGNED directly) -> ValueError.
        Проверяем что пропуск READY не разрешён.
        """
        from backend.services.assembly_service import assign_vehicle

        req = await _create_test_request(db_session)
        payload = AssignVehicle(
            vehicle_info="Truck X",
            vehicle_brand="GAZ",
            driver_phone="+79991234567",
            pickup_date="2026-03-22",
            pickup_time_slot="08:00-12:00",
            pickup_cost=1,
            delivery_date="2026-03-23",
        )
        with pytest.raises(ValueError, match="Cannot transition"):
            await assign_vehicle(db_session, PROJECT_ID, req.id, payload)

    async def test_reopen_from_ready_to_in_progress(self, db_session):
        """9b. READY -> IN_PROGRESS (reopen): allowed, clears actual_ready_date."""
        req = await _create_test_request(db_session)
        req = await mark_ready(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.READY
        assert req.actual_ready_date == date.today()

        req = await start_assembly(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.IN_PROGRESS
        assert req.actual_ready_date is None

        req = await mark_ready(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.READY
        assert req.actual_ready_date == date.today()


@pytest.mark.asyncio
class TestShipAndCancel:
    """Tests 10-11: Ship and cancel shipped."""

    async def test_ship_decreases_stock_and_creates_shipment(self, db_session):
        """10. Ship -> stock decreased, OutboundShipment created, FBO supply linked."""
        req = await _create_test_request(db_session)
        wh_id = req.warehouse_id

        # Get initial stock
        nom_result = await db_session.execute(
            text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
            {"pid": PROJECT_ID, "bc": TEST_BARCODE_1},
        )
        nom_id = nom_result.scalar()
        stock_before = await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == PROJECT_ID,
                WarehouseStock.warehouse_id == wh_id,
                WarehouseStock.nomenclature_id == nom_id,
            )
        )
        initial_qty = stock_before.scalar_one().quantity

        # Move through lifecycle
        await start_assembly(db_session, PROJECT_ID, req.id)
        await mark_ready(db_session, PROJECT_ID, req.id)
        await assign_vehicle(
            db_session,
            PROJECT_ID,
            req.id,
            AssignVehicle(
                vehicle_info="Truck X",
                vehicle_brand="KAMAZ",
                driver_phone="+79990000000",
                pickup_date="2026-03-22",
                pickup_time_slot="12:00-16:00",
                pickup_cost=20000,
                delivery_date="2026-03-24",
            ),
        )
        req = await ship_request(db_session, PROJECT_ID, req.id)

        # Check stock decreased
        stock_after = await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == PROJECT_ID,
                WarehouseStock.warehouse_id == wh_id,
                WarehouseStock.nomenclature_id == nom_id,
            )
        )
        final_qty = stock_after.scalar_one().quantity
        assert final_qty == initial_qty - 5  # quantity from test request

        # Check OutboundShipment created
        assert req.outbound_shipment_id is not None
        ship_result = await db_session.execute(
            select(OutboundShipment).where(
                OutboundShipment.id == req.outbound_shipment_id,
                OutboundShipment.project_id == PROJECT_ID,
            )
        )
        shipment = ship_result.scalar_one()
        assert shipment.status == "SHIPPED"

        # Check FBO supply linked
        fbo_id = await _get_fbo_supply_id(db_session)
        fbo_result = await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == fbo_id))
        fbo = fbo_result.scalar_one()
        assert fbo.outbound_shipment_id == req.outbound_shipment_id

    async def test_cancel_shipped_restores_stock(self, db_session):
        """11. Cancel SHIPPED -> stock restored, OutboundShipment soft-deleted."""
        req = await _create_test_request(db_session)
        wh_id = req.warehouse_id

        nom_result = await db_session.execute(
            text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
            {"pid": PROJECT_ID, "bc": TEST_BARCODE_1},
        )
        nom_id = nom_result.scalar()
        stock_before_q = await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == PROJECT_ID,
                WarehouseStock.warehouse_id == wh_id,
                WarehouseStock.nomenclature_id == nom_id,
            )
        )
        initial_qty = stock_before_q.scalar_one().quantity

        # Ship it
        await start_assembly(db_session, PROJECT_ID, req.id)
        await mark_ready(db_session, PROJECT_ID, req.id)
        await assign_vehicle(
            db_session,
            PROJECT_ID,
            req.id,
            AssignVehicle(
                vehicle_info="Truck Y",
                vehicle_brand="GAZ",
                driver_phone="+79991111111",
                pickup_date="2026-03-22",
                pickup_time_slot="08:00-12:00",
                pickup_cost=10000,
                delivery_date="2026-03-23",
            ),
        )
        req = await ship_request(db_session, PROJECT_ID, req.id)
        shipment_id = req.outbound_shipment_id

        # Cancel it
        req = await cancel_request(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.CANCELLED
        assert req.outbound_shipment_id is None
        assert req.shipped_at is None

        # Stock restored
        stock_after_q = await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == PROJECT_ID,
                WarehouseStock.warehouse_id == wh_id,
                WarehouseStock.nomenclature_id == nom_id,
            )
        )
        restored_qty = stock_after_q.scalar_one().quantity
        assert restored_qty == initial_qty

        # OutboundShipment soft-deleted
        ship_result = await db_session.execute(select(OutboundShipment).where(OutboundShipment.id == shipment_id))
        shipment = ship_result.scalar_one()
        assert shipment.is_deleted is True


async def _create_and_ship(db_session, *, pickup_cost: int = 15000) -> AssemblyRequest:
    """Helper: create a test request and move it through the full lifecycle to SHIPPED."""
    req = await _create_test_request(db_session)
    await start_assembly(db_session, PROJECT_ID, req.id)
    await mark_ready(db_session, PROJECT_ID, req.id)
    await assign_vehicle(
        db_session,
        PROJECT_ID,
        req.id,
        AssignVehicle(
            vehicle_info="Truck R",
            vehicle_brand="MAN",
            driver_phone="+79992222222",
            pickup_date="2026-03-22",
            pickup_time_slot="10:00-14:00",
            pickup_cost=pickup_cost,
            delivery_date="2026-03-24",
        ),
    )
    return await ship_request(db_session, PROJECT_ID, req.id)


@pytest.mark.asyncio
class TestReturnToWarehouse:
    """Возврат: WB не принял поставку → RETURNED, товар на склад, логистика сохранена."""

    async def test_return_restores_stock_keeps_shipment_and_logistics(self, db_session):
        req = await _create_and_ship(db_session, pickup_cost=15000)
        wh_id = req.warehouse_id
        shipment_id = req.outbound_shipment_id
        assert shipment_id is not None

        nom_result = await db_session.execute(
            text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
            {"pid": PROJECT_ID, "bc": TEST_BARCODE_1},
        )
        nom_id = nom_result.scalar()
        shipped_qty = (
            await db_session.execute(
                select(WarehouseStock.quantity).where(
                    WarehouseStock.project_id == PROJECT_ID,
                    WarehouseStock.warehouse_id == wh_id,
                    WarehouseStock.nomenclature_id == nom_id,
                )
            )
        ).scalar_one()

        # Return to warehouse
        req = await return_to_warehouse(db_session, PROJECT_ID, req.id)

        assert req.status == AssemblyStatus.RETURNED
        # Зеркало последней попытки очищено (для переотгрузки/пере-связки FBW);
        # сама попытка сохранена на OutboundShipment.
        assert req.outbound_shipment_id is None
        assert req.shipped_at is None
        assert req.wb_fbo_supply_id is None

        # Stock restored (+5 back to source warehouse)
        restored_qty = (
            await db_session.execute(
                select(WarehouseStock.quantity).where(
                    WarehouseStock.project_id == PROJECT_ID,
                    WarehouseStock.warehouse_id == wh_id,
                    WarehouseStock.nomenclature_id == nom_id,
                )
            )
        ).scalar_one()
        assert restored_qty == shipped_qty + 5

        # OutboundShipment NOT soft-deleted (unlike cancel)
        shipment = (
            await db_session.execute(select(OutboundShipment).where(OutboundShipment.id == shipment_id))
        ).scalar_one()
        assert shipment.is_deleted is False

        # A return InboundReceipt (ACCEPTED) was created on the source warehouse
        receipt = (
            await db_session.execute(
                select(InboundReceipt).where(
                    InboundReceipt.project_id == PROJECT_ID,
                    InboundReceipt.warehouse_id == wh_id,
                    InboundReceipt.comment.like(f"%{req.number}%"),
                )
            )
        ).scalar_one()
        assert receipt.status == "ACCEPTED"
        assert receipt.is_defect is False

        # FBO supply flagged as return-processed (GOODS) so it leaves «недоприёмка»
        fbo_id = await _get_fbo_supply_id(db_session)
        fbo = (await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == fbo_id))).scalar_one()
        assert fbo.return_processed_at is not None
        assert fbo.return_type == "GOODS"
        assert fbo.return_qty == 5

        # Возврат-приёмка привязана к заявке и номеру попытки
        assert receipt.assembly_request_id == req.id
        assert receipt.assembly_attempt_no == 1

        # Logistics analytics STILL counts the returned attempt (перевозка оплачена)
        analytics = await get_logistics_analytics(db_session, PROJECT_ID)
        assert analytics["summary"]["total_shipments"] == 1
        assert float(analytics["summary"]["total_cost"]) == 15000.0

    async def test_return_blocked_from_in_progress(self, db_session):
        req = await _create_test_request(db_session)
        with pytest.raises(ValueError):
            await return_to_warehouse(db_session, PROJECT_ID, req.id)

    async def test_return_idempotent_second_call_blocked(self, db_session):
        """Повторный возврат по уже RETURNED-заявке запрещён (RETURNED→RETURNED нет)."""
        req = await _create_and_ship(db_session)
        req = await return_to_warehouse(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.RETURNED
        with pytest.raises(ValueError):
            await return_to_warehouse(db_session, PROJECT_ID, req.id)

    async def test_return_blocked_when_fbo_already_returned(self, db_session):
        """Кросс-флоу: возврат уже оформлен через «недоприёмку» → no double-restock."""
        req = await _create_and_ship(db_session)
        wh_id = req.warehouse_id

        # Симулируем, что process_fbo_return уже отработал по этой поставке.
        fbo_id = await _get_fbo_supply_id(db_session)
        fbo = (await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == fbo_id))).scalar_one()
        fbo.return_processed_at = utcnow()
        await db_session.commit()

        nom_id = (
            await db_session.execute(
                text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
                {"pid": PROJECT_ID, "bc": TEST_BARCODE_1},
            )
        ).scalar()
        qty_before = (
            await db_session.execute(
                select(WarehouseStock.quantity).where(
                    WarehouseStock.project_id == PROJECT_ID,
                    WarehouseStock.warehouse_id == wh_id,
                    WarehouseStock.nomenclature_id == nom_id,
                )
            )
        ).scalar_one()

        with pytest.raises(ValueError):
            await return_to_warehouse(db_session, PROJECT_ID, req.id)

        # Сток НЕ задвоился, заявка осталась SHIPPED.
        qty_after = (
            await db_session.execute(
                select(WarehouseStock.quantity).where(
                    WarehouseStock.project_id == PROJECT_ID,
                    WarehouseStock.warehouse_id == wh_id,
                    WarehouseStock.nomenclature_id == nom_id,
                )
            )
        ).scalar_one()
        assert qty_after == qty_before
        reloaded = await get_assembly_request(db_session, PROJECT_ID, req.id)
        assert reloaded is not None
        assert reloaded.status == AssemblyStatus.SHIPPED

    async def test_return_from_delivered(self, db_session):
        """DELIVERED → RETURNED тоже работает (возврат после приёмки WB)."""
        req = await _create_and_ship(db_session)
        req = await deliver_request(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.DELIVERED
        req = await return_to_warehouse(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.RETURNED

    async def test_return_to_different_warehouse(self, db_session):
        """Возврат на ДРУГОЙ склад: остаток падает на выбранный склад, не на источник."""
        req = await _create_and_ship(db_session)
        wh2 = await _create_second_fulfillment_wh(db_session, stock_for=[TEST_BARCODE_1])
        nom_id = (
            await db_session.execute(
                text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
                {"pid": PROJECT_ID, "bc": TEST_BARCODE_1},
            )
        ).scalar()

        req = await return_to_warehouse(db_session, PROJECT_ID, req.id, return_warehouse_id=wh2)
        assert req.status == AssemblyStatus.RETURNED

        # Остаток вернулся на wh2 (+5 к посеянным 100), не на склад-источник
        qty2 = (
            await db_session.execute(
                select(WarehouseStock.quantity).where(
                    WarehouseStock.project_id == PROJECT_ID,
                    WarehouseStock.warehouse_id == wh2,
                    WarehouseStock.nomenclature_id == nom_id,
                )
            )
        ).scalar_one()
        assert qty2 == 105

        receipt = (
            await db_session.execute(
                select(InboundReceipt).where(InboundReceipt.assembly_request_id == req.id)
            )
        ).scalar_one()
        assert receipt.warehouse_id == wh2

        attempts = await get_assembly_attempts(db_session, PROJECT_ID, req.id)
        assert len(attempts) == 1
        assert attempts[0]["outcome"] == "rejected"
        assert attempts[0]["returned_to_warehouse_id"] == wh2


@pytest.mark.asyncio
class TestReshipChain:
    """Цепочка попыток: отгрузил → не приняли → вернул → переотгрузил новым водителем."""

    async def test_reship_creates_second_attempt_and_sums_logistics(self, db_session):
        # Попытка 1 — отгружена и возвращена
        req = await _create_and_ship(db_session, pickup_cost=15000)
        req = await return_to_warehouse(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.RETURNED

        # Переотгрузка: возврат в READY
        req = await reopen_for_reship(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.READY

        # Привязываем НОВУЮ FBW-поставку (другой склад WB)
        fbo2 = WbFboSupply(
            project_id=PROJECT_ID,
            wb_supply_id="ASM-FBO-TEST-2",
            wb_status=WbSupplyStatus.ACTIVE,
            name="FBO 2",
            warehouse_name="Коледино",
            created_at_wb=datetime(2026, 3, 25),
        )
        db_session.add(fbo2)
        await db_session.commit()
        req = await update_assembly_request(
            db_session, PROJECT_ID, req.id, AssemblyRequestUpdate(wb_fbo_supply_id=fbo2.id)
        )
        assert req.wb_fbo_supply_id == fbo2.id

        # Новый водитель + отгрузка (попытка 2)
        await assign_vehicle(
            db_session,
            PROJECT_ID,
            req.id,
            AssignVehicle(
                vehicle_info="Truck B",
                vehicle_brand="Volvo",
                driver_phone="+79993333333",
                pickup_date="2026-03-26",
                pickup_time_slot="09:00-13:00",
                pickup_cost=18000,
                delivery_date="2026-03-28",
            ),
        )
        req = await ship_request(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.SHIPPED

        # Цепочка попыток: 1 отклонена, 2 в пути; водители/склады WB по-попыточно
        attempts = await get_assembly_attempts(db_session, PROJECT_ID, req.id)
        assert len(attempts) == 2
        a1, a2 = attempts
        assert a1["attempt_no"] == 1
        assert a1["outcome"] == "rejected"
        assert a1["driver_phone"] == "+79992222222"
        assert a2["attempt_no"] == 2
        assert a2["outcome"] == "in_transit"
        assert a2["driver_phone"] == "+79993333333"
        assert a2["wb_warehouse_name"] == "Коледино"

        # Логистика суммирует ОБЕ попытки (платили за оба рейса)
        analytics = await get_logistics_analytics(db_session, PROJECT_ID)
        assert analytics["summary"]["total_shipments"] == 2
        assert float(analytics["summary"]["total_cost"]) == 33000.0

    async def test_close_from_returned(self, db_session):
        """Из RETURNED можно закрыть заявку (терминально)."""
        req = await _create_and_ship(db_session)
        req = await return_to_warehouse(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.RETURNED
        req = await close_request(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.CLOSED

    async def test_attempts_accepted_after_deliver(self, db_session):
        """get_assembly_attempts: SHIPPED → in_transit, после deliver → accepted."""
        req = await _create_and_ship(db_session)
        attempts = await get_assembly_attempts(db_session, PROJECT_ID, req.id)
        assert len(attempts) == 1
        assert attempts[0]["outcome"] == "in_transit"

        await deliver_request(db_session, PROJECT_ID, req.id)
        attempts = await get_assembly_attempts(db_session, PROJECT_ID, req.id)
        assert attempts[0]["outcome"] == "accepted"


@pytest.mark.asyncio
class TestEditClosedStatuses:
    """Редактирование мета-полей разрешено в SHIPPED; позиции — нет."""

    async def test_edit_meta_allowed_when_shipped(self, db_session):
        req = await _create_and_ship(db_session)
        updated = await update_assembly_request(
            db_session,
            PROJECT_ID,
            req.id,
            AssemblyRequestUpdate(pallets_count=9, comment="скорректировано после отгрузки"),
        )
        assert updated.status == AssemblyStatus.SHIPPED
        assert updated.pallets_count == 9
        assert updated.comment == "скорректировано после отгрузки"

    async def test_edit_items_blocked_when_shipped(self, db_session):
        req = await _create_and_ship(db_session)
        with pytest.raises(ValueError):
            await update_assembly_request(
                db_session,
                PROJECT_ID,
                req.id,
                AssemblyRequestUpdate(items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=3)]),
            )


@pytest.mark.asyncio
class TestBulkOperations:
    """Test 12: Bulk assign vehicle."""

    async def test_bulk_assign_vehicle(self, db_session):
        """12. Bulk assign vehicle -> all get vehicle_info, status VEHICLE_ASSIGNED."""
        # Create first request
        req1 = await _create_test_request(db_session)
        await start_assembly(db_session, PROJECT_ID, req1.id)
        await mark_ready(db_session, PROJECT_ID, req1.id)

        # Create a second FBO supply for second request
        fbo2 = WbFboSupply(
            project_id=PROJECT_ID,
            wb_supply_id="ASM-FBO-TEST-2",
            wb_status=WbSupplyStatus.ACTIVE,
            name="ASM_TEST FBO Supply 2",
            warehouse_name="Коледино",
            created_at_wb=datetime(2026, 3, 21),
        )
        db_session.add(fbo2)
        await db_session.flush()
        await db_session.commit()

        wh_id = await _get_fulfillment_wh_id(db_session)
        payload2 = AssemblyRequestCreate(
            warehouse_id=wh_id,
            wb_fbo_supply_id=fbo2.id,
            pallets_count=1,
            pallet_weight_kg=Decimal("120.00"),
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_2, quantity=3)],
        )
        req2 = await create_assembly_request(db_session, PROJECT_ID, payload2)
        await start_assembly(db_session, PROJECT_ID, req2.id)
        await mark_ready(db_session, PROJECT_ID, req2.id)

        # Bulk assign
        bulk_payload = AssignVehicle(
            vehicle_info="Truck BULK-1",
            vehicle_brand="MAN",
            driver_phone="+79993333333",
            pickup_date="2026-03-22",
            pickup_time_slot="08:00-12:00",
            pickup_cost=30000,
            delivery_date="2026-03-24",
        )
        results = await assign_vehicle_bulk(db_session, PROJECT_ID, [req1.id, req2.id], bulk_payload)
        assert len(results) == 2
        for r in results:
            assert r.status == AssemblyStatus.VEHICLE_ASSIGNED
            assert r.vehicle_info == "Truck BULK-1"


@pytest.mark.asyncio
class TestInsufficientStock:
    """Test 13: Ship with insufficient stock."""

    async def test_create_without_fbo_supply(self, db_session):
        """14. Create assembly without wb_fbo_supply_id -> should succeed, status IN_PROGRESS."""
        wh_id = await _get_fulfillment_wh_id(db_session)
        payload = AssemblyRequestCreate(
            warehouse_id=wh_id,
            wb_fbo_supply_id=None,
            pallets_count=1,
            pallet_weight_kg=Decimal("100.00"),
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=5)],
        )
        req = await create_assembly_request(db_session, PROJECT_ID, payload)
        assert req.status == AssemblyStatus.IN_PROGRESS
        assert req.wb_fbo_supply_id is None

        loaded = await get_assembly_request(db_session, PROJECT_ID, req.id)
        assert loaded is not None
        assert loaded.wb_fbo_supply_id is None
        assert len(loaded.items) == 1

    async def test_mark_ready_without_fbo_raises(self, db_session):
        """15. Create without FBO, try mark_ready -> should raise ValueError."""
        wh_id = await _get_fulfillment_wh_id(db_session)
        payload = AssemblyRequestCreate(
            warehouse_id=wh_id,
            wb_fbo_supply_id=None,
            pallets_count=2,
            pallet_weight_kg=Decimal("150.00"),
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=5)],
        )
        req = await create_assembly_request(db_session, PROJECT_ID, payload)
        await start_assembly(db_session, PROJECT_ID, req.id)

        with pytest.raises(ValueError, match="без привязанной поставки WB"):
            await mark_ready(db_session, PROJECT_ID, req.id)

    async def test_update_attach_fbo_then_ready(self, db_session):
        """16. Create without FBO -> update with FBO -> mark_ready -> should succeed."""
        wh_id = await _get_fulfillment_wh_id(db_session)
        fbo_id = await _get_fbo_supply_id(db_session)

        # Create without FBO
        payload = AssemblyRequestCreate(
            warehouse_id=wh_id,
            wb_fbo_supply_id=None,
            pallets_count=2,
            pallet_weight_kg=Decimal("150.00"),
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=5)],
        )
        req = await create_assembly_request(db_session, PROJECT_ID, payload)
        assert req.wb_fbo_supply_id is None

        # Attach FBO supply via update
        update_payload = AssemblyRequestUpdate(wb_fbo_supply_id=fbo_id)
        req = await update_assembly_request(db_session, PROJECT_ID, req.id, update_payload)
        assert req.wb_fbo_supply_id == fbo_id

        # Now progress through lifecycle
        await start_assembly(db_session, PROJECT_ID, req.id)
        req = await mark_ready(db_session, PROJECT_ID, req.id)
        assert req.status == AssemblyStatus.READY

    async def test_ship_insufficient_stock(self, db_session):
        """13. Ship with insufficient stock -> ValueError with deficit details."""
        wh_id = await _get_fulfillment_wh_id(db_session)
        fbo_id = await _get_fbo_supply_id(db_session)

        # Create with valid quantity (stock=100 is enough)
        payload = AssemblyRequestCreate(
            warehouse_id=wh_id,
            wb_fbo_supply_id=fbo_id,
            pallets_count=1,
            pallet_weight_kg=Decimal("100.00"),
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=50)],
        )
        req = await create_assembly_request(db_session, PROJECT_ID, payload)
        await start_assembly(db_session, PROJECT_ID, req.id)
        await mark_ready(db_session, PROJECT_ID, req.id)
        await assign_vehicle(
            db_session,
            PROJECT_ID,
            req.id,
            AssignVehicle(
                vehicle_info="Truck Z",
                vehicle_brand="MAN",
                driver_phone="+79992222222",
                pickup_date="2026-03-22",
                pickup_time_slot="16:00-20:00",
                pickup_cost=25000,
                delivery_date="2026-03-25",
            ),
        )

        # Now reduce stock to 0 so ship fails
        from sqlalchemy import update

        from backend.models.warehouse import WarehouseStock

        await db_session.execute(
            update(WarehouseStock)
            .where(WarehouseStock.project_id == PROJECT_ID, WarehouseStock.warehouse_id == wh_id)
            .values(quantity=0)
        )
        await db_session.flush()

        with pytest.raises(ValueError, match="Недостаточно остатков"):
            await ship_request(db_session, PROJECT_ID, req.id)


@pytest.mark.asyncio
class TestAvailableStockValidation:
    """Tests: создание/изменение заявки учитывает резерв из других активных заявок."""

    async def test_create_blocks_when_exceeds_warehouse_quantity(self, db_session):
        """Create заявку на больше чем физический stock → ValueError."""
        wh_id = await _get_fulfillment_wh_id(db_session)
        # stock = 100 по умолчанию; пробуем 150
        payload = AssemblyRequestCreate(
            warehouse_id=wh_id,
            wb_fbo_supply_id=None,
            pallets_count=1,
            pallet_weight_kg=Decimal("10.00"),
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=150)],
        )
        with pytest.raises(ValueError, match="Недостаточно доступных остатков"):
            await create_assembly_request(db_session, PROJECT_ID, payload)

    async def test_create_blocks_when_other_request_holds_reserve(self, db_session):
        """stock=100, заявка А резервирует 80 → новая на 30 → ValueError (доступно 20)."""
        # Заявка А: 80 шт.
        req_a = await create_assembly_request(
            db_session,
            PROJECT_ID,
            AssemblyRequestCreate(
                warehouse_id=await _get_fulfillment_wh_id(db_session),
                wb_fbo_supply_id=None,
                pallets_count=1,
                pallet_weight_kg=Decimal("10.00"),
                items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=80)],
            ),
        )
        assert req_a.status == AssemblyStatus.IN_PROGRESS

        # Заявка Б: 30 шт. — должна упасть, доступно только 20.
        with pytest.raises(ValueError, match="Недостаточно доступных остатков"):
            await create_assembly_request(
                db_session,
                PROJECT_ID,
                AssemblyRequestCreate(
                    warehouse_id=await _get_fulfillment_wh_id(db_session),
                    wb_fbo_supply_id=None,
                    pallets_count=1,
                    pallet_weight_kg=Decimal("10.00"),
                    items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=30)],
                ),
            )

    async def test_update_excludes_own_reservation(self, db_session):
        """Update в IN_PROGRESS заявке: при пересчёте резерва не вычитать саму себя.
        stock=100, заявка резервирует 80, обновляем до 90 → должно пройти (80 от себя
        исключаются, доступно 100, нужно 90).
        """
        wh_id = await _get_fulfillment_wh_id(db_session)
        req = await create_assembly_request(
            db_session,
            PROJECT_ID,
            AssemblyRequestCreate(
                warehouse_id=wh_id,
                wb_fbo_supply_id=None,
                pallets_count=1,
                pallet_weight_kg=Decimal("10.00"),
                items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=80)],
            ),
        )
        # Должно пройти: свой резерв исключается из reserved.
        updated = await update_assembly_request(
            db_session,
            PROJECT_ID,
            req.id,
            AssemblyRequestUpdate(
                items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=90)],
            ),
        )
        assert updated.items[0].quantity == 90

    async def test_update_blocks_when_other_reserve_too_high(self, db_session):
        """Update заявки А до 50, когда другая заявка Б держит 70 на этом же товаре.
        stock=100, available_for_A = 100 - 70 = 30 → 50 не пройдёт.
        """
        wh_id = await _get_fulfillment_wh_id(db_session)
        req_a = await create_assembly_request(
            db_session,
            PROJECT_ID,
            AssemblyRequestCreate(
                warehouse_id=wh_id,
                wb_fbo_supply_id=None,
                pallets_count=1,
                pallet_weight_kg=Decimal("10.00"),
                items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=20)],
            ),
        )
        await create_assembly_request(
            db_session,
            PROJECT_ID,
            AssemblyRequestCreate(
                warehouse_id=wh_id,
                wb_fbo_supply_id=None,
                pallets_count=1,
                pallet_weight_kg=Decimal("10.00"),
                items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=70)],
            ),
        )
        # Теперь А пытается увеличиться до 50, но доступно 100-70=30
        with pytest.raises(ValueError, match="Недостаточно доступных остатков"):
            await update_assembly_request(
                db_session,
                PROJECT_ID,
                req_a.id,
                AssemblyRequestUpdate(
                    items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=50)],
                ),
            )


# --- FF link: ff_link filter + batch enrichment (R2) ------------------------


async def _link_ff_request(db_session, *, assembly_request_id: int, warehouse_id: int, total_qty: int | None = None):
    """Helper: создать зеркало ФФ-заявки, привязанное к нашей заявке на сборку.

    total_qty задаёт суммарное кол-во (skladbot → сверка расхождения «по итогам»)."""
    from backend.models.fulfillment import FulfillmentRequest

    ff = FulfillmentRequest(
        project_id=PROJECT_ID,
        warehouse_id=warehouse_id,
        provider="skladbot",
        external_id=f"FF-EXT-{assembly_request_id}",
        number=f"WH-R-{assembly_request_id}",
        kind="assembly",
        stage_title="Приёмка",
        total_qty=total_qty,
        assembly_request_id=assembly_request_id,
    )
    db_session.add(ff)
    await db_session.commit()
    return ff


async def _connect_ff_integration(db_session, warehouse_id: int) -> None:
    """Helper: активная ФФ-интеграция (skladbot), привязанная к складу."""
    from backend.models.integrations import IntegrationKey

    db_session.add(
        IntegrationKey(
            project_id=PROJECT_ID,
            service="skladbot",
            label=f"FF test {warehouse_id}",
            encrypted_key="placeholder-encrypted",
            is_active=True,
            warehouse_id=warehouse_id,
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
class TestFfLinkFilter:
    """ff_link фильтр в list_assembly_requests: none / linked / unset."""

    async def test_ff_link_none_excludes_linked(self, db_session):
        """ff_link='none' возвращает только заявки БЕЗ привязанной ФФ-заявки."""
        wh_id = await _get_fulfillment_wh_id(db_session)
        linked = await _create_test_request(db_session)
        unlinked = await create_assembly_request(
            db_session,
            PROJECT_ID,
            AssemblyRequestCreate(
                warehouse_id=wh_id,
                wb_fbo_supply_id=None,
                pallets_count=1,
                pallet_weight_kg=Decimal("10.00"),
                items=[AssemblyItemCreate(barcode=TEST_BARCODE_2, quantity=3)],
            ),
        )
        await _link_ff_request(db_session, assembly_request_id=linked.id, warehouse_id=wh_id)

        items, _total = await list_assembly_requests(db_session, PROJECT_ID, ff_link="none")
        ids = {r.id for r in items}
        assert unlinked.id in ids
        assert linked.id not in ids

    async def test_ff_link_linked_only_linked(self, db_session):
        """ff_link='linked' возвращает только заявки С привязанной ФФ-заявкой."""
        wh_id = await _get_fulfillment_wh_id(db_session)
        linked = await _create_test_request(db_session)
        unlinked = await create_assembly_request(
            db_session,
            PROJECT_ID,
            AssemblyRequestCreate(
                warehouse_id=wh_id,
                wb_fbo_supply_id=None,
                pallets_count=1,
                pallet_weight_kg=Decimal("10.00"),
                items=[AssemblyItemCreate(barcode=TEST_BARCODE_2, quantity=3)],
            ),
        )
        await _link_ff_request(db_session, assembly_request_id=linked.id, warehouse_id=wh_id)

        items, _total = await list_assembly_requests(db_session, PROJECT_ID, ff_link="linked")
        ids = {r.id for r in items}
        assert linked.id in ids
        assert unlinked.id not in ids

    async def test_ff_link_unset_returns_all(self, db_session):
        """ff_link=None — без фильтра, возвращает и привязанные, и нет."""
        wh_id = await _get_fulfillment_wh_id(db_session)
        linked = await _create_test_request(db_session)
        unlinked = await create_assembly_request(
            db_session,
            PROJECT_ID,
            AssemblyRequestCreate(
                warehouse_id=wh_id,
                wb_fbo_supply_id=None,
                pallets_count=1,
                pallet_weight_kg=Decimal("10.00"),
                items=[AssemblyItemCreate(barcode=TEST_BARCODE_2, quantity=3)],
            ),
        )
        await _link_ff_request(db_session, assembly_request_id=linked.id, warehouse_id=wh_id)

        items, _total = await list_assembly_requests(db_session, PROJECT_ID)
        ids = {r.id for r in items}
        assert linked.id in ids
        assert unlinked.id in ids


@pytest.mark.asyncio
class TestFfLinkEnrichment:
    """Batch-обогащение списка полями ФФ-заявки (router._enrich_ff_links)."""

    async def test_enrich_populates_ff_fields_when_linked(self, db_session):
        """Заявка ФФ-интегрированного склада c привязкой получает ff_request_number;
        непривязанная остаётся null."""
        from backend.routers.assembly import _enrich_ff_links

        wh_id = await _get_fulfillment_wh_id(db_session)
        await _connect_ff_integration(db_session, wh_id)

        linked = await _create_test_request(db_session)
        unlinked = await create_assembly_request(
            db_session,
            PROJECT_ID,
            AssemblyRequestCreate(
                warehouse_id=wh_id,
                wb_fbo_supply_id=None,
                pallets_count=1,
                pallet_weight_kg=Decimal("10.00"),
                items=[AssemblyItemCreate(barcode=TEST_BARCODE_2, quantity=3)],
            ),
        )
        ff = await _link_ff_request(db_session, assembly_request_id=linked.id, warehouse_id=wh_id)

        items, _total = await list_assembly_requests(db_session, PROJECT_ID)
        responses = [AssemblyRequestResponse.model_validate(await _build_response(db_session, r)) for r in items]
        await _enrich_ff_links(db_session, PROJECT_ID, items, responses)

        by_id = {r.id: r for r in responses}
        assert by_id[linked.id].ff_request_id == ff.id
        assert by_id[linked.id].ff_request_number == ff.number
        assert by_id[linked.id].ff_stage_title == "Приёмка"
        assert by_id[linked.id].ff_warehouse_id == wh_id
        # Непривязанная заявка того же склада — поля остаются null
        assert by_id[unlinked.id].ff_request_id is None
        assert by_id[unlinked.id].ff_request_number is None

    async def test_enrich_populates_without_active_integration(self, db_session):
        """Номер ФФ-заявки — историческая привязка: подтягивается по реальной связи
        fulfillment_requests.assembly_request_id, даже если ключ интеграции склада НЕ
        активен (раньше гейт это скрывал; теперь номер виден всегда, в т.ч. локально)."""
        from backend.routers.assembly import _enrich_ff_links

        wh_id = await _get_fulfillment_wh_id(db_session)
        # ФФ-интеграцию НЕ подключаем — но физическая привязка ФФ-заявки есть.
        linked = await _create_test_request(db_session)
        ff = await _link_ff_request(db_session, assembly_request_id=linked.id, warehouse_id=wh_id)

        items, _total = await list_assembly_requests(db_session, PROJECT_ID)
        responses = [AssemblyRequestResponse.model_validate(await _build_response(db_session, r)) for r in items]
        await _enrich_ff_links(db_session, PROJECT_ID, items, responses)

        by_id = {r.id: r for r in responses}
        assert by_id[linked.id].ff_request_id == ff.id
        assert by_id[linked.id].ff_request_number == ff.number
        assert by_id[linked.id].ff_warehouse_id == wh_id


@pytest.mark.asyncio
class TestJointFboSupply:
    """Совместная WB FBO-поставка: одна поставка («Совместный номер») несёт ≥2
    сборок — по одной на ФФ-источник (напр. wms + wms2)."""

    async def _make_joint_pair(self, db_session):
        """Две сборки на одной FBO-поставке, но с РАЗНЫХ складов-источников."""
        wh1 = await _get_fulfillment_wh_id(db_session)
        wh2 = await _create_second_fulfillment_wh(db_session, stock_for=[TEST_BARCODE_1])
        fbo_id = await _get_fbo_supply_id(db_session)
        a1 = await create_assembly_request(
            db_session,
            PROJECT_ID,
            AssemblyRequestCreate(
                warehouse_id=wh1,
                wb_fbo_supply_id=fbo_id,
                pallets_count=1,
                pallet_weight_kg=Decimal("100.00"),
                items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=5)],
            ),
        )
        a2 = await create_assembly_request(
            db_session,
            PROJECT_ID,
            AssemblyRequestCreate(
                warehouse_id=wh2,
                wb_fbo_supply_id=fbo_id,
                pallets_count=1,
                pallet_weight_kg=Decimal("100.00"),
                items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=3)],
            ),
        )
        return a1, a2, fbo_id

    async def test_two_warehouses_share_one_supply(self, db_session):
        """Разные склады-источники → обе сборки привязаны к одной поставке (1:N)."""
        a1, a2, fbo_id = await self._make_joint_pair(db_session)
        assert a1.wb_fbo_supply_id == fbo_id
        assert a2.wb_fbo_supply_id == fbo_id
        assert a1.warehouse_id != a2.warehouse_id

    async def test_joint_only_filter(self, db_session):
        """joint_only=True возвращает только сборки совместных поставок."""
        a1, a2, _fbo_id = await self._make_joint_pair(db_session)
        # Одиночная сборка на ОТДЕЛЬНОЙ поставке — не совместная.
        lone_supply = WbFboSupply(
            project_id=PROJECT_ID,
            wb_supply_id="ASM-FBO-TEST-LONE",
            wb_status=WbSupplyStatus.ACTIVE,
            name="Lone",
            warehouse_name="Казань",
            created_at_wb=datetime(2026, 3, 21),
        )
        db_session.add(lone_supply)
        await db_session.flush()
        wh1 = await _get_fulfillment_wh_id(db_session)
        lone = await create_assembly_request(
            db_session,
            PROJECT_ID,
            AssemblyRequestCreate(
                warehouse_id=wh1,
                wb_fbo_supply_id=lone_supply.id,
                pallets_count=1,
                pallet_weight_kg=Decimal("100.00"),
                items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=1)],
            ),
        )
        items, total = await list_assembly_requests(db_session, PROJECT_ID, joint_only=True, limit=100)
        ids = {r.id for r in items}
        assert ids == {a1.id, a2.id}
        assert lone.id not in ids
        assert total == 2

    async def test_enrich_joint_flag_and_siblings(self, db_session):
        """_enrich_joint ставит joint_supply=True и siblings (другая сборка поставки)."""
        from backend.routers.assembly import _enrich_joint

        a1, a2, _fbo_id = await self._make_joint_pair(db_session)
        items, _total = await list_assembly_requests(db_session, PROJECT_ID, limit=100)
        responses = [AssemblyRequestResponse.model_validate(await _build_response(db_session, r)) for r in items]
        await _enrich_joint(db_session, PROJECT_ID, items, responses)

        by_id = {r.id: r for r in responses}
        assert by_id[a1.id].joint_supply is True
        assert by_id[a2.id].joint_supply is True
        assert {s.assembly_id for s in (by_id[a1.id].joint_siblings or [])} == {a2.id}
        assert by_id[a1.id].joint_siblings[0].warehouse_id == a2.warehouse_id

    async def test_auto_deliver_delivers_all_shipped(self, db_session):
        """ACCEPTED поставка → ВСЕ отгруженные сборки → DELIVERED (не только одна)."""
        from backend.services.fbo_supply.sync import _auto_deliver_assembly

        a1, a2, fbo_id = await self._make_joint_pair(db_session)
        await db_session.execute(
            text("UPDATE assembly_requests SET status = 'SHIPPED' WHERE id IN (:a, :b)"),
            {"a": a1.id, "b": a2.id},
        )
        await db_session.commit()
        db_session.expire_all()

        changed = await _auto_deliver_assembly(db_session, PROJECT_ID, fbo_id)
        await db_session.commit()
        assert changed is True

        rows = (
            await db_session.execute(
                text("SELECT status FROM assembly_requests WHERE id IN (:a, :b)"),
                {"a": a1.id, "b": a2.id},
            )
        ).scalars().all()
        assert set(rows) == {"DELIVERED"}

    async def test_refresh_joint_non_destructive(self, db_session):
        """«Из FBO» на совместной сборке НЕ перестраивает позиции (no error, состав цел).

        Без guard refresh затянул бы весь состав поставки (BC1=10) в сборку (было 5).
        """
        from backend.services.assembly.analytics import refresh_from_fbo

        a1, _a2, _fbo_id = await self._make_joint_pair(db_session)
        before = {(i.barcode, i.quantity) for i in (await get_assembly_request(db_session, PROJECT_ID, a1.id)).items}
        res = await refresh_from_fbo(db_session, PROJECT_ID, a1.id, skip_force_enrich=True)
        assert res.added == 0 and res.removed == 0 and res.changed == 0
        after = {(i.barcode, i.quantity) for i in (await get_assembly_request(db_session, PROJECT_ID, a1.id)).items}
        assert before == after  # позиции совместной сборки не тронуты

    async def test_combined_ff_mismatch_match(self, db_session):
        """Расхождение совместной поставки = СУММА 2 сборок vs СУММА их заявок ФФ.

        our: a1=5 + a2=3 = 8; ФФ: 5 + 3 = 8 → совпадает (False у обеих)."""
        from backend.services import fulfillment_service

        a1, a2, _fbo_id = await self._make_joint_pair(db_session)
        await _link_ff_request(db_session, assembly_request_id=a1.id, warehouse_id=a1.warehouse_id, total_qty=5)
        await _link_ff_request(db_session, assembly_request_id=a2.id, warehouse_id=a2.warehouse_id, total_qty=3)

        m = await fulfillment_service.get_assembly_ff_mismatch_map(db_session, PROJECT_ID, {a1.id, a2.id})
        assert m[a1.id] is False and m[a2.id] is False

        detail = await fulfillment_service.get_assembly_ff_mismatch_detail(db_session, PROJECT_ID, a1.id)
        assert detail["our_total"] == 8  # сумма обеих сборок ДДС
        assert detail["ff_total"] == 8  # сумма обеих заявок ФФ
        assert "," in (detail["assembly_number"] or "")  # объединённая подпись «ASM-…, ASM-…»

    async def test_combined_ff_mismatch_detects_diff(self, db_session):
        """Сумма ФФ ≠ сумме сборок → расхождение True у обеих (combined).

        our: 5 + 3 = 8; ФФ: 5 + 5 = 10 → расхождение."""
        from backend.services import fulfillment_service

        a1, a2, _fbo_id = await self._make_joint_pair(db_session)
        await _link_ff_request(db_session, assembly_request_id=a1.id, warehouse_id=a1.warehouse_id, total_qty=5)
        await _link_ff_request(db_session, assembly_request_id=a2.id, warehouse_id=a2.warehouse_id, total_qty=5)

        m = await fulfillment_service.get_assembly_ff_mismatch_map(db_session, PROJECT_ID, {a1.id, a2.id})
        assert m[a1.id] is True and m[a2.id] is True

        detail = await fulfillment_service.get_assembly_ff_mismatch_detail(db_session, PROJECT_ID, a1.id)
        assert detail["our_total"] == 8 and detail["ff_total"] == 10


@pytest.mark.asyncio
class TestListBuildBatched:
    """Список сборок строит ответ батч-запросами, без per-row N+1.

    Роутер раньше дёргал на КАЖДУЮ заявку контрагента + номенклатуру + остатки —
    сотни запросов на ~400 строк через PgBouncer → тормоза списка/фильтров/поиска.
    prefetch_list_maps собирает всё фиксированным числом запросов, а
    _build_response с картами в БД не ходит. Покрываем: (1) ответ бит-в-бит равен
    per-row сборке, (2) build-loop не масштабируется по числу строк.
    """

    @staticmethod
    @contextmanager
    def _count_queries():
        """Считает SQL-запросы через before_cursor_execute на sync-движке."""
        from tests.conftest_api import test_engine

        counter = {"n": 0}

        def _before(conn, cursor, statement, params, context, executemany):
            counter["n"] += 1

        event.listen(test_engine.sync_engine, "before_cursor_execute", _before)
        try:
            yield counter
        finally:
            event.remove(test_engine.sync_engine, "before_cursor_execute", _before)

    async def _seed_two_requests(self, db_session):
        """req1: wh1 + перевозчик; req2: wh2 + своя FBO.

        Покрывает все per-row пути prefetch: номенклатура, остатки (2 склада),
        контрагент-перевозчик.
        """
        from backend.models.counterparty import Counterparty

        carrier = Counterparty(project_id=PROJECT_ID, name="ИП Батч Тест", primary_type="CARRIER")
        db_session.add(carrier)
        await db_session.flush()
        req1 = await _create_test_request(db_session)
        req1.counterparty_id = carrier.id

        wh2_id = await _create_second_fulfillment_wh(db_session, stock_for=[TEST_BARCODE_2])
        fbo2 = WbFboSupply(
            project_id=PROJECT_ID,
            wb_supply_id="ASM-FBO-TEST-2",
            wb_status=WbSupplyStatus.ACTIVE,
            name="ASM_TEST FBO Supply 2",
            warehouse_name="Тула",
            created_at_wb=datetime(2026, 3, 21),
        )
        db_session.add(fbo2)
        await db_session.flush()
        nom2_id = (
            await db_session.execute(
                text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
                {"pid": PROJECT_ID, "bc": TEST_BARCODE_2},
            )
        ).scalar()
        req2 = AssemblyRequest(
            project_id=PROJECT_ID,
            warehouse_id=wh2_id,
            wb_fbo_supply_id=fbo2.id,
            number="ASM-BATCH-TEST-2",
            status=AssemblyStatus.IN_PROGRESS,
            pallets_count=1,
            pallet_weight_kg=Decimal("10.00"),
        )
        db_session.add(req2)
        await db_session.flush()
        db_session.add(
            AssemblyRequestItem(
                project_id=PROJECT_ID,
                assembly_request_id=req2.id,
                nomenclature_id=nom2_id,
                barcode=TEST_BARCODE_2,
                quantity=3,
            )
        )
        await db_session.commit()
        return req1, req2

    async def test_batch_build_matches_per_row(self, db_session):
        """Ответ с prefetch-картами бит-в-бит равен per-row сборке."""
        await self._seed_two_requests(db_session)
        items, _ = await list_assembly_requests(db_session, PROJECT_ID, limit=500)
        assert len(items) >= 2

        prefetch = await prefetch_list_maps(db_session, PROJECT_ID, items)
        for req in items:
            per_row = await _build_response(db_session, req)
            batched = await _build_response(db_session, req, **prefetch)
            assert batched == per_row

    async def test_build_loop_is_constant_queries(self, db_session):
        """prefetch + build-loop не масштабируется по числу строк (нет N+1)."""
        await self._seed_two_requests(db_session)
        items, _ = await list_assembly_requests(db_session, PROJECT_ID, limit=500)
        assert len(items) >= 2

        with self._count_queries() as c:
            prefetch = await prefetch_list_maps(db_session, PROJECT_ID, items)
            for req in items:
                await _build_response(db_session, req, **prefetch)
        batched = c["n"]

        with self._count_queries() as c2:
            for req in items:
                await _build_response(db_session, req)
        per_row = c2["n"]

        # Батч-путь дешевле и ограничен константой (≤ число prefetch-запросов),
        # per-row растёт с числом строк.
        assert batched < per_row
        assert batched <= 6
