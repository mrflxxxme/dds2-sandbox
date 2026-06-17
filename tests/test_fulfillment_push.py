"""
Service tests — PUSH нашей заявки на сборку в ФФ (skladbot тип 851).

create_ff_request_from_assembly: резолв ШК → product_data_id (мок resolve_products),
POST /v1/requests (мок create_request), зеркалирование + связь со сборкой.
Никаких реальных HTTP-вызовов — SkladbotClient мокается через monkeypatch.
"""

import base64
import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.integrations.resilience import RateLimitError
from backend.integrations.skladbot_client import SkladbotApiError, SkladbotClient
from backend.models import (
    FulfillmentStatusEvent,
    IntegrationKey,
    Nomenclature,
    Warehouse,
)
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.schemas.fulfillment import FfCreateRequestPayload
from backend.services import fulfillment_service
from backend.utils.crypto import encrypt as _encrypt

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _fake_jwt(payload: dict) -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'RS256', 'typ': 'JWT'})}.{b64(payload)}.fake_signature"


FAKE_TOKEN = _fake_jwt({"sub": "6282", "exp": 1893456000.0})  # exp = 2030-01-01 UTC
FAKE_CUSTOMER = {"id": 6282, "name": "ООО ТЕСТ ФФ"}

# Справочники GET /v1/requests/form-data: поля marketplace/marketplace_warehouse —
# select по integer id (НЕ имя). Казань=77, Коледино=10, Wildberries=1.
FAKE_FORM_DATA = {
    "utils": {
        "marketplaces": [{"text": "Wildberries", "value": 1}, {"text": "Ozon", "value": 2}],
        "marketplaceWarehouses": [
            {"text": "Казань", "value": 77, "marketplace": 1, "customer": None, "is_active": 1},
            {"text": "МСК Коледино", "value": 10, "marketplace": 1, "customer": None, "is_active": 1},
            {"text": "Ozon Хоругвино", "value": 30, "marketplace": 2, "customer": None, "is_active": 1},
        ],
        "marketplaceDeliveryTypes": [
            {"text": "Прямая", "value": "straight"},
            {"text": "Cross dock", "value": "cross_dock"},
        ],
    }
}


@pytest.fixture(autouse=True)
def _mock_form_data(monkeypatch):
    """form-data замокан во всех тестах — create валидирует склад/маркетплейс по нему."""

    async def fake_form_data(self):
        return FAKE_FORM_DATA

    monkeypatch.setattr(SkladbotClient, "fetch_form_data", fake_form_data)


def _payload(**over) -> FfCreateRequestPayload:
    base = {
        "marketplace_warehouse_id": 77,  # Казань
        "collection_date": date(2026, 6, 28),
        "unloading_date": date(2026, 6, 28),
    }
    base.update(over)
    return FfCreateRequestPayload(**base)


def _mock_resolve(monkeypatch, mapping: dict[str, int], available: int = 999):
    """resolve_products → {barcode: {...product_data_id...}} только для известных ШК."""

    async def fake_resolve(self, customer_id, request_type_id, barcodes):
        return {
            bc: {"product_data_id": pdid, "amount": 5, "counts": available, "name": "Товар", "vendor_code": "V"}
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
            "id": 990001,
            "delivery_number": "WH-R-990001",
            "type": "3. Доставка на склад МП",
            "status": "new",
            "stage_code": "cargo_pickup",
            "stage_title": "Забор груза",
            "created_at": "2026-06-17 10:00:00",
        }

    monkeypatch.setattr(SkladbotClient, "create_request", fake_create)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def warehouse(db_session, project):
    wh = Warehouse(project_id=project.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(wh)
    await db_session.commit()
    await db_session.refresh(wh)
    return wh


@pytest_asyncio.fixture
async def connected_key(db_session, project, warehouse, monkeypatch):
    """Подключённый skladbot-ключ (test_connection замокан)."""

    async def fake_test_connection(self):
        return FAKE_CUSTOMER

    async def fake_count_customers(self):
        return 1

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_test_connection)
    monkeypatch.setattr(SkladbotClient, "count_customers", fake_count_customers)
    await fulfillment_service.connect(db_session, project.id, warehouse.id, "skladbot", FAKE_TOKEN)
    return await fulfillment_service.get_integration(db_session, project.id, warehouse.id)


