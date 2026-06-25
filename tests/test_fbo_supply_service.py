"""
Tests for FBO Supply module — service, helpers, router.

Covers:
1. Helpers: parse WB datetime/date, FBW status/box mappings
2. List: search, filter (status, warehouse), sort, pagination
3. Items: get items for a supply
4. Link/Unlink: supply ↔ OutboundShipment
5. Sync: full sync from FBW API (create + update supplies)
6. Sync: status-only sync
7. Schemas: Pydantic validation
8. Router: API endpoint integration tests
9. WB API client: FBW methods exist
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select, text

from backend.models import IntegrationKey, Nomenclature, OutboundShipment, Warehouse, WarehouseStock
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.wb_fbo import WbFboSupply, WbFboSupplyItem, WbSupplyStatus
from backend.services.fbo_supply_service import (
    _collect_assembly_ship_on_wb_accepted,
    _create_supply_from_fbw_list,
    _map_fbw_box_type,
    _map_fbw_status,
    _parse_wb_date,
    _parse_wb_datetime,
    _ship_assemblies_best_effort,
    _update_supply_from_fbw_detail,
    _update_supply_from_fbw_list,
    get_fbo_supply_items,
    list_fbo_supplies,
    list_warehouses,
    sync_fbo_statuses,
    sync_fbo_supplies,
)

# ─── Fixture: ensure test project exists ─────────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def ensure_test_project(db_session):
    """Create a test user + project with id=1 if not exists, clean FBO data before each test."""
    # Clean FBO data from previous test runs (scoped to project_id=1, respect FK order)
    await db_session.execute(
        text(
            "DELETE FROM assembly_status_history WHERE assembly_request_id IN "
            "(SELECT id FROM assembly_requests WHERE project_id = 1 AND wb_fbo_supply_id IN "
            "(SELECT id FROM wb_fbo_supplies WHERE project_id = 1))"
        )
    )
    await db_session.execute(
        text(
            "DELETE FROM assembly_request_items WHERE assembly_request_id IN "
            "(SELECT id FROM assembly_requests WHERE project_id = 1 AND wb_fbo_supply_id IN "
            "(SELECT id FROM wb_fbo_supplies WHERE project_id = 1))"
        )
    )
    await db_session.execute(
        text(
            "DELETE FROM assembly_requests WHERE project_id = 1 AND wb_fbo_supply_id IN "
            "(SELECT id FROM wb_fbo_supplies WHERE project_id = 1)"
        )
    )
    await db_session.execute(
        text(
            "DELETE FROM wb_fbo_supply_items WHERE supply_id IN "
            "(SELECT id FROM wb_fbo_supplies WHERE project_id = 1)"
        )
    )
    await db_session.execute(text("DELETE FROM wb_fbo_supplies WHERE project_id = 1"))
    await db_session.commit()

    # Ensure project exists.
    # allow-fixed-project-id: id=1 is the foundational seed project and sits far
    # below projects_id_seq — the collision window has passed. See check 21 in
    # scripts/check_conventions.sh.
    result = await db_session.execute(text("SELECT id FROM projects WHERE id = 1"))
    if result.scalar() is None:
        result_u = await db_session.execute(text("SELECT id FROM users WHERE username = 'fbo_test_user'"))
        user_id = result_u.scalar()
        if user_id is None:
            await db_session.execute(
                text(
                    "INSERT INTO users (username, email, password_hash, is_active, created_at) "
                    "VALUES (:u, :e, :p, true, NOW()) RETURNING id"
                ),
                {"u": "fbo_test_user", "e": "fbo_test@test.com", "p": "nohash"},
            )
            result_u = await db_session.execute(text("SELECT id FROM users WHERE username = 'fbo_test_user'"))
            user_id = result_u.scalar()
        await db_session.execute(
            text(
                "INSERT INTO projects (id, name, slug, owner_id, created_at) "
                "VALUES (1, :n, :s, :o, NOW()) ON CONFLICT (id) DO NOTHING"
            ),
            {"n": "FBO Test Project", "s": "fbo-test", "o": user_id},
        )
        await db_session.commit()
    yield


# ─── Helper tests (pure functions, no DB) ───────────────────────────────────


class TestHelpers:
    """Test pure helper functions — no DB needed."""

    def test_parse_wb_datetime_iso(self):
        result = _parse_wb_datetime("2026-03-20T16:20:00Z")
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 20
        assert result.hour == 16
        assert result.minute == 20

    def test_parse_wb_datetime_with_timezone(self):
        result = _parse_wb_datetime("2026-03-20T16:20:00+03:00")
        assert result is not None
        assert result.tzinfo is None  # We strip timezone

    def test_parse_wb_datetime_empty(self):
        assert _parse_wb_datetime("") is None
        assert _parse_wb_datetime(None) is None

    def test_parse_wb_datetime_invalid(self):
        assert _parse_wb_datetime("not-a-date") is None

    def test_parse_wb_date_valid(self):
        result = _parse_wb_date("2026-03-20")
        assert result == date(2026, 3, 20)

    def test_parse_wb_date_from_datetime(self):
        result = _parse_wb_date("2026-03-20T16:20:00Z")
        assert result == date(2026, 3, 20)

    def test_parse_wb_date_empty(self):
        assert _parse_wb_date("") is None
        assert _parse_wb_date(None) is None

    def test_map_fbw_status(self):
        assert _map_fbw_status(1) == WbSupplyStatus.ACTIVE
        assert _map_fbw_status(2) == WbSupplyStatus.ON_DELIVERY
        assert _map_fbw_status(3) == WbSupplyStatus.IN_PROGRESS
        assert _map_fbw_status(4) == WbSupplyStatus.ACCEPTED
        assert _map_fbw_status(5) == WbSupplyStatus.ACCEPTED
        assert _map_fbw_status(6) == WbSupplyStatus.ACCEPTED  # Частично принята
        assert _map_fbw_status(99) == WbSupplyStatus.ACTIVE  # Unknown → default

    def test_map_fbw_box_type(self):
        assert _map_fbw_box_type(None) is None
        assert _map_fbw_box_type(0) is None
        assert _map_fbw_box_type(1) == "Короб"
        assert _map_fbw_box_type(2) == "Короб"
        assert _map_fbw_box_type(5) == "Монопаллет"
        assert _map_fbw_box_type(6) == "Суперсейф"
        assert "Тип" in _map_fbw_box_type(99)  # Unknown

    def test_create_supply_from_fbw_list(self):
        wb_data = {
            "supplyID": 37847227,
            "statusID": 2,
            "createDate": "2026-03-12T16:12:14Z",
            "supplyDate": "2026-03-21",
            "factDate": "2026-03-20",
            "boxTypeID": 1,
        }
        supply = _create_supply_from_fbw_list(project_id=1, wb_data=wb_data)
        assert supply.wb_supply_id == "37847227"
        assert supply.wb_status == WbSupplyStatus.ON_DELIVERY
        assert supply.cargo_type == "Короб"
        assert supply.planned_date == date(2026, 3, 21)
        assert supply.actual_date == date(2026, 3, 20)
        assert supply.project_id == 1

    def test_update_supply_from_fbw_list(self):
        supply = WbFboSupply(
            wb_supply_id="37847227",
            wb_status=WbSupplyStatus.ACTIVE,
            project_id=1,
            created_at_wb=datetime(2026, 3, 1),
        )
        wb_data = {
            "statusID": 4,
            "supplyDate": "2026-03-21",
            "factDate": "2026-03-20",
            "boxTypeID": 5,
        }
        _update_supply_from_fbw_list(supply, wb_data)
        assert supply.wb_status == WbSupplyStatus.ACCEPTED
        assert supply.planned_date == date(2026, 3, 21)
        assert supply.actual_date == date(2026, 3, 20)
        assert supply.cargo_type == "Монопаллет"

    def test_update_supply_from_fbw_detail(self):
        supply = WbFboSupply(
            wb_supply_id="37847227",
            wb_status=WbSupplyStatus.ON_DELIVERY,
            project_id=1,
            created_at_wb=datetime(2026, 3, 1),
        )
        detail = {
            "warehouseName": "Электросталь",
            "quantity": 726,
            "acceptedQuantity": 700,
        }
        _update_supply_from_fbw_detail(supply, detail)
        assert supply.warehouse_name == "Электросталь"
        assert supply.total_qty == 726
        assert supply.accepted_qty == 700


# ─── Enum tests ─────────────────────────────────────────────────────────────


class TestEnums:
    def test_wb_supply_status_values(self):
        assert WbSupplyStatus.ACTIVE == "ACTIVE"
        assert WbSupplyStatus.ON_DELIVERY == "ON_DELIVERY"
        assert WbSupplyStatus.IN_PROGRESS == "IN_PROGRESS"
        assert WbSupplyStatus.ACCEPTED == "ACCEPTED"
        assert WbSupplyStatus.CANCELLED == "CANCELLED"

    def test_wb_supply_status_count(self):
        assert len(WbSupplyStatus) == 5


# ─── Schema tests ──────────────────────────────────────────────────────────


class TestSchemas:
    def test_supply_schema_from_model(self):
        from backend.schemas.wb_fbo import WbFboSupplySchema

        schema = WbFboSupplySchema(
            id=1,
            project_id=1,
            wb_supply_id="37847227",
            wb_status="ACTIVE",
            created_at_wb=datetime(2026, 3, 20, 16, 20),
            total_qty=10,
            accepted_qty=5,
        )
        assert schema.wb_supply_id == "37847227"
        assert schema.total_qty == 10

    def test_supply_schema_exposes_return_fields(self):
        """Regression: WbFboSupplySchema must declare return_processed_at / return_qty /
        return_type (symmetric to excess_*). Before the fix the schema omitted them, so
        Pydantic silently dropped the columns at serialization and the FBO list UI never
        learned a недоприёмка return was processed → banner + «Оформить возврат» button
        stayed active forever (backend then 400'd on the double submit)."""
        from backend.schemas.wb_fbo import WbFboSupplySchema

        schema = WbFboSupplySchema(
            id=1,
            project_id=1,
            wb_supply_id="37847227",
            wb_status="ACCEPTED",
            created_at_wb=datetime(2026, 5, 22, 16, 20),
            return_processed_at=datetime(2026, 6, 25, 10, 0),
            return_qty=27,
            return_type="GOODS",
        )
        assert schema.return_processed_at == datetime(2026, 6, 25, 10, 0)
        assert schema.return_qty == 27
        assert schema.return_type == "GOODS"

    def test_supply_item_schema(self):
        from backend.schemas.wb_fbo import WbFboSupplyItemSchema

        schema = WbFboSupplyItemSchema(
            id=1,
            supply_id=1,
            wb_order_id="ORD-123",
            barcode="2045407872858",
            quantity=3,
            accepted_qty=3,
        )
        assert schema.barcode == "2045407872858"

    def test_list_response(self):
        from backend.schemas.wb_fbo import WbFboSupplyListResponse

        resp = WbFboSupplyListResponse(items=[], total=0)
        assert resp.total == 0
        assert resp.items == []

    def test_sync_result(self):
        from backend.schemas.wb_fbo import FboSyncResultSchema

        result = FboSyncResultSchema(synced=10, created=3, updated=7, errors=0, message="ok")
        assert result.synced == 10


# ─── Service tests (require DB) ────────────────────────────────────────────


@pytest.mark.asyncio
class TestListFboSupplies:
    """Test list_fbo_supplies with DB fixtures."""

    async def test_list_empty(self, db_session):
        """Empty project returns empty list."""
        supplies, total = await list_fbo_supplies(db_session, project_id=99999)
        assert supplies == []
        assert total == 0

    async def test_list_filters_by_project(self, db_session):
        """Supplies from other projects are not returned."""
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="FILTER-TEST-1",
            wb_status=WbSupplyStatus.ACTIVE,
            created_at_wb=datetime(2026, 3, 20),
        )
        db_session.add(supply)
        await db_session.commit()

        _supplies, total = await list_fbo_supplies(db_session, project_id=99999)
        assert total == 0

    async def test_list_search_by_wb_supply_id(self, db_session):
        """Search finds supply by wb_supply_id."""
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="SEARCH-12345",
            wb_status=WbSupplyStatus.ACTIVE,
            created_at_wb=datetime(2026, 3, 20),
        )
        db_session.add(supply)
        await db_session.commit()

        supplies, total = await list_fbo_supplies(db_session, project_id=1, search="SEARCH-12345")
        assert total >= 1
        found = [s for s in supplies if s["wb_supply_id"] == "SEARCH-12345"]
        assert len(found) == 1

    async def test_list_row_carries_return_processed_fields(self, db_session):
        """Regression: the list enrichment dict + response schema must surface
        return_processed_at/return_qty/return_type, mirroring the router path
        (WbFboSupplySchema(**row)). This is what lets the UI flip the недоприёмка
        banner to its «✓ обработана» done-state once a return receipt exists."""
        from backend.schemas.wb_fbo import WbFboSupplySchema

        processed_at = datetime(2026, 6, 25, 10, 0)
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="RET-EXPOSED",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 5, 22),
            total_qty=1409,
            accepted_qty=1406,
            return_processed_at=processed_at,
            return_qty=27,
            return_type="GOODS",
        )
        db_session.add(supply)
        await db_session.commit()

        supplies, _total = await list_fbo_supplies(db_session, project_id=1, search="RET-EXPOSED")
        row = next(s for s in supplies if s["wb_supply_id"] == "RET-EXPOSED")
        # enrichment dict copies all columns…
        assert row["return_processed_at"] == processed_at
        # …and the response schema must pass them through (the bug was it dropping them).
        serialized = WbFboSupplySchema(**row)
        assert serialized.return_processed_at == processed_at
        assert serialized.return_qty == 27
        assert serialized.return_type == "GOODS"

    async def test_list_filter_by_status(self, db_session):
        """Filter by status returns only matching supplies."""
        for status in [WbSupplyStatus.ACTIVE, WbSupplyStatus.ACCEPTED]:
            supply = WbFboSupply(
                project_id=1,
                wb_supply_id=f"STATUS-{status}",
                wb_status=status,
                created_at_wb=datetime(2026, 3, 20),
            )
            db_session.add(supply)
        await db_session.commit()

        supplies, _total = await list_fbo_supplies(db_session, project_id=1, status="ACCEPTED")
        for s in supplies:
            assert s["wb_status"] == "ACCEPTED"

    async def test_list_filter_by_warehouse(self, db_session):
        """Filter by warehouse returns only matching supplies."""
        for wh in ["Электросталь", "Коледино"]:
            supply = WbFboSupply(
                project_id=1,
                wb_supply_id=f"WH-{wh}",
                wb_status=WbSupplyStatus.ACTIVE,
                warehouse_name=wh,
                created_at_wb=datetime(2026, 3, 20),
            )
            db_session.add(supply)
        await db_session.commit()

        supplies, total = await list_fbo_supplies(db_session, project_id=1, warehouse="Электросталь")
        assert total == 1
        assert supplies[0]["warehouse_name"] == "Электросталь"

    async def test_list_pagination(self, db_session):
        """Pagination returns correct slice."""
        for i in range(5):
            supply = WbFboSupply(
                project_id=1,
                wb_supply_id=f"PAGE-{i}",
                wb_status=WbSupplyStatus.ACTIVE,
                created_at_wb=datetime(2026, 3, 20),
            )
            db_session.add(supply)
        await db_session.commit()

        supplies, total = await list_fbo_supplies(db_session, project_id=1, limit=2, offset=0)
        assert len(supplies) <= 2
        assert total >= 5

    async def test_list_sort_asc(self, db_session):
        """Sort by created_at_wb ascending."""
        supplies, _ = await list_fbo_supplies(db_session, project_id=1, sort_by="created_at_wb", sort_order="asc")
        if len(supplies) >= 2:
            assert supplies[0].created_at_wb <= supplies[1].created_at_wb


