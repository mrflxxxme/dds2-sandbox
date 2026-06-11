"""
Service tests — fulfillment integration (skladbot): sync stocks/requests, link, connect.

SkladbotClient полностью мокается через monkeypatch — никаких реальных HTTP-вызовов.
"""

import base64
import json
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.integrations.skladbot_client import SkladbotClient, decode_jwt_exp
from backend.integrations.wmscelicom_client import WmsCelicomClient, normalize_base_url
from backend.models import (
    FulfillmentRequest,
    FulfillmentStock,
    InboundReceipt,
    IntegrationKey,
    Nomenclature,
    Warehouse,
    WarehouseStock,
)
from backend.models.assembly import AssemblyRequest
from backend.services import fulfillment_service

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


def _req(req_id, number=None, status="new", created="2026-06-10", archived=0, completed=0, stage_title="Новая"):
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
        "stage_code": None,
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
        await fulfillment_service.connect(db_session, project.id, warehouse.id, "migfull", WMS_TOKEN)


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
