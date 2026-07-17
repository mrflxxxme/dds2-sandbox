"""
Тесты: авто-создание приёмки у ФФ (skladbot тип 852) при переводе машины в
«Отправлена» (DISPATCHED) + авто-связь созданной приёмки ФФ с нашей InboundReceipt.

push_ff_inbound: резолв ШК → product_data_id, POST /v1/requests (тип 852, единственное
обязательное поле product_arrival_date). Интеграция: update_vehicle_status(DISPATCHED)
создаёт приёмку у ФФ, нашу приёмку (EXPECTED) и связывает их; при сбое ФФ — блокирует
перевод (статус остаётся CUSTOMS). Все HTTP замоканы — реальных вызовов нет.
"""

import base64
import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.integrations.skladbot_client import SkladbotApiError, SkladbotClient
from backend.models import FulfillmentRequest, IntegrationKey, Nomenclature
from backend.models.enums import VehicleStatus
from backend.models.warehouse import InboundReceipt, InboundStatus
from backend.schemas.supply_chain import (
    FactoryOrderCreate,
    FactoryOrderItemCreate,
    SplitItem,
    SplitToVehiclesRequest,
    VehicleCreate,
    VehicleStatusUpdate,
    VehicleUpdate,
)
from backend.services import fulfillment_service
from backend.services.supply_chain.factory_orders import create_factory_order, split_to_vehicles
from backend.services.supply_chain.vehicle_delivery import create_vehicle, update_vehicle, update_vehicle_status
from backend.services.warehouse_crud import create_warehouse
from backend.utils.crypto import encrypt as _encrypt

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _fake_jwt(payload: dict) -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'RS256', 'typ': 'JWT'})}.{b64(payload)}.fake_signature"


FAKE_TOKEN = _fake_jwt({"sub": "6282", "exp": 1893456000.0})
FAKE_CUSTOMER = {"id": 6282, "name": "ООО ТЕСТ ФФ"}


def _mock_resolve(monkeypatch, mapping: dict[str, int], available: int = 0):
    async def fake_resolve(self, customer_id, request_type_id, barcodes):
        return {
            bc: {"product_data_id": pdid, "amount": 0, "counts": available, "name": "Товар", "vendor_code": "V"}
            for bc, pdid in mapping.items()
            if bc in barcodes
        }

    monkeypatch.setattr(SkladbotClient, "resolve_products", fake_resolve)


def _mock_create(monkeypatch, holder: dict, response: dict | None = None, exc: Exception | None = None):
    async def fake_create(self, payload):
        holder["payload"] = payload
        if exc is not None:
            raise exc
        return response or {
            "id": 206977,
            "delivery_number": "WH-R-206977",
            "type": "2.1 Приемка без согласование маркировки",
            "status": "new",
            "created_at": "2026-07-14 10:00:00",
        }

    monkeypatch.setattr(SkladbotClient, "create_request", fake_create)


@pytest_asyncio.fixture
async def ff_warehouse(db_session, project):
    return await create_warehouse(
        db_session, project.id, {"name": f"FF-{_uid()}", "warehouse_type": "FULFILLMENT"}
    )


@pytest_asyncio.fixture
async def connected_key(db_session, project, ff_warehouse, monkeypatch):
    """Подключённый skladbot-ключ на ff_warehouse (test_connection замокан)."""

    async def fake_test_connection(self):
        return FAKE_CUSTOMER

    async def fake_count_customers(self):
        return 1

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_test_connection)
    monkeypatch.setattr(SkladbotClient, "count_customers", fake_count_customers)
    await fulfillment_service.connect(db_session, project.id, ff_warehouse.id, "skladbot", FAKE_TOKEN)
    return await fulfillment_service.get_integration(db_session, project.id, ff_warehouse.id)


async def _make_nom(db_session, project_id, barcode):
    nom = Nomenclature(project_id=project_id, barcode=barcode, article_seller="ART")
    db_session.add(nom)
    await db_session.commit()
    await db_session.refresh(nom)
    return nom