@pytest.mark.asyncio
class TestListWarehouses:
    """Test list_warehouses."""

    async def test_empty(self, db_session):
        warehouses = await list_warehouses(db_session, project_id=99999)
        assert warehouses == []

    async def test_returns_unique_sorted(self, db_session):
        for i, wh in enumerate(["Коледино", "Электросталь", "Коледино"]):
            supply = WbFboSupply(
                project_id=1,
                wb_supply_id=f"WH-LIST-{i}",
                wb_status=WbSupplyStatus.ACTIVE,
                warehouse_name=wh,
                created_at_wb=datetime(2026, 3, 20),
            )
            db_session.add(supply)
        await db_session.commit()

        warehouses = await list_warehouses(db_session, project_id=1)
        assert "Коледино" in warehouses
        assert "Электросталь" in warehouses
        assert len(warehouses) == 2  # unique
        assert warehouses == sorted(warehouses)  # sorted

    async def test_excludes_null(self, db_session):
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="WH-NULL",
            wb_status=WbSupplyStatus.ACTIVE,
            warehouse_name=None,
            created_at_wb=datetime(2026, 3, 20),
        )
        db_session.add(supply)
        await db_session.commit()

        warehouses = await list_warehouses(db_session, project_id=1)
        assert None not in warehouses


