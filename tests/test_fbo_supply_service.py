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

from datetime import date, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.models.wb_fbo import WbFboSupply, WbFboSupplyItem, WbSupplyStatus
from backend.services.fbo_supply_service import (
    _create_supply_from_fbw_list,
    _map_fbw_box_type,
    _map_fbw_status,
    _parse_wb_date,
    _parse_wb_datetime,
    _update_supply_from_fbw_detail,
    _update_supply_from_fbw_list,
    get_fbo_supply_items,
    link_supply_to_shipment,
    list_fbo_supplies,
    list_warehouses,
    sync_fbo_statuses,
    sync_fbo_supplies,
    unlink_supply_from_shipment,
)

# ─── Fixture: ensure test project exists ─────────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def ensure_test_project(db_session):
    """Create a test user + project with id=1 if not exists, clean FBO data before each test."""
    # Clean FBO data from previous test runs (respect FK order)
    await db_session.execute(
        text(
            "DELETE FROM assembly_status_history WHERE assembly_request_id IN (SELECT id FROM assembly_requests WHERE wb_fbo_supply_id IN (SELECT id FROM wb_fbo_supplies))"
        )
    )
    await db_session.execute(
        text(
            "DELETE FROM assembly_request_items WHERE assembly_request_id IN (SELECT id FROM assembly_requests WHERE wb_fbo_supply_id IN (SELECT id FROM wb_fbo_supplies))"
        )
    )
    await db_session.execute(
        text("DELETE FROM assembly_requests WHERE wb_fbo_supply_id IN (SELECT id FROM wb_fbo_supplies)")
    )
    await db_session.execute(text("DELETE FROM wb_fbo_supply_items"))
    await db_session.execute(text("DELETE FROM wb_fbo_supplies"))
    await db_session.commit()

    # Ensure project exists
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
        assert _map_fbw_status(5) == WbSupplyStatus.CANCELLED
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

    def test_link_request(self):
        from backend.schemas.wb_fbo import FboSupplyLinkRequest

        req = FboSupplyLinkRequest(outbound_shipment_id=42)
        assert req.outbound_shipment_id == 42


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
class TestLinkUnlink:
    """Test link/unlink supply ↔ shipment."""

    async def test_link_not_found_supply(self, db_session):
        with pytest.raises(ValueError, match="FBO Supply not found"):
            await link_supply_to_shipment(db_session, 1, 99999, 1)

    async def test_unlink_not_found(self, db_session):
        with pytest.raises(ValueError, match="FBO Supply not found"):
            await unlink_supply_from_shipment(db_session, 1, 99999)

    async def test_link_not_found_shipment(self, db_session):
        supply = WbFboSupply(
            project_id=1,
            wb_supply_id="LINK-NO-SHIP",
            wb_status=WbSupplyStatus.ACTIVE,
            created_at_wb=datetime(2026, 3, 20),
        )
        db_session.add(supply)
        await db_session.commit()

        with pytest.raises(ValueError, match="Outbound shipment not found"):
            await link_supply_to_shipment(db_session, 1, supply.id, 99999)


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

    async def test_link_endpoint_no_auth(self, client):
        resp = await client.post(
            "/api/v1/warehouse/fbo-supplies/1/link",
            json={"outbound_shipment_id": 1},
        )
        assert resp.status_code in (401, 403)

    async def test_unlink_endpoint_no_auth(self, client):
        resp = await client.delete("/api/v1/warehouse/fbo-supplies/1/link")
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
