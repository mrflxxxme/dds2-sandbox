# ruff: noqa: RUF001, RUF002, RUF003
"""
API-тесты предраспределения машины в пути (HTTP-контракт роутера).

Сервис-логика покрыта в test_pre_distribution.py — здесь проверяем именно HTTP-слой:
auth-гейт, project-скоуп через X-Project-Id, сериализация запроса/ответа на 4 ручках.
"""

import uuid

import pytest
from sqlalchemy import text

from backend.models.cost import CostOrder, CostOrderItem
from backend.models.enums import VehicleStatus

PREFIX = "/api/v1/warehouse/assembly/pre-distribution"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _project_headers(client, auth_headers) -> tuple[int, dict]:
    resp = await client.post("/api/v1/projects", json={"name": "PreDist API"}, headers=auth_headers)
    pid = resp.json()["id"]
    return pid, {**auth_headers, "X-Project-Id": str(pid)}


async def _seed_vehicle(db_session, pid: int) -> tuple[int, str, str]:
    """ФФ-склад + ШК + машина CUSTOMS (BC×100) в проекте pid. Возвращает (vehicle_id, barcode, order_no)."""
    wh_id = (
        await db_session.execute(
            text(
                "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, is_deleted, created_at, updated_at) "
                "VALUES (:p, :n, 'FULFILLMENT', 1, true, false, NOW(), NOW()) RETURNING id"
            ),
            {"p": pid, "n": f"FF-API-{_uid()}"},
        )
    ).scalar()
    barcode = f"PDAPI-{_uid()}"
    await db_session.execute(
        text("INSERT INTO nomenclature (project_id, barcode, subject, updated_at) VALUES (:p, :b, 'X', NOW())"),
        {"p": pid, "b": barcode},
    )
    order_no = f"PDAPI-{_uid()}"
    vehicle = CostOrder(
        project_id=pid,
        order_no=order_no,
        status=VehicleStatus.CUSTOMS,
        target_warehouse_id=wh_id,
    )
    db_session.add(vehicle)
    await db_session.flush()
    db_session.add(CostOrderItem(project_id=pid, order_no=vehicle.order_no, barcode=barcode, qty=100))
    await db_session.commit()
    return vehicle.id, barcode, order_no


@pytest.mark.asyncio
async def test_vehicles_requires_auth(client):
    resp = await client.get(f"{PREFIX}/vehicles")
    assert resp.status_code in (401, 403, 422)


@pytest.mark.asyncio
async def test_vehicles_empty_for_new_project(client, auth_headers):
    _pid, headers = await _project_headers(client, auth_headers)
    resp = await client.get(f"{PREFIX}/vehicles", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_full_flow_pool_create_advance(client, auth_headers, db_session):
    """pool → create → vehicles(distributed) → advance, всё через HTTP."""
    pid, headers = await _project_headers(client, auth_headers)
    vehicle_id, barcode, order_no = await _seed_vehicle(db_session, pid)

    # Пул
    pool = await client.get(f"{PREFIX}/vehicles/{vehicle_id}/pool", headers=headers)
    assert pool.status_code == 200
    body = pool.json()
    assert body["vehicle"]["can_distribute"] is True
    row = next(r for r in body["rows"] if r["barcode"] == barcode)
    assert row["gross_qty"] == 100 and row["available_qty"] == 100

    # Создание
    created = await client.post(
        PREFIX,
        json={
            "vehicle_id": vehicle_id,
            "rows": [{"barcode": barcode, "wb_warehouse_name": "Электросталь", "qty": 40}],
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    assert created.json()["created"] == 1
    assert created.json()["requests"][0]["is_pre_distribution"] is True
    new_req_id = created.json()["request_ids"][0]

    # Бейдж: заявка в общем списке сборки несёт is_pre_distribution + order_no машины
    asm_list = await client.get("/api/v1/warehouse/assembly", headers=headers)
    assert asm_list.status_code == 200
    mine_req = next(r for r in asm_list.json()["items"] if r["id"] == new_req_id)
    assert mine_req["is_pre_distribution"] is True
    assert mine_req["source_vehicle_order_no"] == order_no

    # Список: distributed_qty отражает разнесённое
    vehicles = await client.get(f"{PREFIX}/vehicles", headers=headers)
    mine = next(v for v in vehicles.json() if v["id"] == vehicle_id)
    assert mine["distributed_qty"] == 40

    # Ручной advance
    adv = await client.post(f"{PREFIX}/vehicles/{vehicle_id}/advance", headers=headers)
    assert adv.status_code == 200
    assert adv.json()["advanced"] == 1


@pytest.mark.asyncio
async def test_over_commit_returns_400(client, auth_headers, db_session):
    pid, headers = await _project_headers(client, auth_headers)
    vehicle_id, barcode, _order_no = await _seed_vehicle(db_session, pid)
    resp = await client.post(
        PREFIX,
        json={
            "vehicle_id": vehicle_id,
            "rows": [{"barcode": barcode, "wb_warehouse_name": "Казань", "qty": 150}],  # >100
        },
        headers=headers,
    )
    assert resp.status_code == 400
    # Унифицированный конверт ошибок DDS: {"error": {"code", "message", ...}}
    assert "Превышение" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_pool_404_for_non_distributable(client, auth_headers, db_session):
    """Машина без ФФ-склада назначения → 400 на pool (M3 хард-блок через HTTP)."""
    pid, headers = await _project_headers(client, auth_headers)
    vehicle = CostOrder(project_id=pid, order_no=f"PDAPI-{_uid()}", status=VehicleStatus.CUSTOMS, target_warehouse_id=None)
    db_session.add(vehicle)
    await db_session.commit()
    resp = await client.get(f"{PREFIX}/vehicles/{vehicle.id}/pool", headers=headers)
    assert resp.status_code == 400
