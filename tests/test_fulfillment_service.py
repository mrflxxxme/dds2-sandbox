"""
Service tests — fulfillment integration (skladbot): sync stocks/requests, link, connect.

SkladbotClient полностью мокается через monkeypatch — никаких реальных HTTP-вызовов.
"""

import base64
import json
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.integrations.migfull_client import MigfullClient, normalize_tenant_guid
from backend.integrations.resilience import RateLimitError
from backend.integrations.skladbot_client import SkladbotApiError, SkladbotClient, decode_jwt_exp
from backend.integrations.wmscelicom_client import WmsCelicomClient, normalize_base_url
from backend.models import (
    FulfillmentRequest,
    FulfillmentStatusEvent,
    FulfillmentStock,
    InboundReceipt,
    InboundReceiptItem,
    IntegrationKey,
    Nomenclature,
    SyncLog,
    Warehouse,
    WarehouseStock,
)
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus, AssemblyStatusHistory
from backend.services import fulfillment_service
from backend.utils.time import utcnow

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _fake_jwt(payload: dict) -> str:
    """Build a fake unsigned JWT (base64url header.payload.signature)."""

    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{b64({'alg': 'RS256', 'typ': 'JWT'})}.{b64(payload)}.fake_signature"


FAKE_TOKEN = _fake_jwt({"sub": "6282", "exp": 1893456000.0})  # exp = 2030-01-01 UTC
FAKE_CUSTOMER = {"id": 6282, "name": "ООО ТЕСТ ФФ"}


def _item(barcode, amount=0, reserve=0, repair=0, nominal=0, name="Товар", vendor="ART-1", pdid=111):
    """skladbot /v1/products item."""
    return {
        "barcode": barcode,
        "vendor_code": vendor,
        "name": name,
        "amount": amount,
        "reserve_amount": reserve,
        "repair_amount": repair,
        "nominale_amount": nominal,
        "product_data_id": pdid,
        "product_id": 1,
        "system_product_id": 10,
        "marketplace": {"id": 1, "name": "WB"},
        "price": 100,
    }


def _req(
    req_id,
    number=None,
    status="new",
    created="2026-06-10",
    archived=0,
    completed=0,
    stage_title="Новая",
    stage_code=None,
):
    """skladbot /v1/requests row."""
    return {
        "id": req_id,
        "delivery_number": number or f"WH-R-{req_id}",
        "customer": "ООО ТЕСТ ФФ",
        "created_at": created,
        "status": status,
        "executor": None,
        "archived": archived,
        "type": "тип заявки",
        "stage_title": stage_title,
        "stage_code": stage_code,
        "can_be_executed": True,
        "time_to_process": None,
        "expired": False,
        "is_completed": completed,
    }


def _mock_connection(monkeypatch, customer=FAKE_CUSTOMER):
    async def fake_test_connection(self):
        return customer

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_test_connection)


def _mock_products(monkeypatch, items):
    async def fake_fetch_all_products(self, customer_id):
        return items

    monkeypatch.setattr(SkladbotClient, "fetch_all_products", fake_fetch_all_products)


def _mock_requests(monkeypatch, by_type: dict):
    async def fake_fetch_requests(self, type_id):
        return by_type.get(type_id, [])

    monkeypatch.setattr(SkladbotClient, "fetch_requests", fake_fetch_requests)

    # Синк обогащает активные assembly-строки живой деталкой — без дефолтного
    # мока тесты ходили бы в сеть. Тесты со своей деталкой переопределяют ПОСЛЕ.
    async def fake_fetch_request_detail(self, external_id):
        return {}

    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fake_fetch_request_detail)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def warehouse(db_session, project):
    wh = Warehouse(project_id=project.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(wh)
    await db_session.commit()
    await db_session.refresh(wh)
    return wh


@pytest_asyncio.fixture
async def other_warehouse(db_session, other_project):
    wh = Warehouse(project_id=other_project.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(wh)
    await db_session.commit()
    await db_session.refresh(wh)
    return wh


@pytest_asyncio.fixture
async def connected_key(db_session, project, warehouse, monkeypatch):
    """Подключённый skladbot-ключ (test_connection замокан)."""
    _mock_connection(monkeypatch)
    await fulfillment_service.connect(db_session, project.id, warehouse.id, "skladbot", FAKE_TOKEN)
    return await fulfillment_service.get_integration(db_session, project.id, warehouse.id)


async def _make_nomenclature(db_session, project_id, barcode, article="ART-X", subject=None, brand=None):
    nom = Nomenclature(project_id=project_id, barcode=barcode, article_seller=article, subject=subject, brand=brand)
    db_session.add(nom)
    await db_session.commit()
    await db_session.refresh(nom)
    return nom


async def _ff_stocks(db_session, project_id, warehouse_id) -> list[FulfillmentStock]:
    result = await db_session.execute(
        select(FulfillmentStock)
        .where(
            FulfillmentStock.project_id == project_id,
            FulfillmentStock.warehouse_id == warehouse_id,
        )
        .limit(100)
    )
    return list(result.scalars().all())


# ─── decode_jwt_exp ──────────────────────────────────────────────────────────


def test_decode_jwt_exp_valid():
    exp = decode_jwt_exp(FAKE_TOKEN)
    assert exp == datetime(2030, 1, 1, 0, 0, 0)


def test_decode_jwt_exp_no_exp():
    assert decode_jwt_exp(_fake_jwt({"sub": "1"})) is None


def test_decode_jwt_exp_garbage():
    assert decode_jwt_exp("not-a-jwt") is None
    assert decode_jwt_exp("a.%%%невалидный-base64%%%.c") is None
    assert decode_jwt_exp("") is None


# ─── Connect / status / disconnect ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_and_status(db_session, project, warehouse, connected_key):
    status = await fulfillment_service.get_status(db_session, project.id, warehouse.id)
    assert status["connected"] is True
    assert status["provider"] == "skladbot"
    assert status["key_preview"] == "***" + FAKE_TOKEN[-4:]
    assert status["customer_id"] == 6282
    assert status["customer_name"] == "ООО ТЕСТ ФФ"
    assert status["token_expires_at"] is not None
    assert str(status["token_expires_at"]).startswith("2030-01-01")


@pytest.mark.asyncio
async def test_connect_invalid_token_raises(db_session, project, warehouse, monkeypatch):
    _mock_connection(monkeypatch, customer=None)
    with pytest.raises(ValueError):
        await fulfillment_service.connect(db_session, project.id, warehouse.id, "skladbot", FAKE_TOKEN)


@pytest.mark.asyncio
async def test_connect_missing_warehouse_raises(db_session, project, monkeypatch):
    _mock_connection(monkeypatch)
    with pytest.raises(ValueError):
        await fulfillment_service.connect(db_session, project.id, 99999999, "skladbot", FAKE_TOKEN)


@pytest.mark.asyncio
async def test_connect_restores_soft_deleted_key(db_session, project, warehouse, connected_key, monkeypatch):
    """Повторный connect после disconnect восстанавливает строку (НЕ новый INSERT)."""
    key_id = connected_key.id

    ok = await fulfillment_service.disconnect(db_session, project.id, warehouse.id)
    assert ok is True
    assert await fulfillment_service.get_integration(db_session, project.id, warehouse.id) is None

    _mock_connection(monkeypatch)
    status = await fulfillment_service.connect(db_session, project.id, warehouse.id, "skladbot", FAKE_TOKEN)
    assert status["connected"] is True

    key = await fulfillment_service.get_integration(db_session, project.id, warehouse.id)
    assert key is not None
    assert key.id == key_id  # та же строка — restore, не INSERT
    assert key.is_deleted is False
    assert key.is_active is True

    # В таблице ровно одна строка с этим label
    result = await db_session.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project.id,
            IntegrationKey.service == "skladbot",
            IntegrationKey.label == f"warehouse:{warehouse.id}",
        )
    )
    assert len(result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_status_disconnected(db_session, project, warehouse):
    status = await fulfillment_service.get_status(db_session, project.id, warehouse.id)
    assert status["connected"] is False


@pytest.mark.asyncio
async def test_disconnect_not_connected(db_session, project, warehouse):
    assert await fulfillment_service.disconnect(db_session, project.id, warehouse.id) is False


# ─── Sync: stocks ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_stocks_aggregates_duplicate_barcodes(db_session, project, warehouse, connected_key, monkeypatch):
    """Один barcode в нескольких item'ах (WB/OZON версии) — суммируем; пустой barcode — пропускаем."""
    bc = f"BC-{_uid()}"
    _mock_products(
        monkeypatch,
        [
            _item(bc, amount=10, reserve=2, repair=1, nominal=5, pdid=111),
            _item(bc, amount=7, reserve=1, repair=0, nominal=3, pdid=222),
            _item("", amount=99),  # пустой barcode — мимо
            _item(None, amount=50),  # None barcode — мимо
        ],
    )
    _mock_requests(monkeypatch, {})

    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["stocks_synced"] == 1

    rows = await _ff_stocks(db_session, project.id, warehouse.id)
    assert len(rows) == 1
    row = rows[0]
    assert row.barcode == bc
    assert row.qty_good == 17
    assert row.qty_reserve == 3
    assert row.qty_defect == 1
    assert row.qty_nominal == 8
    assert row.external_product_id == "111"  # от первого item'а
    assert row.provider == "skladbot"


@pytest.mark.asyncio
async def test_sync_stocks_maps_nomenclature(db_session, project, warehouse, connected_key, monkeypatch):
    bc_known = f"BC-{_uid()}"
    bc_unknown = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc_known)

    _mock_products(monkeypatch, [_item(bc_known, amount=5), _item(bc_unknown, amount=3)])
    _mock_requests(monkeypatch, {})

    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["stocks_synced"] == 2
    assert result["unmatched_barcodes"] == 1

    rows = {r.barcode: r for r in await _ff_stocks(db_session, project.id, warehouse.id)}
    assert rows[bc_known].nomenclature_id == nom.id
    assert rows[bc_unknown].nomenclature_id is None


@pytest.mark.asyncio
async def test_sync_stocks_full_replacement(db_session, project, warehouse, connected_key, monkeypatch):
    """Ресинк полностью заменяет снапшот: старые строки удаляются."""
    bc_old = f"BC-{_uid()}"
    bc_new = f"BC-{_uid()}"
    _mock_requests(monkeypatch, {})

    _mock_products(monkeypatch, [_item(bc_old, amount=10)])
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    _mock_products(monkeypatch, [_item(bc_new, amount=0)])  # нули тоже сохраняем
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    rows = await _ff_stocks(db_session, project.id, warehouse.id)
    assert [r.barcode for r in rows] == [bc_new]
    assert rows[0].qty_good == 0


@pytest.mark.asyncio
async def test_sync_without_connection_raises(db_session, project, warehouse):
    with pytest.raises(ValueError):
        await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)


# ─── Sync: requests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_requests_kind_classification(db_session, project, warehouse, connected_key, monkeypatch):
    _mock_products(monkeypatch, [])
    _mock_requests(
        monkeypatch,
        {
            851: [_req(1001)],
            852: [_req(1002)],
            2644: [_req(1003)],
        },
    )

    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["requests_synced"] == 3

    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, None)
    kinds = {r["external_id"]: r["kind"] for r in rows}
    assert kinds["1001"] == "assembly"
    assert kinds["1002"] == "inbound"
    assert kinds["1003"] == "inbound"

    assembly_only = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    assert [r["external_id"] for r in assembly_only] == ["1001"]


@pytest.mark.asyncio
async def test_sync_requests_upsert_preserves_links(db_session, project, warehouse, connected_key, monkeypatch):
    """Ресинк обновляет status/stage/synced_at, но НЕ трогает ручные связи."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(2001, status="new", stage_title="Новая")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    # Привязываем нашу заявку на сборку
    doc = AssemblyRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"A-{_uid()[:6]}",
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, None)
    ff_id = rows[0]["id"]
    linked = await fulfillment_service.link_request(db_session, project.id, ff_id, assembly_request_id=doc.id)
    assert linked["assembly_request_id"] == doc.id

    # Ресинк с новым статусом
    _mock_requests(monkeypatch, {851: [_req(2001, status="done", stage_title="Выполнена", completed=1)]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, None)
    assert len(rows) == 1  # UPSERT, не дубль
    row = rows[0]
    assert row["status"] == "done"
    assert row["stage_title"] == "Выполнена"
    assert row["is_completed"] is True
    assert row["assembly_request_id"] == doc.id  # связь сохранена
    assert row["linked_number"] == doc.number


# ─── Link / unlink ───────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def ff_pair(db_session, project, warehouse, connected_key, monkeypatch):
    """Пара ФФ-заявок (assembly + inbound) в БД."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(3001)], 852: [_req(3002)]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, None)
    by_kind = {r["kind"]: r for r in rows}
    return by_kind["assembly"], by_kind["inbound"]