async def _make_nomenclature(db_session, project_id, barcode, article="ART-X"):
    nom = Nomenclature(project_id=project_id, barcode=barcode, article_seller=article)
    db_session.add(nom)
    await db_session.commit()
    await db_session.refresh(nom)
    return nom


async def _make_assembly(db_session, project, warehouse, items, status=AssemblyStatus.IN_PROGRESS.value):
    """Заявка на сборку с составом [(barcode, nomenclature_id, qty)]."""
    doc = AssemblyRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"ASM-{_uid()[:6]}",
        status=status,
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)
    db_session.add_all(
        [
            AssemblyRequestItem(
                project_id=project.id,
                assembly_request_id=doc.id,
                nomenclature_id=nom_id,
                barcode=barcode,
                quantity=qty,
            )
            for barcode, nom_id, qty in items
        ]
    )
    await db_session.commit()
    return doc


# ─── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_push_happy_path(db_session, project, warehouse, connected_key, monkeypatch):
    bc_a, bc_b = f"20{_uid()}", f"21{_uid()}"
    nom_a = await _make_nomenclature(db_session, project.id, bc_a)
    nom_b = await _make_nomenclature(db_session, project.id, bc_b)
    doc = await _make_assembly(db_session, project, warehouse, [(bc_a, nom_a.id, 3), (bc_b, nom_b.id, 7)])

    _mock_resolve(monkeypatch, {bc_a: 5001, bc_b: 5002})
    holder: dict = {}
    _mock_create(monkeypatch, holder)

    result = await fulfillment_service.create_ff_request_from_assembly(
        db_session, project.id, warehouse.id, doc.id, _payload()
    )

    assert result["external_id"] == "990001"
    assert result["ff_number"] == "WH-R-990001"
    assert result["items_sent"] == 2
    assert result["total_qty"] == 10
    assert result["skipped_barcodes"] == []
    assert result["request"]["assembly_request_id"] == doc.id
    assert result["request"]["kind"] == "assembly"
    assert result["request"]["dest_warehouse"] == "Казань"
    assert result["request"]["total_qty"] == 10
    assert result["request"]["linked_number"] == doc.number

    # POST-контракт
    payload = holder["payload"]
    assert payload["customer_id"] == 6282
    assert payload["request_type_id"] == 851
    assert payload["fields"]["marketplace"]["value"] == 1
    assert payload["fields"]["marketplace_delivery_type"]["value"] == "straight"
    assert payload["fields"]["marketplace_warehouse"]["value"] == 77
    assert payload["fields"]["collection_date"]["value"] == "2026-06-28"
    assert payload["fields"]["unloading_date"]["value"] == "2026-06-28"
    prods = {p["barcode"]: p for p in payload["products"]}
    assert prods[bc_a]["product_data_id"] == 5001
    assert prods[bc_a]["amount"] == 3
    assert prods[bc_b]["product_data_id"] == 5002
    assert prods[bc_b]["amount"] == 7

    # Зеркало в БД + связь + журнал «created»
    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    assert len(rows) == 1
    assert rows[0]["external_id"] == "990001"
    assert rows[0]["assembly_request_id"] == doc.id
    # project_id-скоуп обязателен: external_id "990001" хардкожен в нескольких тестах,
    # под xdist -n 2 на общей БД иначе ловим чужие события (iron rule #1).
    ev = await db_session.execute(
        select(FulfillmentStatusEvent).where(
            FulfillmentStatusEvent.project_id == project.id,
            FulfillmentStatusEvent.external_id == "990001",
        )
    )
    events = list(ev.scalars().all())
    assert len(events) == 1
    assert events[0].event_type == "created"