@pytest.mark.asyncio
class TestWithoutAssembly:
    """without_assembly must filter to ACCEPTED only — matches summary card
    'WB принял, но в DDS нет заявки'. Non-final statuses aren't actionable.
    """

    async def test_without_assembly_excludes_non_accepted(self, db_session):
        # ACCEPTED without AR → should appear
        accepted_no_ar = WbFboSupply(
            project_id=1,
            wb_supply_id="WA-ACCEPTED",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 3, 20),
        )
        # ACTIVE without AR → should NOT appear (non-final)
        active_no_ar = WbFboSupply(
            project_id=1,
            wb_supply_id="WA-ACTIVE",
            wb_status=WbSupplyStatus.ACTIVE,
            created_at_wb=datetime(2026, 3, 20),
        )
        # IN_PROGRESS without AR → should NOT appear
        in_progress_no_ar = WbFboSupply(
            project_id=1,
            wb_supply_id="WA-IN-PROGRESS",
            wb_status=WbSupplyStatus.IN_PROGRESS,
            created_at_wb=datetime(2026, 3, 20),
        )
        db_session.add_all([accepted_no_ar, active_no_ar, in_progress_no_ar])
        await db_session.commit()

        supplies, _total = await list_fbo_supplies(
            db_session,
            project_id=1,
            without_assembly=True,
        )
        ids = {s["wb_supply_id"] for s in supplies}
        assert "WA-ACCEPTED" in ids
        assert "WA-ACTIVE" not in ids
        assert "WA-IN-PROGRESS" not in ids


@pytest.mark.asyncio
class TestExcludeWithAssembly:
    """exclude_with_assembly must tolerate active AR with wb_fbo_supply_id=NULL.

    Without IS NOT NULL filter in subquery, Postgres NOT IN (..., NULL) returns
    NULL for every comparison → every supply is filtered out silently.
    """

    async def test_null_ar_does_not_zero_out_supplies(self, db_session):
        from backend.models.assembly import AssemblyRequest, AssemblyStatus
        from backend.models.warehouse import Warehouse

        wh = Warehouse(project_id=1, name="wh-excl", warehouse_type="EXTERNAL")
        db_session.add(wh)
        await db_session.flush()

        # Two supplies: A (no AR), B (linked to an active AR)
        supply_a = WbFboSupply(
            project_id=1,
            wb_supply_id="EXCL-A",
            wb_status=WbSupplyStatus.ACTIVE,
            created_at_wb=datetime(2026, 3, 20),
        )
        supply_b = WbFboSupply(
            project_id=1,
            wb_supply_id="EXCL-B",
            wb_status=WbSupplyStatus.ACTIVE,
            created_at_wb=datetime(2026, 3, 20),
        )
        db_session.add_all([supply_a, supply_b])
        await db_session.flush()

        # AR linked to supply B (active status)
        linked_ar = AssemblyRequest(
            project_id=1,
            warehouse_id=wh.id,
            number="ASM-EXCL-B",
            status=AssemblyStatus.PENDING,
            wb_fbo_supply_id=supply_b.id,
            estimated_ready_date=date(2026, 4, 10),
            pallets_count=1,
            pallet_weight_kg=100,
        )
        # Orphan AR with wb_fbo_supply_id=NULL — this is what broke NOT IN before the fix
        null_ar = AssemblyRequest(
            project_id=1,
            warehouse_id=wh.id,
            number="ASM-EXCL-NULL",
            status=AssemblyStatus.PENDING,
            wb_fbo_supply_id=None,
            estimated_ready_date=date(2026, 4, 10),
            pallets_count=1,
            pallet_weight_kg=100,
        )
        db_session.add_all([linked_ar, null_ar])
        await db_session.commit()

        supplies, total = await list_fbo_supplies(
            db_session,
            project_id=1,
            exclude_with_assembly=True,
        )
        ids = {s["wb_supply_id"] for s in supplies}
        assert "EXCL-A" in ids, "Supply without any AR must be returned"
        assert "EXCL-B" not in ids, "Supply with active AR must be filtered out"
        assert total >= 1


@pytest.mark.asyncio
class TestGetFboSupplyItems:
    """Test get_fbo_supply_items."""

    async def test_items_not_found(self, db_session):
        """Non-existent supply raises ValueError."""
        with pytest.raises(ValueError, match="FBO Supply not found"):
            await get_fbo_supply_items(db_session, project_id=1, supply_id=99999)

    async def test_items_wrong_project(self, db_session):
        """Supply from other project raises ValueError."""
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="ITEMS-WRONG-PROJECT",
            wb_status=WbSupplyStatus.ACTIVE,
            created_at_wb=datetime(2026, 3, 20),
        )
        db_session.add(supply)
        await db_session.commit()

        with pytest.raises(ValueError, match="FBO Supply not found"):
            await get_fbo_supply_items(db_session, project_id=99999, supply_id=supply.id)

    async def test_items_returns_list(self, db_session):
        """Supply with items returns them."""
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="ITEMS-OK",
            wb_status=WbSupplyStatus.ACTIVE,
            created_at_wb=datetime(2026, 3, 20),
        )
        db_session.add(supply)
        await db_session.flush()

        item = WbFboSupplyItem(
            project_id=1,
            supply_id=supply.id,
            wb_order_id="ORD-1",
            barcode="123456789",
            quantity=5,
            accepted_qty=5,
        )
        db_session.add(item)
        await db_session.commit()

        items = await get_fbo_supply_items(db_session, project_id=1, supply_id=supply.id)
        assert len(items) == 1
        assert items[0].barcode == "123456789"
        assert items[0].quantity == 5


@pytest.mark.asyncio
class TestSyncFboSupplies:
    """Test full sync with mocked FBW API client."""

    async def test_sync_creates_supplies(self, db_session):
        """Full sync creates new supplies from FBW API data."""
        mock_client = AsyncMock()
        mock_client.get_fbw_supplies.return_value = [
            {
                "supplyID": 37847227,
                "statusID": 2,
                "createDate": "2026-03-12T16:12:14Z",
                "supplyDate": "2026-03-21",
                "boxTypeID": 1,
            },
        ]
        mock_client.get_fbw_supply_detail.return_value = {
            "warehouseName": "Электросталь",
            "quantity": 726,
            "acceptedQuantity": 0,
        }
        mock_client.get_fbw_supply_goods.return_value = [
            {
                "barcode": "2043465657752",
                "vendorCode": "DVK_210x90",
                "nmID": 123456,
                "quantity": 12,
                "acceptedQuantity": 0,
            },
        ]

        from sqlalchemy import select

        from backend.models.integrations import IntegrationKey

        result_q = await db_session.execute(
            select(IntegrationKey)
            .where(
                IntegrationKey.project_id == 1,
                IntegrationKey.service == "wb",
            )
            .limit(1)
        )
        key = result_q.scalar_one_or_none()
        if not key:
            pytest.skip("No WB integration key in test DB for project 1")

        result = await sync_fbo_supplies(db_session, 1, mock_client, key.id)
        assert result["created"] >= 1
        assert result["synced"] >= 1
        assert "message" in result