@pytest.mark.asyncio
async def test_link_unlink_inbound_happy(db_session, project, warehouse, ff_pair):
    _, ff_inbound = ff_pair
    receipt = InboundReceipt(project_id=project.id, warehouse_id=warehouse.id, number=f"IN-{_uid()[:6]}")
    db_session.add(receipt)
    await db_session.commit()
    await db_session.refresh(receipt)

    row = await fulfillment_service.link_request(
        db_session, project.id, ff_inbound["id"], inbound_receipt_id=receipt.id
    )
    assert row["inbound_receipt_id"] == receipt.id
    assert row["linked_number"] == receipt.number

    row = await fulfillment_service.unlink_request(db_session, project.id, ff_inbound["id"])
    assert row["inbound_receipt_id"] is None
    assert row["assembly_request_id"] is None


@pytest.mark.asyncio
async def test_link_requires_exactly_one_id(db_session, project, ff_pair):
    ff_assembly, _ = ff_pair
    with pytest.raises(ValueError):
        await fulfillment_service.link_request(db_session, project.id, ff_assembly["id"])
    with pytest.raises(ValueError):
        await fulfillment_service.link_request(
            db_session, project.id, ff_assembly["id"], assembly_request_id=1, inbound_receipt_id=2
        )


@pytest.mark.asyncio
async def test_link_wrong_kind_raises(db_session, project, warehouse, ff_pair):
    """inbound_receipt_id нельзя привязать к assembly-заявке и наоборот."""
    ff_assembly, ff_inbound = ff_pair
    receipt = InboundReceipt(project_id=project.id, warehouse_id=warehouse.id, number=f"IN-{_uid()[:6]}")
    doc = AssemblyRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"A-{_uid()[:6]}",
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
    )
    db_session.add_all([receipt, doc])
    await db_session.commit()
    await db_session.refresh(receipt)
    await db_session.refresh(doc)

    with pytest.raises(ValueError):
        await fulfillment_service.link_request(db_session, project.id, ff_assembly["id"], inbound_receipt_id=receipt.id)
    with pytest.raises(ValueError):
        await fulfillment_service.link_request(db_session, project.id, ff_inbound["id"], assembly_request_id=doc.id)


@pytest.mark.asyncio
async def test_link_missing_document_raises(db_session, project, ff_pair):
    ff_assembly, _ = ff_pair
    with pytest.raises(ValueError):
        await fulfillment_service.link_request(db_session, project.id, ff_assembly["id"], assembly_request_id=99999999)


@pytest.mark.asyncio
async def test_link_already_linked_document_raises(db_session, project, warehouse, connected_key, monkeypatch):
    """Документ, уже связанный с другой ФФ-заявкой, привязать нельзя."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(4001), _req(4002)]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    assert len(rows) == 2

    doc = AssemblyRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"A-{_uid()[:6]}",
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    await fulfillment_service.link_request(db_session, project.id, rows[0]["id"], assembly_request_id=doc.id)
    with pytest.raises(ValueError):
        await fulfillment_service.link_request(db_session, project.id, rows[1]["id"], assembly_request_id=doc.id)


@pytest.mark.asyncio
async def test_link_nonexistent_ff_request_returns_none(db_session, project):
    row = await fulfillment_service.link_request(db_session, project.id, 99999999, assembly_request_id=1)
    assert row is None
    row = await fulfillment_service.unlink_request(db_session, project.id, 99999999)
    assert row is None


# ─── list_stocks (UNION с нашим складом) ────────────────────────────────────


@pytest.mark.asyncio
async def test_list_stocks_union_and_diff(db_session, project, warehouse, connected_key, monkeypatch):
    """ФФ-строки + наши строки без ФФ; diff = ff_good - our_quantity."""
    bc_both = f"BC-{_uid()}"  # есть и на ФФ, и у нас
    bc_ff_only = f"BC-{_uid()}"  # только ФФ (без номенклатуры)
    bc_our_only = f"BC-{_uid()}"  # только у нас

    nom_both = await _make_nomenclature(
        db_session, project.id, bc_both, article="ART-BOTH", subject="Накидки", brand="DIVANDEK"
    )
    nom_our = await _make_nomenclature(
        db_session, project.id, bc_our_only, article="ART-OUR", subject="Покрывала", brand="ELKA"
    )

    db_session.add_all(
        [
            WarehouseStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                nomenclature_id=nom_both.id,
                barcode=bc_both,
                quantity=7,
                defect_quantity=1,
            ),
            WarehouseStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                nomenclature_id=nom_our.id,
                barcode=bc_our_only,
                quantity=4,
            ),
        ]
    )
    await db_session.commit()

    _mock_products(monkeypatch, [_item(bc_both, amount=10, repair=2), _item(bc_ff_only, amount=3)])
    _mock_requests(monkeypatch, {})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    rows = {r["barcode"]: r for r in data["rows"]}
    assert set(rows) == {bc_both, bc_ff_only, bc_our_only}

    assert rows[bc_both]["ff_good"] == 10
    assert rows[bc_both]["our_quantity"] == 7
    assert rows[bc_both]["our_defect"] == 1
    assert rows[bc_both]["diff"] == 3
    assert rows[bc_both]["article_seller"] == "ART-BOTH"
    assert rows[bc_both]["subject"] == "Накидки"
    assert rows[bc_both]["brand"] == "DIVANDEK"

    assert rows[bc_ff_only]["diff"] == 3
    assert rows[bc_ff_only]["nomenclature_id"] is None
    assert rows[bc_ff_only]["subject"] is None
    assert rows[bc_ff_only]["brand"] is None

    assert rows[bc_our_only]["ff_good"] == 0
    assert rows[bc_our_only]["our_quantity"] == 4
    assert rows[bc_our_only]["diff"] == -4
    assert rows[bc_our_only]["article_seller"] == "ART-OUR"
    assert rows[bc_our_only]["subject"] == "Покрывала"

    # Списки значений для фильтров — distinct + sorted
    assert data["subjects"] == ["Накидки", "Покрывала"]
    assert data["brands"] == ["DIVANDEK", "ELKA"]

    totals = data["totals"]
    assert totals["ff_good"] == 13
    assert totals["our_quantity"] == 11
    assert totals["diff"] == 2
    assert totals["unmatched"] == 1
    assert data["synced_at"] is not None

    # Сортировка: diff desc, затем barcode
    diffs = [r["diff"] for r in data["rows"]]
    assert diffs == sorted(diffs, reverse=True)


# ─── Project isolation ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_isolation(
    db_session, project, other_project, warehouse, other_warehouse, connected_key, monkeypatch
):
    """Синк и выборки project A не видят и не трогают данные project B."""
    bc_other = f"BC-{_uid()}"
    db_session.add(
        FulfillmentStock(
            project_id=other_project.id,
            warehouse_id=other_warehouse.id,
            provider="skladbot",
            barcode=bc_other,
            qty_good=42,
        )
    )
    db_session.add(
        FulfillmentRequest(
            project_id=other_project.id,
            warehouse_id=other_warehouse.id,
            provider="skladbot",
            external_id="555001",
            kind="assembly",
        )
    )
    await db_session.commit()

    bc_mine = f"BC-{_uid()}"
    _mock_products(monkeypatch, [_item(bc_mine, amount=1)])
    _mock_requests(monkeypatch, {})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    # Чужой сток не удалён полной заменой
    other_rows = await _ff_stocks(db_session, other_project.id, other_warehouse.id)
    assert [r.barcode for r in other_rows] == [bc_other]

    # Выборки не пересекаются
    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    assert [r["barcode"] for r in data["rows"]] == [bc_mine]
    reqs = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, None)
    assert reqs == []

    # Статус подключения изолирован
    status = await fulfillment_service.get_status(db_session, other_project.id, other_warehouse.id)
    assert status["connected"] is False

    # link через чужой project_id не находит заявку
    result = await db_session.execute(
        select(FulfillmentRequest.id).where(
            FulfillmentRequest.project_id == other_project.id,
            FulfillmentRequest.external_id == "555001",
        )
    )
    other_ff_id = result.scalar_one()
    assert await fulfillment_service.unlink_request(db_session, project.id, other_ff_id) is None


# ─── get_request_detail (GET /v1/requests/show/{id}) ─────────────────────────


def _detail_product(barcode, amount=5, accepted=2, repair=1, vendor="V-1", name="Ковер"):
    """skladbot /v1/requests/show/{id} product item."""
    return {
        "id": 1,
        "product_data_id": 1,
        "amount": amount,
        "acceptedAmount": accepted,
        "delivery_amount": 0,
        "repairAmount": repair,
        "recycleAmount": 0,
        "barcode": barcode,
        "vendorCode": vendor,
        "name": name,
        "image": None,
        "color": "белый",
        "services": [],
        "packages": [],
        "size": None,
        "has_components": False,
        "comment": "",
    }


def _detail(products=None, comment=None):
    """skladbot /v1/requests/show/{id} response data."""
    return {
        "id": 2001,
        "delivery_number": "WH-R-2001",
        "executor": "Не назначен",
        "creator": "Тест Тестович",
        "customer": {"id": 6282, "name": "ООО ТЕСТ ФФ"},
        "comment": comment,
        "stage": {"code": "cargo_pickup", "name": "Забор груза", "description": "Едем забирать"},
        "stageLogs": [
            {"stage": "Забор груза", "executor": None, "created_at": "10.06.2026 17:53:35", "spent_time": ""}
        ],
        "fields": [
            {"name": "Маркетплейс", "field": "marketplace", "value": "Wildberries"},
            {"name": "Дата забора", "field": "collection_date", "value": "2026-06-28"},
        ],
        "products": products if products is not None else [],
    }


def _mock_detail(monkeypatch, detail):
    async def fake_fetch_request_detail(self, external_id):
        return detail

    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fake_fetch_request_detail)


async def _mirror_request(db_session, project, warehouse, monkeypatch, req_id=2001):
    """Создать зеркальную ФФ-заявку (kind=assembly) через синк."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(req_id)]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    result = await db_session.execute(
        select(FulfillmentRequest).where(
            FulfillmentRequest.project_id == project.id,
            FulfillmentRequest.external_id == str(req_id),
        )
    )
    return result.scalars().one()


@pytest.mark.asyncio
async def test_request_detail_products_and_nomenclature(db_session, project, warehouse, connected_key, monkeypatch):
    """Деталка: товары нормализуются, известный ШК матчится на номенклатуру, тоталы считаются."""
    bc_known = f"20{_uid()}"
    bc_unknown = f"21{_uid()}"
    await _make_nomenclature(db_session, project.id, bc_known, article="ART-DETAIL")
    mirror = await _mirror_request(db_session, project, warehouse, monkeypatch)
    _mock_detail(
        monkeypatch,
        _detail(products=[_detail_product(bc_known, amount=10, accepted=4), _detail_product(bc_unknown, amount=7)]),
    )

    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, mirror.id)

    assert row is not None
    assert row["number"] == "WH-R-2001"
    assert row["customer_name"] == "ООО ТЕСТ ФФ"
    assert row["executor"] == "Не назначен"
    assert row["stage_description"] == "Едем забирать"
    assert row["total_qty"] == 17
    assert row["total_accepted"] == 6
    by_bc = {p["barcode"]: p for p in row["products"]}
    assert by_bc[bc_known]["article_seller"] == "ART-DETAIL"
    assert by_bc[bc_known]["nomenclature_id"] is not None
    assert by_bc[bc_known]["qty"] == 10 and by_bc[bc_known]["accepted_qty"] == 4
    assert by_bc[bc_unknown]["nomenclature_id"] is None
    assert {f["field"] for f in row["fields"]} == {"marketplace", "collection_date"}
    assert row["stage_logs"][0]["stage"] == "Забор груза"


@pytest.mark.asyncio
async def test_request_detail_unknown_id_returns_none(db_session, project, warehouse, connected_key, monkeypatch):
    _mock_detail(monkeypatch, _detail())
    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, 99999999)
    assert row is None


