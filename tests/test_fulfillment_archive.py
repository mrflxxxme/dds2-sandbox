"""
Service + API tests — локальный архив ФФ-заявок (local_archived).

local_archived — пометка пользователя в DDS: синк её НЕ трогает (в отличие
от archived — зеркала провайдера, затираемого каждым синком).
SkladbotClient мокается через monkeypatch — никаких HTTP-вызовов.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.integrations.skladbot_client import SkladbotClient
from backend.models import FulfillmentRequest, Warehouse
from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.services import fulfillment_service

FAKE_TOKEN = "fake_skladbot_token_1234567890_abcdef"  # noqa: S105 — фейковый токен для тестов


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _mirror(project_id, warehouse_id, *, kind="assembly", created=None, number=None):
    """Зеркальная ФФ-заявка для прямой вставки в БД."""
    return FulfillmentRequest(
        project_id=project_id,
        warehouse_id=warehouse_id,
        provider="skladbot",
        external_id=_uid(),
        number=number,
        kind=kind,
        status="Новая",
        external_created_at=created,
    )


def _req_row(req_id, stage_title="Новая", stage_code=None, completed=0):
    """skladbot /v1/requests row для моков синка."""
    return {
        "id": req_id,
        "delivery_number": f"WH-R-{req_id}",
        "created_at": "2026-06-10",
        "status": "new",
        "archived": 0,
        "type": "3. Доставка на склад МП",
        "stage_title": stage_title,
        "stage_code": stage_code,
        "expired": False,
        "is_completed": completed,
    }


async def _connect_skladbot(db_session, project_id, warehouse_id, monkeypatch):
    async def fake_test_connection(self):
        return {"id": 6282, "name": "ООО ТЕСТ ФФ"}

    async def fake_count_customers(self):
        return 1

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_test_connection)
    monkeypatch.setattr(SkladbotClient, "count_customers", fake_count_customers)
    await fulfillment_service.connect(db_session, project_id, warehouse_id, "skladbot", FAKE_TOKEN)


def _mock_sync(monkeypatch, requests_by_type):
    async def fake_products(self, customer_id):
        return []

    async def fake_requests(self, type_id):
        return requests_by_type.get(type_id, [])

    async def fake_detail(self, external_id):
        return {}

    monkeypatch.setattr(SkladbotClient, "fetch_all_products", fake_products)
    monkeypatch.setattr(SkladbotClient, "fetch_requests", fake_requests)
    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fake_detail)


async def _mirror_row(db_session, project_id, external_id) -> FulfillmentRequest:
    result = await db_session.execute(
        select(FulfillmentRequest).where(
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.external_id == str(external_id),
        )
    )
    return result.scalars().one()


@pytest_asyncio.fixture
async def warehouse(db_session, project):
    wh = Warehouse(project_id=project.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(wh)
    await db_session.commit()
    await db_session.refresh(wh)
    return wh


# ─── archive / unarchive (service) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_unarchive_happy(db_session, project, warehouse):
    req = _mirror(project.id, warehouse.id)
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    row = await fulfillment_service.archive_request(db_session, project.id, req.id, warehouse.id)
    assert row is not None
    assert row["local_archived"] is True
    assert row["local_archived_at"] is not None
    assert row["archived"] is False  # зеркальный archived провайдера не трогаем

    # Дефолтный список — без архивных; вид «Архив» — только архивные
    assert await fulfillment_service.list_requests(db_session, project.id, warehouse.id) == []
    archived = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, show_archived=True)
    assert [r["id"] for r in archived] == [req.id]
    # Фильтр kind работает и в архиве
    archived_kind = await fulfillment_service.list_requests(
        db_session, project.id, warehouse.id, "assembly", show_archived=True
    )
    assert [r["id"] for r in archived_kind] == [req.id]

    row = await fulfillment_service.unarchive_request(db_session, project.id, req.id, warehouse.id)
    assert row["local_archived"] is False
    assert row["local_archived_at"] is None
    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id)
    assert [r["id"] for r in rows] == [req.id]
    assert await fulfillment_service.list_requests(db_session, project.id, warehouse.id, show_archived=True) == []


@pytest.mark.asyncio
async def test_archive_idempotent_keeps_timestamp(db_session, project, warehouse):
    req = _mirror(project.id, warehouse.id)
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    first = await fulfillment_service.archive_request(db_session, project.id, req.id, warehouse.id)
    second = await fulfillment_service.archive_request(db_session, project.id, req.id, warehouse.id)
    assert second["local_archived_at"] == first["local_archived_at"]


@pytest.mark.asyncio
async def test_archive_not_found_returns_none(db_session, project, warehouse):
    assert await fulfillment_service.archive_request(db_session, project.id, 99999999, warehouse.id) is None
    assert await fulfillment_service.unarchive_request(db_session, project.id, 99999999, warehouse.id) is None


@pytest.mark.asyncio
async def test_archive_project_and_warehouse_isolation(db_session, project, other_project, warehouse):
    req = _mirror(project.id, warehouse.id)
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    # Чужой проект не архивирует заявку даже по верному id
    assert await fulfillment_service.archive_request(db_session, other_project.id, req.id, warehouse.id) is None
    # Чужой warehouse_id в path — мимо
    assert await fulfillment_service.archive_request(db_session, project.id, req.id, warehouse.id + 1) is None
    await db_session.refresh(req)
    assert req.local_archived is False


# ─── get_overview исключает локальный архив ──────────────────────────────────


@pytest.mark.asyncio
async def test_overview_excludes_local_archived(db_session, project, warehouse, monkeypatch):
    await _connect_skladbot(db_session, project.id, warehouse.id, monkeypatch)
    active = _mirror(project.id, warehouse.id, created=date(2026, 6, 10))
    to_archive = _mirror(project.id, warehouse.id, created=date(2026, 6, 9))
    db_session.add_all([active, to_archive])
    await db_session.commit()
    await db_session.refresh(active)
    await db_session.refresh(to_archive)
    await fulfillment_service.archive_request(db_session, project.id, to_archive.id, warehouse.id)

    data = await fulfillment_service.get_overview(db_session, project.id)
    wh_row = next(w for w in data["warehouses"] if w["warehouse_id"] == warehouse.id)
    assert wh_row["requests_total"] == 1  # архивная не инфлирует каунты
    assert wh_row["requests_unlinked"] == 1
    assert [r["id"] for r in data["requests"]] == [active.id]


# ─── Синк не трогает local_archived ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_preserves_local_archived(db_session, project, warehouse, monkeypatch):
    await _connect_skladbot(db_session, project.id, warehouse.id, monkeypatch)
    _mock_sync(monkeypatch, {851: [_req_row(7501)]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    req = await _mirror_row(db_session, project.id, "7501")
    row = await fulfillment_service.archive_request(db_session, project.id, req.id, warehouse.id)
    archived_at = row["local_archived_at"]

    # Ресинк с новым статусом: зеркальные поля обновились, локальный архив — нет
    _mock_sync(monkeypatch, {851: [_req_row(7501, stage_title="Погрузка", completed=1)]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    await db_session.refresh(req)
    assert req.local_archived is True
    assert req.local_archived_at == archived_at
    assert req.is_completed is True  # сам синк прошёл и обновил зеркало


@pytest.mark.asyncio
async def test_sync_auto_ready_skips_local_archived(db_session, project, warehouse, monkeypatch):
    """Локально архивная связанная ФФ-заявка не триггерит авто-READY сборки."""
    await _connect_skladbot(db_session, project.id, warehouse.id, monkeypatch)
    _mock_sync(monkeypatch, {851: [_req_row(7601, stage_title="Забор груза", stage_code="cargo_pickup")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = AssemblyRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"A-{_uid()[:6]}",
        status=AssemblyStatus.IN_PROGRESS.value,
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    req = await _mirror_row(db_session, project.id, "7601")
    await fulfillment_service.link_request(db_session, project.id, req.id, assembly_request_id=doc.id)
    await fulfillment_service.archive_request(db_session, project.id, req.id, warehouse.id)

    # Стадия 3+ («Назначение водителя») при НЕархивной заявке дала бы READY
    _mock_sync(monkeypatch, {851: [_req_row(7601, stage_title="Назначение водителя")]})
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_marked_ready"] == 0
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.IN_PROGRESS.value


# ─── API endpoints ───────────────────────────────────────────────────────────


async def _api_project_headers(client, auth_headers) -> dict:
    resp = await client.post("/api/v1/projects", json={"name": "FF Archive API"}, headers=auth_headers)
    return {**auth_headers, "X-Project-Id": str(resp.json()["id"])}


@pytest.mark.asyncio
async def test_api_archive_roundtrip(client, auth_headers, monkeypatch):
    async def fake_test_connection(self):
        return {"id": 6282, "name": "ООО ТЕСТ ФФ"}

    async def fake_count_customers(self):
        return 1

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_test_connection)
    monkeypatch.setattr(SkladbotClient, "count_customers", fake_count_customers)
    _mock_sync(monkeypatch, {851: [_req_row(7701)]})

    headers = await _api_project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/warehouse",
        json={"name": "FF Archive", "warehouse_type": "FULFILLMENT"},
        headers=headers,
    )
    wh_id = resp.json()["id"]
    resp = await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/connect",
        json={"provider": "skladbot", "token": FAKE_TOKEN},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/v1/warehouse/{wh_id}/fulfillment/sync", headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.get(f"/api/v1/warehouse/{wh_id}/fulfillment/requests", headers=headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert rows[0]["local_archived"] is False
    assert rows[0]["local_archived_at"] is None
    ff_id = rows[0]["id"]

    resp = await client.post(f"/api/v1/warehouse/{wh_id}/fulfillment/requests/{ff_id}/archive", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["local_archived"] is True
    assert data["local_archived_at"] is not None

    resp = await client.get(f"/api/v1/warehouse/{wh_id}/fulfillment/requests", headers=headers)
    assert resp.json() == []
    resp = await client.get(
        f"/api/v1/warehouse/{wh_id}/fulfillment/requests?show_archived=true",
        headers=headers,
    )
    assert [r["id"] for r in resp.json()] == [ff_id]

    resp = await client.delete(f"/api/v1/warehouse/{wh_id}/fulfillment/requests/{ff_id}/archive", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["local_archived"] is False

    resp = await client.post(f"/api/v1/warehouse/{wh_id}/fulfillment/requests/99999999/archive", headers=headers)
    assert resp.status_code == 404
    resp = await client.delete(f"/api/v1/warehouse/{wh_id}/fulfillment/requests/99999999/archive", headers=headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_archive_requires_auth(client):
    resp = await client.post("/api/v1/warehouse/1/fulfillment/requests/1/archive")
    assert resp.status_code in (401, 403, 422)
    resp = await client.delete("/api/v1/warehouse/1/fulfillment/requests/1/archive")
    assert resp.status_code in (401, 403, 422)