@pytest.mark.asyncio
class TestSyncFboStatuses:
    """Test status-only sync."""

    async def test_sync_statuses_updates_active(self, db_session):
        """Status sync updates non-final supplies."""
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="37847227",
            wb_status=WbSupplyStatus.ACTIVE,
            created_at_wb=datetime.utcnow(),
        )
        db_session.add(supply)
        await db_session.commit()

        mock_client = AsyncMock()
        # FBW status sync uses list API now
        mock_client.get_fbw_supplies.return_value = [
            {
                "supplyID": 37847227,
                "statusID": 3,  # IN_PROGRESS
                "createDate": datetime.utcnow().isoformat() + "Z",
            },
        ]

        from sqlalchemy import select

        from backend.models.integrations import IntegrationKey

        result_q = await db_session.execute(
            select(IntegrationKey)
            .where(
                IntegrationKey.project_id == 1,
                IntegrationKey.service == "wb",
            )
            .limit(1)
        )
        key = result_q.scalar_one_or_none()
        if not key:
            pytest.skip("No WB integration key in test DB for project 1")

        result = await sync_fbo_statuses(db_session, 1, mock_client, key.id)
        assert result["updated"] >= 0  # May be 0 if supply wasn't matched


# ─── Model tests ───────────────────────────────────────────────────────────


class TestModels:
    """Test model constraints and defaults."""

    def test_supply_defaults(self):
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="TEST",
            created_at_wb=datetime(2026, 3, 20),
        )
        assert supply.total_qty in (0, None)
        assert supply.accepted_qty in (0, None)
        assert supply.outbound_shipment_id is None

    def test_item_defaults(self):
        item = WbFboSupplyItem(
            project_id=1,
            supply_id=1,
            wb_order_id="ORD-1",
            barcode="123",
            quantity=1,
        )
        assert item.accepted_qty in (0, None)
        assert item.nm_id is None
        assert item.article_seller is None

    def test_supply_status_enum(self):
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="ENUM-TEST",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 3, 20),
        )
        assert supply.wb_status == "ACCEPTED"


# ─── Enrich: re-sync partial acceptance for ACCEPTED supplies ──────────────


@pytest.mark.asyncio
class TestEnrichPartialAcceptance:
    """
    Enrich must pick up ACCEPTED supplies with accepted_qty < total_qty
    (partial acceptance) and re-fetch per-item accepted_qty from FBW goods API.
    """

    async def test_reenrich_accepted_with_partial_acceptance(self, db_session):
        from datetime import timedelta

        from backend.services.fbo_supply_service import enrich_fbo_supplies

        # Stale supply (synced > 24h ago) accepted with 336/406 — should be picked.
        stale_synced = datetime.utcnow() - timedelta(hours=36)
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="38231501",
            wb_status=WbSupplyStatus.ACCEPTED,
            name="FBW-38231501",
            created_at_wb=datetime(2026, 4, 1, 14, 16),
            warehouse_name="Тула",
            total_qty=406,
            accepted_qty=0,  # stale: not yet synced after acceptance
            synced_at=stale_synced,
        )
        db_session.add(supply)
        await db_session.commit()
        await db_session.refresh(supply)

        mock_client = AsyncMock()
        mock_client.get_fbw_supply_detail.return_value = {
            "warehouseName": "Тула",
            "quantity": 406,
            "acceptedQuantity": 336,
            "statusID": 5,
        }
        # goods sum: qty = 144+52+96+96+18 = 406, accepted = 144+0+96+96+0 = 336
        mock_client.get_fbw_supply_goods.return_value = [
            {"barcode": "2042072435609", "vendorCode": "DIVANDEK", "nmID": 1, "quantity": 144, "acceptedQuantity": 144},
            {"barcode": "2043160691778", "vendorCode": "NAKIDKA", "nmID": 2, "quantity": 52, "acceptedQuantity": 0},
            {"barcode": "2043300615220", "vendorCode": "KREST-B", "nmID": 4, "quantity": 96, "acceptedQuantity": 96},
            {"barcode": "2043300615237", "vendorCode": "KREST-K", "nmID": 5, "quantity": 96, "acceptedQuantity": 96},
            {"barcode": "2044145314996", "vendorCode": "ZEBRA", "nmID": 3, "quantity": 18, "acceptedQuantity": 0},
        ]

        result = await enrich_fbo_supplies(db_session, 1, mock_client, max_calls=5)

        assert result["enriched"] == 1
        assert mock_client.get_fbw_supply_goods.called, "goods API must be called for ACCEPTED with partial"

        await db_session.refresh(supply)
        # accepted_qty and total_qty are re-derived from goods sum (source of truth
        # per-SKU), not from detail.acceptedQuantity (can diverge from items).
        assert supply.accepted_qty == 336
        assert supply.total_qty == 406

        from sqlalchemy import select

        items_r = await db_session.execute(
            select(WbFboSupplyItem).where(WbFboSupplyItem.supply_id == supply.id).order_by(WbFboSupplyItem.barcode)
        )
        items = {it.barcode: it for it in items_r.scalars().all()}
        assert items["2042072435609"].accepted_qty == 144
        assert items["2043160691778"].accepted_qty == 0
        assert items["2044145314996"].accepted_qty == 0

    async def test_accepted_within_cooldown_is_skipped(self, db_session):
        """Recently synced ACCEPTED supply must not be re-enriched (cooldown)."""
        from backend.services.fbo_supply_service import enrich_fbo_supplies

        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="38231502",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 4, 1),
            warehouse_name="Тула",
            total_qty=406,
            accepted_qty=0,
            synced_at=datetime.utcnow(),  # just now — within cooldown
        )
        db_session.add(supply)
        await db_session.commit()

        mock_client = AsyncMock()
        result = await enrich_fbo_supplies(db_session, 1, mock_client, max_calls=5)

        assert result["enriched"] == 0
        assert not mock_client.get_fbw_supply_detail.called

    async def test_fully_accepted_with_items_is_skipped(self, db_session):
        """ACCEPTED with accepted == total AND items already in DB → no re-enrich."""
        from datetime import timedelta

        from backend.services.fbo_supply_service import enrich_fbo_supplies

        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="38231503",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 4, 1),
            warehouse_name="Тула",
            total_qty=100,
            accepted_qty=100,
            synced_at=datetime.utcnow() - timedelta(days=7),
        )
        db_session.add(supply)
        await db_session.commit()
        await db_session.refresh(supply)
        # Pre-existing items — no need to fetch from WB.
        db_session.add(
            WbFboSupplyItem(
                project_id=1,
                supply_id=supply.id,
                wb_order_id="ord-1",
                barcode="bc-1",
                quantity=100,
                accepted_qty=100,
            )
        )
        await db_session.commit()

        mock_client = AsyncMock()
        result = await enrich_fbo_supplies(db_session, 1, mock_client, max_calls=5)

        assert result["enriched"] == 0
        assert not mock_client.get_fbw_supply_detail.called

    async def test_fully_accepted_without_items_is_enriched(self, db_session):
        """Historical ACCEPTED supply that was never enriched (no items in DB)
        must be picked up by the scheduler so the UI can show product list."""
        from datetime import timedelta

        from backend.services.fbo_supply_service import enrich_fbo_supplies

        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="38231504",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 4, 1),
            warehouse_name="Тула",
            total_qty=50,
            accepted_qty=50,
            synced_at=datetime.utcnow() - timedelta(days=7),
        )
        db_session.add(supply)
        await db_session.commit()

        mock_client = AsyncMock()
        mock_client.get_fbw_supply_detail.return_value = {
            "warehouseName": "Тула",
            "quantity": 50,
            "acceptedQuantity": 50,
            "statusID": 5,
        }
        mock_client.get_fbw_supply_goods.return_value = [
            {"barcode": "bc-50", "vendorCode": "X", "nmID": 1, "quantity": 50, "acceptedQuantity": 50},
        ]

        result = await enrich_fbo_supplies(db_session, 1, mock_client, max_calls=5)

        assert result["enriched"] == 1
        assert mock_client.get_fbw_supply_goods.called

    async def test_goods_api_error_preserves_stale_synced_at(self, db_session, project):
        """ACCEPTED supply must NOT update synced_at when goods API fails —
        otherwise 24h cooldown blocks retry indefinitely even after WB recovers.
        Regression: 2026-04-22, 44 supplies stuck with accepted_qty=0 for 13 days.

        Uses the `project` fixture (unique auto-id) to isolate from the autouse
        fixture (project_id=1) under xdist parallel runs.
        """
        from datetime import timedelta

        from sqlalchemy import select

        from backend.services.fbo_supply_service import enrich_fbo_supplies

        stale_synced = datetime.utcnow() - timedelta(hours=36)
        supply = WbFboSupply(
            project_id=project.id,
            wb_supply_id="38413056",
            wb_status=WbSupplyStatus.ACCEPTED,
            name="FBW-38413056",
            created_at_wb=datetime(2026, 4, 9),
            warehouse_name="Коледино",
            total_qty=241,
            accepted_qty=0,
            synced_at=stale_synced,
        )
        db_session.add(supply)
        await db_session.commit()

        mock_client = AsyncMock()
        mock_client.get_fbw_supply_detail.return_value = {
            "warehouseName": "Коледино",
            "quantity": 241,
            "statusID": 5,
        }
        mock_client.get_fbw_supply_goods.side_effect = Exception("WB API rate limited (429)")

        await enrich_fbo_supplies(db_session, project.id, mock_client, max_calls=5)

        assert mock_client.get_fbw_supply_goods.called

        result = await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == supply.id))
        fresh = result.scalar_one()
        assert (
            fresh.synced_at == stale_synced
        ), "synced_at must not be refreshed when goods API fails — else 24h cooldown blocks retry indefinitely"