@pytest.mark.asyncio
async def test_request_detail_requires_connection(db_session, project, warehouse, connected_key, monkeypatch):
    """После disconnect деталка падает ValueError (токена больше нет)."""
    mirror = await _mirror_request(db_session, project, warehouse, monkeypatch)
    await fulfillment_service.disconnect(db_session, project.id, warehouse.id)
    _mock_detail(monkeypatch, _detail())
    with pytest.raises(ValueError, match="не подключён"):
        await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, mirror.id)


@pytest.mark.asyncio
async def test_request_detail_project_isolation(
    db_session, project, other_project, warehouse, other_warehouse, connected_key, monkeypatch
):
    """Чужой проект не видит деталку заявки даже по верному id."""
    mirror = await _mirror_request(db_session, project, warehouse, monkeypatch)
    _mock_detail(monkeypatch, _detail())
    row = await fulfillment_service.get_request_detail(db_session, other_project.id, other_warehouse.id, mirror.id)
    assert row is None


# ─── Request detail: сверка состава со связанным документом ─────────────────


async def _make_linked_assembly(db_session, project, warehouse, mirror, items):
    """Заявка на сборку с составом [(barcode, nomenclature_id, qty)], привязанная к ФФ-заявке."""
    doc = AssemblyRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"A-{_uid()[:6]}",
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
    await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)
    return doc


@pytest.mark.asyncio
async def test_request_detail_match_full(db_session, project, warehouse, connected_key, monkeypatch):
    """Состав ФФ-заявки совпадает с нашей → matched=True, our_qty по строкам."""
    bc_a, bc_b = f"20{_uid()}", f"21{_uid()}"
    nom_a = await _make_nomenclature(db_session, project.id, bc_a, article="ART-A")
    nom_b = await _make_nomenclature(db_session, project.id, bc_b, article="ART-B")
    mirror = await _mirror_request(db_session, project, warehouse, monkeypatch)
    await _make_linked_assembly(db_session, project, warehouse, mirror, [(bc_a, nom_a.id, 10), (bc_b, nom_b.id, 3)])
    _mock_detail(monkeypatch, _detail(products=[_detail_product(bc_a, amount=10), _detail_product(bc_b, amount=3)]))

    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, mirror.id)

    assert row is not None
    match = row["match"]
    assert match["matched"] is True
    assert match["mismatches"] == []
    assert match["ff_total"] == 13 and match["our_total"] == 13
    assert match["ff_positions"] == 2 and match["our_positions"] == 2
    by_bc = {p["barcode"]: p for p in row["products"]}
    assert by_bc[bc_a]["our_qty"] == 10
    assert by_bc[bc_b]["our_qty"] == 3


@pytest.mark.asyncio
async def test_request_detail_match_mismatches_both_directions(
    db_session, project, warehouse, connected_key, monkeypatch
):
    """Расхождения в обе стороны: qty отличается, есть только у ФФ, есть только у нас."""
    bc_qty, bc_ff_only, bc_our_only = f"20{_uid()}", f"21{_uid()}", f"22{_uid()}"
    nom_qty = await _make_nomenclature(db_session, project.id, bc_qty, article="ART-QTY")
    nom_our = await _make_nomenclature(db_session, project.id, bc_our_only, article="ART-OUR")
    mirror = await _mirror_request(db_session, project, warehouse, monkeypatch)
    await _make_linked_assembly(
        db_session, project, warehouse, mirror, [(bc_qty, nom_qty.id, 7), (bc_our_only, nom_our.id, 2)]
    )
    _mock_detail(
        monkeypatch, _detail(products=[_detail_product(bc_qty, amount=10), _detail_product(bc_ff_only, amount=4)])
    )

    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, mirror.id)

    match = row["match"]
    assert match["matched"] is False
    assert match["ff_total"] == 14 and match["our_total"] == 9
    assert match["ff_positions"] == 2 and match["our_positions"] == 2
    by_bc = {m["barcode"]: m for m in match["mismatches"]}
    assert set(by_bc) == {bc_qty, bc_ff_only, bc_our_only}
    assert by_bc[bc_qty]["ff_qty"] == 10 and by_bc[bc_qty]["our_qty"] == 7 and by_bc[bc_qty]["diff"] == 3
    assert by_bc[bc_ff_only]["ff_qty"] == 4 and by_bc[bc_ff_only]["our_qty"] == 0
    assert by_bc[bc_our_only]["ff_qty"] == 0 and by_bc[bc_our_only]["our_qty"] == 2
    assert by_bc[bc_our_only]["article_seller"] == "ART-OUR"  # номенклатура и для our-only строк
    products = {p["barcode"]: p for p in row["products"]}
    assert products[bc_ff_only]["our_qty"] == 0  # связь есть, в нашей заявке нет
    assert products[bc_qty]["our_qty"] == 7


@pytest.mark.asyncio
async def test_request_detail_without_link_has_no_match(db_session, project, warehouse, connected_key, monkeypatch):
    """Без привязки match=None и our_qty=None."""
    bc = f"20{_uid()}"
    mirror = await _mirror_request(db_session, project, warehouse, monkeypatch)
    _mock_detail(monkeypatch, _detail(products=[_detail_product(bc, amount=5)]))

    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, mirror.id)

    assert row["match"] is None
    assert row["products"][0]["our_qty"] is None


@pytest.mark.asyncio
async def test_request_detail_match_inbound(db_session, project, warehouse, connected_key, monkeypatch):
    """Сверка для приёмки: our_qty из expected_qty InboundReceiptItem."""
    bc = f"20{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-IN")
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {852: [_req(3001)]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    result = await db_session.execute(
        select(FulfillmentRequest).where(
            FulfillmentRequest.project_id == project.id,
            FulfillmentRequest.external_id == "3001",
        )
    )
    mirror = result.scalars().one()

    receipt = InboundReceipt(project_id=project.id, warehouse_id=warehouse.id, number=f"IN-{_uid()[:6]}")
    db_session.add(receipt)
    await db_session.commit()
    await db_session.refresh(receipt)
    db_session.add(
        InboundReceiptItem(
            project_id=project.id, receipt_id=receipt.id, nomenclature_id=nom.id, barcode=bc, expected_qty=8
        )
    )
    await db_session.commit()
    await fulfillment_service.link_request(db_session, project.id, mirror.id, inbound_receipt_id=receipt.id)
    _mock_detail(monkeypatch, _detail(products=[_detail_product(bc, amount=8)]))

    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, mirror.id)

    assert row["match"]["matched"] is True
    assert row["match"]["our_total"] == 8
    assert row["products"][0]["our_qty"] == 8


# ─── ФФ-связь для нашей заявки на сборку ─────────────────────────────────────


@pytest.mark.asyncio
async def test_get_ff_link_for_assembly(db_session, project, other_project, warehouse, connected_key, monkeypatch):
    """Обратный lookup: зеркальная ФФ-заявка по id нашей сборки + изоляция проекта."""
    mirror = await _mirror_request(db_session, project, warehouse, monkeypatch)
    doc = await _make_linked_assembly(db_session, project, warehouse, mirror, [])

    link = await fulfillment_service.get_ff_link_for_assembly(db_session, project.id, doc.id)
    assert link is not None
    assert link["ff_request_id"] == mirror.id
    assert link["ff_request_number"] == "WH-R-2001"
    assert link["ff_stage_title"] == "Новая"
    assert link["ff_warehouse_id"] == warehouse.id

    assert await fulfillment_service.get_ff_link_for_assembly(db_session, other_project.id, doc.id) is None
    assert await fulfillment_service.get_ff_link_for_assembly(db_session, project.id, 99999999) is None


# ─── WMS Celicom (wmscelicom) ────────────────────────────────────────────────

WMS_TOKEN = "0fake0fake0fake0fake0fake0fake00"  # noqa: S105 — фейковый 32-hex
WMS_BASE_INPUT = "test-client.wmscelicom.ru"
WMS_BASE = "https://test-client.wmscelicom.ru"


def _wms_item(item_id, barcode, count=0, virtual=0, name="Товар WMS", article="WMS-ART"):
    """wmscelicom items/get item."""
    return {
        "Id": item_id,
        "Name": name,
        "Count": count,
        "CountVirtual": virtual,
        "Article": article,
        "Barcodes": [barcode] if barcode else [],
        "Complect": 0,
    }


def _wms_shipment(sid, status="Новая", external_order=None, date_time="2026-06-01 10:00:00", packages=None):
    """wmscelicom shipmentsfbo/list row (packages — dict {номер: короб})."""
    row = {
        "shipment_fbo_id": sid,
        "date_time": date_time,
        "status": status,
        "laststatus_datetime": date_time,
        "external_order": external_order,
        "shipped_target": "Маркетплейс Склад",
        "dispatch_date": "2026-06-09 00:00:00",
        "user": {"first_name": "Иван", "last_name": "Петров"},
    }
    if packages is not None:
        row["packages"] = packages
    return row


def _wms_unloading(uid, status="Новая", close=None, create="2026-06-02 09:00:00", items=None):
    """wmscelicom unloadingorders/list row."""
    return {
        "unloading_order_id": uid,
        "create_date_time": create,
        "delivery_date_time": "2026-06-05 12:00:00",
        "status": status,
        "unloading_status": None,
        "unloading_close_date": close,
        "items": items or [],
    }


def _mock_wms_connection(monkeypatch, ok=True):
    async def fake_test_connection(self):
        return ok

    monkeypatch.setattr(WmsCelicomClient, "test_connection", fake_test_connection)


def _mock_wms_fetches(monkeypatch, items=(), shipments=(), unloadings=()):
    async def fake_items(self):
        return list(items)

    async def fake_shipments(self):
        return list(shipments)

    async def fake_unloadings(self):
        return list(unloadings)

    monkeypatch.setattr(WmsCelicomClient, "fetch_all_items", fake_items)
    monkeypatch.setattr(WmsCelicomClient, "fetch_shipments_fbo", fake_shipments)
    monkeypatch.setattr(WmsCelicomClient, "fetch_unloading_orders", fake_unloadings)


@pytest_asyncio.fixture
async def connected_wms_key(db_session, project, warehouse, monkeypatch):
    """Подключённый wmscelicom-ключ (test_connection замокан)."""
    _mock_wms_connection(monkeypatch)
    await fulfillment_service.connect(
        db_session, project.id, warehouse.id, "wmscelicom", WMS_TOKEN, base_url=WMS_BASE_INPUT
    )
    return await fulfillment_service.get_integration(db_session, project.id, warehouse.id)


# ─── normalize_base_url ──────────────────────────────────────────────────────


def test_normalize_base_url_variants():
    assert normalize_base_url("client.wmscelicom.ru") == "https://client.wmscelicom.ru"
    assert normalize_base_url(" https://client.wmscelicom.ru/ ") == "https://client.wmscelicom.ru"
    assert normalize_base_url("CLIENT.WMSCELICOM.RU") == "https://client.wmscelicom.ru"


def test_normalize_base_url_rejects_foreign_and_garbage():
    for bad in (
        "",
        "evil.com",
        "https://evil.com",
        "wmscelicom.ru",  # голый корень — не инстанс
        "https://client.wmscelicom.ru/path",
        "https://client.wmscelicom.ru:8080",
        "http://client.wmscelicom.ru",  # plain http
        "https://evil.com/?x=.wmscelicom.ru",
        "https://user@evil.com#.wmscelicom.ru",
    ):
        with pytest.raises(ValueError):
            normalize_base_url(bad)


# ─── Connect (wmscelicom) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_wms_and_status(db_session, project, warehouse, connected_wms_key):
    status = await fulfillment_service.get_status(db_session, project.id, warehouse.id)
    assert status["connected"] is True
    assert status["provider"] == "wmscelicom"
    assert status["key_preview"] == "***" + WMS_TOKEN[-4:]
    assert status["api_base_url"] == WMS_BASE
    assert status["customer_name"] == "test-client.wmscelicom.ru"
    assert status["customer_id"] is None
    assert status["token_expires_at"] is None


@pytest.mark.asyncio
async def test_connect_wms_requires_base_url(db_session, project, warehouse, monkeypatch):
    _mock_wms_connection(monkeypatch)
    with pytest.raises(ValueError, match="адрес"):
        await fulfillment_service.connect(db_session, project.id, warehouse.id, "wmscelicom", WMS_TOKEN)


