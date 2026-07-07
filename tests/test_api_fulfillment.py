"""
API tests — /warehouse/{warehouse_id}/fulfillment endpoints.

Внешний API skladbot.ru мокается через monkeypatch SkladbotClient — никаких HTTP.
"""

import pytest

from backend.integrations.skladbot_client import SkladbotClient

FAKE_TOKEN = "fake_skladbot_token_1234567890_abcdef"  # noqa: S105 — фейковый токен для тестов


async def _fake_count_one(self):
    """connect проверяет число кабинетов токена — для тестов это всегда 1 (селлер)."""
    return 1


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _project_headers(client, auth_headers) -> dict:
    """Create a test project and return headers with X-Project-Id."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Fulfillment Test"},
        headers=auth_headers,
    )
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}


async def _create_warehouse(client, headers) -> int:
    resp = await client.post(
        "/api/v1/warehouse",
        json={"name": "FF Test", "warehouse_type": "FULFILLMENT"},
        headers=headers,
    )
    assert resp.status_code == 200, f"Warehouse create failed: {resp.text}"
    return resp.json()["id"]


# ─── Status ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_disconnected(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)

    resp = await client.get(f"/api/v1/warehouse/{wh_id}/fulfillment/status", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert data["provider"] is None


# ─── Auth ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_expected_vehicles_excludes_soft_deleted_items(client, auth_headers, db_session):
    """«Ожидаемые поставки» (карточка N поз / M шт) не должна включать soft-deleted
    позиции машины. Перезаливка Excel мягко удаляет старые строки — карточка
    задваивала (V-0027: 118 актив + 153 мёртвых → 10 442 вместо 5 221)."""
    import uuid
    from decimal import Decimal

    from backend.models.cost import CostOrder, CostOrderItem
    from backend.models.enums import VehicleStatus

    headers = await _project_headers(client, auth_headers)
    pid = int(headers["X-Project-Id"])
    wh_id = await _create_warehouse(client, headers)

    order_no = f"V-EXP-SD-{uuid.uuid4().hex[:8]}"
    vehicle = CostOrder(
        project_id=pid,
        order_no=order_no,
        country="CHINA",
        status=VehicleStatus.DISPATCHED,
        target_warehouse_id=wh_id,
        rate_cny=Decimal("1"),
        rate_usd=Decimal("1"),
        rate_eur=Decimal("1"),
        delivery_cost_cny=Decimal("0"),
        delivery_cost_usd=Decimal("0"),
    )
    db_session.add(vehicle)
    await db_session.flush()

    def _item(qty, *, deleted=False):
        it = CostOrderItem(
            project_id=pid,
            order_no=order_no,
            barcode="BC-EXP",
            qty=qty,
            price_cny=Decimal("1"),
            cost_rub=Decimal("0"),
            delivery_rub=Decimal("0"),
            duty_rub=Decimal("0"),
            vat_rub=Decimal("0"),
            util_rub=Decimal("0"),
            total_rub=Decimal("0"),
        )
        if deleted:
            it.soft_delete()
        return it

    db_session.add_all([_item(100), _item(100, deleted=True)])
    await db_session.commit()

    resp = await client.get(f"/api/v1/warehouse/{wh_id}/expected-vehicles", headers=headers)
    assert resp.status_code == 200, resp.text
    row = next(v for v in resp.json() if v["order_no"] == order_no)
    assert row["items_count"] == 1  # dead row excluded
    assert row["total_qty"] == 100  # not 200


@pytest.mark.asyncio
async def test_endpoints_require_auth(client):
    resp = await client.get("/api/v1/warehouse/1/fulfillment/status")
    assert resp.status_code in (401, 403, 422)

    resp = await client.post(
        "/api/v1/warehouse/1/fulfillment/connect",
        json={"provider": "skladbot", "token": FAKE_TOKEN},
    )
    assert resp.status_code in (401, 403, 422)

    resp = await client.post("/api/v1/warehouse/1/fulfillment/sync")
    assert resp.status_code in (401, 403, 422)

    resp = await client.get("/api/v1/warehouse/1/fulfillment/stocks")
    assert resp.status_code in (401, 403, 422)

    resp = await client.get("/api/v1/warehouse/1/fulfillment/requests")
    assert resp.status_code in (401, 403, 422)

    resp = await client.get("/api/v1/warehouse/1/fulfillment/unlinked-assemblies")
    assert resp.status_code in (401, 403, 422)


# ─── Connect ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_invalid_token_400(client, auth_headers, monkeypatch):
    """Невалидный токен (test_connection → None) — 400 с понятной ошибкой."""

    async def fake_test_connection(self):
        return None

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_test_connection)

    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)

    resp = await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/connect",
        json={"provider": "skladbot", "token": FAKE_TOKEN},
        headers=headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_connect_short_token_422(client, auth_headers):
    """Токен короче 20 символов режется Pydantic-валидацией."""
    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)

    resp = await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/connect",
        json={"provider": "skladbot", "token": "short"},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_connect_and_status_roundtrip(client, auth_headers, monkeypatch):
    """connect с валидным (замоканным) токеном → status connected."""

    async def fake_test_connection(self):
        return {"id": 6282, "name": "ООО ТЕСТ ФФ"}

    async def fake_count_customers(self):
        return 1

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_test_connection)
    monkeypatch.setattr(SkladbotClient, "count_customers", fake_count_customers)

    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)

    resp = await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/connect",
        json={"provider": "skladbot", "token": FAKE_TOKEN},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["connected"] is True
    assert data["customer_id"] == 6282
    assert data["key_preview"] == "***" + FAKE_TOKEN[-4:]
    assert FAKE_TOKEN not in resp.text  # сам токен назад не отдаём

    resp = await client.get(f"/api/v1/warehouse/{wh_id}/fulfillment/status", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["connected"] is True

    # disconnect
    resp = await client.delete(f"/api/v1/warehouse/{wh_id}/fulfillment/connect", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    resp = await client.get(f"/api/v1/warehouse/{wh_id}/fulfillment/status", headers=headers)
    assert resp.json()["connected"] is False


@pytest.mark.asyncio
async def test_disconnect_not_connected_404(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)

    resp = await client.delete(f"/api/v1/warehouse/{wh_id}/fulfillment/connect", headers=headers)
    assert resp.status_code == 404


# ─── Sync ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_without_connection_400(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)

    resp = await client.post(f"/api/v1/warehouse/{wh_id}/fulfillment/sync", headers=headers)
    assert resp.status_code == 400


# ─── Stocks / requests (пустые) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stocks_and_requests_empty(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)

    resp = await client.get(f"/api/v1/warehouse/{wh_id}/fulfillment/stocks", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["rows"] == []
    assert data["totals"]["ff_good"] == 0

    resp = await client.get(f"/api/v1/warehouse/{wh_id}/fulfillment/requests", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []

    resp = await client.get(
        f"/api/v1/warehouse/{wh_id}/fulfillment/requests?kind=assembly",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json() == []


# ─── Link 404 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_link_nonexistent_ff_request_404(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)

    resp = await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/requests/99999999/link",
        json={"assembly_request_id": 1},
        headers=headers,
    )
    assert resp.status_code == 404

    resp = await client.delete(
        f"/api/v1/warehouse/{wh_id}/fulfillment/requests/99999999/link",
        headers=headers,
    )
    assert resp.status_code == 404


# ─── Request detail ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_detail_unknown_404(client, auth_headers):
    """Несуществующая зеркальная заявка → 404 (существование проверяется до подключения)."""
    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)

    resp = await client.get(
        f"/api/v1/warehouse/{wh_id}/fulfillment/requests/99999999/detail",
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_request_detail_not_connected_400(client, auth_headers, monkeypatch):
    """Зеркальная заявка есть, но фулфилмент отключён → 400 с понятной ошибкой."""

    async def fake_test_connection(self):
        return {"id": 6282, "name": "ООО ТЕСТ ФФ"}

    async def fake_fetch_all_products(self, customer_id):
        return []

    async def fake_fetch_requests(self, type_id):
        if type_id == 851:
            return [
                {
                    "id": 777001,
                    "delivery_number": "WH-R-777001",
                    "created_at": "2026-06-10",
                    "status": "new",
                    "archived": 0,
                    "type": "3. Доставка на склад МП",
                    "stage_title": "Забор груза",
                    "stage_code": "cargo_pickup",
                    "expired": False,
                    "is_completed": 0,
                }
            ]
        return []

    async def fake_fetch_request_detail(self, external_id):
        # Синк обогащает активные assembly-строки живой деталкой — мок против сети
        return {}

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_test_connection)
    monkeypatch.setattr(SkladbotClient, "count_customers", _fake_count_one)
    monkeypatch.setattr(SkladbotClient, "fetch_all_products", fake_fetch_all_products)
    monkeypatch.setattr(SkladbotClient, "fetch_requests", fake_fetch_requests)
    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fake_fetch_request_detail)

    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)

    resp = await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/connect",
        json={"provider": "skladbot", "token": FAKE_TOKEN},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/v1/warehouse/{wh_id}/fulfillment/sync", headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.get(f"/api/v1/warehouse/{wh_id}/fulfillment/requests", headers=headers)
    ff_id = resp.json()[0]["id"]

    resp = await client.delete(f"/api/v1/warehouse/{wh_id}/fulfillment/connect", headers=headers)
    assert resp.status_code == 200

    resp = await client.get(
        f"/api/v1/warehouse/{wh_id}/fulfillment/requests/{ff_id}/detail",
        headers=headers,
    )
    assert resp.status_code == 400
    assert "не подключён" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_request_detail_requires_auth(client):
    resp = await client.get("/api/v1/warehouse/1/fulfillment/requests/1/detail")
    assert resp.status_code in (401, 403, 422)


@pytest.mark.asyncio
async def test_connect_migfull_requires_tenant_guid_400(client, auth_headers):
    """migfull без GUID кабинета (или с мусорным) — 400 до любого HTTP к провайдеру."""
    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)

    resp = await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/connect",
        json={"provider": "migfull", "token": FAKE_TOKEN},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "GUID" in resp.text

    resp = await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/connect",
        json={"provider": "migfull", "token": FAKE_TOKEN, "tenant_guid": "../../admin"},
        headers=headers,
    )
    assert resp.status_code == 400


# ─── Status history (журнал смены статусов) ──────────────────────────────────


@pytest.mark.asyncio
async def test_status_history_requires_auth(client):
    resp = await client.get("/api/v1/warehouse/1/fulfillment/status-history")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_status_history_empty(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)
    resp = await client.get(f"/api/v1/warehouse/{wh_id}/fulfillment/status-history", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_status_history_after_sync(client, auth_headers, monkeypatch):
    """Синк → endpoint отдаёт событие `created`; фильтр kind работает."""

    async def fake_test_connection(self):
        return {"id": 6282, "name": "ООО ТЕСТ ФФ"}

    async def fake_fetch_all_products(self, customer_id):
        return []

    async def fake_fetch_requests(self, type_id):
        if type_id == 851:
            return [
                {
                    "id": 778001,
                    "delivery_number": "WH-R-778001",
                    "created_at": "2026-06-10",
                    "status": "new",
                    "archived": 0,
                    "type": "3. Доставка на склад МП",
                    "stage_title": "Забор груза",
                    "stage_code": "cargo_pickup",
                    "expired": False,
                    "is_completed": 0,
                }
            ]
        return []

    async def fake_fetch_request_detail(self, external_id):
        return {}

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_test_connection)
    monkeypatch.setattr(SkladbotClient, "count_customers", _fake_count_one)
    monkeypatch.setattr(SkladbotClient, "fetch_all_products", fake_fetch_all_products)
    monkeypatch.setattr(SkladbotClient, "fetch_requests", fake_fetch_requests)
    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fake_fetch_request_detail)

    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)
    await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/connect",
        json={"provider": "skladbot", "token": FAKE_TOKEN},
        headers=headers,
    )
    await client.post(f"/api/v1/warehouse/{wh_id}/fulfillment/sync", headers=headers)

    resp = await client.get(f"/api/v1/warehouse/{wh_id}/fulfillment/status-history", headers=headers)
    assert resp.status_code == 200, resp.text
    events = resp.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "created"
    assert events[0]["kind"] == "assembly"
    assert events[0]["new_stage_title"] == "Забор груза"
    assert events[0]["external_id"] == "778001"

    # фильтр по чужому kind → пусто
    resp = await client.get(f"/api/v1/warehouse/{wh_id}/fulfillment/status-history?kind=inbound", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


# ─── Sync runs (журнал прогонов синхронизации) ───────────────────────────────


@pytest.mark.asyncio
async def test_sync_runs_requires_auth(client):
    resp = await client.get("/api/v1/warehouse/1/fulfillment/sync-runs")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_sync_runs_after_sync(client, auth_headers, monkeypatch):
    """connect + ручной sync → endpoint sync-runs отдаёт прогон со статусом OK."""

    async def fake_test_connection(self):
        return {"id": 6282, "name": "ООО ТЕСТ ФФ"}

    async def fake_fetch_all_products(self, customer_id):
        return []

    async def fake_fetch_requests(self, type_id):
        return []

    async def fake_fetch_request_detail(self, external_id):
        return {}

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_test_connection)
    monkeypatch.setattr(SkladbotClient, "count_customers", _fake_count_one)
    monkeypatch.setattr(SkladbotClient, "fetch_all_products", fake_fetch_all_products)
    monkeypatch.setattr(SkladbotClient, "fetch_requests", fake_fetch_requests)
    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fake_fetch_request_detail)

    headers = await _project_headers(client, auth_headers)
    wh_id = await _create_warehouse(client, headers)

    resp = await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/connect",
        json={"provider": "skladbot", "token": FAKE_TOKEN},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.post(f"/api/v1/warehouse/{wh_id}/fulfillment/sync", headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.get(f"/api/v1/warehouse/{wh_id}/fulfillment/sync-runs", headers=headers)
    assert resp.status_code == 200, resp.text
    runs = resp.json()
    assert isinstance(runs, list)
    assert len(runs) >= 1
    assert any(run["status"] == "OK" for run in runs)