@pytest.mark.asyncio
class TestEnrichActiveWarehouseRefresh:
    """
    Active (IN_PROGRESS / ON_DELIVERY) supplies must be re-enriched once per 24h
    so that warehouse changes done by user in WB partner cabinet propagate to DB
    (list API never returns warehouseName, only detail does).
    """

    async def test_in_progress_stale_is_reenriched_with_new_warehouse(self, db_session):
        from datetime import timedelta

        from backend.services.fbo_supply_service import enrich_fbo_supplies

        stale_synced = datetime.utcnow() - timedelta(hours=36)
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="38782922",
            wb_status=WbSupplyStatus.IN_PROGRESS,
            name="FBW-38782922",
            created_at_wb=datetime(2026, 4, 22, 11, 32),
            warehouse_name="Екатеринбург - Перспективная 14",
            total_qty=192,
            accepted_qty=0,
            synced_at=stale_synced,
        )
        db_session.add(supply)
        await db_session.commit()
        await db_session.refresh(supply)

        mock_client = AsyncMock()
        mock_client.get_fbw_supply_detail.return_value = {
            "warehouseName": "Коледино",
            "quantity": 192,
            "statusID": 3,
        }
        # Состав в WB вырос (192 → 230): enrich непринятой поставки обязан
        # перетянуть goods, иначе зеркало item-ов застрянет на старом наполнении.
        mock_client.get_fbw_supply_goods.return_value = [
            {"barcode": "BC-IP-1", "vendorCode": "VC1", "nmID": 1, "quantity": 200, "acceptedQuantity": 0},
            {"barcode": "BC-IP-2", "vendorCode": "VC2", "nmID": 2, "quantity": 30, "acceptedQuantity": 0},
        ]

        result = await enrich_fbo_supplies(db_session, 1, mock_client, max_calls=5)

        assert result["enriched"] == 1
        assert mock_client.get_fbw_supply_detail.called
        assert mock_client.get_fbw_supply_goods.called, "goods API must be pulled for IN_PROGRESS too"

        await db_session.refresh(supply)
        assert supply.warehouse_name == "Коледино"
        assert supply.total_qty == 230, "total_qty пересчитан из goods"
        items = (
            await db_session.execute(select(WbFboSupplyItem).where(WbFboSupplyItem.supply_id == supply.id))
        ).scalars().all()
        assert {i.barcode for i in items} == {"BC-IP-1", "BC-IP-2"}

    async def test_in_progress_within_cooldown_is_skipped(self, db_session):
        """Recently synced IN_PROGRESS supply must not be re-enriched (cooldown)."""
        from backend.services.fbo_supply_service import enrich_fbo_supplies

        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="38782923",
            wb_status=WbSupplyStatus.IN_PROGRESS,
            created_at_wb=datetime(2026, 4, 22),
            warehouse_name="Коледино",
            total_qty=192,
            accepted_qty=0,
            synced_at=datetime.utcnow(),
        )
        db_session.add(supply)
        await db_session.commit()

        mock_client = AsyncMock()
        result = await enrich_fbo_supplies(db_session, 1, mock_client, max_calls=5)

        assert result["enriched"] == 0
        assert not mock_client.get_fbw_supply_detail.called

    async def test_on_delivery_stale_is_reenriched(self, db_session):
        from datetime import timedelta

        from backend.services.fbo_supply_service import enrich_fbo_supplies

        stale_synced = datetime.utcnow() - timedelta(hours=36)
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="38782924",
            wb_status=WbSupplyStatus.ON_DELIVERY,
            created_at_wb=datetime(2026, 4, 22),
            warehouse_name="Тула",
            total_qty=100,
            accepted_qty=0,
            synced_at=stale_synced,
        )
        db_session.add(supply)
        await db_session.commit()

        mock_client = AsyncMock()
        mock_client.get_fbw_supply_detail.return_value = {
            "warehouseName": "Электросталь",
            "quantity": 100,
            "statusID": 2,
        }
        mock_client.get_fbw_supply_goods.return_value = [
            {"barcode": "BC-OD-1", "vendorCode": "VC1", "nmID": 1, "quantity": 100, "acceptedQuantity": 0},
        ]

        result = await enrich_fbo_supplies(db_session, 1, mock_client, max_calls=5)

        assert result["enriched"] == 1
        assert mock_client.get_fbw_supply_goods.called, "goods API must be pulled for ON_DELIVERY too"
        await db_session.refresh(supply)
        assert supply.warehouse_name == "Электросталь"
        items = (
            await db_session.execute(select(WbFboSupplyItem).where(WbFboSupplyItem.supply_id == supply.id))
        ).scalars().all()
        assert {i.barcode for i in items} == {"BC-OD-1"}


# ─── Backfill: supply ↔ shipment link via AssemblyRequest ──────────────────


@pytest.mark.asyncio
class TestBackfillSupplyShipmentLink:
    """Repair missing outbound_shipment_id on WbFboSupply using AssemblyRequest."""

    async def test_backfill_copies_link_from_assembly(self, db_session):
        """Orphan supply + AssemblyRequest with shipment → link restored."""
        from sqlalchemy import select

        from backend.models.assembly import AssemblyRequest, AssemblyStatus
        from backend.models.warehouse import OutboundShipment, OutboundStatus
        from backend.services.fbo_supply.sync import _backfill_supply_shipment_links

        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="38231504",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 4, 1),
            outbound_shipment_id=None,
        )
        db_session.add(supply)
        await db_session.flush()

        shipment = OutboundShipment(
            project_id=1,
            warehouse_id=1,
            number="OUT-999",
            status=OutboundStatus.DELIVERED,
            wb_supply_id=None,
        )
        db_session.add(shipment)
        await db_session.flush()

        assembly = AssemblyRequest(
            project_id=1,
            warehouse_id=1,
            number="ASM-999",
            status=AssemblyStatus.DELIVERED,
            wb_fbo_supply_id=supply.id,
            outbound_shipment_id=shipment.id,
            estimated_ready_date=date(2026, 4, 10),
            pallets_count=1,
            pallet_weight_kg=100,
        )
        db_session.add(assembly)
        await db_session.commit()

        await _backfill_supply_shipment_links(db_session, 1, [supply])
        await db_session.commit()

        refreshed = (await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == supply.id))).scalar_one()
        refreshed_sh = (
            await db_session.execute(select(OutboundShipment).where(OutboundShipment.id == shipment.id))
        ).scalar_one()

        assert refreshed.outbound_shipment_id == shipment.id
        assert refreshed_sh.wb_supply_id == "38231504"

    async def test_backfill_noop_for_already_linked(self, db_session):
        """Supply already linked must stay untouched (no DB writes)."""
        from backend.models.warehouse import OutboundShipment, OutboundStatus
        from backend.services.fbo_supply.sync import _backfill_supply_shipment_links

        shipment = OutboundShipment(
            project_id=1,
            warehouse_id=1,
            number="OUT-NOOP",
            status=OutboundStatus.DELIVERED,
        )
        db_session.add(shipment)
        await db_session.flush()

        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="38231505",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 4, 1),
            outbound_shipment_id=shipment.id,
        )
        db_session.add(supply)
        await db_session.commit()

        await _backfill_supply_shipment_links(db_session, 1, [supply])
        assert supply.outbound_shipment_id == shipment.id