async def _dispatched_ready_vehicle(db_session, project, target_wh_id, items):
    """Машина в статусе CUSTOMS с инвойсом/ДТ/складом — готова к DISPATCHED.
    items: [(barcode, qty)]. ШК заранее заведены в Nomenclature.
    """
    order = await create_factory_order(
        db_session,
        project.id,
        FactoryOrderCreate(
            order_number=f"FO-{_uid()}",
            factory_name="F",
            items=[FactoryOrderItemCreate(barcode=bc, qty=q, price_cny=Decimal("10")) for bc, q in items],
        ),
    )
    order_no = f"V-{_uid()}"
    await create_vehicle(db_session, project.id, VehicleCreate(order_no=order_no, rate_cny=Decimal("12.5")))
    await split_to_vehicles(
        db_session,
        project.id,
        order.id,
        SplitToVehiclesRequest(
            assignments=[
                SplitItem(factory_order_item_id=it.id, qty=it.qty, vehicle_order_no=order_no)
                for it in order.items
            ]
        ),
    )
    await update_vehicle_status(db_session, project.id, order_no, VehicleStatusUpdate(status=VehicleStatus.SHIPPED))
    await update_vehicle_status(
        db_session, project.id, order_no, VehicleStatusUpdate(status=VehicleStatus.CUSTOMS, dt_number="DT-1")
    )
    await update_vehicle(
        db_session,
        project.id,
        order_no,
        VehicleUpdate(invoice_no="INV-1", target_warehouse_id=target_wh_id, estimated_arrival_date=date(2026, 7, 20)),
    )
    return order_no


# ─── push_ff_inbound (unit) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_push_ff_inbound_happy_contract(db_session, project, ff_warehouse, connected_key, monkeypatch):
    bc_a, bc_b = f"20{_uid()}", f"21{_uid()}"
    _mock_resolve(monkeypatch, {bc_a: 5001, bc_b: 5002})
    holder: dict = {}
    _mock_create(monkeypatch, holder)

    row = await fulfillment_service.push_ff_inbound(
        db_session, project.id, ff_warehouse.id, {bc_a: 3, bc_b: 7}, date(2026, 7, 20), comment="Машина V-1 (DDS)"
    )

    assert row is not None
    assert row["external_id"] == "206977"
    assert row["kind"] == "inbound"
    assert row["type_id"] == 852
    assert row["total_qty"] == 10

    p = holder["payload"]
    assert p["customer_id"] == 6282
    assert p["request_type_id"] == 852
    assert p["fields"] == {"product_arrival_date": {"value": "2026-07-20"}}
    assert p["notify"] is False
    prods = {x["barcode"]: x["amount"] for x in p["products"]}
    assert prods == {bc_a: 3, bc_b: 7}


@pytest.mark.asyncio
async def test_push_ff_inbound_blocks_on_unresolved(db_session, project, ff_warehouse, connected_key, monkeypatch):
    """ШК не заведён в карточках ФФ → блок, POST не вызывается."""
    bc_ok, bc_no = f"20{_uid()}", f"21{_uid()}"
    _mock_resolve(monkeypatch, {bc_ok: 6001})  # bc_no не резолвится
    holder: dict = {}
    _mock_create(monkeypatch, holder)

    with pytest.raises(ValueError, match="не найдены в карточках ФФ"):
        await fulfillment_service.push_ff_inbound(
            db_session, project.id, ff_warehouse.id, {bc_ok: 4, bc_no: 9}, date(2026, 7, 20)
        )
    assert "payload" not in holder


@pytest.mark.asyncio
async def test_push_ff_inbound_zero_stock_ok(db_session, project, ff_warehouse, connected_key, monkeypatch):
    """Нулевой остаток у ФФ приёмку НЕ блокирует (приход, не расход)."""
    bc = f"20{_uid()}"
    _mock_resolve(monkeypatch, {bc: 7001}, available=0)
    _mock_create(monkeypatch, {})
    row = await fulfillment_service.push_ff_inbound(
        db_session, project.id, ff_warehouse.id, {bc: 5}, date(2026, 7, 20)
    )
    assert row is not None and row["total_qty"] == 5


@pytest.mark.asyncio
async def test_push_ff_inbound_not_connected_returns_none(db_session, project, ff_warehouse):
    """ФФ не подключён к складу → None (переход машины не блокируется)."""
    row = await fulfillment_service.push_ff_inbound(
        db_session, project.id, ff_warehouse.id, {"111": 1}, date(2026, 7, 20)
    )
    assert row is None