@pytest.mark.asyncio
async def test_push_skips_unresolved_barcodes(db_session, project, warehouse, connected_key, monkeypatch):
    bc_ok, bc_no = f"20{_uid()}", f"21{_uid()}"
    nom_ok = await _make_nomenclature(db_session, project.id, bc_ok)
    nom_no = await _make_nomenclature(db_session, project.id, bc_no)
    doc = await _make_assembly(db_session, project, warehouse, [(bc_ok, nom_ok.id, 4), (bc_no, nom_no.id, 9)])

    _mock_resolve(monkeypatch, {bc_ok: 6001})  # bc_no не имеет остатка у ФФ
    holder: dict = {}
    _mock_create(monkeypatch, holder)

    result = await fulfillment_service.create_ff_request_from_assembly(
        db_session, project.id, warehouse.id, doc.id, _payload()
    )
    assert result["items_sent"] == 1
    assert result["total_qty"] == 4
    assert result["skipped_barcodes"] == [bc_no]
    assert [p["barcode"] for p in holder["payload"]["products"]] == [bc_ok]


@pytest.mark.asyncio
async def test_push_none_resolved_raises(db_session, project, warehouse, connected_key, monkeypatch):
    bc = f"20{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    doc = await _make_assembly(db_session, project, warehouse, [(bc, nom.id, 2)])
    _mock_resolve(monkeypatch, {})  # ничего не доступно
    _mock_create(monkeypatch, {})

    with pytest.raises(ValueError, match="Ни один ШК"):
        await fulfillment_service.create_ff_request_from_assembly(
            db_session, project.id, warehouse.id, doc.id, _payload()
        )


@pytest.mark.asyncio
async def test_push_idempotent_already_linked(db_session, project, warehouse, connected_key, monkeypatch):
    bc = f"20{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    doc = await _make_assembly(db_session, project, warehouse, [(bc, nom.id, 2)])
    _mock_resolve(monkeypatch, {bc: 7001})
    holder: dict = {}
    _mock_create(monkeypatch, holder)

    await fulfillment_service.create_ff_request_from_assembly(db_session, project.id, warehouse.id, doc.id, _payload())
    # Повторный вызов — заявка уже связана
    with pytest.raises(ValueError, match="уже отправлена"):
        await fulfillment_service.create_ff_request_from_assembly(
            db_session, project.id, warehouse.id, doc.id, _payload()
        )


@pytest.mark.asyncio
async def test_push_not_connected_raises(db_session, project, warehouse, monkeypatch):
    bc = f"20{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    doc = await _make_assembly(db_session, project, warehouse, [(bc, nom.id, 2)])
    with pytest.raises(ValueError, match="не подключён"):
        await fulfillment_service.create_ff_request_from_assembly(
            db_session, project.id, warehouse.id, doc.id, _payload()
        )


@pytest.mark.asyncio
async def test_push_wrong_provider_raises(db_session, project, warehouse, monkeypatch):
    """Склад подключён к migfull — push отклоняется (только skladbot)."""
    bc = f"20{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    doc = await _make_assembly(db_session, project, warehouse, [(bc, nom.id, 2)])
    db_session.add(
        IntegrationKey(
            project_id=project.id,
            service="migfull",
            label=f"warehouse:{warehouse.id}",
            encrypted_key=_encrypt("x" * 32),
            is_active=True,
            warehouse_id=warehouse.id,
            config={"tenant_guid": str(uuid.uuid4())},
        )
    )
    await db_session.commit()
    with pytest.raises(ValueError, match="только для skladbot"):
        await fulfillment_service.create_ff_request_from_assembly(
            db_session, project.id, warehouse.id, doc.id, _payload()
        )


@pytest.mark.asyncio
async def test_push_assembly_not_found_returns_none(db_session, project, warehouse, connected_key):
    result = await fulfillment_service.create_ff_request_from_assembly(
        db_session, project.id, warehouse.id, 999999, _payload()
    )
    assert result is None


@pytest.mark.asyncio
async def test_push_cancelled_assembly_raises(db_session, project, warehouse, connected_key, monkeypatch):
    bc = f"20{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    doc = await _make_assembly(db_session, project, warehouse, [(bc, nom.id, 2)], status=AssemblyStatus.CANCELLED.value)
    with pytest.raises(ValueError, match="отменённую"):
        await fulfillment_service.create_ff_request_from_assembly(
            db_session, project.id, warehouse.id, doc.id, _payload()
        )