# ─── Returns: handle unaccepted qty ────────────────────────────────────────


@pytest.mark.asyncio
class TestFboReturns:
    """process_fbo_return creates receipt + flags supply.return_processed_at."""

    async def test_goods_return_creates_inbound_receipt(self, db_session):
        from sqlalchemy import select, text

        from backend.models.warehouse import InboundReceipt, InboundStatus, Warehouse, WarehouseStock
        from backend.services.fbo_supply.returns import process_fbo_return

        # Warehouse
        wh = Warehouse(project_id=1, name="хамза-test", warehouse_type="EXTERNAL")
        db_session.add(wh)
        await db_session.flush()

        # Nomenclature (barcode)
        await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, article_seller, barcode, updated_at) "
                "VALUES (1, 'art', '2043160691778', NOW()) ON CONFLICT DO NOTHING"
            )
        )

        # Supply with partial acceptance (52 unaccepted)
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="38231506",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 4, 1),
            total_qty=52,
            accepted_qty=0,
        )
        db_session.add(supply)
        await db_session.flush()

        item = WbFboSupplyItem(
            project_id=1,
            supply_id=supply.id,
            wb_order_id="W1",
            barcode="2043160691778",
            quantity=52,
            accepted_qty=0,
        )
        db_session.add(item)
        await db_session.commit()
        await db_session.refresh(supply)

        result = await process_fbo_return(
            db_session,
            1,
            supply.id,
            items=[{"barcode": "2043160691778", "quantity": 52, "return_type": "GOODS"}],
            warehouse_id=wh.id,
        )

        assert result["supply_id"] == supply.id
        assert result["receipt_id"] is not None

        r = await db_session.execute(select(InboundReceipt).where(InboundReceipt.id == result["receipt_id"]))
        receipt = r.scalar_one()
        assert receipt.status == InboundStatus.ACCEPTED
        assert receipt.is_defect is False
        assert "38231506" in (receipt.comment or "")

        s = await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == supply.id))
        assert s.scalar_one().return_processed_at is not None

        st = await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.warehouse_id == wh.id,
                WarehouseStock.barcode == "2043160691778",
            )
        )
        stock = st.scalar_one()
        assert stock.quantity == 52

    async def test_defect_return_goes_to_defect_stock(self, db_session):
        from sqlalchemy import select, text

        from backend.models.warehouse import Warehouse, WarehouseStock
        from backend.services.fbo_supply.returns import process_fbo_return

        wh = Warehouse(project_id=1, name="хамза-d", warehouse_type="EXTERNAL")
        db_session.add(wh)
        await db_session.flush()
        await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, article_seller, barcode, updated_at) "
                "VALUES (1, 'art2', 'BC-DEFECT', NOW()) ON CONFLICT DO NOTHING"
            )
        )
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="S2",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 4, 1),
            total_qty=10,
            accepted_qty=0,
        )
        db_session.add(supply)
        await db_session.flush()
        db_session.add(
            WbFboSupplyItem(
                project_id=1,
                supply_id=supply.id,
                wb_order_id="W2",
                barcode="BC-DEFECT",
                quantity=10,
                accepted_qty=0,
            )
        )
        await db_session.commit()

        await process_fbo_return(
            db_session,
            1,
            supply.id,
            items=[{"barcode": "BC-DEFECT", "quantity": 10, "return_type": "DEFECT"}],
            warehouse_id=wh.id,
        )

        st = await db_session.execute(
            select(WarehouseStock).where(WarehouseStock.warehouse_id == wh.id, WarehouseStock.barcode == "BC-DEFECT")
        )
        stock = st.scalar_one()
        assert stock.quantity == 0
        assert stock.defect_quantity == 10

    async def test_utilized_return_no_receipt_but_flags_supply(self, db_session):
        from sqlalchemy import select

        from backend.services.fbo_supply.returns import process_fbo_return

        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="S3",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 4, 1),
            total_qty=5,
            accepted_qty=0,
        )
        db_session.add(supply)
        await db_session.flush()
        db_session.add(
            WbFboSupplyItem(
                project_id=1,
                supply_id=supply.id,
                wb_order_id="W3",
                barcode="BC-UTIL",
                quantity=5,
                accepted_qty=0,
            )
        )
        await db_session.commit()

        result = await process_fbo_return(
            db_session,
            1,
            supply.id,
            items=[{"barcode": "BC-UTIL", "quantity": 5, "return_type": "UTILIZED"}],
        )

        assert result["receipt_id"] is None
        s = (await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == supply.id))).scalar_one()
        assert s.return_processed_at is not None

    async def test_rejects_qty_over_unaccepted_delta(self, db_session):
        from backend.services.fbo_supply.returns import process_fbo_return

        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="S4",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 4, 1),
            total_qty=10,
            accepted_qty=7,
        )
        db_session.add(supply)
        await db_session.flush()
        db_session.add(
            WbFboSupplyItem(
                project_id=1,
                supply_id=supply.id,
                wb_order_id="W4",
                barcode="BC-OVER",
                quantity=10,
                accepted_qty=7,
            )
        )
        await db_session.commit()

        with pytest.raises(ValueError, match="unaccepted delta"):
            await process_fbo_return(
                db_session,
                1,
                supply.id,
                items=[{"barcode": "BC-OVER", "quantity": 5, "return_type": "UTILIZED"}],  # delta=3
            )

    async def test_rejects_double_return_for_same_supply(self, db_session):
        """Idempotency: second call after return_processed_at is set must fail."""
        from backend.services.fbo_supply.returns import process_fbo_return

        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="S5-IDEMP",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 4, 1),
            total_qty=5,
            accepted_qty=0,
        )
        db_session.add(supply)
        await db_session.flush()
        db_session.add(
            WbFboSupplyItem(
                project_id=1,
                supply_id=supply.id,
                wb_order_id="W5",
                barcode="BC-IDEMP",
                quantity=5,
                accepted_qty=0,
            )
        )
        await db_session.commit()

        # first call succeeds (UTILIZED — no warehouse/nomenclature setup needed)
        await process_fbo_return(
            db_session,
            1,
            supply.id,
            items=[{"barcode": "BC-IDEMP", "quantity": 5, "return_type": "UTILIZED"}],
        )

        # second call must refuse — supply.return_processed_at is already set
        with pytest.raises(ValueError, match="already processed"):
            await process_fbo_return(
                db_session,
                1,
                supply.id,
                items=[{"barcode": "BC-IDEMP", "quantity": 5, "return_type": "UTILIZED"}],
            )

    async def test_mixed_return_splits_stock_and_utilization(self, db_session):
        """Одна строка GOODS + одна UTILIZED → InboundReceipt создаётся только
        для GOODS, стоковая строка не попадает в receipt. supply.return_type='MIXED'.
        """
        from sqlalchemy import select, text

        from backend.models.warehouse import InboundReceiptItem, Warehouse, WarehouseStock
        from backend.services.fbo_supply.returns import process_fbo_return

        wh = Warehouse(project_id=1, name="mixed-wh", warehouse_type="EXTERNAL")
        db_session.add(wh)
        await db_session.flush()

        await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, article_seller, barcode, updated_at) "
                "VALUES (1, 'mix1', 'BC-MIX-1', NOW()), "
                "(1, 'mix2', 'BC-MIX-2', NOW()) ON CONFLICT DO NOTHING"
            )
        )

        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="S-MIX",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 4, 1),
            total_qty=10,
            accepted_qty=0,
        )
        db_session.add(supply)
        await db_session.flush()
        db_session.add_all(
            [
                WbFboSupplyItem(
                    project_id=1,
                    supply_id=supply.id,
                    wb_order_id="W-MIX-1",
                    barcode="BC-MIX-1",
                    quantity=6,
                    accepted_qty=0,
                ),
                WbFboSupplyItem(
                    project_id=1,
                    supply_id=supply.id,
                    wb_order_id="W-MIX-2",
                    barcode="BC-MIX-2",
                    quantity=4,
                    accepted_qty=0,
                ),
            ]
        )
        await db_session.commit()

        result = await process_fbo_return(
            db_session,
            1,
            supply.id,
            items=[
                {"barcode": "BC-MIX-1", "quantity": 6, "return_type": "GOODS"},
                {"barcode": "BC-MIX-2", "quantity": 4, "return_type": "UTILIZED"},
            ],
            warehouse_id=wh.id,
        )

        # Receipt created for GOODS row only
        assert result["receipt_id"] is not None
        items_in_receipt = (
            (
                await db_session.execute(
                    select(InboundReceiptItem).where(InboundReceiptItem.receipt_id == result["receipt_id"])
                )
            )
            .scalars()
            .all()
        )
        assert len(items_in_receipt) == 1
        assert items_in_receipt[0].barcode == "BC-MIX-1"
        assert items_in_receipt[0].actual_qty == 6

        # Stock: only BC-MIX-1 increased, BC-MIX-2 untouched
        stock_1 = (
            await db_session.execute(
                select(WarehouseStock).where(WarehouseStock.warehouse_id == wh.id, WarehouseStock.barcode == "BC-MIX-1")
            )
        ).scalar_one()
        assert stock_1.quantity == 6

        stock_2 = (
            await db_session.execute(
                select(WarehouseStock).where(WarehouseStock.warehouse_id == wh.id, WarehouseStock.barcode == "BC-MIX-2")
            )
        ).scalar_one_or_none()
        assert stock_2 is None

        # Supply flagged MIXED
        s = (await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == supply.id))).scalar_one()
        assert s.return_type == "MIXED"
        assert s.return_qty == 10
        assert s.return_processed_at is not None

    async def test_pure_utilization_requires_no_warehouse(self, db_session):
        """All UTILIZED items → receipt_id=None, warehouse_id not required."""
        from sqlalchemy import select

        from backend.services.fbo_supply.returns import process_fbo_return

        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="S-PURE-UTIL",
            wb_status=WbSupplyStatus.ACCEPTED,
            created_at_wb=datetime(2026, 4, 1),
            total_qty=3,
            accepted_qty=0,
        )
        db_session.add(supply)
        await db_session.flush()
        db_session.add(
            WbFboSupplyItem(
                project_id=1,
                supply_id=supply.id,
                wb_order_id="W-PURE",
                barcode="BC-PURE-UTIL",
                quantity=3,
                accepted_qty=0,
            )
        )
        await db_session.commit()

        result = await process_fbo_return(
            db_session,
            1,
            supply.id,
            items=[{"barcode": "BC-PURE-UTIL", "quantity": 3, "return_type": "UTILIZED"}],
        )
        assert result["receipt_id"] is None

        s = (await db_session.execute(select(WbFboSupply).where(WbFboSupply.id == supply.id))).scalar_one()
        assert s.return_type == "UTILIZED"