@pytest.mark.asyncio
async def test_connect_wms_failed_probe_raises(db_session, project, warehouse, monkeypatch):
    _mock_wms_connection(monkeypatch, ok=False)
    with pytest.raises(ValueError, match="WMS Celicom"):
        await fulfillment_service.connect(
            db_session, project.id, warehouse.id, "wmscelicom", WMS_TOKEN, base_url=WMS_BASE_INPUT
        )


@pytest.mark.asyncio
async def test_connect_second_provider_over_active_raises(db_session, project, warehouse, connected_key, monkeypatch):
    """Один склад — один активный провайдер: skladbot уже подключён → wmscelicom отказ."""
    _mock_wms_connection(monkeypatch)
    with pytest.raises(ValueError, match="отключите"):
        await fulfillment_service.connect(
            db_session, project.id, warehouse.id, "wmscelicom", WMS_TOKEN, base_url=WMS_BASE_INPUT
        )


@pytest.mark.asyncio
async def test_connect_unsupported_provider_raises(db_session, project, warehouse):
    with pytest.raises(ValueError, match="провайдер"):
        await fulfillment_service.connect(db_session, project.id, warehouse.id, "nosuchff", WMS_TOKEN)


# ─── Sync (wmscelicom) ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_wms_stocks_mapping(db_session, project, warehouse, connected_wms_key, monkeypatch):
    """Count→good, CountVirtual→nominal, Article→vendor_code, Id→external_product_id, Barcodes[0]."""
    bc_known = f"BC-{_uid()}"
    bc_unknown = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc_known)

    _mock_wms_fetches(
        monkeypatch,
        items=[
            _wms_item(11, bc_known, count=7, virtual=2, article="ART-W1"),
            _wms_item(22, bc_unknown, count=3),
            _wms_item(33, None, count=99),  # без barcode — мимо
        ],
    )

    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["stocks_synced"] == 2
    assert result["unmatched_barcodes"] == 1

    rows = {r.barcode: r for r in await _ff_stocks(db_session, project.id, warehouse.id)}
    row = rows[bc_known]
    assert row.provider == "wmscelicom"
    assert row.qty_good == 7
    assert row.qty_nominal == 2
    assert row.qty_reserve == 0 and row.qty_defect == 0
    assert row.vendor_code == "ART-W1"
    assert row.external_product_id == "11"
    assert row.nomenclature_id == nom.id
    assert rows[bc_unknown].nomenclature_id is None


@pytest.mark.asyncio
async def test_sync_wms_requests_kinds_and_statuses(db_session, project, warehouse, connected_wms_key, monkeypatch):
    _mock_wms_fetches(
        monkeypatch,
        shipments=[
            _wms_shipment(101, status="Новая"),
            _wms_shipment(102, status="Отгружена", external_order="ORD-9921"),
            _wms_shipment(103, status="Принята в СЦ c разногласиями"),
            _wms_shipment(104, status="Аннулирована"),
        ],
        unloadings=[
            _wms_unloading(201, status="Новая"),
            _wms_unloading(202, status="Принята", close="2026-06-07 18:00:00"),
        ],
    )

    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["requests_synced"] == 6

    rows = {r["external_id"]: r for r in await fulfillment_service.list_requests(db_session, project.id, warehouse.id)}
    assert rows["101"]["kind"] == "assembly"
    assert rows["101"]["is_completed"] is False
    assert rows["101"]["type_name"] == "Отгрузка FBO"
    assert rows["101"]["external_created_at"] is not None

    assert rows["102"]["is_completed"] is True
    assert rows["102"]["number"] == "ORD-9921"
    assert rows["103"]["is_completed"] is True
    assert rows["104"]["archived"] is True

    assert rows["201"]["kind"] == "inbound"
    assert rows["201"]["is_completed"] is False
    assert rows["202"]["is_completed"] is True

    assembly_only = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    assert {r["external_id"] for r in assembly_only} == {"101", "102", "103", "104"}


@pytest.mark.asyncio
async def test_sync_wms_upsert_preserves_links(db_session, project, warehouse, connected_wms_key, monkeypatch):
    _mock_wms_fetches(monkeypatch, unloadings=[_wms_unloading(301, status="Новая")])
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = InboundReceipt(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"IR-{_uid()}",
        status="EXPECTED",
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)

    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "inbound")
    ff_id = next(r["id"] for r in rows if r["external_id"] == "301")
    await fulfillment_service.link_request(db_session, project.id, ff_id, inbound_receipt_id=doc.id)

    _mock_wms_fetches(monkeypatch, unloadings=[_wms_unloading(301, status="Принята", close="2026-06-08 10:00:00")])
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "inbound")
    row = next(r for r in rows if r["external_id"] == "301")
    assert row["inbound_receipt_id"] == doc.id  # связь пережила ресинк
    assert row["status"] == "Принята"
    assert row["is_completed"] is True


# ─── Деталка (wmscelicom, из raw) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wms_request_detail_assembly_from_raw(db_session, project, warehouse, connected_wms_key, monkeypatch):
    """Деталка отгрузки FBO: товары агрегируются из коробов, без HTTP-вызова."""
    bc_known = f"BC-{_uid()}"
    bc_unknown = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc_known, article="ART-WMS-D")

    packages = {
        "1": {
            "number": 1,
            "barcode": "WB_1",
            "items": [
                {"name": "Товар А", "barcode": bc_known, "count": 3},
                {"name": "Товар Б", "barcode": bc_unknown, "count": 2},
            ],
        },
        "2": {
            "number": 2,
            "barcode": "WB_2",
            "items": [
                {"name": "Товар А", "barcode": bc_known, "count": 4},
            ],
        },
    }
    _mock_wms_fetches(monkeypatch, shipments=[_wms_shipment(401, status="На сборке", packages=packages)])
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    ff_id = next(r["id"] for r in rows if r["external_id"] == "401")

    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, ff_id)
    assert row is not None
    assert row["total_qty"] == 9
    assert row["creator"] == "Иван Петров"
    by_bc = {p["barcode"]: p for p in row["products"]}
    assert by_bc[bc_known]["qty"] == 7  # 3 + 4 из двух коробов
    assert by_bc[bc_known]["nomenclature_id"] == nom.id
    assert by_bc[bc_known]["article_seller"] == "ART-WMS-D"
    assert by_bc[bc_unknown]["qty"] == 2
    assert by_bc[bc_unknown]["nomenclature_id"] is None
    field_names = {f["name"] for f in row["fields"]}
    assert "Площадка" in field_names
    assert "Коробов" in field_names


@pytest.mark.asyncio
async def test_wms_request_detail_inbound_from_raw(db_session, project, warehouse, connected_wms_key, monkeypatch):
    bc = f"BC-{_uid()}"
    items = [{"item_id": 5, "name": "Товар В", "barcode": bc, "count": 6, "comment": "хрупкое"}]
    _mock_wms_fetches(monkeypatch, unloadings=[_wms_unloading(501, status="Новая", items=items)])
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "inbound")
    ff_id = next(r["id"] for r in rows if r["external_id"] == "501")

    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, ff_id)
    assert row is not None
    assert row["total_qty"] == 6
    assert row["products"][0]["barcode"] == bc
    assert row["products"][0]["comment"] == "хрупкое"
    field_names = {f["name"] for f in row["fields"]}
    assert "Дата поставки" in field_names


@pytest.mark.asyncio
async def test_wms_request_detail_works_without_connection(
    db_session, project, warehouse, connected_wms_key, monkeypatch
):
    """Деталка wmscelicom строится из raw — работает даже после disconnect."""
    _mock_wms_fetches(monkeypatch, unloadings=[_wms_unloading(601, status="Новая")])
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "inbound")
    ff_id = next(r["id"] for r in rows if r["external_id"] == "601")

    await fulfillment_service.disconnect(db_session, project.id, warehouse.id)
    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, ff_id)
    assert row is not None
    assert row["products"] == []


# ─── Классификатор стадий сборки (_assembly_ready_signal) ────────────────────


def test_assembly_ready_signal_skladbot_codes_and_titles():
    sig = fulfillment_service._assembly_ready_signal
    # Стадии 1–2 (WIP) по stage_code → сборка ещё идёт
    assert sig("skladbot", "cargo_pickup", "Забор груза", False) is False
    assert sig("skladbot", "delivery_to_the_marketplace_warehouse", "Указание обьема груза v2", False) is False
    # Fallback по названию (код пуст) — обе орфографии провайдера
    assert sig("skladbot", None, "Забор груза", False) is False
    assert sig("skladbot", None, "Указание обьема груза v2", False) is False
    assert sig("skladbot", None, "Указание объема груза", False) is False
    # Стадия 3+ (логистика) → готов
    assert sig("skladbot", None, "Указание виды работ логистики", False) is True
    assert sig("skladbot", "driver_assignment", "Назначение водителя", False) is True
    # Завершённая → готов независимо от стадии
    assert sig("skladbot", "cargo_pickup", "Забор груза", True) is True
    # Стадия неизвестна → НЕ сигнал (не рискуем ложным READY)
    assert sig("skladbot", None, None, False) is False
    assert sig("skladbot", "", "  ", False) is False


def test_assembly_ready_signal_wmscelicom_only_completed():
    sig = fulfillment_service._assembly_ready_signal
    assert sig("wmscelicom", None, "На сборке", False) is False
    assert sig("wmscelicom", None, "Передана в доставку", False) is False
    assert sig("wmscelicom", None, "Отгружена", True) is True


# ─── total_qty / dest_warehouse: merge-семантика _apply_requests ─────────────


def _norm_row(external_id="7001", total_qty=None, dest_warehouse=None, **over):
    """Нормализованная строка заявки (общий вид после _normalize_*)."""
    row = {
        "external_id": external_id,
        "kind": "assembly",
        "number": f"WH-R-{external_id}",
        "type_id": 851,
        "type_name": "3. Доставка на склад МП",
        "status": "new",
        "stage_code": "cargo_pickup",
        "stage_title": "Забор груза",
        "is_completed": False,
        "archived": False,
        "expired": False,
        "total_qty": total_qty,
        "dest_warehouse": dest_warehouse,
        "external_created_at": None,
        "raw": {},
    }
    row.update(over)
    return row


async def _mirror_row(db_session, project_id, external_id) -> FulfillmentRequest:
    result = await db_session.execute(
        select(FulfillmentRequest).where(
            FulfillmentRequest.project_id == project_id,
            FulfillmentRequest.external_id == str(external_id),
        )
    )
    return result.scalars().one()


@pytest.mark.asyncio
async def test_apply_requests_writes_and_keeps_enrichment(db_session, project, warehouse):
    """INSERT пишет total_qty/dest_warehouse; UPDATE не затирает их None'ом."""
    await fulfillment_service._apply_requests(
        db_session, project.id, warehouse.id, "skladbot", [_norm_row(total_qty=50, dest_warehouse="Москва (Коледино)")]
    )
    await db_session.commit()
    req = await _mirror_row(db_session, project.id, "7001")
    assert req.total_qty == 50
    assert req.dest_warehouse == "Москва (Коледино)"

    # None (деталка не запрашивалась) → старое значение остаётся
    await fulfillment_service._apply_requests(db_session, project.id, warehouse.id, "skladbot", [_norm_row()])
    await db_session.commit()
    req = await _mirror_row(db_session, project.id, "7001")
    assert req.total_qty == 50
    assert req.dest_warehouse == "Москва (Коледино)"

    # Новое не-None → перезапись
    await fulfillment_service._apply_requests(
        db_session, project.id, warehouse.id, "skladbot", [_norm_row(total_qty=60, dest_warehouse="Казань")]
    )
    await db_session.commit()
    req = await _mirror_row(db_session, project.id, "7001")
    assert req.total_qty == 60
    assert req.dest_warehouse == "Казань"


# ─── Обогащение skladbot живой деталкой при синке ────────────────────────────


def _enrich_detail(amounts=(3, 4), mp_value="Москва (Коледино)"):
    """Деталка /v1/requests/show/{id} для обогащения: products + поле «Склад МП»."""
    return {
        "products": [{"amount": a, "barcode": f"B-{i}"} for i, a in enumerate(amounts)],
        "fields": [
            {"name": "Маркетплейс", "field": "marketplace", "value": "Wildberries"},
            {"name": "Склад МП", "field": "marketplace_warehouse", "value": mp_value},
        ],
    }