@pytest.mark.asyncio
async def test_push_empty_items_raises(db_session, project, warehouse, connected_key):
    doc = await _make_assembly(db_session, project, warehouse, [])
    with pytest.raises(ValueError, match="нет позиций"):
        await fulfillment_service.create_ff_request_from_assembly(
            db_session, project.id, warehouse.id, doc.id, _payload()
        )


@pytest.mark.asyncio
async def test_push_wrong_warehouse_raises(db_session, project, warehouse, connected_key, monkeypatch):
    """Сборка другого склада не отправляется через этот склад."""
    other = Warehouse(project_id=project.id, name=f"FF2-{_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)
    bc = f"20{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    doc = await _make_assembly(db_session, project, other, [(bc, nom.id, 2)])
    with pytest.raises(ValueError, match="другому складу"):
        await fulfillment_service.create_ff_request_from_assembly(
            db_session, project.id, warehouse.id, doc.id, _payload()
        )


@pytest.mark.asyncio
async def test_push_provider_error_surfaced(db_session, project, warehouse, connected_key, monkeypatch):
    bc = f"20{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    doc = await _make_assembly(db_session, project, warehouse, [(bc, nom.id, 2)])
    _mock_resolve(monkeypatch, {bc: 8001})
    _mock_create(monkeypatch, {}, exc=SkladbotApiError("bad", status_code=422))
    with pytest.raises(ValueError, match="отклонил создание"):
        await fulfillment_service.create_ff_request_from_assembly(
            db_session, project.id, warehouse.id, doc.id, _payload()
        )
    # Заявка у ФФ не создалась → зеркала нет
    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    assert rows == []


@pytest.mark.asyncio
async def test_push_unknown_warehouse_id_raises(db_session, project, warehouse, connected_key, monkeypatch):
    """id склада МП, которого нет в form-data → отказ ДО создания реального заказа."""
    bc = f"20{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    doc = await _make_assembly(db_session, project, warehouse, [(bc, nom.id, 2)])
    _mock_resolve(monkeypatch, {bc: 8001})
    holder: dict = {}
    _mock_create(monkeypatch, holder)

    with pytest.raises(ValueError, match="склад МП недоступен"):
        await fulfillment_service.create_ff_request_from_assembly(
            db_session, project.id, warehouse.id, doc.id, _payload(marketplace_warehouse_id=999999)
        )
    # create_request не вызывался — заказа у ФФ нет
    assert "payload" not in holder
    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    assert rows == []


@pytest.mark.asyncio
async def test_create_form_options_and_suggestion(db_session, project, warehouse, connected_key):
    """Форма создания: склады МП Wildberries (id), типы поставки, подбор по складу WB заявки."""
    bc = f"20{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    doc = await _make_assembly(db_session, project, warehouse, [(bc, nom.id, 2)])
    doc.wb_warehouse_name_manual = "Коледино"  # → должно матчить «МСК Коледино» (id 10)
    await db_session.commit()

    form = await fulfillment_service.get_ff_create_form(db_session, project.id, warehouse.id, doc.id)
    assert form.marketplace_id == 1
    assert form.marketplace_name == "Wildberries"
    names = {w.name for w in form.warehouses}
    assert names == {"Казань", "МСК Коледино"}  # Ozon-склад отфильтрован
    assert form.suggested_warehouse_id == 10
    assert {d.value for d in form.delivery_types} == {"straight", "cross_dock"}


# ─── connect: выбор кабинета (пиннинг customer_id для FF-operator токена) ────


@pytest.mark.asyncio
async def test_connect_multi_customer_requires_id(db_session, project, warehouse, monkeypatch):
    """Токен видит >1 кабинета и customer_id не задан → требуем явный выбор."""

    async def fake_tc(self):
        return {"id": 6398, "name": "ИП Волошин"}  # customers[0] произвольный

    async def fake_count(self):
        return 205

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_tc)
    monkeypatch.setattr(SkladbotClient, "count_customers", fake_count)
    with pytest.raises(ValueError, match="укажите customer_id"):
        await fulfillment_service.connect(db_session, project.id, warehouse.id, "skladbot", FAKE_TOKEN)