@pytest.mark.asyncio
async def test_push_ff_inbound_other_provider_returns_none(db_session, project, ff_warehouse):
    """migfull — создание приёмки пока не поддержано → None (no-op)."""
    db_session.add(
        IntegrationKey(
            project_id=project.id,
            service="migfull",
            label=f"warehouse:{ff_warehouse.id}",
            encrypted_key=_encrypt("x" * 32),
            is_active=True,
            warehouse_id=ff_warehouse.id,
            config={"tenant_guid": str(uuid.uuid4())},
        )
    )
    await db_session.commit()
    row = await fulfillment_service.push_ff_inbound(
        db_session, project.id, ff_warehouse.id, {"111": 1}, date(2026, 7, 20)
    )
    assert row is None


# ─── Интеграция: DISPATCHED → приёмка ФФ + авто-связь ─────────────────────────


@pytest.mark.asyncio
async def test_dispatch_creates_and_links_ff_inbound(db_session, project, ff_warehouse, connected_key, monkeypatch):
    bc_a, bc_b = f"30{_uid()}", f"31{_uid()}"
    await _make_nom(db_session, project.id, bc_a)
    await _make_nom(db_session, project.id, bc_b)
    order_no = await _dispatched_ready_vehicle(db_session, project, ff_warehouse.id, [(bc_a, 3), (bc_b, 7)])

    _mock_resolve(monkeypatch, {bc_a: 5001, bc_b: 5002})
    holder: dict = {}
    _mock_create(monkeypatch, holder)

    result = await update_vehicle_status(
        db_session, project.id, order_no, VehicleStatusUpdate(status=VehicleStatus.DISPATCHED)
    )

    # Наша приёмка создана и связана с приёмкой ФФ
    assert result["ff_inbound_number"] == "WH-R-206977"
    receipt_id = result["inbound_receipt_id"]
    ff = (
        await db_session.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.project_id == project.id,
                FulfillmentRequest.provider == "skladbot",
                FulfillmentRequest.external_id == "206977",
            )
        )
    ).scalar_one()
    assert ff.inbound_receipt_id == receipt_id
    assert ff.kind == "inbound"
    assert ff.type_id == 852
    # дата прихода приёмки ФФ = estimated_arrival_date машины
    assert holder["payload"]["fields"]["product_arrival_date"]["value"] == "2026-07-20"


@pytest.mark.asyncio
async def test_dispatch_blocked_when_ff_create_fails(db_session, project, ff_warehouse, connected_key, monkeypatch):
    """Сбой создания приёмки у ФФ → перевод в «Отправлена» блокируется: статус
    остаётся CUSTOMS, нашей приёмки нет."""
    bc = f"32{_uid()}"
    await _make_nom(db_session, project.id, bc)
    order_no = await _dispatched_ready_vehicle(db_session, project, ff_warehouse.id, [(bc, 4)])

    _mock_resolve(monkeypatch, {bc: 6001})
    _mock_create(monkeypatch, {}, exc=SkladbotApiError("bad", status_code=422))

    with pytest.raises(ValueError, match="отклонил приёмку"):
        await update_vehicle_status(
            db_session, project.id, order_no, VehicleStatusUpdate(status=VehicleStatus.DISPATCHED)
        )

    # Статус НЕ изменился, приёмки нет
    from backend.models.cost import CostOrder

    vehicle = (
        await db_session.execute(
            select(CostOrder).where(CostOrder.project_id == project.id, CostOrder.order_no == order_no)
        )
    ).scalar_one()
    assert vehicle.status == VehicleStatus.CUSTOMS
    assert vehicle.inbound_receipt_id is None
    receipts = (
        (
            await db_session.execute(
                select(InboundReceipt).where(
                    InboundReceipt.project_id == project.id, InboundReceipt.cost_order_id == vehicle.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert receipts == []


@pytest.mark.asyncio
async def test_dispatch_without_ff_key_unchanged(db_session, project, monkeypatch):
    """Склад без ФФ-ключа — DISPATCHED создаёт приёмку как раньше, без ФФ."""
    wh = await create_warehouse(db_session, project.id, {"name": f"EXT-{_uid()}", "warehouse_type": "EXTERNAL"})
    bc = f"33{_uid()}"
    await _make_nom(db_session, project.id, bc)
    order_no = await _dispatched_ready_vehicle(db_session, project, wh.id, [(bc, 2)])

    result = await update_vehicle_status(
        db_session, project.id, order_no, VehicleStatusUpdate(status=VehicleStatus.DISPATCHED)
    )
    assert result["inbound_receipt_id"] is not None
    assert "ff_inbound_number" not in result