@pytest.mark.asyncio
async def test_sync_enriches_active_assembly_with_detail(db_session, project, warehouse, connected_key, monkeypatch):
    """Активная assembly-строка получает total_qty/dest_warehouse из деталки; inbound — нет."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(8001)], 852: [_req(8002)]})
    calls: list[str] = []

    async def fake_detail(self, external_id):
        calls.append(str(external_id))
        return _enrich_detail()

    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fake_detail)

    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    assert calls == ["8001"]  # только assembly; inbound деталкой не обогащаем
    row = await _mirror_row(db_session, project.id, "8001")
    assert row.total_qty == 7
    assert row.dest_warehouse == "Москва (Коледино)"
    inbound = await _mirror_row(db_session, project.id, "8002")
    assert inbound.total_qty is None
    assert inbound.dest_warehouse is None

    # Новые поля отдаются наружу (list_requests / _request_to_dict)
    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    assert rows[0]["total_qty"] == 7
    assert rows[0]["dest_warehouse"] == "Москва (Коледино)"


@pytest.mark.asyncio
async def test_sync_detail_backfill_rules(db_session, project, warehouse, connected_key, monkeypatch):
    """Активным — деталка каждый синк; завершённым — только пока total_qty IS NULL."""
    _mock_products(monkeypatch, [])
    calls: list[str] = []

    async def fake_detail(self, external_id):
        calls.append(str(external_id))
        return _enrich_detail(amounts=(5,))

    _mock_requests(monkeypatch, {851: [_req(8101)]})
    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fake_detail)
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert calls == ["8101", "8101"]  # активная — на каждом синке (заявлено меняется)

    # Завершённая с уже известным total_qty → деталка не дёргается
    calls.clear()
    _mock_requests(monkeypatch, {851: [_req(8101, completed=1)]})
    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fake_detail)
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert calls == []

    # Завершённая, но в зеркале total_qty IS NULL → бэкфилл один раз
    req = await _mirror_row(db_session, project.id, "8101")
    req.total_qty = None
    await db_session.commit()
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert calls == ["8101"]
    req = await _mirror_row(db_session, project.id, "8101")
    assert req.total_qty == 5


@pytest.mark.asyncio
async def test_sync_detail_rate_limit_stops_enrichment_not_sync(
    db_session, project, warehouse, connected_key, monkeypatch
):
    """429 на деталке прекращает обогащение, но синк завершается успешно."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(8201), _req(8202)]})
    calls: list[str] = []

    async def fake_detail(self, external_id):
        calls.append(str(external_id))
        raise RateLimitError("429")

    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fake_detail)

    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["requests_synced"] == 2  # синк не упал
    assert len(calls) == 1  # после 429 остальные деталки пропущены
    for ext in ("8201", "8202"):
        row = await _mirror_row(db_session, project.id, ext)
        assert row.total_qty is None


@pytest.mark.asyncio
async def test_sync_detail_api_error_skips_row(db_session, project, warehouse, connected_key, monkeypatch):
    """4xx/мусор на одной деталке пропускает строку, остальные обогащаются."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(8301), _req(8302)]})

    async def fake_detail(self, external_id):
        if str(external_id) == "8301":
            raise SkladbotApiError("not found", status_code=404)
        return _enrich_detail(amounts=(2,))

    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fake_detail)

    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert (await _mirror_row(db_session, project.id, "8301")).total_qty is None
    assert (await _mirror_row(db_session, project.id, "8302")).total_qty == 2


@pytest.mark.asyncio
async def test_sync_detail_garbage_does_not_break_sync(db_session, project, warehouse, connected_key, monkeypatch):
    """PHP-мусор в деталке (amount «12,5»/None, не-dict product, склад >300 симв.) не валит синк."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(8401)]})

    async def fake_detail(self, external_id):
        return {
            "products": [{"amount": "12,5"}, {"amount": None}, "junk", {"amount": "7"}],
            "fields": [{"field": "marketplace_warehouse", "value": "С" * 400}],
        }

    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fake_detail)

    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["requests_synced"] == 1  # синк жив
    row = await _mirror_row(db_session, project.id, "8401")
    assert row.total_qty == 7  # мусорные amount → 0, валидный учтён
    assert row.dest_warehouse == "С" * 300  # кламп под String(300)


@pytest.mark.asyncio
async def test_sync_detail_all_garbage_keeps_previous_total(db_session, project, warehouse, connected_key, monkeypatch):
    """Дрейф формы деталки (всё нечитаемо / data списком) не затирает прежний total_qty нулём."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(8402)]})

    async def good_detail(self, external_id):
        return _enrich_detail(amounts=(5,))

    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", good_detail)
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert (await _mirror_row(db_session, project.id, "8402")).total_qty == 5

    async def broken_detail(self, external_id):
        return {"products": [{"amount": "abc"}], "fields": []}

    _mock_requests(monkeypatch, {851: [_req(8402)]})
    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", broken_detail)
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert (await _mirror_row(db_session, project.id, "8402")).total_qty == 5  # не затёрто нулём

    async def list_detail(self, external_id):
        return ["laravel", "may", "return", "list"]

    _mock_requests(monkeypatch, {851: [_req(8402)]})
    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", list_detail)
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["requests_synced"] == 1  # неожиданная форма — пропуск, не падение
    assert (await _mirror_row(db_session, project.id, "8402")).total_qty == 5


def test_safe_int_php_coercion():
    """'2' → 2, мусор/None/false → 0 — не роняет нормализацию wms/skladbot."""
    assert fulfillment_service._safe_int("2") == 2
    assert fulfillment_service._safe_int(3) == 3
    assert fulfillment_service._safe_int("12,5") == 0
    assert fulfillment_service._safe_int("abc") == 0
    assert fulfillment_service._safe_int(None) == 0
    assert fulfillment_service._safe_int(False) == 0
    assert fulfillment_service._safe_int([1]) == 0


# ─── Авто-READY: стадия 3+ у ФФ переводит связанную сборку в READY ───────────


async def _make_assembly_doc(db_session, project, warehouse, status=AssemblyStatus.IN_PROGRESS.value):
    doc = AssemblyRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"A-{_uid()[:6]}",
        status=status,
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)
    return doc


async def _history_rows(db_session, project_id, doc_id) -> list[AssemblyStatusHistory]:
    result = await db_session.execute(
        select(AssemblyStatusHistory)
        .where(
            AssemblyStatusHistory.project_id == project_id,
            AssemblyStatusHistory.assembly_request_id == doc_id,
        )
        .order_by(AssemblyStatusHistory.id)
        .limit(100)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_sync_marks_linked_assembly_ready(db_session, project, warehouse, connected_key, monkeypatch):
    """Стадии 1–2 не триггерят; стадия 3+ переводит связанную сборку IN_PROGRESS → READY."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(9001, stage_title="Забор груза", stage_code="cargo_pickup")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = await _make_assembly_doc(db_session, project, warehouse)
    mirror = await _mirror_row(db_session, project.id, "9001")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)

    # Стадия 2 («объём груза») — сборка ещё идёт
    _mock_requests(monkeypatch, {851: [_req(9001, stage_title="Указание обьема груза v2")]})
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_marked_ready"] == 0
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.IN_PROGRESS.value

    # Стадия логистики → READY + история + actual_ready_date
    _mock_requests(monkeypatch, {851: [_req(9001, stage_title="Назначение водителя")]})
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_marked_ready"] == 1
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.READY.value
    assert doc.actual_ready_date == date.today()

    history = await _history_rows(db_session, project.id, doc.id)
    assert len(history) == 1
    assert history[0].old_status == AssemblyStatus.IN_PROGRESS.value
    assert history[0].new_status == AssemblyStatus.READY.value
    assert history[0].changed_by == "ff_sync"
    assert "WH-R-9001" in (history[0].comment or "")

    # Повторный синк идемпотентен: заявка уже READY
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_marked_ready"] == 0
    history = await _history_rows(db_session, project.id, doc.id)
    assert len(history) == 1


@pytest.mark.asyncio
async def test_sync_auto_ready_negative_cases(db_session, project, warehouse, connected_key, monkeypatch):
    """VEHICLE_ASSIGNED и несвязанные не трогаются; archived ФФ не триггерит."""
    _mock_products(monkeypatch, [])
    _mock_requests(
        monkeypatch,
        {
            851: [
                _req(9101, stage_title="Назначение водителя"),  # привязана к VEHICLE_ASSIGNED
                _req(9102, stage_title="Назначение водителя"),  # не привязана
                _req(9103, stage_title="Назначение водителя", archived=1),  # archived → мимо
            ]
        },
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc_vehicle = await _make_assembly_doc(db_session, project, warehouse, status=AssemblyStatus.VEHICLE_ASSIGNED.value)
    doc_archived_ff = await _make_assembly_doc(db_session, project, warehouse)
    m1 = await _mirror_row(db_session, project.id, "9101")
    m3 = await _mirror_row(db_session, project.id, "9103")
    await fulfillment_service.link_request(db_session, project.id, m1.id, assembly_request_id=doc_vehicle.id)
    await fulfillment_service.link_request(db_session, project.id, m3.id, assembly_request_id=doc_archived_ff.id)

    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_marked_ready"] == 0
    await db_session.refresh(doc_vehicle)
    await db_session.refresh(doc_archived_ff)
    assert doc_vehicle.status == AssemblyStatus.VEHICLE_ASSIGNED.value
    assert doc_archived_ff.status == AssemblyStatus.IN_PROGRESS.value
    assert await _history_rows(db_session, project.id, doc_vehicle.id) == []
    assert await _history_rows(db_session, project.id, doc_archived_ff.id) == []


@pytest.mark.asyncio
async def test_link_request_with_ready_stage_marks_ready(db_session, project, warehouse, connected_key, monkeypatch):
    """Привязка к ФФ-заявке на готовой стадии сразу переводит сборку IN_PROGRESS → READY."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(9201, stage_title="Погрузка")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = await _make_assembly_doc(db_session, project, warehouse)
    mirror = await _mirror_row(db_session, project.id, "9201")
    row = await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)

    assert row["linked_status"] == AssemblyStatus.READY.value  # UI сразу видит новый статус
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.READY.value
    assert doc.actual_ready_date == date.today()
    history = await _history_rows(db_session, project.id, doc.id)
    assert [h.changed_by for h in history] == ["ff_sync"]
    assert history[0].new_status == AssemblyStatus.READY.value


@pytest.mark.asyncio
async def test_link_request_wip_stage_keeps_in_progress(db_session, project, warehouse, connected_key, monkeypatch):
    """Привязка на WIP-стадии статус сборки не меняет."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(9301, stage_title="Забор груза", stage_code="cargo_pickup")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = await _make_assembly_doc(db_session, project, warehouse)
    mirror = await _mirror_row(db_session, project.id, "9301")
    row = await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)

    assert row["linked_status"] == AssemblyStatus.IN_PROGRESS.value
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.IN_PROGRESS.value
    assert await _history_rows(db_session, project.id, doc.id) == []


# ─── wmscelicom: total_qty / dest_warehouse из списочных методов ─────────────