@pytest.mark.asyncio
async def test_connect_with_customer_id_pins_it(db_session, project, warehouse, monkeypatch):
    """customer_id задан и найден среди кабинетов токена → пинним именно его."""

    async def fake_tc(self):
        return {"id": 6398, "name": "ИП Волошин"}  # customers[0] != 6282

    async def fake_find(self, cid):
        return {"id": 6282, "name": "ООО ПЛЮС ВАЙБ"} if cid == 6282 else None

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_tc)
    monkeypatch.setattr(SkladbotClient, "find_customer", fake_find)
    status = await fulfillment_service.connect(
        db_session, project.id, warehouse.id, "skladbot", FAKE_TOKEN, customer_id=6282
    )
    assert status["customer_id"] == 6282
    assert status["customer_name"] == "ООО ПЛЮС ВАЙБ"


@pytest.mark.asyncio
async def test_connect_customer_id_not_found_raises(db_session, project, warehouse, monkeypatch):
    async def fake_tc(self):
        return {"id": 6398, "name": "ИП Волошин"}

    async def fake_find(self, cid):
        return None

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_tc)
    monkeypatch.setattr(SkladbotClient, "find_customer", fake_find)
    with pytest.raises(ValueError, match="не найден"):
        await fulfillment_service.connect(
            db_session, project.id, warehouse.id, "skladbot", FAKE_TOKEN, customer_id=9999
        )


# ─── SkladbotClient: обработка статусов (2xx vs ошибки) ─────────────────────


class _FakeResp:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body
        self.headers: dict = {}
        self.text = json.dumps(body)

    def json(self):
        return self._body


@pytest.mark.asyncio
async def test_client_accepts_201_created(monkeypatch):
    """POST /v1/requests отвечает 201 Created — клиент должен считать это успехом."""
    client = SkladbotClient("tok", project_id=-101)

    async def fake_request(self, method, url, **kw):
        return _FakeResp(201, {"data": {"id": 197985, "delivery_number": "WH-R-197985"}})

    monkeypatch.setattr("httpx.AsyncClient.request", fake_request)
    out = await client.create_request({"customer_id": 6282})
    assert out["id"] == 197985
    assert out["delivery_number"] == "WH-R-197985"


@pytest.mark.asyncio
async def test_client_resolve_products_200(monkeypatch):
    client = SkladbotClient("tok", project_id=-102)

    async def fake_request(self, method, url, **kw):
        return _FakeResp(
            200, [{"barcode": "111", "product_data_id": 42, "amount": 2, "counts": 5, "is_main_barcode": 1}]
        )

    monkeypatch.setattr("httpx.AsyncClient.request", fake_request)
    res = await client.resolve_products(6282, 851, ["111"])
    assert res["111"]["product_data_id"] == 42
    assert res["111"]["counts"] == 5


@pytest.mark.asyncio
async def test_client_500_raises(monkeypatch):
    client = SkladbotClient("tok", project_id=-103)

    async def fake_request(self, method, url, **kw):
        return _FakeResp(500, {"error": "boom"})

    monkeypatch.setattr("httpx.AsyncClient.request", fake_request)
    with pytest.raises(ValueError, match="500"):
        await client.create_request({})


@pytest.mark.asyncio
async def test_push_resolve_rate_limited_surfaced(db_session, project, warehouse, connected_key, monkeypatch):
    bc = f"20{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    doc = await _make_assembly(db_session, project, warehouse, [(bc, nom.id, 2)])

    async def fake_resolve(self, customer_id, request_type_id, barcodes):
        raise RateLimitError("429", retry_after=60)

    monkeypatch.setattr(SkladbotClient, "resolve_products", fake_resolve)
    with pytest.raises(ValueError, match="частоту запросов"):
        await fulfillment_service.create_ff_request_from_assembly(
            db_session, project.id, warehouse.id, doc.id, _payload()
        )
