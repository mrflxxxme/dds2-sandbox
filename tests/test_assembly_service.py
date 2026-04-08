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

from datetime import date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.warehouse import OutboundShipment, WarehouseStock
from backend.models.wb_fbo import WbFboSupply, WbSupplyStatus
from backend.schemas.assembly import (
    AssemblyItemCreate,
    AssemblyRequestCreate,
    AssemblyRequestUpdate,
    AssignVehicle,
)
from backend.services.assembly_service import (
    assign_vehicle,
    assign_vehicle_bulk,
    cancel_request,
    create_assembly_request,
    get_assembly_request,
    list_assembly_requests,
    mark_ready,
    ship_request,
    start_assembly,
    update_assembly_request,
)

PROJECT_ID = 1
OTHER_PROJECT_ID = 99999
TEST_BARCODE_1 = "TEST_BC_ASM_001"
TEST_BARCODE_2 = "TEST_BC_ASM_002"


@pytest_asyncio.fixture(autouse=True)
async def setup_test_data(db_session):
    """Clean assembly data and ensure test fixtures exist."""
    # Clean in dependency order (scoped to project_id to avoid cross-test contamination)
    await db_session.execute(
        text(
            "DELETE FROM assembly_request_items WHERE assembly_request_id IN "
            "(SELECT id FROM assembly_requests WHERE project_id = :pid)"
        ),
        {"pid": PROJECT_ID},
    )
    await db_session.execute(text("DELETE FROM assembly_requests WHERE project_id = :pid"), {"pid": PROJECT_ID})
    # Unlink FBO supplies from outbound shipments before deleting them
    await db_session.execute(
        text(
            "UPDATE wb_fbo_supplies SET outbound_shipment_id = NULL "
            "WHERE project_id = :pid AND outbound_shipment_id IN "
            "(SELECT id FROM outbound_shipments WHERE number LIKE 'OUT-%' AND project_id = :pid)"
        ),
        {"pid": PROJECT_ID},
    )
    # Clean outbound shipments created by tests
    await db_session.execute(
        text(
            "DELETE FROM outbound_shipment_items WHERE shipment_id IN (SELECT id FROM outbound_shipments WHERE number LIKE 'OUT-%' AND project_id = :pid)"
        ),
        {"pid": PROJECT_ID},
    )
    await db_session.execute(
        text("DELETE FROM outbound_shipments WHERE number LIKE 'OUT-%' AND project_id = :pid"), {"pid": PROJECT_ID}
    )
    # Clean stock
    await db_session.execute(
        text(
            "DELETE FROM stock_movements WHERE reference_type IN ('ASSEMBLY', 'ASSEMBLY_CANCEL') AND project_id = :pid"
        ),
        {"pid": PROJECT_ID},
    )
    await db_session.execute(text("DELETE FROM warehouse_stock WHERE project_id = :pid"), {"pid": PROJECT_ID})
    # Clean FBO test data
    await db_session.execute(
        text(
            "DELETE FROM wb_fbo_supply_items WHERE supply_id IN (SELECT id FROM wb_fbo_supplies WHERE project_id = :pid AND name LIKE 'ASM_TEST%')"
        ),
        {"pid": PROJECT_ID},
    )
    await db_session.execute(
        text("DELETE FROM wb_fbo_supplies WHERE project_id = :pid AND name LIKE 'ASM_TEST%'"), {"pid": PROJECT_ID}
    )
    await db_session.commit()

    # Ensure project exists
    result = await db_session.execute(text("SELECT id FROM projects WHERE id = :pid"), {"pid": PROJECT_ID})
    if result.scalar() is None:
        result_u = await db_session.execute(text("SELECT id FROM users WHERE username = 'asm_test_user'"))
        user_id = result_u.scalar()
        if user_id is None:
            await db_session.execute(
                text(
                    "INSERT INTO users (username, email, password_hash, is_active, created_at) "
                    "VALUES (:u, :e, :p, true, NOW()) RETURNING id"
                ),
                {"u": "asm_test_user", "e": "asm_test@test.com", "p": "nohash"},
            )
            result_u = await db_session.execute(text("SELECT id FROM users WHERE username = 'asm_test_user'"))
            user_id = result_u.scalar()
        await db_session.execute(
            text(
                "INSERT INTO projects (id, name, slug, owner_id, created_at) "
                "VALUES (:pid, :n, :s, :o, NOW()) ON CONFLICT (id) DO NOTHING"
            ),
            {"pid": PROJECT_ID, "n": "ASM Test Project", "s": "asm-test", "o": user_id},
        )
        await db_session.commit()

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


# --- Tests ------------------------------------------------------------------


@pytest.mark.asyncio
class TestCreateAssemblyRequest:
    """Tests 1-4: Create assembly request."""

    async def test_create_valid(self, db_session):
        """1. Create with valid data -> status PENDING, items saved."""
        req = await _create_test_request(db_session)
        assert req.status == AssemblyStatus.PENDING
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

    async def test_create_already_linked_fbo(self, db_session):
        """4. Create with already-linked FBO supply -> ValueError."""
        # First create a request
        await _create_test_request(db_session)

        # Try to create another for the same FBO supply
        wh_id = await _get_fulfillment_wh_id(db_session)
        fbo_id = await _get_fbo_supply_id(db_session)
        payload = AssemblyRequestCreate(
            warehouse_id=wh_id,
            wb_fbo_supply_id=fbo_id,
            pallets_count=1,
            pallet_weight_kg=Decimal("100.00"),
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=1)],
        )
        with pytest.raises(ValueError, match="already has an active assembly request"):
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

    async def test_edit_items_in_ready_raises(self, db_session):
        """7b. Edit items in READY status -> ValueError."""
        req = await _create_test_request(db_session)
        await start_assembly(db_session, PROJECT_ID, req.id)
        await mark_ready(db_session, PROJECT_ID, req.id)

        payload = AssemblyRequestUpdate(
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_2, quantity=3)],
        )
        with pytest.raises(ValueError, match="READY"):
            await update_assembly_request(db_session, PROJECT_ID, req.id, payload)


@pytest.mark.asyncio
class TestLifecycle:
    """Test 8-9: Full lifecycle and invalid transitions."""

    async def test_full_lifecycle(self, db_session):
        """8. Full lifecycle: PENDING -> IN_PROGRESS -> READY -> VEHICLE_ASSIGNED -> SHIPPED."""
        req = await _create_test_request(db_session)
        assert req.status == AssemblyStatus.PENDING

        req = await start_assembly(db_session, PROJECT_ID, req.id)
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
        """9. Skip status (PENDING -> READY directly) -> ValueError."""
        req = await _create_test_request(db_session)
        with pytest.raises(ValueError, match="Cannot transition"):
            await mark_ready(db_session, PROJECT_ID, req.id)


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
        """14. Create assembly without wb_fbo_supply_id -> should succeed, status PENDING."""
        wh_id = await _get_fulfillment_wh_id(db_session)
        payload = AssemblyRequestCreate(
            warehouse_id=wh_id,
            wb_fbo_supply_id=None,
            pallets_count=1,
            pallet_weight_kg=Decimal("100.00"),
            items=[AssemblyItemCreate(barcode=TEST_BARCODE_1, quantity=5)],
        )
        req = await create_assembly_request(db_session, PROJECT_ID, payload)
        assert req.status == AssemblyStatus.PENDING
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