@pytest.mark.asyncio
async def test_sync_wms_enrichment_fields(db_session, project, warehouse, connected_wms_key, monkeypatch):
    """total_qty из packages.items / items (PHP-коэрсия), dest_warehouse из shipped_target."""
    packages = {
        "1": {"number": 1, "items": [{"barcode": "BC-1", "count": 3}, {"barcode": "BC-2", "count": 2}]},
        "2": {"number": 2, "items": [{"barcode": "BC-1", "count": 4}]},
    }
    _mock_wms_fetches(
        monkeypatch,
        shipments=[
            _wms_shipment(701, packages=packages),
            _wms_shipment(702),  # packages нет (ограничение провайдера) → нет данных
        ],
        unloadings=[
            _wms_unloading(801, items=[{"barcode": "BC-3", "count": 6}, None, {"barcode": "BC-4", "count": "2"}]),
        ],
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    shipment = await _mirror_row(db_session, project.id, "701")
    assert shipment.total_qty == 9
    assert shipment.dest_warehouse == "Маркетплейс Склад"
    empty = await _mirror_row(db_session, project.id, "702")
    assert empty.total_qty is None  # пустой состав = нет данных, не 0
    assert empty.dest_warehouse == "Маркетплейс Склад"
    unloading = await _mirror_row(db_session, project.id, "801")
    assert unloading.total_qty == 8  # null-элемент пропущен, "2" скоэрсирован
    assert unloading.dest_warehouse is None


# ─── migfull («Натали», migfull.app) ─────────────────────────────────────────

MIG_TOKEN = "0fake0fake0fake0fake0fake0fake00"  # noqa: S105 — фейковый ≥20 симв.
MIG_GUID = "11111111-2222-3333-4444-555555555555"


def _mig_guid(n: int) -> str:
    """Детерминированный UUID-подобный guid товара/заявки для тестов."""
    return f"{n:08x}-0000-4000-8000-{n:012x}"


def _mig_product(guid, name="Товар Натали", actual=0, locked=0, available=0, sku=None):
    """migfull /products row (штрихкодов в списке нет — только в карточке)."""
    return {
        "guid": guid,
        "name": name,
        "sku": sku,
        "gtin": None,
        "size": "M",
        "color": "красный",
        "stock_actual": actual,
        "stock_locked": locked,
        "stock_available": available,
    }


def _mig_line(product_guid, qty, name="Товар Натали", defective=False):
    """Строка заявки migfull (planned/shipped/incoming/received)."""
    return {
        "product_guid": product_guid,
        "quantity": qty,
        "is_defective": defective,
        "product": {"guid": product_guid, "name": name, "size": "M", "color": "красный"},
    }


def _mig_shipment(
    guid,
    status="uploaded",
    display=None,
    reference="PVB-0000100",
    planned_total=0,
    dest="ВБ | МО Коледино",
    created="2026-06-01T10:00:00.000000Z",
    planned_lines=None,
    shipped_lines=None,
):
    """migfull /shipments row (planned/shipped_lines приходят в списке целиком)."""
    return {
        "guid": guid,
        "reference": reference,
        "client_shipment_number": "коледино ковры",
        "shipment_date": "2026-06-11",
        "shipment_forecast": "2026-06-11",
        "status": status,
        "status_display": display or status,
        "shipment_type": "fbo",
        "shipment_type_display": "FBO",
        "marketplace": {"name": "Вайлдберис"},
        "destination_marketplace": {"name": dest} if dest else None,
        "containers_count": 3,
        "pallets_count": 0,
        "planned_quantity_total": planned_total,
        "shipped_quantity_total": 0,
        "planned_lines": planned_lines or [],
        "shipped_lines": shipped_lines or [],
        "created_at": created,
        "notes": None,
        "processor": None,
    }


def _mig_submission(
    guid,
    status="processing",
    display=None,
    reference="PVB-0000050",
    sub_date="2026-06-02",
    created="2026-06-01T09:00:00.000000Z",
):
    """migfull /submissions row (состава в списке нет — только lines-эндпоинты)."""
    return {
        "guid": guid,
        "reference": reference,
        "client_reference": "поставка ковров",
        "submission_date": sub_date,
        "status": status,
        "status_display": display or status,
        "submission_lines_count": 2,
        "created_at": created,
        "notes": None,
        "client_comment": None,
        "processor": None,
    }


def _mock_mig_connection(monkeypatch, ok=True):
    async def fake_test_connection(self):
        return ok

    monkeypatch.setattr(MigfullClient, "test_connection", fake_test_connection)


def _mock_mig_fetches(
    monkeypatch,
    products=(),
    shipments=(),
    submissions=(),
    product_details=None,
    submission_lines=None,
    calls=None,
):
    """Замокать ВСЕ fetch-методы MigfullClient (синк зовёт каждый из них).

    product_details: {guid: detail-dict с barcodes[]}; submission_lines:
    {(guid, line_type): rows}. calls — счётчик вызовов per-метод для ассертов.
    """
    counters = calls if calls is not None else {}
    counters.setdefault("product", 0)
    counters.setdefault("submission_lines", 0)

    async def fake_products(self):
        return list(products)

    async def fake_shipments(self):
        return list(shipments)

    async def fake_submissions(self):
        return list(submissions)

    async def fake_product(self, guid):
        counters["product"] += 1
        return (product_details or {}).get(guid, {})

    async def fake_submission_lines(self, guid, line_type):
        counters["submission_lines"] += 1
        return (submission_lines or {}).get((guid, line_type), [])

    monkeypatch.setattr(MigfullClient, "fetch_all_products", fake_products)
    monkeypatch.setattr(MigfullClient, "fetch_shipments", fake_shipments)
    monkeypatch.setattr(MigfullClient, "fetch_submissions", fake_submissions)
    monkeypatch.setattr(MigfullClient, "fetch_product", fake_product)
    monkeypatch.setattr(MigfullClient, "fetch_submission_lines", fake_submission_lines)


@pytest_asyncio.fixture
async def connected_mig_key(db_session, project, warehouse, monkeypatch):
    """Подключённый migfull-ключ (test_connection замокан)."""
    _mock_mig_connection(monkeypatch)
    await fulfillment_service.connect(db_session, project.id, warehouse.id, "migfull", MIG_TOKEN, tenant_guid=MIG_GUID)
    return await fulfillment_service.get_integration(db_session, project.id, warehouse.id)


# ─── normalize_tenant_guid ───────────────────────────────────────────────────


def test_normalize_tenant_guid_variants():
    assert normalize_tenant_guid(MIG_GUID) == MIG_GUID
    assert normalize_tenant_guid(f"  {MIG_GUID.upper()}  ") == MIG_GUID


def test_normalize_tenant_guid_rejects_garbage():
    for bad in ("", "not-a-uuid", "8150beac", f"{MIG_GUID}/../admin", f"{MIG_GUID}x"):
        with pytest.raises(ValueError):
            normalize_tenant_guid(bad)


# ─── Connect (migfull) ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_migfull_and_status(db_session, project, warehouse, connected_mig_key):
    status = await fulfillment_service.get_status(db_session, project.id, warehouse.id)
    assert status["connected"] is True
    assert status["provider"] == "migfull"
    assert status["key_preview"] == "***" + MIG_TOKEN[-4:]
    assert status["tenant_guid"] == MIG_GUID
    assert status["api_base_url"] is None
    assert "migfull.app" in status["customer_name"]
    assert status["token_expires_at"] is None


@pytest.mark.asyncio
async def test_connect_migfull_requires_tenant_guid(db_session, project, warehouse, monkeypatch):
    _mock_mig_connection(monkeypatch)
    with pytest.raises(ValueError, match="GUID"):
        await fulfillment_service.connect(db_session, project.id, warehouse.id, "migfull", MIG_TOKEN)


@pytest.mark.asyncio
async def test_connect_migfull_failed_probe_raises(db_session, project, warehouse, monkeypatch):
    _mock_mig_connection(monkeypatch, ok=False)
    with pytest.raises(ValueError, match=r"migfull\.app"):
        await fulfillment_service.connect(
            db_session, project.id, warehouse.id, "migfull", MIG_TOKEN, tenant_guid=MIG_GUID
        )


# ─── Sync (migfull): остатки и barcode-кэш ───────────────────────────────────


@pytest.mark.asyncio
async def test_sync_migfull_stocks_barcode_resolution(db_session, project, warehouse, connected_mig_key, monkeypatch):
    """Штрихкоды только в карточке: деталка зовётся для товаров с остатком,
    primary-штрихкод приоритетен, служебные позиции и нерезолвленные — мимо."""
    bc_known = f"BC-{_uid()}"
    bc_primary = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc_known)

    p1, p2, p3, p4 = _mig_guid(1), _mig_guid(2), _mig_guid(3), _mig_guid(4)
    calls: dict = {}
    _mock_mig_fetches(
        monkeypatch,
        products=[
            _mig_product(p1, actual=7, locked=2, available=5, sku="SKU-1"),
            _mig_product(p2, actual=3),
            _mig_product(p3),  # без остатка — деталка не зовётся, строки нет
            _mig_product(p4, name="ФФ грузовое место - короб 60х40х40", actual=44),  # служебная
        ],
        product_details={
            p1: {"barcodes": [{"value": bc_known, "is_primary": True}]},
            p2: {
                "barcodes": [{"value": "BC-SECONDARY", "is_primary": False}, {"value": bc_primary, "is_primary": True}]
            },
        },
        calls=calls,
    )

    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["stocks_synced"] == 2
    assert calls["product"] == 2  # только p1 и p2: с остатком и без кэша

    rows = {r.barcode: r for r in await _ff_stocks(db_session, project.id, warehouse.id)}
    row = rows[bc_known]
    assert row.provider == "migfull"
    assert row.qty_good == 7
    assert row.qty_reserve == 2
    assert row.qty_nominal == 5
    assert row.qty_defect == 0
    assert row.vendor_code == "SKU-1"
    assert row.external_product_id == p1
    assert row.nomenclature_id == nom.id
    assert rows[bc_primary].nomenclature_id is None  # primary выигрывает у secondary