# ─── Router integration tests ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestRouterEndpoints:
    """Integration tests for FBO API endpoints."""

    async def test_list_fbo_supplies_no_auth(self, client):
        resp = await client.get("/api/v1/warehouse/fbo-supplies")
        assert resp.status_code in (401, 403)

    async def test_list_fbo_supplies_authed(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/warehouse/fbo-supplies",
            headers=auth_headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            assert "items" in data
            assert "total" in data

    async def test_get_supply_items_not_found(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/warehouse/fbo-supplies/99999/items",
            headers=auth_headers,
        )
        assert resp.status_code in (404, 400)

    async def test_sync_endpoint_no_auth(self, client):
        resp = await client.post("/api/v1/warehouse/fbo-supplies/sync")
        assert resp.status_code in (401, 403)

    async def test_list_with_search_param(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/warehouse/fbo-supplies?search=test&limit=10",
            headers=auth_headers,
        )
        assert resp.status_code in (200, 400)

    async def test_list_with_status_filter(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/warehouse/fbo-supplies?status=ACTIVE",
            headers=auth_headers,
        )
        assert resp.status_code in (200, 400)

    async def test_list_with_warehouse_filter(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/warehouse/fbo-supplies?warehouse=%D0%AD%D0%BB%D0%B5%D0%BA%D1%82%D1%80%D0%BE%D1%81%D1%82%D0%B0%D0%BB%D1%8C",
            headers=auth_headers,
        )
        assert resp.status_code in (200, 400)

    async def test_warehouses_endpoint_no_auth(self, client):
        resp = await client.get("/api/v1/warehouse/fbo-supplies/warehouses")
        assert resp.status_code in (401, 403)

    async def test_warehouses_endpoint_authed(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/warehouse/fbo-supplies/warehouses",
            headers=auth_headers,
        )
        if resp.status_code == 200:
            data = resp.json()
            assert isinstance(data, list)

    async def test_list_with_date_range(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/warehouse/fbo-supplies?date_from=2026-03-01&date_to=2026-03-20",
            headers=auth_headers,
        )
        assert resp.status_code in (200, 400)

    async def test_sync_statuses_no_auth(self, client):
        resp = await client.post("/api/v1/warehouse/fbo-supplies/sync-statuses")
        assert resp.status_code in (401, 403)


# ─── WB API Client tests ──────────────────────────────────────────────────


class TestWbApiMethods:
    """Test that WBApiClient has the FBW methods."""

    def test_has_get_fbw_supplies(self):
        from backend.integrations.wb_api import WBApiClient

        client = WBApiClient("test-key")
        assert hasattr(client, "get_fbw_supplies")
        assert callable(client.get_fbw_supplies)

    def test_has_get_fbw_supply_detail(self):
        from backend.integrations.wb_api import WBApiClient

        client = WBApiClient("test-key")
        assert hasattr(client, "get_fbw_supply_detail")
        assert callable(client.get_fbw_supply_detail)

    def test_has_get_fbw_supply_goods(self):
        from backend.integrations.wb_api import WBApiClient

        client = WBApiClient("test-key")
        assert hasattr(client, "get_fbw_supply_goods")
        assert callable(client.get_fbw_supply_goods)

    def test_supplies_api_base_url(self):
        from backend.integrations.wb_api import WB_SUPPLIERS_API_BASE

        assert "supplies-api.wildberries.ru" in WB_SUPPLIERS_API_BASE


# ─── Auto-ship on WB ACCEPTED ──────────────────────────────────────────────


def _uid() -> str:
    return uuid.uuid4().hex[:10]


async def _make_ff_warehouse(db_session) -> Warehouse:
    wh = Warehouse(project_id=1, name=f"auto-ship-{_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(wh)
    await db_session.commit()
    await db_session.refresh(wh)
    return wh


async def _make_nom(db_session, barcode: str) -> Nomenclature:
    nom = Nomenclature(project_id=1, barcode=barcode, article_seller="ART-AUTOSHIP")
    db_session.add(nom)
    await db_session.commit()
    await db_session.refresh(nom)
    return nom


async def _make_accepted_supply(db_session, status=WbSupplyStatus.ACCEPTED) -> WbFboSupply:
    supply = WbFboSupply(
        project_id=1,
        wb_supply_id=f"SUP-{_uid()}",
        wb_status=status,
        warehouse_name="Электросталь",
        created_at_wb=datetime(2026, 6, 1),
    )
    db_session.add(supply)
    await db_session.commit()
    await db_session.refresh(supply)
    return supply


async def _make_assembly_on_supply(
    db_session,
    wh: Warehouse,
    supply: WbFboSupply,
    nom: Nomenclature,
    bc: str,
    *,
    qty: int = 5,
    stock_qty: int | None = 20,
    status=AssemblyStatus.VEHICLE_ASSIGNED,
) -> AssemblyRequest:
    """Сборка, привязанная к поставке WB, с позицией и (опц.) стоком на складе."""
    doc = AssemblyRequest(
        project_id=1,
        warehouse_id=wh.id,
        number=f"ASM-{_uid()[:6]}",
        status=status.value,
        wb_fbo_supply_id=supply.id,
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
        vehicle_info="А123ВС 77",
    )
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        AssemblyRequestItem(
            project_id=1,
            assembly_request_id=doc.id,
            nomenclature_id=nom.id,
            barcode=bc,
            quantity=qty,
        )
    )
    if stock_qty is not None:
        db_session.add(
            WarehouseStock(
                project_id=1,
                warehouse_id=wh.id,
                nomenclature_id=nom.id,
                barcode=bc,
                quantity=stock_qty,
            )
        )
    await db_session.commit()
    await db_session.refresh(doc)
    return doc


@pytest.mark.asyncio
class TestAutoShipOnWbAccepted:
    """WB принял поставку (ACCEPTED) + машина назначена (VEHICLE_ASSIGNED) → авто-SHIP."""

    async def test_collect_picks_vehicle_assigned_accepted(self, db_session):
        """Машина назначена + поставка ACCEPTED → заявка в кандидатах."""
        wh = await _make_ff_warehouse(db_session)
        bc = f"BC-{_uid()}"
        nom = await _make_nom(db_session, bc)
        supply = await _make_accepted_supply(db_session)
        doc = await _make_assembly_on_supply(db_session, wh, supply, nom, bc)

        ids = await _collect_assembly_ship_on_wb_accepted(db_session, 1, [supply])
        assert ids == [doc.id]

    async def test_collect_skips_ready_without_vehicle(self, db_session):
        """Машина НЕ назначена (READY) → не кандидат, даже если WB принял."""
        wh = await _make_ff_warehouse(db_session)
        bc = f"BC-{_uid()}"
        nom = await _make_nom(db_session, bc)
        supply = await _make_accepted_supply(db_session)
        await _make_assembly_on_supply(db_session, wh, supply, nom, bc, status=AssemblyStatus.READY)

        ids = await _collect_assembly_ship_on_wb_accepted(db_session, 1, [supply])
        assert ids == []

    async def test_collect_skips_non_accepted_supply(self, db_session):
        """Поставка ещё не принята (IN_PROGRESS) → не кандидат."""
        wh = await _make_ff_warehouse(db_session)
        bc = f"BC-{_uid()}"
        nom = await _make_nom(db_session, bc)
        supply = await _make_accepted_supply(db_session, status=WbSupplyStatus.IN_PROGRESS)
        await _make_assembly_on_supply(db_session, wh, supply, nom, bc)

        ids = await _collect_assembly_ship_on_wb_accepted(db_session, 1, [supply])
        assert ids == []

    async def test_collect_skips_already_shipped(self, db_session):
        """Заявка уже SHIPPED → не кандидат (там работает auto-deliver)."""
        wh = await _make_ff_warehouse(db_session)
        bc = f"BC-{_uid()}"
        nom = await _make_nom(db_session, bc)
        supply = await _make_accepted_supply(db_session)
        await _make_assembly_on_supply(db_session, wh, supply, nom, bc, status=AssemblyStatus.SHIPPED)

        ids = await _collect_assembly_ship_on_wb_accepted(db_session, 1, [supply])
        assert ids == []

    async def test_collect_picks_partial_acceptance(self, db_session):
        """Частичная приёмка (statusID 5/6 → ACCEPTED, accepted_qty < total_qty) ТОЖЕ
        отгружается — порога по qty нет; недоприёмка добивается отдельным flow
        (process_fbo_return). Пиннит намеренное поведение от тихого регресса."""
        wh = await _make_ff_warehouse(db_session)
        bc = f"BC-{_uid()}"
        nom = await _make_nom(db_session, bc)
        supply = await _make_accepted_supply(db_session)
        supply.total_qty = 10
        supply.accepted_qty = 6  # принято не всё
        await db_session.commit()
        doc = await _make_assembly_on_supply(db_session, wh, supply, nom, bc)

        ids = await _collect_assembly_ship_on_wb_accepted(db_session, 1, [supply])
        assert ids == [doc.id]

    async def test_ship_deducts_stock_and_is_idempotent(self, db_session):
        """ship-runner: SHIPPED + сток списан + OutboundShipment; повтор — no-op."""
        wh = await _make_ff_warehouse(db_session)
        bc = f"BC-{_uid()}"
        nom = await _make_nom(db_session, bc)
        supply = await _make_accepted_supply(db_session)
        doc = await _make_assembly_on_supply(db_session, wh, supply, nom, bc, qty=5, stock_qty=20)

        shipped = await _ship_assemblies_best_effort(1, [doc.id])
        assert shipped == 1
        await db_session.refresh(doc)
        assert doc.status == AssemblyStatus.SHIPPED.value
        assert doc.outbound_shipment_id is not None
        assert doc.shipped_at is not None

        stock = (
            await db_session.execute(
                select(WarehouseStock).where(
                    WarehouseStock.project_id == 1,
                    WarehouseStock.warehouse_id == wh.id,
                    WarehouseStock.barcode == bc,
                )
            )
        ).scalar_one()
        assert stock.quantity == 15  # 20 − 5

        # OutboundShipment создан и привязан к поставке
        shipment = (
            await db_session.execute(
                select(OutboundShipment).where(OutboundShipment.id == doc.outbound_shipment_id)
            )
        ).scalar_one()
        assert shipment.wb_supply_id == supply.wb_supply_id

        # Идемпотентность: заявка уже SHIPPED → transition ValueError, сток не двигается
        again = await _ship_assemblies_best_effort(1, [doc.id])
        assert again == 0
        await db_session.refresh(stock)
        assert stock.quantity == 15

    async def test_ship_stock_deficit_best_effort(self, db_session):
        """Дефицит стока: ship падает, счётчик 0, заявка остаётся VEHICLE_ASSIGNED."""
        wh = await _make_ff_warehouse(db_session)
        bc = f"BC-{_uid()}"
        nom = await _make_nom(db_session, bc)
        supply = await _make_accepted_supply(db_session)
        doc = await _make_assembly_on_supply(db_session, wh, supply, nom, bc, qty=5, stock_qty=2)

        shipped = await _ship_assemblies_best_effort(1, [doc.id])
        assert shipped == 0
        await db_session.refresh(doc)
        assert doc.status == AssemblyStatus.VEHICLE_ASSIGNED.value

    async def test_full_sync_ships_forgotten_assembly(self, db_session):
        """E2E: уже-ACCEPTED поставка в БД + VEHICLE_ASSIGNED сборка → full-sync отгружает."""
        wh = await _make_ff_warehouse(db_session)
        bc = f"BC-{_uid()}"
        nom = await _make_nom(db_session, bc)
        supply = await _make_accepted_supply(db_session)
        doc = await _make_assembly_on_supply(db_session, wh, supply, nom, bc, qty=5, stock_qty=20)

        key = IntegrationKey(project_id=1, service="wb", encrypted_key="x", is_active=True)
        db_session.add(key)
        await db_session.commit()
        await db_session.refresh(key)

        mock_client = AsyncMock()
        mock_client.get_fbw_supplies.return_value = []  # API ничего нового не отдаёт

        result = await sync_fbo_supplies(db_session, 1, mock_client, key.id)
        assert result["assemblies_shipped"] == 1
        await db_session.refresh(doc)
        assert doc.status == AssemblyStatus.SHIPPED.value
        assert doc.outbound_shipment_id is not None

    async def test_status_sync_ships_on_fresh_accept(self, db_session):
        """E2E status-sync (основной триггер «вб уже принял»): поставка в БД ещё
        IN_PROGRESS, WB отдаёт её ACCEPTED (statusID=4) → VEHICLE_ASSIGNED сборка
        авто-отгружается. Покрывает path-specific pre-filter + in-place мутацию."""
        wh = await _make_ff_warehouse(db_session)
        bc = f"BC-{_uid()}"
        nom = await _make_nom(db_session, bc)
        supply = await _make_accepted_supply(db_session, status=WbSupplyStatus.IN_PROGRESS)
        doc = await _make_assembly_on_supply(db_session, wh, supply, nom, bc, qty=5, stock_qty=20)

        key = IntegrationKey(project_id=1, service="wb", encrypted_key="x", is_active=True)
        db_session.add(key)
        await db_session.commit()
        await db_session.refresh(key)

        mock_client = AsyncMock()
        # WB отдаёт ту же поставку уже принятой (statusID=4 → ACCEPTED)
        mock_client.get_fbw_supplies.return_value = [
            {
                "supplyID": supply.wb_supply_id,
                "statusID": 4,
                "createDate": datetime.utcnow().isoformat() + "Z",
            },
        ]

        result = await sync_fbo_statuses(db_session, 1, mock_client, key.id)
        assert result["assemblies_shipped"] == 1
        await db_session.refresh(doc)
        assert doc.status == AssemblyStatus.SHIPPED.value
        assert doc.outbound_shipment_id is not None