@pytest.mark.asyncio
async def test_sync_migfull_barcode_cache_persists(db_session, project, warehouse, connected_mig_key, monkeypatch):
    """Второй синк берёт guid→barcode из прошлого снапшота — деталка не зовётся."""
    bc = f"BC-{_uid()}"
    p1 = _mig_guid(11)
    calls: dict = {}
    _mock_mig_fetches(
        monkeypatch,
        products=[_mig_product(p1, actual=5)],
        product_details={p1: {"barcodes": [{"value": bc, "is_primary": True}]}},
        calls=calls,
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert calls["product"] == 1

    calls2: dict = {}
    _mock_mig_fetches(monkeypatch, products=[_mig_product(p1, actual=6)], calls=calls2)
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert calls2["product"] == 0  # кэш из зеркала, деталек нет

    rows = {r.barcode: r for r in await _ff_stocks(db_session, project.id, warehouse.id)}
    assert rows[bc].qty_good == 6


# ─── Sync (migfull): заявки ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_migfull_requests_kinds_and_statuses(db_session, project, warehouse, connected_mig_key, monkeypatch):
    s1, s2, s3, s4 = _mig_guid(21), _mig_guid(22), _mig_guid(23), _mig_guid(24)
    u1, u2 = _mig_guid(31), _mig_guid(32)
    _mock_mig_fetches(
        monkeypatch,
        shipments=[
            _mig_shipment(s1, status="uploaded", display="Загружен", planned_total=249, reference="PVB-0000217"),
            _mig_shipment(s2, status="ready", display="Собран"),
            _mig_shipment(s3, status="closed", display="Закрыт"),
            _mig_shipment(s4, status="canceled", display="Отменён"),
        ],
        submissions=[
            _mig_submission(u1, status="processing", display="В обработке", reference="PVB-0000105"),
            _mig_submission(u2, status="closed", display="Закрыт"),
        ],
    )

    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["requests_synced"] == 6

    rows = {r["external_id"]: r for r in await fulfillment_service.list_requests(db_session, project.id, warehouse.id)}
    assert rows[s1]["kind"] == "assembly"
    assert rows[s1]["number"] == "PVB-0000217"
    assert rows[s1]["type_name"] == "Отгрузка FBO"
    assert rows[s1]["status"] == "Загружен"
    assert rows[s1]["stage_code"] == "uploaded"
    assert rows[s1]["total_qty"] == 249
    assert rows[s1]["dest_warehouse"] == "ВБ | МО Коледино"
    assert rows[s1]["is_completed"] is False
    assert rows[s1]["external_created_at"] == date(2026, 6, 1)

    assert rows[s2]["is_completed"] is False
    assert rows[s2]["stage_code"] == "ready"
    assert rows[s3]["is_completed"] is True
    assert rows[s4]["archived"] is True

    assert rows[u1]["kind"] == "inbound"
    assert rows[u1]["type_name"] == "Приёмка"
    assert rows[u1]["status"] == "В обработке"
    assert rows[u1]["external_created_at"] == date(2026, 6, 2)  # submission_date
    assert rows[u2]["is_completed"] is True


@pytest.mark.asyncio
async def test_sync_migfull_submission_enrichment(db_session, project, warehouse, connected_mig_key, monkeypatch):
    """Активные приёмки получают total_qty из lines/incoming (служебные позиции
    не считаются); закрытые — разовый бэкфилл, второй синк их не дёргает;
    отменённые не бэкфиллятся вовсе (вечный NULL съедал бы cap)."""
    active, closed, canceled = _mig_guid(41), _mig_guid(42), _mig_guid(43)
    svc = _mig_line(_mig_guid(99), 100, name="ФФ грузовое место - товар россыпью")
    lines = {
        (active, "incoming"): [_mig_line(_mig_guid(51), 3), _mig_line(_mig_guid(52), 4), svc],
        (closed, "incoming"): [_mig_line(_mig_guid(51), 14)],
    }
    calls: dict = {}
    _mock_mig_fetches(
        monkeypatch,
        submissions=[
            _mig_submission(active, status="processing"),
            _mig_submission(closed, status="closed"),
            _mig_submission(canceled, status="canceled"),
        ],
        submission_lines=lines,
        calls=calls,
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert calls["submission_lines"] == 2  # active + бэкфилл closed; canceled — мимо

    assert (await _mirror_row(db_session, project.id, active)).total_qty == 7
    assert (await _mirror_row(db_session, project.id, closed)).total_qty == 14

    calls2: dict = {}
    _mock_mig_fetches(
        monkeypatch,
        submissions=[
            _mig_submission(active, status="processing"),
            _mig_submission(closed, status="closed"),
        ],
        submission_lines=lines,
        calls=calls2,
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert calls2["submission_lines"] == 1  # только активная: closed уже обогащена


@pytest.mark.asyncio
async def test_sync_migfull_enrich_rate_limit_does_not_fail_sync(
    db_session, project, warehouse, connected_mig_key, monkeypatch
):
    sub = _mig_guid(61)
    _mock_mig_fetches(monkeypatch, submissions=[_mig_submission(sub, status="processing")])

    async def fake_lines(self, guid, line_type):
        raise RateLimitError("429", retry_after=60)

    monkeypatch.setattr(MigfullClient, "fetch_submission_lines", fake_lines)

    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["requests_synced"] == 1
    assert (await _mirror_row(db_session, project.id, sub)).total_qty is None


# ─── Деталка (migfull) ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migfull_request_detail_assembly_from_raw(db_session, project, warehouse, connected_mig_key, monkeypatch):
    """Деталка отгрузки из raw зеркала (planned/shipped_lines), штрихкоды —
    из снапшота остатков; работает даже после disconnect."""
    bc = f"BC-{_uid()}"
    p1, p2 = _mig_guid(71), _mig_guid(72)
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-MIG-D")

    ship = _mig_shipment(
        _mig_guid(70),
        status="ready",
        display="Собран",
        planned_total=9,
        planned_lines=[
            _mig_line(p1, 3, name="Ковер розовый"),
            _mig_line(p1, 4, name="Ковер розовый"),
            _mig_line(p2, 2),
        ],
        shipped_lines=[_mig_line(p1, 5)],
    )
    _mock_mig_fetches(
        monkeypatch,
        products=[_mig_product(p1, actual=7), _mig_product(p2, actual=2)],
        product_details={
            p1: {"barcodes": [{"value": bc, "is_primary": True}]},
            # p2 — карточка без штрихкодов: позиция в деталке остаётся без barcode
        },
        shipments=[ship],
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    await fulfillment_service.disconnect(db_session, project.id, warehouse.id)

    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    ff_id = next(r["id"] for r in rows if r["external_id"] == _mig_guid(70))
    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, ff_id)
    assert row is not None
    assert row["total_qty"] == 9
    by_bc = {p["barcode"]: p for p in row["products"]}
    assert by_bc[bc]["qty"] == 7  # 3 + 4 агрегированы по товару
    assert by_bc[bc]["delivery_qty"] == 5
    assert by_bc[bc]["nomenclature_id"] == nom.id
    assert by_bc[bc]["article_seller"] == "ART-MIG-D"
    assert by_bc[None]["qty"] == 2  # p2 без штрихкода
    field_names = {f["name"] for f in row["fields"]}
    assert "Склад МП" in field_names
    assert "Коробов" in field_names


@pytest.mark.asyncio
async def test_migfull_request_detail_inbound_live_lines(
    db_session, project, warehouse, connected_mig_key, monkeypatch
):
    """Деталка приёмки — живые lines/incoming + received (с браком)."""
    bc = f"BC-{_uid()}"
    p1 = _mig_guid(81)
    sub = _mig_guid(80)
    lines = {
        (sub, "incoming"): [_mig_line(p1, 10)],
        (sub, "received"): [_mig_line(p1, 8), _mig_line(p1, 1, defective=True)],
    }
    _mock_mig_fetches(
        monkeypatch,
        products=[_mig_product(p1, actual=8)],
        product_details={p1: {"barcodes": [{"value": bc, "is_primary": True}]}},
        submissions=[_mig_submission(sub, status="processing")],
        submission_lines=lines,
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "inbound")
    ff_id = next(r["id"] for r in rows if r["external_id"] == sub)
    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, ff_id)
    assert row is not None
    assert row["total_qty"] == 10
    assert row["total_accepted"] == 8
    product = row["products"][0]
    assert product["barcode"] == bc
    assert product["qty"] == 10
    assert product["accepted_qty"] == 8
    assert product["defect_qty"] == 1
    field_names = {f["name"] for f in row["fields"]}
    assert "Дата поставки" in field_names

    # Приёмке нужен живой клиент: после disconnect — понятная ошибка
    await fulfillment_service.disconnect(db_session, project.id, warehouse.id)
    with pytest.raises(ValueError, match="не подключён"):
        await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, ff_id)


# ─── Авто-READY и классификатор (migfull) ────────────────────────────────────


def test_assembly_ready_signal_migfull():
    sig = fulfillment_service._assembly_ready_signal
    assert sig("migfull", "uploaded", "Загружен", False) is False
    assert sig("migfull", "ready", "Собран", False) is True
    assert sig("migfull", "closed", "Закрыт", True) is True  # is_completed
    assert sig("migfull", None, None, False) is False


@pytest.mark.asyncio
async def test_sync_migfull_marks_linked_assembly_ready(db_session, project, warehouse, connected_mig_key, monkeypatch):
    """uploaded не триггерит; ready переводит связанную сборку IN_PROGRESS → READY."""
    ship_guid = _mig_guid(91)
    _mock_mig_fetches(monkeypatch, shipments=[_mig_shipment(ship_guid, status="uploaded", display="Загружен")])
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = await _make_assembly_doc(db_session, project, warehouse)
    mirror = await _mirror_row(db_session, project.id, ship_guid)
    await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.IN_PROGRESS.value  # uploaded — не сигнал

    _mock_mig_fetches(monkeypatch, shipments=[_mig_shipment(ship_guid, status="ready", display="Собран")])
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_marked_ready"] == 1
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.READY.value
    assert doc.actual_ready_date == date.today()

    history = await _history_rows(db_session, project.id, doc.id)
    assert len(history) == 1
    assert history[0].changed_by == "ff_sync"


# ─── Status history (журнал смены статусов синком) ───────────────────────────


async def _status_events(db_session, project_id, warehouse_id) -> list[FulfillmentStatusEvent]:
    """Все события истории склада в порядке вставки (id asc)."""
    result = await db_session.execute(
        select(FulfillmentStatusEvent)
        .where(
            FulfillmentStatusEvent.project_id == project_id,
            FulfillmentStatusEvent.warehouse_id == warehouse_id,
        )
        .order_by(FulfillmentStatusEvent.id)
        .limit(100)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_status_history_created_on_first_sync(db_session, project, warehouse, connected_key, monkeypatch):
    """Первый синк → событие `created` на каждую заявку (assembly + inbound)."""
    _mock_products(monkeypatch, [])
    _mock_requests(
        monkeypatch,
        {
            851: [_req(7001, status="new", stage_title="Новая", stage_code="cargo_pickup")],
            852: [_req(7002, status="new", stage_title="Создана")],
        },
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    events = await _status_events(db_session, project.id, warehouse.id)
    assert len(events) == 2
    by_ext = {e.external_id: e for e in events}
    assert by_ext["7001"].event_type == "created"
    assert by_ext["7001"].kind == "assembly"
    assert by_ext["7001"].old_status is None  # появление — старого состояния нет
    assert by_ext["7001"].new_status == "new"
    assert by_ext["7001"].new_stage_title == "Новая"
    assert by_ext["7001"].new_stage_code == "cargo_pickup"
    assert by_ext["7001"].new_is_completed is False
    assert by_ext["7002"].kind == "inbound"
    assert by_ext["7002"].new_stage_title == "Создана"


@pytest.mark.asyncio
async def test_status_history_changed_on_stage_transition(db_session, project, warehouse, connected_key, monkeypatch):
    """Смена стадии при ресинке → событие `changed` со старым и новым значением."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(7101, status="new", stage_title="Забор груза", stage_code="cargo_pickup")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    _mock_requests(
        monkeypatch, {851: [_req(7101, status="work", stage_title="Назначен водитель", stage_code="driver")]}
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    events = await _status_events(db_session, project.id, warehouse.id)
    assert len(events) == 2  # created + changed
    changed = events[1]
    assert changed.event_type == "changed"
    assert changed.old_stage_title == "Забор груза"
    assert changed.new_stage_title == "Назначен водитель"
    assert changed.old_status == "new"
    assert changed.new_status == "work"


@pytest.mark.asyncio
async def test_status_history_no_event_on_unchanged_resync(db_session, project, warehouse, connected_key, monkeypatch):
    """Ресинк без смены статуса (меняются лишь synced_at/raw) → новых событий нет."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(7201, status="new", stage_title="Новая", stage_code="cargo_pickup")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    events = await _status_events(db_session, project.id, warehouse.id)
    assert len(events) == 1  # только `created`, повторы статус не меняли
    assert events[0].event_type == "created"


@pytest.mark.asyncio
async def test_status_history_completed_flag_transition(db_session, project, warehouse, connected_key, monkeypatch):
    """Завершение заявки фиксируется сменой флага is_completed."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(7301, status="work", stage_title="В работе")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    _mock_requests(monkeypatch, {851: [_req(7301, status="done", stage_title="Завершена", completed=1)]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    events = await _status_events(db_session, project.id, warehouse.id)
    assert len(events) == 2
    changed = events[1]
    assert changed.old_is_completed is False
    assert changed.new_is_completed is True
    assert changed.new_stage_title == "Завершена"


@pytest.mark.asyncio
async def test_status_history_archived_flag_transition(db_session, project, warehouse, connected_key, monkeypatch):
    """Уход заявки в архив провайдера фиксируется сменой флага archived."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(7351, status="work", stage_title="В работе", archived=0)]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    _mock_requests(monkeypatch, {851: [_req(7351, status="work", stage_title="В работе", archived=1)]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    events = await _status_events(db_session, project.id, warehouse.id)
    assert len(events) == 2
    changed = events[1]
    assert changed.event_type == "changed"
    assert changed.old_archived is False
    assert changed.new_archived is True


@pytest.mark.asyncio
async def test_list_status_events_ordering_and_filters(db_session, project, warehouse, connected_key, monkeypatch):
    """list_status_events: новые сверху + фильтры по kind и ff_request_id."""
    _mock_products(monkeypatch, [])
    _mock_requests(
        monkeypatch,
        {
            851: [_req(7401, status="new", stage_title="Новая")],
            852: [_req(7402, status="new", stage_title="Создана")],
        },
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    # сменим стадию только у сборки
    _mock_requests(
        monkeypatch,
        {
            851: [_req(7401, status="work", stage_title="В работе")],
            852: [_req(7402, status="new", stage_title="Создана")],
        },
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    all_events = await fulfillment_service.list_status_events(db_session, project.id, warehouse.id)
    assert len(all_events) == 3  # 2 created + 1 changed
    # Новые сверху: changed-событие (последний синк) первое
    assert all_events[0]["event_type"] == "changed"
    assert all_events[0]["external_id"] == "7401"

    assembly_only = await fulfillment_service.list_status_events(db_session, project.id, warehouse.id, kind="assembly")
    assert {e["external_id"] for e in assembly_only} == {"7401"}
    assert len(assembly_only) == 2  # created + changed

    ff_id = all_events[0]["fulfillment_request_id"]
    one_request = await fulfillment_service.list_status_events(
        db_session, project.id, warehouse.id, ff_request_id=ff_id
    )
    assert len(one_request) == 2
    assert all(e["fulfillment_request_id"] == ff_id for e in one_request)


@pytest.mark.asyncio
async def test_list_status_events_project_isolation(
    db_session, project, warehouse, connected_key, monkeypatch, other_project
):
    """Журнал чужого проекта не виден (фильтр project_id)."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(7501, status="new", stage_title="Новая")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    assert await fulfillment_service.list_status_events(db_session, project.id, warehouse.id)  # есть
    assert await fulfillment_service.list_status_events(db_session, other_project.id, warehouse.id) == []


# ─── Журнал прогонов синка (sync_log → FfSyncRun) ────────────────────────────


async def _add_sync_log(
    db_session,
    integration_id,
    *,
    service="skladbot",
    sync_type="fulfillment",
    status="OK",
    started_at,
    finished_at=None,
    rows_fetched=0,
    rows_inserted=0,
    error_msg=None,
) -> SyncLog:
    """Прямая вставка строки sync_log (минуя сервис) для проверки маппинга/порядка."""
    log = SyncLog(
        integration_id=integration_id,
        service=service,
        sync_type=sync_type,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        rows_fetched=rows_fetched,
        rows_inserted=rows_inserted,
        error_msg=error_msg,
    )
    db_session.add(log)
    await db_session.commit()
    await db_session.refresh(log)
    return log


@pytest.mark.asyncio
async def test_list_sync_runs_maps_and_orders(db_session, project, warehouse, connected_key):
    """list_sync_runs: новые сверху, корректный маппинг полей, чужой sync_type исключён."""
    base = datetime(2026, 6, 15, 12, 0, 0)

    # Самый старый OK-прогон (stocks=5, requests=8-5=3, длительность 30с)
    await _add_sync_log(
        db_session,
        connected_key.id,
        status="OK",
        started_at=base,
        finished_at=base + timedelta(seconds=30),
        rows_fetched=8,
        rows_inserted=5,
    )
    # Средний — ERROR с текстом, ещё не закрыт (finished_at IS NULL)
    await _add_sync_log(
        db_session,
        connected_key.id,
        status="ERROR",
        started_at=base + timedelta(minutes=10),
        finished_at=None,
        rows_fetched=0,
        rows_inserted=0,
        error_msg="boom upstream",
    )
    # Самый новый OK-прогон (rows_fetched < rows_inserted → requests кламп в 0)
    await _add_sync_log(
        db_session,
        connected_key.id,
        status="OK",
        started_at=base + timedelta(minutes=20),
        finished_at=base + timedelta(minutes=20, seconds=12),
        rows_fetched=4,
        rows_inserted=4,
    )
    # Чужой sync_type под тем же ключом — в журнал ФФ попадать не должен
    await _add_sync_log(
        db_session,
        connected_key.id,
        sync_type="stocks",
        status="OK",
        started_at=base + timedelta(minutes=30),
        finished_at=base + timedelta(minutes=30, seconds=5),
        rows_fetched=99,
        rows_inserted=99,
    )

    runs = await fulfillment_service.list_sync_runs(db_session, project.id, warehouse.id)
    assert len(runs) == 3  # «stocks» исключён фильтром sync_type

    # Новые сверху: 20мин → 10мин → 0мин
    newest, middle, oldest = runs
    assert newest["status"] == "OK"
    assert newest["stocks_synced"] == 4
    assert newest["requests_synced"] == 0  # max(4 - 4, 0)
    assert newest["duration_seconds"] == 12
    assert newest["error_msg"] is None

    assert middle["status"] == "ERROR"
    assert middle["error_msg"] == "boom upstream"
    assert middle["duration_seconds"] is None  # finished_at IS NULL
    assert middle["stocks_synced"] == 0
    assert middle["requests_synced"] == 0

    assert oldest["status"] == "OK"
    assert oldest["stocks_synced"] == 5
    assert oldest["requests_synced"] == 3  # 8 - 5
    assert oldest["duration_seconds"] is not None and oldest["duration_seconds"] > 0
    assert oldest["service"] == "skladbot"


@pytest.mark.asyncio
async def test_list_sync_runs_project_isolation(db_session, project, other_project, warehouse, connected_key):
    """Прогон под ключом проекта A не виден из проекта B (изоляция через integration_keys)."""
    await _add_sync_log(
        db_session,
        connected_key.id,
        status="OK",
        started_at=datetime(2026, 6, 15, 9, 0, 0),
        finished_at=datetime(2026, 6, 15, 9, 0, 10),
        rows_fetched=2,
        rows_inserted=1,
    )

    mine = await fulfillment_service.list_sync_runs(db_session, project.id, warehouse.id)
    assert len(mine) == 1

    theirs = await fulfillment_service.list_sync_runs(db_session, other_project.id, warehouse.id)
    assert theirs == []


@pytest.mark.asyncio
async def test_sync_warehouse_logged_writes_ok_log(db_session, project, warehouse, connected_key, monkeypatch):
    """Успешный логируемый синк пишет ровно одну OK-строку sync_log, видимую в журнале."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(6001)]})

    result = await fulfillment_service.sync_warehouse_logged(db_session, project.id, warehouse.id)
    assert result["requests_synced"] == 1

    logs = (
        (
            await db_session.execute(
                select(SyncLog).where(
                    SyncLog.integration_id == connected_key.id,
                    SyncLog.sync_type == "fulfillment",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(logs) == 1
    log = logs[0]
    assert log.status == "OK"
    assert log.finished_at is not None
    assert log.rows_inserted == result["stocks_synced"]
    assert log.rows_fetched == result["stocks_synced"] + result["requests_synced"]

    runs = await fulfillment_service.list_sync_runs(db_session, project.id, warehouse.id)
    assert len(runs) == 1
    assert runs[0]["status"] == "OK"
    assert runs[0]["stocks_synced"] == result["stocks_synced"]
    assert runs[0]["requests_synced"] == result["requests_synced"]


@pytest.mark.asyncio
async def test_sync_warehouse_logged_records_error_and_reraises(
    db_session, project, warehouse, connected_key, monkeypatch
):
    """Без подключения — ValueError ДО лога (строк нет); провайдер-ошибка → ERROR-строка + re-raise."""
    # 1) Несвязанный склад: путь падает до записи лога
    other_wh = Warehouse(project_id=project.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(other_wh)
    await db_session.commit()
    await db_session.refresh(other_wh)

    with pytest.raises(ValueError):
        await fulfillment_service.sync_warehouse_logged(db_session, project.id, other_wh.id)

    no_logs = (
        (
            await db_session.execute(
                select(SyncLog)
                .join(IntegrationKey, SyncLog.integration_id == IntegrationKey.id)
                .where(
                    IntegrationKey.warehouse_id == other_wh.id,
                    SyncLog.sync_type == "fulfillment",
                )
            )
        )
        .scalars()
        .all()
    )
    assert no_logs == []

    # 2) Подключённый ключ, но провайдер падает HTTP-ошибкой → ERROR-строка + re-raise
    async def boom_products(self, customer_id):
        raise httpx.HTTPError("upstream 500")

    _mock_requests(monkeypatch, {})
    monkeypatch.setattr(SkladbotClient, "fetch_all_products", boom_products)

    with pytest.raises(ValueError):
        await fulfillment_service.sync_warehouse_logged(db_session, project.id, warehouse.id)

    err_logs = (
        (
            await db_session.execute(
                select(SyncLog).where(
                    SyncLog.integration_id == connected_key.id,
                    SyncLog.sync_type == "fulfillment",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(err_logs) == 1
    assert err_logs[0].status == "ERROR"
    assert err_logs[0].error_msg
    assert err_logs[0].finished_at is not None


# ─── list_unlinked_assemblies: наши сборки без связанной ФФ-заявки ───────────


async def _make_assembly_with_items(
    db_session,
    project,
    warehouse,
    items,
    status=AssemblyStatus.IN_PROGRESS.value,
    estimated_ready_date=None,
):
    """Сборка с позициями [(barcode, nomenclature_id, qty)] и заданным статусом."""
    doc = AssemblyRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"A-{_uid()[:6]}",
        status=status,
        estimated_ready_date=estimated_ready_date,
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
    )
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)
    if items:
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


async def _make_ff_mirror(db_session, project, warehouse, external_id, assembly_request_id=None):
    """Прямая вставка строки зеркала FulfillmentRequest (assembly), опционально связанной."""
    req = FulfillmentRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        provider="skladbot",
        external_id=str(external_id),
        kind="assembly",
        status="new",
        is_completed=False,
        archived=False,
        expired=False,
        assembly_request_id=assembly_request_id,
        synced_at=utcnow(),
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)
    return req


@pytest.mark.asyncio
async def test_unlinked_assemblies_returns_active_with_qty_brands_order(db_session, project, warehouse):
    """Активные сборки в 3 статусах без линка: total_qty/brands и порядок created_at desc."""
    bc_a, bc_b = f"30{_uid()}", f"31{_uid()}"
    nom_a = await _make_nomenclature(db_session, project.id, bc_a, article="ART-A", brand="BrandA")
    nom_b = await _make_nomenclature(db_session, project.id, bc_b, article="ART-B", brand="BrandB")

    older = await _make_assembly_with_items(
        db_session, project, warehouse, [(bc_a, nom_a.id, 5)], status=AssemblyStatus.IN_PROGRESS.value
    )
    newer = await _make_assembly_with_items(
        db_session,
        project,
        warehouse,
        [(bc_a, nom_a.id, 3), (bc_b, nom_b.id, 4)],
        status=AssemblyStatus.READY.value,
    )
    # Гарантируем порядок created_at: newer создан позже older (commit-порядок)
    rows = await fulfillment_service.list_unlinked_assemblies(db_session, project.id, warehouse.id)

    by_id = {r["id"]: r for r in rows}
    assert older.id in by_id and newer.id in by_id
    assert by_id[older.id]["total_qty"] == 5
    assert by_id[older.id]["brands"] == "BrandA"
    assert by_id[newer.id]["total_qty"] == 7
    assert by_id[newer.id]["brands"] == "BrandA, BrandB"  # distinct, по алфавиту
    # Порядок: newer (created позже) раньше older в списке
    ids_order = [r["id"] for r in rows if r["id"] in {older.id, newer.id}]
    assert ids_order == [newer.id, older.id]


@pytest.mark.asyncio
async def test_unlinked_assemblies_excludes_linked(db_session, project, warehouse):
    """Сборка со связанной ФФ-заявкой (assembly_request_id) не попадает в выдачу."""
    bc = f"32{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-L", brand="BrandL")
    linked_doc = await _make_assembly_with_items(db_session, project, warehouse, [(bc, nom.id, 2)])
    unlinked_doc = await _make_assembly_with_items(db_session, project, warehouse, [(bc, nom.id, 9)])
    await _make_ff_mirror(db_session, project, warehouse, _uid(), assembly_request_id=linked_doc.id)

    rows = await fulfillment_service.list_unlinked_assemblies(db_session, project.id, warehouse.id)
    ids = {r["id"] for r in rows}
    assert linked_doc.id not in ids
    assert unlinked_doc.id in ids


@pytest.mark.asyncio
async def test_unlinked_assemblies_excludes_wrong_statuses(db_session, project, warehouse):
    """SHIPPED/PENDING и прочие статусы вне IN_PROGRESS/READY/VEHICLE_ASSIGNED исключены."""
    bc = f"33{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-S")
    in_progress = await _make_assembly_with_items(
        db_session, project, warehouse, [(bc, nom.id, 1)], status=AssemblyStatus.IN_PROGRESS.value
    )
    vehicle = await _make_assembly_with_items(
        db_session, project, warehouse, [(bc, nom.id, 1)], status=AssemblyStatus.VEHICLE_ASSIGNED.value
    )
    shipped = await _make_assembly_with_items(
        db_session, project, warehouse, [(bc, nom.id, 1)], status=AssemblyStatus.SHIPPED.value
    )
    pending = await _make_assembly_with_items(
        db_session, project, warehouse, [(bc, nom.id, 1)], status=AssemblyStatus.PENDING.value
    )

    rows = await fulfillment_service.list_unlinked_assemblies(db_session, project.id, warehouse.id)
    ids = {r["id"] for r in rows}
    assert in_progress.id in ids
    assert vehicle.id in ids
    assert shipped.id not in ids
    assert pending.id not in ids


@pytest.mark.asyncio
async def test_unlinked_assemblies_project_isolation(db_session, project, warehouse, other_project, other_warehouse):
    """Чужой проект не видит наши несвязанные сборки (и наоборот)."""
    bc = f"34{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-ISO")
    await _make_assembly_with_items(db_session, project, warehouse, [(bc, nom.id, 4)])

    rows = await fulfillment_service.list_unlinked_assemblies(db_session, other_project.id, other_warehouse.id)
    assert rows == []
