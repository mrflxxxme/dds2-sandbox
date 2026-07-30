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
    InboundStatus,
    IntegrationKey,
    Nomenclature,
    SyncLog,
    Warehouse,
    WarehouseStock,
)
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus, AssemblyStatusHistory
from backend.models.gazelka import GazelkaOrder, GazelkaOrderStatus
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
    expired=0,
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
        "expired": expired,
        "is_completed": completed,
    }


def _mock_connection(monkeypatch, customer=FAKE_CUSTOMER, total=1):
    async def fake_test_connection(self):
        return customer

    async def fake_count_customers(self):
        return total

    async def fake_find_customer(self, customer_id):
        return customer if customer and customer.get("id") == customer_id else None

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_test_connection)
    monkeypatch.setattr(SkladbotClient, "count_customers", fake_count_customers)
    monkeypatch.setattr(SkladbotClient, "find_customer", fake_find_customer)


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


def _mock_request_detail(monkeypatch, products):
    """Деталка заявки с per-line фактом: products=[{barcode, amount, acceptedAmount}].

    Нужна авто-приёму приёмок (skladbot принимает по acceptedAmount, а не заявленному).
    Вызывать ПОСЛЕ _mock_requests — он ставит дефолтную пустую деталку.
    """

    async def fake_fetch_request_detail(self, external_id):
        return {"products": products}

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
    assert status["has_portal_operator"] is False


@pytest.mark.asyncio
async def test_status_portal_operator_without_integration(db_session, project, warehouse):
    """Склад портального оператора (Хамза) без API-интеграции: connected=False,
    но has_portal_operator=True — фронт не предлагает создать заявку ФФ (сборка
    уже видна оператору в /ff/*)."""
    from backend.models.refs import ProjectSetting

    db_session.add(ProjectSetting(project_id=project.id, key="ff_warehouses_999", value=json.dumps([warehouse.id])))
    await db_session.commit()
    status = await fulfillment_service.get_status(db_session, project.id, warehouse.id)
    assert status["connected"] is False
    assert status["has_portal_operator"] is True


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


@pytest.mark.asyncio
async def test_list_stocks_migfull_splits_reserve_into_ready_and_defect(db_session, project, warehouse):
    """migfull: резерв (stock_locked) = Собрано (активные отгрузки, capped) + Брак.
    Активные = ready+uploaded+new (все держат резерв); closed/canceled — нет. diff =
    ff_good − (наш годный + наш брак), т.к. stock_actual включает брак."""
    bc_pure = f"BC-{_uid()}"  # резерв = весь брак (нет собранных отгрузок)
    bc_mixed = f"BC-{_uid()}"  # резерв = собрано + брак
    bc_over = f"BC-{_uid()}"  # собрано планирует больше, чем locked → кап
    nom_pure = await _make_nomenclature(db_session, project.id, bc_pure, article="ART-PURE")
    nom_mixed = await _make_nomenclature(db_session, project.id, bc_mixed, article="ART-MIX")
    nom_over = await _make_nomenclature(db_session, project.id, bc_over, article="ART-OVER")

    db_session.add_all(
        [
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                barcode=bc_pure,
                external_product_id="g-pure",
                nomenclature_id=nom_pure.id,
                qty_good=479,
                qty_reserve=87,
                qty_defect=0,
                qty_nominal=392,
            ),
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                barcode=bc_mixed,
                external_product_id="g-mixed",
                nomenclature_id=nom_mixed.id,
                qty_good=90,
                qty_reserve=30,
                qty_defect=0,
                qty_nominal=60,
            ),
            WarehouseStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                nomenclature_id=nom_pure.id,
                barcode=bc_pure,
                quantity=392,
                defect_quantity=87,
            ),
            WarehouseStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                nomenclature_id=nom_mixed.id,
                barcode=bc_mixed,
                quantity=70,
                defect_quantity=20,
            ),
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                barcode=bc_over,
                external_product_id="g-over",
                nomenclature_id=nom_over.id,
                qty_good=50,
                qty_reserve=8,  # locked < сумма заявок (20+3) → кап
                qty_defect=0,
                qty_nominal=42,
            ),
            # g-mixed: 10 (ready) + 7 (uploaded) собрано; closed 999 — игнор
            FulfillmentRequest(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                external_id="ship-uploaded",
                kind="assembly",
                stage_code="uploaded",
                raw={"planned_lines": [{"product_guid": "g-mixed", "quantity": 7}]},
            ),
            FulfillmentRequest(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                external_id="ship-ready",
                kind="assembly",
                stage_code="ready",
                raw={
                    "planned_lines": [
                        {"product_guid": "g-mixed", "quantity": 10},
                        {"product_guid": "g-over", "quantity": 20},  # > locked 8
                    ]
                },
            ),
            FulfillmentRequest(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                external_id="ship-uploaded-over",
                kind="assembly",
                stage_code="uploaded",
                raw={"planned_lines": [{"product_guid": "g-over", "quantity": 3}]},
            ),
            FulfillmentRequest(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                external_id="ship-closed",
                kind="assembly",
                stage_code="closed",
                raw={"planned_lines": [{"product_guid": "g-mixed", "quantity": 999}]},
            ),
        ]
    )
    await db_session.commit()

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    rows = {r["barcode"]: r for r in data["rows"]}

    # bc_pure: нет собранных отгрузок и приёмок → весь резерв (87) = брак ФФ
    assert rows[bc_pure]["ff_reserve_ready"] == 0
    assert rows[bc_pure]["ff_inbound_locked"] == 0
    assert rows[bc_pure]["ff_defect"] == 87
    assert rows[bc_pure]["diff"] == 479 - (392 + 87)  # 0 — реальный дрейф

    # bc_mixed: 30 = 17 собрано (10 ready + 7 uploaded) + 13 брак (closed 999 НЕ учтён)
    m = rows[bc_mixed]
    assert m["ff_reserve_ready"] == 17
    assert m["ff_defect"] == 13
    assert m["ff_reserve_ready"] + m["ff_inbound_locked"] + m["ff_defect"] == m["ff_reserve"]
    assert m["diff"] == 90 - (70 + 20)  # 0

    # bc_over: locked 8 < собрано (ready 20 + uploaded 3 = 23) → кап: собрано=min(23,8)=8, брак=0
    o = rows[bc_over]
    assert o["ff_reserve_ready"] == 8
    assert o["ff_defect"] == 0
    assert o["ff_reserve_ready"] + o["ff_inbound_locked"] + o["ff_defect"] == o["ff_reserve"]

    totals = data["totals"]
    assert totals["ff_reserve_ready"] == 17 + 8
    assert totals["ff_defect"] == 87 + 13


@pytest.mark.asyncio
async def test_list_stocks_migfull_inbound_locked_out_of_defect(db_session, project, warehouse):
    """migfull: свежий приход, залоченный migfull (позиции в EXPECTED-приёмке) и не
    «собранный» в отгрузку, выносится из «Брака» в бакет «В приёмке» (кейс мозаики)."""
    bc_full = f"BC-{_uid()}"  # приёмка ≥ резерва → весь резерв в «В приёмке»
    bc_part = f"BC-{_uid()}"  # приёмка < резерва → часть в приёмке, часть брак
    nom_full = await _make_nomenclature(db_session, project.id, bc_full, article="MOSAIC")
    nom_part = await _make_nomenclature(db_session, project.id, bc_part, article="PART")
    db_session.add_all(
        [
            FulfillmentStock(
                project_id=project.id, warehouse_id=warehouse.id, provider="migfull",
                barcode=bc_full, external_product_id="g-full", nomenclature_id=nom_full.id,
                qty_good=345, qty_reserve=345, qty_defect=0, qty_nominal=0,
            ),
            FulfillmentStock(
                project_id=project.id, warehouse_id=warehouse.id, provider="migfull",
                barcode=bc_part, external_product_id="g-part", nomenclature_id=nom_part.id,
                qty_good=100, qty_reserve=100, qty_defect=0, qty_nominal=0,
            ),
        ]
    )
    receipt = InboundReceipt(
        project_id=project.id, warehouse_id=warehouse.id, number=f"IN-{_uid()[:6]}", status="EXPECTED"
    )
    db_session.add(receipt)
    await db_session.commit()
    await db_session.refresh(receipt)
    db_session.add_all(
        [
            InboundReceiptItem(project_id=project.id, receipt_id=receipt.id, nomenclature_id=nom_full.id, barcode=bc_full, expected_qty=414),
            InboundReceiptItem(project_id=project.id, receipt_id=receipt.id, nomenclature_id=nom_part.id, barcode=bc_part, expected_qty=30),
        ]
    )
    await db_session.commit()

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    rows = {r["barcode"]: r for r in data["rows"]}

    # bc_full: весь резерв 345 = «В приёмке» (приёмка 414 ≥ 345), брак 0
    f = rows[bc_full]
    assert f["ff_reserve_ready"] == 0
    assert f["ff_inbound_locked"] == 345
    assert f["ff_defect"] == 0
    assert f["ff_reserve_ready"] + f["ff_inbound_locked"] + f["ff_defect"] == f["ff_reserve"]

    # bc_part: приёмка 30 → «В приёмке» 30, брак 70
    p = rows[bc_part]
    assert p["ff_inbound_locked"] == 30
    assert p["ff_defect"] == 70
    assert p["ff_reserve_ready"] + p["ff_inbound_locked"] + p["ff_defect"] == p["ff_reserve"]

    assert data["totals"]["ff_inbound_locked"] == 345 + 30


# ─── FBS-вычет из ff_good (ff_fbs) ──────────────────────────────────────────
#
# Ни один из трёх WMS-провайдеров не снимает свой остаток под FBS-продажи
# (сверка 29.07.2026): мы списали единицу из ledger'а (движение FBS_ORDER),
# зеркало не шелохнулось — каждая продажа читалась как ложное «у ФФ больше».
# list_stocks вычитает нетто FBS-отгрузок из ff_good, КАП по наблюдаемому
# ПРОФИЦИТУ (ff_good − наш итог): после выравнивания остатков (ADJUSTMENT /
# провайдер списал сам) вычет самоизлечивается в 0, а lifetime-нетто движений
# не рождает вечное ложное «у нас больше».


def test_fbs_ref_type_matches_orders_service():
    """Связка констант между модулями: прямой импорт дал бы цикл
    (wb_fbs.stock_service уже импортирует fulfillment_service) — паттерн
    tests/test_wb_fbs_locks.py::TestLockTtlVsJobBudget."""
    from backend.services.wb_fbs import orders_service  # late import — см. docstring

    assert fulfillment_service._FBS_WRITEOFF_REF_TYPE == orders_service._WRITEOFF_REF_TYPE
    assert fulfillment_service._FBS_WRITEOFF_REF_TYPE == "FBS_ORDER"


async def _fbs_move(db_session, project_id, warehouse_id, nomenclature_id, barcode, qty):
    """qty=-1 — OUTBOUND-списание продажи, qty=+1 — INBOUND-сторно отмены."""
    from backend.models.warehouse import MovementType, StockMovement

    db_session.add(
        StockMovement(
            project_id=project_id,
            warehouse_id=warehouse_id,
            nomenclature_id=nomenclature_id,
            barcode=barcode,
            movement_type=(MovementType.OUTBOUND if qty < 0 else MovementType.INBOUND).value,
            quantity=qty,
            reference_type="FBS_ORDER",
        )
    )
    await db_session.commit()


async def _seed_ff_and_our(db_session, project, warehouse, barcode, ff_good, our_qty):
    nom = await _make_nomenclature(db_session, project.id, barcode)
    db_session.add(
        FulfillmentStock(
            project_id=project.id,
            warehouse_id=warehouse.id,
            provider="skladbot",
            barcode=barcode,
            nomenclature_id=nom.id,
            qty_good=ff_good,
        )
    )
    if our_qty:
        db_session.add(
            WarehouseStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                nomenclature_id=nom.id,
                barcode=barcode,
                quantity=our_qty,
            )
        )
    await db_session.commit()
    return nom


@pytest.mark.asyncio
async def test_list_stocks_fbs_deducted_from_ff_good(db_session, project, warehouse):
    """Нетто FBS-отгрузок вычитается из ff_good; diff становится честным."""
    bc = f"BC-{_uid()}"
    nom = await _seed_ff_and_our(db_session, project, warehouse, bc, ff_good=10, our_qty=7)
    for _ in range(3):
        await _fbs_move(db_session, project.id, warehouse.id, nom.id, bc, -1)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    row = next(r for r in data["rows"] if r["barcode"] == bc)
    assert row["ff_fbs"] == 3
    assert row["ff_good"] == 7  # 10 − 3
    assert row["diff"] == 0  # 7 − 7: ложное «у ФФ больше» ушло
    assert data["totals"]["ff_fbs"] == 3
    assert data["totals"]["diff"] == 0


@pytest.mark.asyncio
async def test_list_stocks_fbs_inbound_storno_reduces_net(db_session, project, warehouse):
    """INBOUND-сторно отменённого задания уменьшает нетто вычета."""
    bc = f"BC-{_uid()}"
    nom = await _seed_ff_and_our(db_session, project, warehouse, bc, ff_good=10, our_qty=8)
    for _ in range(3):
        await _fbs_move(db_session, project.id, warehouse.id, nom.id, bc, -1)
    await _fbs_move(db_session, project.id, warehouse.id, nom.id, bc, 1)  # отмена вернула единицу

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    row = next(r for r in data["rows"] if r["barcode"] == bc)
    assert row["ff_fbs"] == 2  # нетто: 3 − 1
    assert row["ff_good"] == 8
    assert row["diff"] == 0


@pytest.mark.asyncio
async def test_list_stocks_fbs_capped_by_ff_good(db_session, project, warehouse):
    """Кап по ff_good: если провайдер начнёт списывать сам, зеркало не уйдёт в минус."""
    bc = f"BC-{_uid()}"
    nom = await _seed_ff_and_our(db_session, project, warehouse, bc, ff_good=2, our_qty=0)
    for _ in range(5):
        await _fbs_move(db_session, project.id, warehouse.id, nom.id, bc, -1)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    row = next(r for r in data["rows"] if r["barcode"] == bc)
    assert row["ff_fbs"] == 2  # применили не больше, чем было в зеркале
    assert row["ff_good"] == 0  # не −3
    assert row["diff"] == 0


@pytest.mark.asyncio
async def test_list_stocks_fbs_all_reverted_no_deduction(db_session, project, warehouse):
    """Нетто ≤ 0 (всё сторнировано) — вычета нет вовсе."""
    bc = f"BC-{_uid()}"
    nom = await _seed_ff_and_our(db_session, project, warehouse, bc, ff_good=5, our_qty=5)
    await _fbs_move(db_session, project.id, warehouse.id, nom.id, bc, -1)
    await _fbs_move(db_session, project.id, warehouse.id, nom.id, bc, 1)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    row = next(r for r in data["rows"] if r["barcode"] == bc)
    assert row["ff_fbs"] == 0
    assert row["ff_good"] == 5


@pytest.mark.asyncio
async def test_list_stocks_without_fbs_movements_unchanged(db_session, project, warehouse):
    """Склад без FBS-движений не меняется: чужой склад проекта не подмешивается."""
    bc = f"BC-{_uid()}"
    nom = await _seed_ff_and_our(db_session, project, warehouse, bc, ff_good=10, our_qty=7)
    # Движение на ДРУГОМ складе того же проекта — не должно затронуть этот.
    other = Warehouse(project_id=project.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)
    await _fbs_move(db_session, project.id, other.id, nom.id, bc, -1)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    row = next(r for r in data["rows"] if r["barcode"] == bc)
    assert row["ff_fbs"] == 0
    assert row["ff_good"] == 10
    assert row["diff"] == 3
    assert data["totals"]["ff_fbs"] == 0


@pytest.mark.asyncio
async def test_list_stocks_fbs_no_deduction_after_adjustment_alignment(db_session, project, warehouse):
    """После сверки ADJUSTMENT'ом (mirror == our) вычет НЕ применяется: diff = 0.

    Вычет — lifetime-нетто движений и сам по себе не гаснет. Прежний кап по
    одному ff_good после выравнивания остатков продолжал вычитать историю
    FBS-продаж и давал ВЕЧНОЕ ложное «у нас больше». Кап по наблюдаемому
    профициту (ff_good − our) самоизлечивается: профицита нет — вычета нет.
    """
    bc = f"BC-{_uid()}"
    nom = await _seed_ff_and_our(db_session, project, warehouse, bc, ff_good=7, our_qty=7)
    for _ in range(3):
        await _fbs_move(db_session, project.id, warehouse.id, nom.id, bc, -1)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    row = next(r for r in data["rows"] if r["barcode"] == bc)
    assert row["ff_fbs"] == 0  # applied = 0: профицита нет
    assert row["ff_good"] == 7
    assert row["diff"] == 0  # не −3


@pytest.mark.asyncio
async def test_list_stocks_fbs_capped_by_observed_surplus(db_session, project, warehouse):
    """Частичный профицит капит вычет: applied = min(нетто, профицит)."""
    bc = f"BC-{_uid()}"
    # Профицит 3 (10 − 7), нетто движений 5 → применяем только 3.
    nom = await _seed_ff_and_our(db_session, project, warehouse, bc, ff_good=10, our_qty=7)
    for _ in range(5):
        await _fbs_move(db_session, project.id, warehouse.id, nom.id, bc, -1)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    row = next(r for r in data["rows"] if r["barcode"] == bc)
    assert row["ff_fbs"] == 3
    assert row["ff_good"] == 7
    assert row["diff"] == 0  # не −2: лишнее нетто не продавливает «у нас больше»


# ─── migfull короб → россыпь (box → loose) ──────────────────────────────────


def test_ean13_check_digit():
    assert fulfillment_service._ean13_check_digit("204316033057") == "8"
    assert fulfillment_service._ean13_check_digit("204346565776") == "9"


def test_itf14_to_ean13_gtin14():
    # ШК короба (ITF14) → ШК россыпи (EAN13): индикатор отброшен, чек пересчитан
    assert fulfillment_service._itf14_to_ean13("12043160330575") == "2043160330578"
    assert fulfillment_service._itf14_to_ean13("12043465657766") == "2043465657769"
    # EAN13 (13 цифр) / нецифры / пусто — не короб
    assert fulfillment_service._itf14_to_ean13("2043160330578") is None
    assert fulfillment_service._itf14_to_ean13("1204316033057X") is None
    assert fulfillment_service._itf14_to_ean13("") is None


def test_migfull_box_pack():
    # Короб: ITF14 + «короб N шт.» в названии → (россыпь EAN13, N)
    assert fulfillment_service._migfull_box_pack(
        "12043160330575", "KOSIHKA_210x90_160x90_светло-серый короб 20 шт., 210х90"
    ) == ("2043160330578", 20)
    # Россыпь (EAN13) — не короб
    assert fulfillment_service._migfull_box_pack("2043160330578", "KOSIHKA россыпь") is None
    # ITF14 без «короб N шт.» в названии — не сводим (кол-во неизвестно)
    assert fulfillment_service._migfull_box_pack("12043160330575", "KOSIHKA без указания") is None
    # «короб 1 шт» — не короб (units<=1)
    assert fulfillment_service._migfull_box_pack("12043160330575", "X короб 1 шт.") is None


def test_normalize_migfull_stock_box_vs_loose():
    box = fulfillment_service._normalize_migfull_stock(
        {"guid": "g-box", "name": "ELKA короб 10 шт., бежевый", "stock_actual": 5, "stock_locked": 1},
        {"g-box": "12043160330575"},
    )
    assert box["barcode"] == "12043160330575"
    assert box["base_barcode"] == "2043160330578"
    assert box["units_per_box"] == 10
    assert box["qty_good"] == 5

    loose = fulfillment_service._normalize_migfull_stock(
        {"guid": "g-loose", "name": "ELKA россыпь", "stock_actual": 3},
        {"g-loose": "2043160330578"},
    )
    assert loose["base_barcode"] is None
    assert loose["units_per_box"] == 1


@pytest.mark.asyncio
async def test_apply_stocks_box_matches_nomenclature_via_base(db_session, project, warehouse):
    """Короб (ITF14) матчится к номенклатуре по base_barcode (россыпь EAN13)."""
    base_bc = "2043160330578"
    box_bc = "12043160330575"
    nom = await _make_nomenclature(db_session, project.id, base_bc, article="ART-BOX")

    items = [
        {
            "barcode": box_bc,
            "base_barcode": base_bc,
            "units_per_box": 20,
            "name": "X короб 20 шт.",
            "vendor_code": None,
            "external_product_id": "g-box",
            "qty_good": 5,
            "qty_reserve": 0,
            "qty_defect": 0,
            "qty_nominal": 5,
        },
        {
            "barcode": base_bc,
            "base_barcode": None,
            "units_per_box": 1,
            "name": "X россыпь",
            "vendor_code": None,
            "external_product_id": "g-loose",
            "qty_good": 3,
            "qty_reserve": 0,
            "qty_defect": 0,
            "qty_nominal": 3,
        },
    ]
    written, unmatched = await fulfillment_service._apply_stocks(db_session, project.id, warehouse.id, "migfull", items)
    await db_session.commit()
    assert written == 2
    assert unmatched == 0  # обе строки сматчены по эффективному ШК

    rows = {r.barcode: r for r in await _ff_stocks(db_session, project.id, warehouse.id)}
    assert rows[box_bc].nomenclature_id == nom.id
    assert rows[box_bc].base_barcode == base_bc
    assert rows[box_bc].units_per_box == 20
    assert rows[base_bc].nomenclature_id == nom.id
    assert rows[base_bc].units_per_box == 1


@pytest.mark.asyncio
async def test_list_stocks_merges_box_into_loose(db_session, project, warehouse):
    """list_stocks сводит короб (qty×units) к россыпи: одна строка на товар."""
    base_bc = "2043160330578"  # есть короб + россыпь + наш сток
    box_bc = "12043160330575"
    only_box_base = "2043465657769"  # только короб, без россыпи и нашего стока
    only_box_bc = "12043465657766"

    nom = await _make_nomenclature(db_session, project.id, base_bc, article="ART-DUAL", subject="Накидки")
    nom2 = await _make_nomenclature(db_session, project.id, only_box_base, article="ART-ONLYBOX")

    db_session.add_all(
        [
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                barcode=box_bc,
                base_barcode=base_bc,
                units_per_box=20,
                nomenclature_id=nom.id,
                name="DUAL короб 20 шт.",
                qty_good=5,
                qty_reserve=1,
            ),
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                barcode=base_bc,
                base_barcode=None,
                units_per_box=1,
                nomenclature_id=nom.id,
                name="DUAL россыпь",
                qty_good=3,
            ),
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                barcode=only_box_bc,
                base_barcode=only_box_base,
                units_per_box=12,
                nomenclature_id=nom2.id,
                name="ONLYBOX короб 12 шт.",
                qty_good=10,
            ),
            WarehouseStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                nomenclature_id=nom.id,
                barcode=base_bc,
                quantity=100,
            ),
        ]
    )
    await db_session.commit()

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    rows = {r["barcode"]: r for r in data["rows"]}
    # Короб + россыпь сведены в ОДНУ строку под ШК россыпи
    assert set(rows) == {base_bc, only_box_base}

    dual = rows[base_bc]
    assert dual["ff_good"] == 5 * 20 + 3  # короб в штуках + россыпь = 103
    assert dual["ff_reserve"] == 1 * 20  # резерв короба тоже ×units
    assert dual["ff_box_units"] == 100  # из них коробами
    assert dual["ff_box_count"] == 5  # 5 физических коробов
    assert dual["our_quantity"] == 100
    assert dual["diff"] == 103 - 100
    assert dual["nomenclature_id"] == nom.id
    assert dual["article_seller"] == "ART-DUAL"

    only = rows[only_box_base]
    assert only["ff_good"] == 10 * 12  # 120, только короб
    assert only["ff_box_units"] == 120
    assert only["our_quantity"] == 0
    assert only["nomenclature_id"] == nom2.id  # короб сматчен к номенклатуре

    assert data["totals"]["ff_box_units"] == 100 + 120
    assert data["totals"]["unmatched"] == 0


@pytest.mark.asyncio
async def test_list_box_packs(db_session, project, warehouse):
    """Видимая таблица сопоставления: только коробные строки + наша номенклатура."""
    base_bc = "2043160330578"
    box_bc = "12043160330575"
    nom = await _make_nomenclature(db_session, project.id, base_bc, article="ART-BOX", subject="Накидки")
    db_session.add_all(
        [
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                barcode=box_bc,
                base_barcode=base_bc,
                units_per_box=20,
                nomenclature_id=nom.id,
                name="X короб 20 шт.",
                qty_good=5,
            ),
            # россыпь — НЕ короб, в сопоставление не попадает
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                barcode=base_bc,
                base_barcode=None,
                units_per_box=1,
                nomenclature_id=nom.id,
                name="X россыпь",
                qty_good=3,
            ),
            # короб без номенклатуры (unmatched)
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                barcode="12043465657766",
                base_barcode="2043465657769",
                units_per_box=12,
                name="Y короб 12 шт.",
                qty_good=2,
            ),
        ]
    )
    await db_session.commit()

    packs = await fulfillment_service.list_box_packs(db_session, project.id, warehouse.id)
    assert len(packs) == 2  # только коробные строки (россыпь исключена)
    by_box = {p["box_barcode"]: p for p in packs}
    p = by_box[box_bc]
    assert p["base_barcode"] == base_bc
    assert p["units_per_box"] == 20
    assert p["box_qty"] == 5
    assert p["units_qty"] == 100
    assert p["matched"] is True
    assert p["article_seller"] == "ART-BOX"
    assert by_box["12043465657766"]["matched"] is False
    # сортировка по units_qty desc
    units = [p["units_qty"] for p in packs]
    assert units == sorted(units, reverse=True)


@pytest.mark.asyncio
async def test_fetch_ff_composition_migfull_box_converts(db_session, project, warehouse):
    """Состав сборки migfull: короб (5 шт) сводится к россыпи в штуках (5×20=100)."""
    base_bc = "2043160330578"
    db_session.add(
        FulfillmentStock(
            project_id=project.id,
            warehouse_id=warehouse.id,
            provider="migfull",
            barcode="12043160330575",
            base_barcode=base_bc,
            units_per_box=20,
            external_product_id="g-box",
            name="X короб 20 шт.",
            qty_good=5,
        )
    )
    await db_session.commit()
    req = FulfillmentRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        provider="migfull",
        external_id="ship-1",
        kind="assembly",
        raw={
            "planned_lines": [
                {"product_guid": "g-box", "quantity": 5, "product": {"guid": "g-box", "name": "X короб 20 шт."}}
            ]
        },
    )
    comp = await fulfillment_service._fetch_ff_composition(db_session, project.id, warehouse.id, req)
    assert comp == {base_bc: 100}


@pytest.mark.asyncio
async def test_request_detail_migfull_box_converts(db_session, project, warehouse, connected_mig_key):
    """Деталка сборки migfull: коробная позиция матчится к номенклатуре и считается в штуках."""
    base_bc = "2043160330578"
    nom = await _make_nomenclature(db_session, project.id, base_bc, article="ART-BOXDET")
    db_session.add(
        FulfillmentStock(
            project_id=project.id,
            warehouse_id=warehouse.id,
            provider="migfull",
            barcode="12043160330575",
            base_barcode=base_bc,
            units_per_box=20,
            external_product_id="g-box",
            nomenclature_id=nom.id,
            name="X короб 20 шт.",
            qty_good=5,
        )
    )
    req = FulfillmentRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        provider="migfull",
        external_id="ship-det-1",
        number="PVB-1",
        kind="assembly",
        status="uploaded",
        raw={
            "planned_lines": [
                {"product_guid": "g-box", "quantity": 5, "product": {"guid": "g-box", "name": "X короб 20 шт."}}
            ],
            "shipped_lines": [],
        },
    )
    db_session.add(req)
    await db_session.commit()
    await db_session.refresh(req)

    detail = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, req.id)
    assert detail is not None
    prods = detail["products"]
    assert len(prods) == 1
    p = prods[0]
    assert p["barcode"] == base_bc  # ШК россыпи, не короба
    assert p["nomenclature_id"] == nom.id
    assert p["article_seller"] == "ART-BOXDET"
    assert p["qty"] == 100  # 5 коробов × 20
    assert p["units_per_box"] == 20
    assert p["box_qty"] == 5
    assert detail["total_qty"] == 100


# ─── Ручное сопоставление короба (override) ─────────────────────────────────


def test_normalize_migfull_stock_override_wins():
    """Ручной override побеждает авто-вывод в нормализации остатка."""
    out = fulfillment_service._normalize_migfull_stock(
        {"guid": "g1", "name": "X короб 20 шт.", "stock_actual": 2},
        {"g1": "12043160330575"},
        {"12043160330575": ("2049999999999", 7)},
    )
    assert out["base_barcode"] == "2049999999999"  # не авто 2043160330578
    assert out["units_per_box"] == 7


@pytest.mark.asyncio
async def test_set_box_override_applies_and_persists(db_session, project, warehouse):
    box_bc = "99999999999994"  # ITF14, авто не сработает (нет «короб N шт.»)
    nom = await _make_nomenclature(db_session, project.id, "2040000000017", article="ART-OVR")
    db_session.add(
        FulfillmentStock(
            project_id=project.id,
            warehouse_id=warehouse.id,
            provider="migfull",
            barcode=box_bc,
            base_barcode=None,
            units_per_box=1,
            name="Странный короб без шаблона",
            qty_good=4,
        )
    )
    await db_session.commit()

    pack = await fulfillment_service.set_box_override(db_session, project.id, warehouse.id, box_bc, nom.id, 6)
    assert pack is not None
    assert pack["base_barcode"] == "2040000000017"
    assert pack["units_per_box"] == 6
    assert pack["units_qty"] == 24  # 4 кор × 6
    assert pack["matched"] is True
    assert pack["source"] == "manual"
    assert pack["article_seller"] == "ART-OVR"

    # применилось к текущему остатку (без пересинка)
    rows = {r.barcode: r for r in await _ff_stocks(db_session, project.id, warehouse.id)}
    assert rows[box_bc].base_barcode == "2040000000017"
    assert rows[box_bc].units_per_box == 6
    assert rows[box_bc].nomenclature_id == nom.id
    # override персистентен (применится и на будущих синках)
    ov = await fulfillment_service._load_box_overrides(db_session, project.id, warehouse.id)
    assert ov[box_bc] == ("2040000000017", 6)


@pytest.mark.asyncio
async def test_set_box_override_validation(db_session, project, warehouse):
    nom_no_bc = await _make_nomenclature(db_session, project.id, "", article="ART-NOBC")
    nom_ok = await _make_nomenclature(db_session, project.id, "2040000000024", article="ART-OK")
    with pytest.raises(ValueError):  # units < 1
        await fulfillment_service.set_box_override(db_session, project.id, warehouse.id, "12340000000000", nom_ok.id, 0)
    with pytest.raises(ValueError):  # номенклатуры нет
        await fulfillment_service.set_box_override(db_session, project.id, warehouse.id, "12340000000000", 99999999, 5)
    with pytest.raises(ValueError):  # у номенклатуры нет ШК
        await fulfillment_service.set_box_override(
            db_session, project.id, warehouse.id, "12340000000000", nom_no_bc.id, 5
        )


@pytest.mark.asyncio
async def test_delete_box_override_reverts_to_auto(db_session, project, warehouse):
    box_bc = "12043160330575"
    base_bc = "2043160330578"
    nom_auto = await _make_nomenclature(db_session, project.id, base_bc, article="ART-AUTO")
    nom_manual = await _make_nomenclature(db_session, project.id, "2040000000031", article="ART-MAN")
    db_session.add(
        FulfillmentStock(
            project_id=project.id,
            warehouse_id=warehouse.id,
            provider="migfull",
            barcode=box_bc,
            base_barcode=None,
            units_per_box=1,
            name="X короб 20 шт.",
            qty_good=3,
        )
    )
    await db_session.commit()

    # ручной override на другой товар/кол-во
    await fulfillment_service.set_box_override(db_session, project.id, warehouse.id, box_bc, nom_manual.id, 7)
    rows = {r.barcode: r for r in await _ff_stocks(db_session, project.id, warehouse.id)}
    assert rows[box_bc].nomenclature_id == nom_manual.id
    assert rows[box_bc].units_per_box == 7

    # снять override → возврат к авто-выводу (GTIN-14 + «короб 20 шт.»)
    pack = await fulfillment_service.delete_box_override(db_session, project.id, warehouse.id, box_bc)
    assert pack["source"] == "auto"
    assert pack["base_barcode"] == base_bc
    assert pack["units_per_box"] == 20
    assert pack["nomenclature_id"] == nom_auto.id
    ov = await fulfillment_service._load_box_overrides(db_session, project.id, warehouse.id)
    assert box_bc not in ov


@pytest.mark.asyncio
async def test_list_box_packs_source_classification(db_session, project, warehouse):
    base_bc = "2043160330578"
    nom = await _make_nomenclature(db_session, project.id, base_bc, article="ART-A")
    db_session.add_all(
        [
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                barcode="12043160330575",
                base_barcode=base_bc,
                units_per_box=20,
                nomenclature_id=nom.id,
                name="X короб 20 шт.",
                qty_good=5,
            ),
            # ITF14 без base_barcode — авто не справился, надо вручную
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                barcode="99999999999994",
                base_barcode=None,
                units_per_box=1,
                name="Непонятный короб",
                qty_good=2,
            ),
            # россыпь (EAN13) — НЕ короб, в список не попадает
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=warehouse.id,
                provider="migfull",
                barcode=base_bc,
                base_barcode=None,
                units_per_box=1,
                nomenclature_id=nom.id,
                name="россыпь",
                qty_good=9,
            ),
        ]
    )
    await db_session.commit()

    raw_packs = await fulfillment_service.list_box_packs(db_session, project.id, warehouse.id)
    packs = {p["box_barcode"]: p for p in raw_packs}
    assert set(packs) == {"12043160330575", "99999999999994"}  # россыпь исключена
    assert packs["12043160330575"]["source"] == "auto"
    assert packs["99999999999994"]["source"] == "unmapped"
    assert packs["99999999999994"]["matched"] is False
    assert packs["99999999999994"]["base_barcode"] is None  # unmapped — без россыпи
    # Регресс: каждая строка проходит response-валидацию FfBoxPack (у unmapped
    # base_barcode=None — схема обязана это допускать, иначе endpoint падает 500)
    from backend.schemas.fulfillment import FfBoxPack

    for p in raw_packs:
        FfBoxPack.model_validate(p)


@pytest.mark.asyncio
async def test_search_nomenclature_only_with_barcode(db_session, project, warehouse):
    await _make_nomenclature(db_session, project.id, "2041111111111", article="KOVER-RED")
    await _make_nomenclature(db_session, project.id, "2042222222222", article="KOVER-BLUE")
    await _make_nomenclature(db_session, project.id, "", article="KOVER-NOBC")  # без ШК — не вернётся

    res = await fulfillment_service.search_nomenclature(db_session, project.id, "kover")
    arts = {r["article_seller"] for r in res}
    assert "KOVER-RED" in arts
    assert "KOVER-BLUE" in arts
    assert "KOVER-NOBC" not in arts  # без ШК россыпи привязать нельзя

    res2 = await fulfillment_service.search_nomenclature(db_session, project.id, "2041111")
    assert [r["article_seller"] for r in res2] == ["KOVER-RED"]


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
    """Сверка ТОЛЬКО по нашим ШК: расходится qty (bc_qty) и есть только у нас
    (bc_our_only, мы отправили — ФФ не заявил). Лишний ШК только у ФФ (bc_ff_only),
    которого нет в нашей сборке, расхождением НЕ считается — он виден в общей
    таблице состава с «В нашей заявке» = 0. Тоталы — по всему составу ФФ."""
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
    # bc_ff_only (лишний у ФФ, our_qty==0) НЕ в mismatches — сверяем по нашим ШК
    assert set(by_bc) == {bc_qty, bc_our_only}
    assert by_bc[bc_qty]["ff_qty"] == 10 and by_bc[bc_qty]["our_qty"] == 7 and by_bc[bc_qty]["diff"] == 3
    assert by_bc[bc_our_only]["ff_qty"] == 0 and by_bc[bc_our_only]["our_qty"] == 2
    assert by_bc[bc_our_only]["article_seller"] == "ART-OUR"  # номенклатура и для our-only строк
    products = {p["barcode"]: p for p in row["products"]}
    assert products[bc_ff_only]["our_qty"] == 0  # лишний ШК ФФ виден в общей таблице состава
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


def _wms_shipment(
    sid, status="Новая", external_order=None, date_time="2026-06-01 10:00:00", packages=None, low_packages=None
):
    """wmscelicom shipmentsfbo/list row (Packages — dict {номер: короб}).

    Боевой API кладёт состав в `Packages` (заглавная: items — один товар-dict
    на короб); lowercase `packages` приходит с пустыми items — `low_packages`
    моделирует этот fallback-формат.
    """
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
        row["Packages"] = packages
    if low_packages is not None:
        row["packages"] = low_packages
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


def _wms_dispatch(
    orderid,
    status="new",
    shipment_status=None,
    warehouse="Казань",
    items=None,
    date_time="2026-06-01 10:00:00",
):
    """wmscelicom dispatchorders/list row (заявка на отгрузку, зОГ).

    `status` — английский статус заявки; `shipment_status` — русский статус
    связанной FBO-отгрузки (None пока её нет); `warehouse` — склад отгрузки
    (город МП); `items` — состав ([{barcode, count, name}]).
    """
    return {
        "orderid": orderid,
        "date_time": date_time,
        "externalid": None,
        "shipmentid": "",
        "status": status,
        "shipment_status": shipment_status,
        "warehouse": warehouse,
        "user": {"first_name": "Иван", "last_name": "Петров"},
        "items": items if items is not None else [],
    }


def _mock_wms_fetches(monkeypatch, items=(), dispatches=(), unloadings=()):
    async def fake_items(self):
        return list(items)

    async def fake_dispatch(self, terminal_from, terminal_to):
        return list(dispatches)

    async def fake_unloadings(self):
        return list(unloadings)

    monkeypatch.setattr(WmsCelicomClient, "fetch_all_items", fake_items)
    monkeypatch.setattr(WmsCelicomClient, "fetch_dispatch_orders", fake_dispatch)
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
        dispatches=[
            _wms_dispatch(101, status="new", warehouse="Казань"),
            _wms_dispatch(102, status="ondelivery", shipment_status="Отгружена", warehouse="Тула"),
            _wms_dispatch(103, status="ondelivery", shipment_status="Принята в СЦ c разногласиями"),
            _wms_dispatch(104, status="annuled"),
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
    assert rows["101"]["type_name"] == "Заявка на отгрузку"
    assert rows["101"]["status"] == "Новая"  # english «new» → русский ярлык
    assert rows["101"]["dest_warehouse"] == "Казань"  # склад отгрузки = город МП
    assert rows["101"]["number"] is None  # зОГ-номер API не отдаёт
    assert rows["101"]["external_created_at"] is not None

    assert rows["102"]["is_completed"] is True
    assert rows["102"]["status"] == "Отгружена"  # shipment_status приоритетнее english
    assert rows["102"]["dest_warehouse"] == "Тула"
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
    """Деталка заявки на отгрузку: товары агрегируются из items[], без HTTP-вызова."""
    bc_known = f"BC-{_uid()}"
    bc_unknown = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc_known, article="ART-WMS-D")

    # Состав заявки на отгрузку — top-level items[]; тот же ШК в двух позициях агрегируется.
    items = [
        {"id": 1, "name": "Товар А", "barcode": bc_known, "count": 3},
        {"id": 2, "name": "Товар Б", "barcode": bc_unknown, "count": 2},
        {"id": 3, "name": "Товар А", "barcode": bc_known, "count": 4},
    ]
    _mock_wms_fetches(
        monkeypatch,
        dispatches=[
            _wms_dispatch(401, status="combinig", shipment_status="На сборке", warehouse="Казань", items=items)
        ],
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    ff_id = next(r["id"] for r in rows if r["external_id"] == "401")

    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, ff_id)
    assert row is not None
    assert row["total_qty"] == 9
    assert row["creator"] == "Иван Петров"
    by_bc = {p["barcode"]: p for p in row["products"]}
    assert by_bc[bc_known]["qty"] == 7  # 3 + 4 из двух позиций
    assert by_bc[bc_known]["nomenclature_id"] == nom.id
    assert by_bc[bc_known]["article_seller"] == "ART-WMS-D"
    assert by_bc[bc_unknown]["qty"] == 2
    assert by_bc[bc_unknown]["nomenclature_id"] is None
    field_names = {f["name"] for f in row["fields"]}
    assert "Склад отгрузки" in field_names


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


def test_assembly_ready_signal_wmscelicom():
    sig = fulfillment_service._assembly_ready_signal
    # Сборка идёт — не готово
    assert sig("wmscelicom", None, "Новая", False) is False
    assert sig("wmscelicom", None, "На сборке", False) is False
    assert sig("wmscelicom", None, "Передана в доставку", False) is False
    # «Ожидает отгрузки» — короб собран, ждёт машину = наш READY (как Газпром)
    assert sig("wmscelicom", None, "Ожидает отгрузки", False) is True
    # Терминальные статусы отгрузки FBO → готово (is_completed)
    assert sig("wmscelicom", None, "Отгружена", True) is True
    assert sig("wmscelicom", None, "Принята в СЦ/Складе", True) is True


def test_ff_status_code():
    code = fulfillment_service._ff_status_code
    ASM, INB = "assembly", "inbound"
    # wmscelicom assembly: Новая/На сборке → в сборке, Ожидает отгрузки → готово, терминальные → отгружена
    assert code("wmscelicom", ASM, None, "Новая", False, False, False) == "assembling"
    assert code("wmscelicom", ASM, None, "На сборке", False, False, False) == "assembling"
    assert code("wmscelicom", ASM, None, "Ожидает отгрузки", False, False, False) == "ready"
    assert code("wmscelicom", ASM, None, "Отгружена", True, False, False) == "shipped"
    assert code("wmscelicom", ASM, None, "Принята в СЦ/Складе", True, False, False) == "shipped"
    # Оверлеи: архив и просрочка
    assert code("wmscelicom", ASM, None, "Новая", False, True, False) == "archived"
    assert code("wmscelicom", ASM, None, "На сборке", False, False, True) == "expired"
    # skladbot/migfull сборка — «готово» по тому же сигналу, что и авто-READY
    assert code("skladbot", ASM, "cargo_pickup", "Забор груза", False, False, False) == "assembling"
    assert code("skladbot", ASM, "driver_assignment", "Назначение водителя", False, False, False) == "ready"
    assert code("migfull", ASM, "ready", "Собран", False, False, False) == "ready"
    assert code("migfull", ASM, "closed", "Закрыт", True, False, False) == "shipped"
    # inbound: ожидается → принято на остатки
    assert code("wmscelicom", INB, None, "Ожидает приемки", False, False, False) == "expected"
    assert code("wmscelicom", INB, None, "Принята", True, False, False) == "accepted"
    assert code("migfull", INB, None, "Отменен", False, True, False) == "archived"


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
async def test_sync_marks_expired_linked_assembly_ready(db_session, project, warehouse, connected_key, monkeypatch):
    """Просроченная (expired) ФФ-заявка на готовой стадии всё равно авто-READY сборки.

    Регрессия (прод-инцидент 2026-06-16): expired=True ошибочно исключал заявку
    из авто-READY, и сборка, чья ФФ-заявка дошла до «Указание виды работ
    логистики» (stage_code=logistics_works) будучи просроченной, навсегда
    зависала в IN_PROGRESS (WH-R-196281 → ASM-455). expired = «просрочена» —
    заявка ещё активна, не dead.
    """
    _mock_products(monkeypatch, [])
    # WIP-стадия, ещё не просрочена — линкуем сборку, статус не меняется
    _mock_requests(monkeypatch, {851: [_req(9401, stage_title="Указание обьема груза v2")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = await _make_assembly_doc(db_session, project, warehouse)
    mirror = await _mirror_row(db_session, project.id, "9401")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.IN_PROGRESS.value

    # Стадия логистики + ПРОСРОЧЕНА → сборка всё равно переходит в READY
    _mock_requests(
        monkeypatch,
        {851: [_req(9401, stage_title="Указание виды работ логистики", stage_code="logistics_works", expired=1)]},
    )
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_marked_ready"] == 1
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.READY.value
    assert doc.actual_ready_date == date.today()


@pytest.mark.asyncio
async def test_sync_marks_linked_wms_assembly_ready(db_session, project, warehouse, connected_wms_key, monkeypatch):
    """wmscelicom: «Ожидает отгрузки» переводит связанную сборку IN_PROGRESS → READY (как Газпром)."""
    _mock_wms_fetches(monkeypatch, dispatches=[_wms_dispatch(950, status="combinig", shipment_status="На сборке")])
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = await _make_assembly_doc(db_session, project, warehouse)
    mirror = await _mirror_row(db_session, project.id, "950")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)

    # «На сборке» — сборка ещё идёт, статус ФФ = assembling
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_marked_ready"] == 0
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.IN_PROGRESS.value
    assembling_rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    assert next(r for r in assembling_rows if r["external_id"] == "950")["ff_status"] == "assembling"

    # «Ожидает отгрузки» → авто-READY + статус ФФ = ready
    _mock_wms_fetches(
        monkeypatch, dispatches=[_wms_dispatch(950, status="waitingdelivery", shipment_status="Ожидает отгрузки")]
    )
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_marked_ready"] == 1
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.READY.value
    assert doc.actual_ready_date == date.today()

    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    ff_row = next(r for r in rows if r["external_id"] == "950")
    assert ff_row["ff_status"] == "ready"


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


# ─── Авто-ACCEPT приёмок по сигналу ФФ (is_completed) ───────────────────────


def test_inbound_accept_signal():
    sig = fulfillment_service._inbound_accept_signal
    assert sig("skladbot", None, None, True) is True  # ФФ принял на остатки
    assert sig("skladbot", "acceptance", "Приемка", False) is False  # ещё в стадии «Приемка»
    # skladbot: терминальная стадия «Завершение» = принято, даже без is_completed
    # (живой кейс FR 202523 / IN-186, склад «Газпром»).
    assert sig("skladbot", "completion", "Завершение", False) is True


@pytest.mark.asyncio
async def test_sync_accepts_linked_inbound_receipt(db_session, project, warehouse, connected_key, monkeypatch):
    """ФФ принял приёмку на остатки (is_completed) → наша EXPECTED приёмка ACCEPT + сток.

    Прод-сценарий WH-R-196798 → IN-150: пока ФФ в стадии «Приемка» — приёмка
    остаётся EXPECTED; как только is_completed — авто-ACCEPT (постит сток).
    Проверяем и просроченную (expired) заявку, и идемпотентность повторного синка.
    """
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    _mock_products(monkeypatch, [])

    # Приёмка ФФ ещё идёт (стадия «Приемка») — появляется в зеркале
    _mock_requests(monkeypatch, {852: [_req(8801, stage_title="Приемка", stage_code="acceptance")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    # Наша приёмка EXPECTED + позиция; линкуем к ФФ-заявке
    receipt = InboundReceipt(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"IN-{_uid()[:6]}",
        status=InboundStatus.EXPECTED,
    )
    db_session.add(receipt)
    await db_session.flush()
    db_session.add(
        InboundReceiptItem(
            project_id=project.id,
            receipt_id=receipt.id,
            nomenclature_id=nom.id,
            barcode=bc,
            expected_qty=10,
            actual_qty=0,
        )
    )
    await db_session.commit()
    mirror = await _mirror_row(db_session, project.id, "8801")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, inbound_receipt_id=receipt.id)

    # Пока ФФ не принял — приёмка остаётся EXPECTED, стока нет
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["inbound_receipts_accepted"] == 0
    await db_session.refresh(receipt)
    assert receipt.status == InboundStatus.EXPECTED

    # ФФ завершил приёмку (is_completed) + ПРОСРОЧЕНА → наша приёмка ACCEPT + сток.
    # skladbot принимает по ФАКТУ — деталка отдаёт acceptedAmount=10 (= заявленному).
    _mock_requests(
        monkeypatch,
        {852: [_req(8801, status="new", completed=1, stage_title=None, stage_code=None, expired=1)]},
    )
    _mock_request_detail(monkeypatch, [{"barcode": bc, "amount": 10, "acceptedAmount": 10}])
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["inbound_receipts_accepted"] == 1
    await db_session.refresh(receipt)
    assert receipt.status == InboundStatus.ACCEPTED
    assert receipt.actual_date == date.today()

    # Сток запостен по факту ФФ (acceptedAmount) = 10
    stock = (
        await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project.id,
                WarehouseStock.warehouse_id == warehouse.id,
                WarehouseStock.barcode == bc,
            )
        )
    ).scalar_one()
    assert stock.quantity == 10

    # Идемпотентность: повторный синк не задвоит сток
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["inbound_receipts_accepted"] == 0
    await db_session.refresh(stock)
    assert stock.quantity == 10


@pytest.mark.asyncio
async def test_sync_accepts_inbound_by_ff_fact_undercount(db_session, project, warehouse, connected_key, monkeypatch):
    """ФФ (skladbot) принял МЕНЬШЕ заявленного → на остаток встаёт факт, не заявленное.

    Прод-баг (receipt 200 ↔ ff-request 555, Газпром): недовоз ФФ ложно вставал на
    остаток целиком (auto-fill из expected). Теперь авто-приём берёт acceptedAmount.
    """
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {852: [_req(8820, stage_title="Приемка", stage_code="acceptance")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    receipt = await _make_receipt_with_item(db_session, project, warehouse, nom, bc, expected_qty=10)
    mirror = await _mirror_row(db_session, project.id, "8820")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, inbound_receipt_id=receipt.id)

    # ФФ завершил, но принял 7 из 10 (недовоз 3)
    _mock_requests(monkeypatch, {852: [_req(8820, status="new", completed=1, stage_title=None, stage_code=None)]})
    _mock_request_detail(monkeypatch, [{"barcode": bc, "amount": 10, "acceptedAmount": 7}])
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["inbound_receipts_accepted"] == 1

    await db_session.refresh(receipt)
    assert receipt.status == InboundStatus.ACCEPTED
    item_qty = (
        await db_session.execute(
            select(InboundReceiptItem.actual_qty).where(InboundReceiptItem.receipt_id == receipt.id)
        )
    ).scalar_one()
    assert item_qty == 7  # факт, не заявленные 10
    stock = (
        await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project.id,
                WarehouseStock.warehouse_id == warehouse.id,
                WarehouseStock.barcode == bc,
            )
        )
    ).scalar_one()
    assert stock.quantity == 7


@pytest.mark.asyncio
async def test_sync_inbound_defers_accept_when_ff_fact_unavailable(
    db_session, project, warehouse, connected_key, monkeypatch
):
    """Факт skladbot недоступен (деталка пустая) → приём ОТКЛАДЫВАЕТСЯ, не по заявленному."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {852: [_req(8821, stage_title="Приемка", stage_code="acceptance")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    receipt = await _make_receipt_with_item(db_session, project, warehouse, nom, bc, expected_qty=10)
    mirror = await _mirror_row(db_session, project.id, "8821")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, inbound_receipt_id=receipt.id)

    # Завершено, но деталка без состава (дрейф/сбой) → helper вернёт None
    _mock_requests(monkeypatch, {852: [_req(8821, status="new", completed=1, stage_title=None, stage_code=None)]})
    _mock_request_detail(monkeypatch, [])
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["inbound_receipts_accepted"] == 0
    await db_session.refresh(receipt)
    assert receipt.status == InboundStatus.EXPECTED  # не приняли по заявленному


@pytest.mark.asyncio
async def test_sync_inbound_no_accept_without_link(db_session, project, warehouse, connected_key, monkeypatch):
    """Завершённая приёмка ФФ без связи с нашей приёмкой ничего не постит."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {852: [_req(8802, status="new", completed=1, stage_title=None)]})
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["inbound_receipts_accepted"] == 0


@pytest.mark.asyncio
async def test_sync_accepts_inbound_completed_and_archived(
    db_session, project, warehouse, connected_key, monkeypatch
):
    """ФФ принял приёмку и СРАЗУ сдал заявку в архив (is_completed + archived).

    Прод-баг (WH-R-204611 → receipt 201, склад 5): skladbot при завершении
    приёмки ставит is_completed И archived=True одновременно. Фильтр
    archived==False в авто-приёме отсекал такую заявку → приёмка навсегда
    висела EXPECTED. Теперь archived НЕ блокирует, если заявка завершена
    (симметрично авто-SHIP сборок). Отменённая (archived без is_completed)
    по-прежнему НЕ принимается — проверяется отдельно ниже.
    """
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {852: [_req(8830, stage_title="Приемка", stage_code="acceptance")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    receipt = await _make_receipt_with_item(db_session, project, warehouse, nom, bc, expected_qty=10)
    mirror = await _mirror_row(db_session, project.id, "8830")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, inbound_receipt_id=receipt.id)

    # ФФ завершил приёмку и в ТОТ ЖЕ синк сдал заявку в архив
    _mock_requests(
        monkeypatch,
        {852: [_req(8830, status="new", completed=1, archived=1, stage_title=None, stage_code=None)]},
    )
    _mock_request_detail(monkeypatch, [{"barcode": bc, "amount": 10, "acceptedAmount": 10}])
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["inbound_receipts_accepted"] == 1
    await db_session.refresh(receipt)
    assert receipt.status == InboundStatus.ACCEPTED


@pytest.mark.asyncio
async def test_sync_inbound_no_accept_when_archived_without_completed(
    db_session, project, warehouse, connected_key, monkeypatch
):
    """Отменённая приёмка (archived=True БЕЗ is_completed) НЕ принимается.

    Гард: смягчая archived-фильтр (см. test выше), не должны начать принимать
    отменённые заявки. is_completed=False → _inbound_accept_signal ложно.
    """
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {852: [_req(8831, stage_title="Приемка", stage_code="acceptance")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    receipt = await _make_receipt_with_item(db_session, project, warehouse, nom, bc, expected_qty=10)
    mirror = await _mirror_row(db_session, project.id, "8831")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, inbound_receipt_id=receipt.id)

    # Заявку отменили: archived=True, но приёмка НЕ завершена (completed=0)
    _mock_requests(
        monkeypatch,
        {852: [_req(8831, status="new", completed=0, archived=1, stage_title=None, stage_code=None)]},
    )
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["inbound_receipts_accepted"] == 0
    await db_session.refresh(receipt)
    assert receipt.status == InboundStatus.EXPECTED


# ─── Авто-ACCEPT приёмки ПРЯМО ПРИ ПРИВЯЗКЕ (симметрия с авто-READY сборки) ───


async def _make_receipt_with_item(db_session, project, warehouse, nom, bc, expected_qty=10):
    receipt = InboundReceipt(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"IN-{_uid()[:6]}",
        status=InboundStatus.EXPECTED,
    )
    db_session.add(receipt)
    await db_session.flush()
    db_session.add(
        InboundReceiptItem(
            project_id=project.id,
            receipt_id=receipt.id,
            nomenclature_id=nom.id,
            barcode=bc,
            expected_qty=expected_qty,
            actual_qty=0,
        )
    )
    await db_session.commit()
    await db_session.refresh(receipt)
    return receipt


@pytest.mark.asyncio
async def test_link_inbound_completed_auto_accepts(db_session, project, warehouse, connected_key, monkeypatch):
    """Привязка ЗАВЕРШЁННОЙ приёмки ФФ к нашей EXPECTED сразу принимает её + постит сток.

    Регрессия: раньше приёмка принималась только следующим синком (в отличие от
    сборки, что авто-READY прямо при привязке). Теперь линк завершённой заявки
    принимает приёмку немедленно.
    """
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    _mock_products(monkeypatch, [])
    # ФФ уже завершил приёмку (is_completed); деталка отдаёт факт acceptedAmount=10
    _mock_requests(monkeypatch, {852: [_req(8810, status="new", completed=1, stage_title=None)]})
    _mock_request_detail(monkeypatch, [{"barcode": bc, "amount": 10, "acceptedAmount": 10}])
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    receipt = await _make_receipt_with_item(db_session, project, warehouse, nom, bc, expected_qty=10)
    mirror = await _mirror_row(db_session, project.id, "8810")

    row = await fulfillment_service.link_request(db_session, project.id, mirror.id, inbound_receipt_id=receipt.id)
    assert row["linked_status"] == InboundStatus.ACCEPTED.value  # UI сразу видит принятую

    await db_session.refresh(receipt)
    assert receipt.status == InboundStatus.ACCEPTED
    assert receipt.actual_date == date.today()

    stock = (
        await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project.id,
                WarehouseStock.warehouse_id == warehouse.id,
                WarehouseStock.barcode == bc,
            )
        )
    ).scalar_one()
    assert stock.quantity == 10


@pytest.mark.asyncio
async def test_link_inbound_not_completed_stays_expected(db_session, project, warehouse, connected_key, monkeypatch):
    """Привязка НЕзавершённой приёмки ФФ не принимает нашу — ждём синк/завершения ФФ."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {852: [_req(8811, stage_title="Приемка", stage_code="acceptance")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    receipt = await _make_receipt_with_item(db_session, project, warehouse, nom, bc)
    mirror = await _mirror_row(db_session, project.id, "8811")

    row = await fulfillment_service.link_request(db_session, project.id, mirror.id, inbound_receipt_id=receipt.id)
    assert row["linked_status"] == InboundStatus.EXPECTED.value

    await db_session.refresh(receipt)
    assert receipt.status == InboundStatus.EXPECTED
    stock = (
        await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project.id,
                WarehouseStock.barcode == bc,
            )
        )
    ).scalar_one_or_none()
    assert stock is None  # сток не постился


# ─── Авто-SHIP: ФФ отгрузил груз + у нас назначена машина → SHIPPED ──────────


def test_assembly_shipped_signal():
    sig = fulfillment_service._assembly_shipped_signal
    assert sig(True) is True  # ФФ отгрузил
    assert sig(False) is False  # ещё не отгружен


async def _make_vehicle_assigned_doc(db_session, project, warehouse, nom, bc, qty=5, stock_qty=20):
    """Сборка VEHICLE_ASSIGNED с позицией + сток на складе (достаточный для отгрузки)."""
    doc = AssemblyRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"A-{_uid()[:6]}",
        status=AssemblyStatus.VEHICLE_ASSIGNED.value,
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
        vehicle_info="А123ВС 77",
    )
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        AssemblyRequestItem(
            project_id=project.id,
            assembly_request_id=doc.id,
            nomenclature_id=nom.id,
            barcode=bc,
            quantity=qty,
        )
    )
    db_session.add(
        WarehouseStock(
            project_id=project.id,
            warehouse_id=warehouse.id,
            nomenclature_id=nom.id,
            barcode=bc,
            quantity=stock_qty,
        )
    )
    await db_session.commit()
    await db_session.refresh(doc)
    return doc


@pytest.mark.asyncio
async def test_sync_ships_vehicle_assigned_assembly(db_session, project, warehouse, connected_key, monkeypatch):
    """ФФ отгрузил (is_completed) + наша сборка VEHICLE_ASSIGNED → SHIPPED + сток списан."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    _mock_products(monkeypatch, [])
    # ФФ ещё везёт груз на склад МП (стадия логистики, не завершено)
    _mock_requests(monkeypatch, {851: [_req(9501, stage_title="Погрузка")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = await _make_vehicle_assigned_doc(db_session, project, warehouse, nom, bc, qty=5, stock_qty=20)
    mirror = await _mirror_row(db_session, project.id, "9501")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)

    # Пока ФФ не отгрузил — сборка остаётся VEHICLE_ASSIGNED, сток на месте
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_shipped"] == 0
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.VEHICLE_ASSIGNED.value

    # ФФ отгрузил FBO (is_completed) → наша сборка SHIPPED, сток списан, OutboundShipment создан
    _mock_requests(monkeypatch, {851: [_req(9501, status="done", completed=1, stage_title="Выполнена")]})
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_shipped"] == 1
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.SHIPPED.value
    assert doc.outbound_shipment_id is not None
    assert doc.shipped_at is not None

    stock = (
        await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project.id,
                WarehouseStock.warehouse_id == warehouse.id,
                WarehouseStock.barcode == bc,
            )
        )
    ).scalar_one()
    assert stock.quantity == 15  # 20 − 5

    # Идемпотентность: повторный синк не задвоит отгрузку/списание
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_shipped"] == 0
    await db_session.refresh(stock)
    assert stock.quantity == 15


@pytest.mark.asyncio
async def test_sync_ships_when_ff_completed_and_archived(db_session, project, warehouse, connected_key, monkeypatch):
    """ФФ отгрузил И сдал заявку в архив (is_completed + archived, как skladbot у «Газпром»)
    → авто-SHIP всё равно срабатывает: archived-после-завершения ≠ отмена."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(9701, stage_title="Погрузка")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = await _make_vehicle_assigned_doc(db_session, project, warehouse, nom, bc, qty=5, stock_qty=20)
    mirror = await _mirror_row(db_session, project.id, "9701")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)

    # is_completed=1 И archived=1 одновременно (status «new» — сырой ярлык skladbot)
    _mock_requests(monkeypatch, {851: [_req(9701, status="new", completed=1, archived=1, stage_title="Выполнена")]})
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_shipped"] == 1
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.SHIPPED.value
    assert doc.outbound_shipment_id is not None


@pytest.mark.asyncio
async def test_sync_no_ship_when_ff_archived_not_completed(db_session, project, warehouse, connected_key, monkeypatch):
    """Отменённая ФФ-заявка (archived без is_completed) НЕ отгружает сборку — сток не двигаем."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(9801, stage_title="Погрузка")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = await _make_vehicle_assigned_doc(db_session, project, warehouse, nom, bc, qty=5, stock_qty=20)
    mirror = await _mirror_row(db_session, project.id, "9801")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)

    _mock_requests(monkeypatch, {851: [_req(9801, status="canceled", completed=0, archived=1, stage_title="Отменена")]})
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_shipped"] == 0
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.VEHICLE_ASSIGNED.value


@pytest.mark.asyncio
async def test_sync_no_ship_when_not_vehicle_assigned(db_session, project, warehouse, connected_key, monkeypatch):
    """ФФ отгрузил, но машина у нас НЕ назначена (READY) → авто-SHIP не срабатывает."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(9601, stage_title="Погрузка")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = await _make_assembly_doc(db_session, project, warehouse, status=AssemblyStatus.READY.value)
    mirror = await _mirror_row(db_session, project.id, "9601")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)

    _mock_requests(monkeypatch, {851: [_req(9601, status="done", completed=1, stage_title="Выполнена")]})
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_shipped"] == 0
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.READY.value


async def _make_gazelka_ready_doc(db_session, project, warehouse, nom, bc, qty=5, stock_qty=20):
    """Сборка READY, ведётся Газелькой (активная связь MATCHED) + позиция + сток."""
    doc = AssemblyRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"A-{_uid()[:6]}",
        status=AssemblyStatus.READY.value,
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
    )
    db_session.add(doc)
    await db_session.flush()
    db_session.add(
        AssemblyRequestItem(
            project_id=project.id, assembly_request_id=doc.id,
            nomenclature_id=nom.id, barcode=bc, quantity=qty,
        )
    )
    db_session.add(
        WarehouseStock(
            project_id=project.id, warehouse_id=warehouse.id,
            nomenclature_id=nom.id, barcode=bc, quantity=stock_qty,
        )
    )
    db_session.add(
        GazelkaOrder(
            project_id=project.id, assembly_request_id=doc.id,
            status=GazelkaOrderStatus.MATCHED, gazelka_ref="G-1",
        )
    )
    await db_session.commit()
    await db_session.refresh(doc)
    return doc


@pytest.mark.asyncio
async def test_sync_ships_gazelka_ready_on_ff_completed(db_session, project, warehouse, connected_key, monkeypatch):
    """ФФ закрыл заявку (is_completed) + Gazelka-связанная сборка в READY (машину назначить
    нельзя — агрегатор) → авто-SHIP из READY + сток списан. Зеркало WB-ACCEPTED авто-шипа."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(9911, stage_title="Погрузка")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = await _make_gazelka_ready_doc(db_session, project, warehouse, nom, bc, qty=5, stock_qty=20)
    mirror = await _mirror_row(db_session, project.id, "9911")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)

    # ФФ ещё не закрыл — сборка остаётся READY
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_shipped"] == 0
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.READY.value

    # ФФ закрыл заявку (is_completed) → Gazelka-READY отгружается
    _mock_requests(monkeypatch, {851: [_req(9911, status="done", completed=1, stage_title="Выполнена")]})
    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_shipped"] == 1
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.SHIPPED.value
    assert doc.outbound_shipment_id is not None

    stock = (
        await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project.id,
                WarehouseStock.warehouse_id == warehouse.id,
                WarehouseStock.barcode == bc,
            )
        )
    ).scalar_one()
    assert stock.quantity == 15  # 20 − 5


@pytest.mark.asyncio
async def test_sync_ship_stock_deficit_best_effort(db_session, project, warehouse, connected_key, monkeypatch):
    """Дефицит стока: ship_request падает, синк не валится, сборка остаётся VEHICLE_ASSIGNED."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(9701, status="done", completed=1, stage_title="Выполнена")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    # Сборке нужно 5, на складе только 2 → дефицит
    doc = await _make_vehicle_assigned_doc(db_session, project, warehouse, nom, bc, qty=5, stock_qty=2)
    mirror = await _mirror_row(db_session, project.id, "9701")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)

    result = await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    assert result["assemblies_shipped"] == 0
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.VEHICLE_ASSIGNED.value
    stock = (
        await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project.id,
                WarehouseStock.barcode == bc,
            )
        )
    ).scalar_one()
    assert stock.quantity == 2  # не списан


# ─── wmscelicom: total_qty / dest_warehouse из списочных методов ─────────────


@pytest.mark.asyncio
async def test_sync_wms_enrichment_fields(db_session, project, warehouse, connected_wms_key, monkeypatch):
    """total_qty из items[] (PHP-коэрсия count, null-элементы), dest_warehouse из warehouse (город, false→None)."""
    _mock_wms_fetches(
        monkeypatch,
        dispatches=[
            _wms_dispatch(
                701,
                warehouse="Казань",
                items=[{"barcode": "BC-1", "count": 3}, None, {"barcode": "BC-2", "count": "4"}],
            ),
            _wms_dispatch(702, warehouse=False, items=[]),  # пусто → нет данных; PHP false-warehouse → None
        ],
        unloadings=[
            _wms_unloading(801, items=[{"barcode": "BC-3", "count": 6}, None, {"barcode": "BC-4", "count": "2"}]),
        ],
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    d1 = await _mirror_row(db_session, project.id, "701")
    assert d1.total_qty == 7  # 3 + "4" (PHP-строка), null-элемент пропущен
    assert d1.dest_warehouse == "Казань"
    d2 = await _mirror_row(db_session, project.id, "702")
    assert d2.total_qty is None  # пустой состав = нет данных, не 0
    assert d2.dest_warehouse is None  # PHP false → None
    unloading = await _mirror_row(db_session, project.id, "801")
    assert unloading.total_qty == 8  # null-элемент пропущен, "2" скоэрсирован
    assert unloading.dest_warehouse is None


def test_normalize_wms_dispatch():
    """dispatchorders row → assembly: ключ orderid, склад=город, состав из items[]."""
    norm = fulfillment_service._normalize_wms_dispatch
    # Активная (FBO-отгрузки нет): русский ярлык из english-статуса, агрегат состава по ШК
    r = norm(
        _wms_dispatch(
            3757,
            status="waitforcombine",
            warehouse="Тула ",
            items=[{"barcode": "BC-1", "count": 10}, {"barcode": "BC-1", "count": 5}],
        )
    )
    assert r["external_id"] == "3757"
    assert r["kind"] == "assembly"
    assert r["number"] is None  # зОГ-номер API не отдаёт
    assert r["type_name"] == "Заявка на отгрузку"
    assert r["status"] == "Ожидает сборки"
    assert r["dest_warehouse"] == "Тула"  # _coerce_dest триммит хвостовой пробел
    assert r["total_qty"] == 15
    assert r["is_completed"] is False
    assert r["archived"] is False
    # Отгружена: shipment_status приоритетнее english, is_completed
    r2 = norm(_wms_dispatch(3758, status="ondelivery", shipment_status="Отгружена", warehouse="Казань"))
    assert r2["status"] == "Отгружена"
    assert r2["is_completed"] is True
    assert r2["total_qty"] is None  # пустой состав = нет данных
    # Аннулирована
    assert norm(_wms_dispatch(3759, status="annuled"))["archived"] is True
    # PHP-гочи: warehouse=false → None, shipment_status="" → english fallback
    r4 = norm(_wms_dispatch(3760, status="new", shipment_status="", warehouse=False))
    assert r4["dest_warehouse"] is None
    assert r4["status"] == "Новая"


def test_raw_assembly_composition_dispatch_and_legacy():
    """Состав wms: top-level items[] (dispatchorders) + fallback Packages (легаси shipmentsfbo)."""
    comp = fulfillment_service._raw_assembly_composition
    disp = {
        "orderid": 1,
        "items": [{"barcode": "A", "count": 3}, None, {"barcode": "A", "count": "2"}, {"barcode": "B", "count": 1}],
    }
    assert comp("wmscelicom", disp) == {"A": 5, "B": 1}
    legacy = {"shipment_fbo_id": 99, "Packages": {"1": {"items": {"barcode": "C", "count": 4}}}}
    assert comp("wmscelicom", legacy) == {"C": 4}
    assert comp("skladbot", disp) == {}
    assert comp("wmscelicom", None) == {}


@pytest.mark.asyncio
async def test_sync_wms_purges_legacy_shipmentsfbo(db_session, project, warehouse, connected_wms_key, monkeypatch):
    """Переход на dispatchorders: несвязанные легаси-строки (raw с shipment_fbo_id) сносятся, связанные — нет."""
    doc = await _make_assembly_doc(db_session, project, warehouse)
    orphan = FulfillmentRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        provider="wmscelicom",
        external_id="6983",
        kind="assembly",
        status="Отгружена",
        is_completed=True,
        raw={"shipment_fbo_id": 6983, "shipped_target": "Wildberries"},
    )
    linked = FulfillmentRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        provider="wmscelicom",
        external_id="6984",
        kind="assembly",
        status="Отгружена",
        is_completed=True,
        assembly_request_id=doc.id,
        raw={"shipment_fbo_id": 6984, "shipped_target": "Wildberries"},
    )
    db_session.add_all([orphan, linked])
    await db_session.commit()

    _mock_wms_fetches(monkeypatch, dispatches=[_wms_dispatch(3757, status="new", warehouse="Казань")])
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    rows = {
        r["external_id"]
        for r in await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    }
    assert "6983" not in rows  # несвязанная легаси-строка снесена
    assert "6984" in rows  # связанная со сборкой — сохранена
    assert "3757" in rows  # новая заявка на отгрузку


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


def _mig_return(
    guid,
    status="uploaded",
    reference="PVB-0000069",
    ret_date="2026-07-30",
    notes="ВОЗВРАТ ДЛЯ ФБС",
    incoming_lines=None,
    created="2026-07-30T08:00:00.000000Z",
):
    """migfull /returns row — строки состава ВСТРОЕНЫ в список."""
    lines = incoming_lines or []
    return {
        "guid": guid,
        "reference": reference,
        "return_date": ret_date,
        "notes": notes,
        "client_comment": None,
        "status": status,
        "external_id": None,
        "processed_at": None,
        "processed_by": None,
        "created_at": created,
        "updated_at": created,
        "updated_web_at": created,
        "incoming_lines_count": len(lines),
        "outgoing_lines_count": 0,
        "incoming_lines": lines,
        "outgoing_lines": [],
        "processor": None,
    }


def _mock_mig_fetches(
    monkeypatch,
    products=(),
    shipments=(),
    submissions=(),
    returns=(),
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

    async def fake_returns(self):
        return list(returns)

    async def fake_product(self, guid):
        counters["product"] += 1
        return (product_details or {}).get(guid, {})

    async def fake_submission_lines(self, guid, line_type):
        counters["submission_lines"] += 1
        return (submission_lines or {}).get((guid, line_type), [])

    monkeypatch.setattr(MigfullClient, "fetch_all_products", fake_products)
    monkeypatch.setattr(MigfullClient, "fetch_shipments", fake_shipments)
    monkeypatch.setattr(MigfullClient, "fetch_submissions", fake_submissions)
    monkeypatch.setattr(MigfullClient, "fetch_returns", fake_returns)
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


@pytest.mark.asyncio
async def test_migfull_inbound_detail_resolves_barcode_absent_from_mirror(
    db_session, project, warehouse, connected_mig_key, monkeypatch
):
    """Товар приёмки без остатка не попал в зеркало → ШК дотягивается живой карточкой.

    Баг «не опознанный баркод»: строка приёмки несёт guid, но товар с нулевым
    остатком при синке не карточится → в зеркале ШК нет. Деталка должна
    дорезолвить ШК напрямую из карточки и сматчить с номенклатурой.
    """
    bc = f"BC-{_uid()}"
    p_zero = _mig_guid(85)  # есть в /products, но остаток 0 → не карточится при синке
    sub = _mig_guid(84)
    await _make_nomenclature(db_session, project.id, bc, article="ART-NEW")
    lines = {
        (sub, "incoming"): [_mig_line(p_zero, 6)],
        (sub, "received"): [_mig_line(p_zero, 6)],
    }
    calls: dict = {}
    _mock_mig_fetches(
        monkeypatch,
        products=[_mig_product(p_zero, actual=0, locked=0)],
        product_details={p_zero: {"barcodes": [{"value": bc, "is_primary": True}]}},
        submissions=[_mig_submission(sub, status="processing")],
        submission_lines=lines,
        calls=calls,
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    # Зеркало пусто по этому guid: zero-stock товар не закарточен синком
    stocks = await _ff_stocks(db_session, project.id, warehouse.id)
    assert all(s.external_product_id != p_zero for s in stocks)
    product_calls_after_sync = calls["product"]

    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "inbound")
    ff_id = next(r["id"] for r in rows if r["external_id"] == sub)
    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, ff_id)
    assert row is not None
    # Деталка сходила в карточку (live-резолв) и сматчила номенклатуру
    assert calls["product"] > product_calls_after_sync
    product = row["products"][0]
    assert product["barcode"] == bc
    assert product["nomenclature_id"] is not None
    assert product["article_seller"] == "ART-NEW"
    assert product["qty"] == 6


@pytest.mark.asyncio
async def test_migfull_guid_barcode_override_resolves_inbound_detail(
    db_session, project, warehouse, connected_mig_key, monkeypatch
):
    """Карточка товара БЕЗ ШК (barcodes[] пуст) → строка приёмки не опознана;
    ручной ШК короба (set_guid_barcode) сводит к россыпи и матчит номенклатуру,
    delete — возвращает в «не опознан»."""
    itf14 = "12044388647254"
    box_name = "ковер 1 шт 160х200_бежевыйоднотон короб 16 шт., 160х200, однотон"
    base_barcode, units = fulfillment_service._migfull_box_pack(itf14, box_name)
    assert units == 16  # из «короб 16 шт.»
    nom = await _make_nomenclature(db_session, project.id, base_barcode, article="ART-EMPTY")

    p = _mig_guid(120)
    sub = _mig_guid(121)
    lines = {
        (sub, "incoming"): [_mig_line(p, 2, name=box_name)],
        (sub, "received"): [_mig_line(p, 2, name=box_name)],
    }
    _mock_mig_fetches(
        monkeypatch,
        products=[_mig_product(p, name=box_name, actual=2)],
        product_details={p: {"barcodes": []}},  # карточка провайдера без ШК
        submissions=[_mig_submission(sub, status="processing")],
        submission_lines=lines,
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "inbound")
    ff_id = next(r["id"] for r in rows if r["external_id"] == sub)

    # До override: товар не опознан, но guid доступен фронту для привязки
    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, ff_id)
    prod = row["products"][0]
    assert prod["barcode"] is None
    assert prod["nomenclature_id"] is None
    assert prod["product_guid"] == p

    # Ручной ШК короба → сводится к россыпи, units из «короб 16 шт.», сматчен
    saved = await fulfillment_service.set_guid_barcode(db_session, project.id, warehouse.id, p, itf14, note="ручной")
    assert saved == {"product_guid": p, "barcode": itf14, "note": "ручной"}
    row2 = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, ff_id)
    prod2 = row2["products"][0]
    assert prod2["barcode"] == base_barcode
    assert prod2["units_per_box"] == 16
    assert prod2["qty"] == 2 * 16  # 32 шт россыпи
    assert prod2["nomenclature_id"] == nom.id
    assert prod2["article_seller"] == "ART-EMPTY"

    # delete → снова не опознан (зеркало по этому guid пусто — пересинка не было)
    await fulfillment_service.delete_guid_barcode(db_session, project.id, warehouse.id, p)
    row3 = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, ff_id)
    assert row3["products"][0]["nomenclature_id"] is None


@pytest.mark.asyncio
async def test_migfull_guid_barcode_override_seeds_stock_on_sync(
    db_session, project, warehouse, connected_mig_key, monkeypatch
):
    """Ручной ШК заведён ДО синка → синк выводит короб в зеркало остатков
    (barcode=ITF14, base_barcode=россыпь, units, номенклатура) и самоподдерживается."""
    itf14 = "12044388679644"
    box_name = "ковер 1 шт 200х300_трава короб 9 шт., 200х300, трава"
    base_barcode, units = fulfillment_service._migfull_box_pack(itf14, box_name)
    nom = await _make_nomenclature(db_session, project.id, base_barcode, article="ART-TRAVA")
    p = _mig_guid(130)

    await fulfillment_service.set_guid_barcode(db_session, project.id, warehouse.id, p, itf14)
    calls: dict = {}
    _mock_mig_fetches(
        monkeypatch,
        products=[_mig_product(p, name=box_name, actual=3)],
        product_details={p: {"barcodes": []}},  # карточка без ШК — override её замещает
        calls=calls,
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    # Override сделал guid «опознанным» → синк не дёргал пустую карточку
    assert calls["product"] == 0
    seeded = next(s for s in await _ff_stocks(db_session, project.id, warehouse.id) if s.external_product_id == p)
    assert seeded.barcode == itf14
    assert seeded.base_barcode == base_barcode
    assert seeded.units_per_box == units
    assert seeded.nomenclature_id == nom.id


@pytest.mark.asyncio
async def test_migfull_assembly_detail_resolves_barcode_absent_from_mirror(
    db_session, project, warehouse, connected_mig_key, monkeypatch
):
    """Отгрузка: товар с нулевым остатком не попал в зеркало, но карточка несёт ШК →
    деталка дотягивает живой карточкой (как приёмка), а не оставляет «—». После
    disconnect деталка всё ещё открывается из зеркала+raw (без живого добора)."""
    bc = f"BC-{_uid()}"
    p_zero = _mig_guid(150)  # есть в /products, остаток 0 → не карточится при синке
    ship = _mig_guid(151)
    await _make_nomenclature(db_session, project.id, bc, article="ART-SHIP")
    shipment = _mig_shipment(
        ship, status="ready", display="Собран", planned_total=4,
        planned_lines=[_mig_line(p_zero, 4, name="Ковер отгрузка")],
    )
    calls: dict = {}
    _mock_mig_fetches(
        monkeypatch,
        products=[_mig_product(p_zero, actual=0, locked=0)],
        product_details={p_zero: {"barcodes": [{"value": bc, "is_primary": True}]}},
        shipments=[shipment],
        calls=calls,
    )
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    stocks = await _ff_stocks(db_session, project.id, warehouse.id)
    assert all(s.external_product_id != p_zero for s in stocks)  # zero-stock → не в зеркале
    product_calls_after_sync = calls["product"]

    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    ff_id = next(r["id"] for r in rows if r["external_id"] == ship)
    row = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, ff_id)
    # деталка отгрузки сходила в карточку (live-резолв) — раньше для отгрузки этого не было
    assert calls["product"] > product_calls_after_sync
    product = row["products"][0]
    assert product["barcode"] == bc
    assert product["nomenclature_id"] is not None
    assert product["article_seller"] == "ART-SHIP"
    assert product["qty"] == 4

    # После disconnect деталка отгрузки всё ещё открывается (из зеркала+raw, без добора)
    await fulfillment_service.disconnect(db_session, project.id, warehouse.id)
    row2 = await fulfillment_service.get_request_detail(db_session, project.id, warehouse.id, ff_id)
    assert row2 is not None
    assert row2["products"][0]["barcode"] is None  # zero-stock + нет клиента → не опознан


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
async def test_status_events_enriched_with_dest_qty_and_link(
    db_session, project, warehouse, connected_key, monkeypatch
):
    """list_status_events обогащает строки складом сдачи, кол-вом и нашей заявкой."""
    _mock_products(monkeypatch, [])
    _mock_requests(monkeypatch, {851: [_req(8901, stage_title="Забор груза", stage_code="cargo_pickup")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    doc = await _make_assembly_doc(db_session, project, warehouse)
    mirror = await _mirror_row(db_session, project.id, "8901")
    await fulfillment_service.link_request(db_session, project.id, mirror.id, assembly_request_id=doc.id)

    # Склад сдачи / кол-во проставляем на зеркале (как enrich из деталки skladbot)
    mirror = await _mirror_row(db_session, project.id, "8901")
    mirror.dest_warehouse = "Казань"
    mirror.total_qty = 777
    await db_session.commit()

    events = await fulfillment_service.list_status_events(db_session, project.id, warehouse.id, kind="assembly")
    ev = next(e for e in events if e["external_id"] == "8901")
    assert ev["dest_warehouse"] == "Казань"
    assert ev["total_qty"] == 777
    assert ev["linked_number"] == doc.number

    # Несвязанная заявка без склада/кол-ва — поля пустые
    _mock_requests(monkeypatch, {851: [_req(8902, stage_title="Забор груза", stage_code="cargo_pickup")]})
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    events = await fulfillment_service.list_status_events(db_session, project.id, warehouse.id, kind="assembly")
    ev2 = next(e for e in events if e["external_id"] == "8902")
    assert ev2["dest_warehouse"] is None
    assert ev2["linked_number"] is None


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
    wb_warehouse_name_manual=None,
):
    """Сборка с позициями [(barcode, nomenclature_id, qty)] и заданным статусом."""
    doc = AssemblyRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"A-{_uid()[:6]}",
        status=status,
        estimated_ready_date=estimated_ready_date,
        wb_warehouse_name_manual=wb_warehouse_name_manual,
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
        db_session,
        project,
        warehouse,
        [(bc_a, nom_a.id, 5)],
        status=AssemblyStatus.IN_PROGRESS.value,
        wb_warehouse_name_manual="Коледино",
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
    assert by_id[older.id]["dest_warehouse"] == "Коледино"  # ручной склад сдачи
    assert by_id[newer.id]["total_qty"] == 7
    assert by_id[newer.id]["brands"] == "BrandA, BrandB"  # distinct, по алфавиту
    assert by_id[newer.id]["dest_warehouse"] is None  # ни FBO, ни ручного
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


# ─── list_stocks: досчёт логистики ФФ (баг «Указание вида работ логистики») ──


async def _make_ff_stock_row(db_session, project, warehouse, barcode, nom_id, qty_good):
    """Строка зеркала остатка ФФ (россыпь, skladbot)."""
    row = FulfillmentStock(
        project_id=project.id,
        warehouse_id=warehouse.id,
        provider="skladbot",
        barcode=barcode,
        nomenclature_id=nom_id,
        qty_good=qty_good,
        qty_reserve=0,
        qty_defect=0,
        qty_nominal=0,
        units_per_box=1,
        synced_at=utcnow(),
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def _make_logistics_ff_request(db_session, project, warehouse, assembly_id, is_completed=False):
    req = FulfillmentRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        provider="skladbot",
        external_id=f"FF-{_uid()}",
        kind="assembly",
        status="logistics",
        stage_code="logistics_works",
        stage_title="Указание виды работ логистики",
        is_completed=is_completed,
        archived=False,
        expired=False,
        assembly_request_id=assembly_id,
        synced_at=utcnow(),
    )
    db_session.add(req)
    await db_session.commit()
    return req


@pytest.mark.asyncio
async def test_list_stocks_counts_logistics_writeoff_on_ff_side(db_session, project, warehouse):
    """ФФ списал сток на стадии logistics_works, но груз на складе и сборка не
    отгружена → состав досчитывается к ff_good, расхождение = 0."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-LOGI", subject="Ковры", brand="НУ-НУ")
    await _make_ff_stock_row(db_session, project, warehouse, bc, nom.id, qty_good=396)  # зеркало уже списало 330
    db_session.add(
        WarehouseStock(
            project_id=project.id, warehouse_id=warehouse.id,
            nomenclature_id=nom.id, barcode=bc, quantity=726,  # наш сток держит полный
        )
    )
    await db_session.commit()
    asm = await _make_assembly_with_items(
        db_session, project, warehouse, [(bc, nom.id, 330)], status=AssemblyStatus.READY.value
    )
    await _make_logistics_ff_request(db_session, project, warehouse, asm.id)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    row = {r["barcode"]: r for r in data["rows"]}[bc]
    assert row["ff_logistics"] == 330
    assert row["ff_good"] == 726  # 396 зеркало + 330 логистика
    assert row["our_quantity"] == 726
    assert row["diff"] == 0
    assert data["totals"]["ff_logistics"] == 330
    assert data["totals"]["diff"] == 0


@pytest.mark.asyncio
async def test_list_stocks_logistics_not_counted_when_shipped(db_session, project, warehouse):
    """Сборка SHIPPED (наш сток уже списан ship_request) → логистику НЕ досчитываем,
    иначе ложный излишек."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-SHIP")
    await _make_ff_stock_row(db_session, project, warehouse, bc, nom.id, qty_good=100)
    asm = await _make_assembly_with_items(
        db_session, project, warehouse, [(bc, nom.id, 50)], status=AssemblyStatus.SHIPPED.value
    )
    await _make_logistics_ff_request(db_session, project, warehouse, asm.id)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    row = {r["barcode"]: r for r in data["rows"]}[bc]
    assert row["ff_logistics"] == 0
    assert row["ff_good"] == 100
    assert row["diff"] == 100


@pytest.mark.asyncio
async def test_list_stocks_logistics_not_counted_when_completed(db_session, project, warehouse):
    """ФФ-заявка is_completed=True (груз уехал) → логистику НЕ досчитываем."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-DONE")
    await _make_ff_stock_row(db_session, project, warehouse, bc, nom.id, qty_good=100)
    asm = await _make_assembly_with_items(
        db_session, project, warehouse, [(bc, nom.id, 50)], status=AssemblyStatus.VEHICLE_ASSIGNED.value
    )
    await _make_logistics_ff_request(db_session, project, warehouse, asm.id, is_completed=True)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    row = {r["barcode"]: r for r in data["rows"]}[bc]
    assert row["ff_logistics"] == 0
    assert row["diff"] == 100


@pytest.mark.asyncio
async def test_list_stocks_logistics_clamped_when_ff_has_not_written_off(db_session, project, warehouse):
    """skladbot списывает ПОЗИЦИОННО: зеркало эту позицию ещё держит.

    Досчёт состава «как есть» положил бы товар второй раз и родил ложный ПРОФИЦИТ ФФ
    (прод-эпизод 27.07: +1303 фантома на складе Газпром). Кламп → досчёт 0, diff 0.
    """
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-NOWRITEOFF")
    await _make_ff_stock_row(db_session, project, warehouse, bc, nom.id, qty_good=240)  # НЕ списал
    db_session.add(
        WarehouseStock(
            project_id=project.id, warehouse_id=warehouse.id,
            nomenclature_id=nom.id, barcode=bc, quantity=240,
        )
    )
    await db_session.commit()
    asm = await _make_assembly_with_items(
        db_session, project, warehouse, [(bc, nom.id, 80)], status=AssemblyStatus.READY.value
    )
    await _make_logistics_ff_request(db_session, project, warehouse, asm.id)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    row = {r["barcode"]: r for r in data["rows"]}[bc]
    assert row["ff_logistics"] == 0  # без клампа было бы 80
    assert row["ff_good"] == 240
    assert row["diff"] == 0


@pytest.mark.asyncio
async def test_list_stocks_logistics_clamped_to_partial_writeoff(db_session, project, warehouse):
    """ФФ списал ЧАСТЬ позиции (40 из 80) → досчитываем ровно недостачу, не весь состав."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-PARTIAL")
    await _make_ff_stock_row(db_session, project, warehouse, bc, nom.id, qty_good=200)  # списал 40
    db_session.add(
        WarehouseStock(
            project_id=project.id, warehouse_id=warehouse.id,
            nomenclature_id=nom.id, barcode=bc, quantity=240,
        )
    )
    await db_session.commit()
    asm = await _make_assembly_with_items(
        db_session, project, warehouse, [(bc, nom.id, 80)], status=AssemblyStatus.READY.value
    )
    await _make_logistics_ff_request(db_session, project, warehouse, asm.id)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    row = {r["barcode"]: r for r in data["rows"]}[bc]
    assert row["ff_logistics"] == 40  # без клампа было бы 80 → ложный +40
    assert row["ff_good"] == 240
    assert row["diff"] == 0


@pytest.mark.asyncio
async def test_list_stocks_logistics_does_not_mask_real_ff_surplus(db_session, project, warehouse):
    """Реальный профицит ФФ виден: транзит маскирует недостачу, но не создаёт профицит."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-SURPLUS")
    await _make_ff_stock_row(db_session, project, warehouse, bc, nom.id, qty_good=300)
    db_session.add(
        WarehouseStock(
            project_id=project.id, warehouse_id=warehouse.id,
            nomenclature_id=nom.id, barcode=bc, quantity=240,
        )
    )
    await db_session.commit()
    asm = await _make_assembly_with_items(
        db_session, project, warehouse, [(bc, nom.id, 80)], status=AssemblyStatus.READY.value
    )
    await _make_logistics_ff_request(db_session, project, warehouse, asm.id)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    row = {r["barcode"]: r for r in data["rows"]}[bc]
    assert row["ff_logistics"] == 0
    assert row["diff"] == 60  # настоящий излишек ФФ не съеден и не раздут


@pytest.mark.asyncio
async def test_list_stocks_logistics_no_phantom_row_without_stock(db_session, project, warehouse):
    """ШК есть только в составе сборки (ни зеркала, ни нашего остатка) → строки-фантома нет."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-PHANTOM")
    asm = await _make_assembly_with_items(
        db_session, project, warehouse, [(bc, nom.id, 50)], status=AssemblyStatus.READY.value
    )
    await _make_logistics_ff_request(db_session, project, warehouse, asm.id)

    data = await fulfillment_service.list_stocks(db_session, project.id, warehouse.id)
    assert bc not in {r["barcode"] for r in data["rows"]}  # без клампа была бы строка diff=+50
    assert data["totals"]["ff_logistics"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Недоступное к отгрузке у ФФ (вычет из зеркала FBS)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_ff_unavailable_skladbot_defect_is_not_subtracted(db_session, project, warehouse):
    """У skladbot брак лежит ОТДЕЛЬНОЙ корзиной — вычитать его из годного нельзя.

    `amount` (→ qty_good) и `repair_amount` (→ qty_defect) дизъюнктны: на проде
    27.07.2026 у 64 строк из 115 брак БОЛЬШЕ годного, что при вложенной корзине
    невозможно. Прод-кейс 160х230_вишня (склад Газпром, ШК 2044388467336):
    годного 8, брака 14 — вычет схлопывал зеркало FBS в 0, и позиция уезжала
    к WB нулём при живом остатке.
    """
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-DEFECT")
    row = await _make_ff_stock_row(db_session, project, warehouse, bc, nom.id, qty_good=8)
    row.qty_defect = 14
    await db_session.commit()

    out = await fulfillment_service.ff_unavailable_by_nomenclature(db_session, project.id, [warehouse.id])
    assert out == {}


@pytest.mark.asyncio
async def test_ff_unavailable_migfull_uses_provider_available(db_session, project, warehouse):
    """У migfull `qty_good` = stock_actual (весь физический), свободное — qty_nominal."""
    bc = f"BC-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc, article="ART-MIGFULL")
    row = await _make_ff_stock_row(db_session, project, warehouse, bc, nom.id, qty_good=478)
    row.provider = "migfull"
    row.qty_nominal = 12
    row.qty_defect = 0
    await db_session.commit()

    out = await fulfillment_service.ff_unavailable_by_nomenclature(db_session, project.id, [warehouse.id])
    assert out == {warehouse.id: {nom.id: 466}}


# ═══════════════════════════════════════════════════════════════════════════════
# Авто-приём перемещения по факту связанной ФФ-приёмки (PVB-* ← TR-*)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCollectTransferFactCandidates:
    async def _mk_req(self, db_session, project, warehouse, *, ext, transfer_id=None,
                      completed=True, applied=False, kind="inbound", local_archived=False):
        from backend.utils.time import utcnow

        req = FulfillmentRequest(
            project_id=project.id,
            warehouse_id=warehouse.id,
            provider="migfull",
            external_id=ext,
            kind=kind,
            number=ext,
            is_completed=completed,
            local_archived=local_archived,
            stock_transfer_id=transfer_id,
            transfer_fact_applied_at=utcnow() if applied else None,
        )
        db_session.add(req)
        await db_session.commit()
        return req

    @pytest.mark.asyncio
    async def test_collects_only_unapplied_completed_linked(self, db_session, project, warehouse):
        from backend.models.warehouse import StockTransfer
        from backend.services.fulfillment_service import _collect_transfer_fact_candidates

        tr = StockTransfer(
            project_id=project.id, from_warehouse_id=warehouse.id,
            to_warehouse_id=warehouse.id, number=f"TR-T-{project.id}", status="IN_TRANSIT",
        )
        db_session.add(tr)
        await db_session.commit()

        target = await self._mk_req(db_session, project, warehouse, ext="pvb-1", transfer_id=tr.id)
        await self._mk_req(db_session, project, warehouse, ext="pvb-2", transfer_id=tr.id, applied=True)
        # НЕзавершённая («В обработке») — тоже кандидат: порционный приём по
        # фактам до закрытия (канон юзера 2026-07-28); финальный маркер по ней
        # вызывающий не ставит (is_completed=False в 5-м элементе кортежа).
        processing = await self._mk_req(db_session, project, warehouse, ext="pvb-3", transfer_id=tr.id, completed=False)
        await self._mk_req(db_session, project, warehouse, ext="pvb-4", transfer_id=None)
        await self._mk_req(db_session, project, warehouse, ext="pvb-5", transfer_id=tr.id, local_archived=True)

        rows = await _collect_transfer_fact_candidates(db_session, project.id, warehouse.id, "migfull")
        assert {(r[0], r[2], r[4]) for r in rows} == {
            (target.id, "pvb-1", True),
            (processing.id, "pvb-3", False),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Возвраты migfull + пара «вскрытие коробов» (возврат коробов ↔ поступление россыпью)
# ═══════════════════════════════════════════════════════════════════════════════


async def _mk_repack_sku(db_session, project, warehouse, units):
    """Короб + россыпь одного SKU в зеркале остатков → (box_guid, loose_guid, base_barcode)."""
    base = f"B-{_uid()}"
    box_guid, loose_guid = f"bg-{_uid()}", f"lg-{_uid()}"
    db_session.add_all(
        [
            FulfillmentStock(
                project_id=project.id, warehouse_id=warehouse.id, provider="migfull",
                barcode=f"X-{_uid()}", base_barcode=base, units_per_box=units,
                external_product_id=box_guid, qty_good=0, qty_reserve=0, qty_defect=0, qty_nominal=0,
            ),
            FulfillmentStock(
                project_id=project.id, warehouse_id=warehouse.id, provider="migfull",
                barcode=base, units_per_box=1,
                external_product_id=loose_guid, qty_good=0, qty_reserve=0, qty_defect=0, qty_nominal=0,
            ),
        ]
    )
    await db_session.commit()
    return box_guid, loose_guid, base


async def _mk_repack_req(
    db_session, project, warehouse, *, kind, lines, created,
    number=None, archived=False, completed=False, notes="ДЛЯ ФБС",
    inbound_receipt_id=None, repack_return_id=None, repack_matched_at=None,
):
    """FF-заявка зеркала migfull с составом в raw (incoming_lines)."""
    req = FulfillmentRequest(
        project_id=project.id, warehouse_id=warehouse.id, provider="migfull",
        external_id=f"g-{_uid()}", kind=kind, number=number or f"PVB-{_uid()[:7]}",
        stage_code="uploaded", archived=archived, is_completed=completed,
        external_created_at=created,
        raw={"incoming_lines": lines, "notes": notes},
        inbound_receipt_id=inbound_receipt_id,
        repack_return_id=repack_return_id, repack_matched_at=repack_matched_at,
    )
    db_session.add(req)
    await db_session.commit()
    return req


@pytest.mark.asyncio
async def test_sync_migfull_returns_mirrored_and_idempotent(
    db_session, project, warehouse, connected_mig_key, monkeypatch
):
    """Возврат из /returns зеркалится (kind=return, «Возврат», стадия из status,
    total_qty = Σ строк incoming, строки в raw); повторный синк не дублирует."""
    rg, pg = _mig_guid(701), _mig_guid(702)
    ret = _mig_return(
        rg, reference="PVB-0000069",
        incoming_lines=[_mig_line(pg, 1), _mig_line(pg, 5)],
    )
    _mock_mig_fetches(monkeypatch, returns=[ret])

    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)
    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    rows = (
        await db_session.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.project_id == project.id,
                FulfillmentRequest.kind == "return",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.external_id == rg
    assert row.number == "PVB-0000069"
    assert row.type_name == "Возврат"
    assert row.stage_code == "uploaded"
    assert row.status == "Загружен"
    assert row.total_qty == 6
    assert row.archived is False and row.is_completed is False
    assert row.external_created_at == date(2026, 7, 30)
    assert (row.raw or {}).get("incoming_lines")  # состав в raw — для матчера/деталки


@pytest.mark.asyncio
async def test_repack_matcher_exact_pair_and_idempotent(db_session, project, warehouse):
    """Кейс PVB-69↔133: коробá 1,3,5,5,5,2 × units_per_box → штуки поступления
    72,48,90,90,80,18. Точная пара помечается на обоих; повторный прогон — 0."""
    box_qtys = [1, 3, 5, 5, 5, 2]
    units = [72, 16, 18, 18, 16, 9]
    skus = [await _mk_repack_sku(db_session, project, warehouse, u) for u in units]

    d = date(2026, 7, 30)
    ret = await _mk_repack_req(
        db_session, project, warehouse, kind="return", number="PVB-0000069",
        lines=[_mig_line(sku[0], q) for sku, q in zip(skus, box_qtys)],
        created=d, notes="ВОЗВРАТ ДЛЯ ФБС",
    )
    inb = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound", number="PVB-0000133",
        lines=[_mig_line(sku[1], q * u) for sku, q, u in zip(skus, box_qtys, units)],
        created=d, notes="ПОСТУПЛЕНИЕ ДЛЯ ФБС",
    )

    matched = await fulfillment_service._match_repack_pairs(db_session, project.id, warehouse.id)
    assert matched == 1
    await db_session.commit()
    await db_session.refresh(ret)
    await db_session.refresh(inb)
    assert inb.repack_return_id == ret.id
    assert inb.repack_matched_at is not None
    assert ret.repack_matched_at is not None
    assert ret.repack_return_id is None  # id пары — только у поступления

    # Идемпотентность: помеченные не перематчиваются
    assert await fulfillment_service._match_repack_pairs(db_session, project.id, warehouse.id) == 0


@pytest.mark.asyncio
async def test_repack_matcher_qty_mismatch_warns_not_marks(db_session, project, warehouse, caplog):
    """Расхождение штук (пересечение >80%) — warning в лог, пара НЕ помечается."""
    import logging as _logging

    box_guid, loose_guid, _base = await _mk_repack_sku(db_session, project, warehouse, 20)
    d = date(2026, 7, 30)
    ret = await _mk_repack_req(
        db_session, project, warehouse, kind="return",
        lines=[_mig_line(box_guid, 5)], created=d,  # 100 шт
    )
    inb = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound",
        lines=[_mig_line(loose_guid, 90)], created=d,  # 90 шт (90%)
    )

    with caplog.at_level(_logging.WARNING, logger="dds.fulfillment"):
        assert await fulfillment_service._match_repack_pairs(db_session, project.id, warehouse.id) == 0
    assert any("пара НЕ помечена" in r.message for r in caplog.records)
    await db_session.refresh(inb)
    await db_session.refresh(ret)
    assert inb.repack_return_id is None
    assert ret.repack_matched_at is None and inb.repack_matched_at is None


@pytest.mark.asyncio
async def test_repack_matcher_skips_linked_and_out_of_window(db_session, project, warehouse):
    """Поступление с нашим документом (inbound_receipt_id) и поступление вне окна
    ±3 дня — не кандидаты, даже при точном равенстве состава."""
    box_guid, loose_guid, _base = await _mk_repack_sku(db_session, project, warehouse, 10)
    d = date(2026, 7, 30)
    receipt = InboundReceipt(
        project_id=project.id, warehouse_id=warehouse.id, number=f"IN-{_uid()[:6]}", status="EXPECTED"
    )
    db_session.add(receipt)
    await db_session.commit()

    ret = await _mk_repack_req(
        db_session, project, warehouse, kind="return", lines=[_mig_line(box_guid, 2)], created=d
    )
    linked = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound",
        lines=[_mig_line(loose_guid, 20)], created=d, inbound_receipt_id=receipt.id,
    )
    late = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound",
        lines=[_mig_line(loose_guid, 20)], created=d + timedelta(days=5),
    )

    assert await fulfillment_service._match_repack_pairs(db_session, project.id, warehouse.id) == 0
    for req in (ret, linked, late):
        await db_session.refresh(req)
    assert linked.repack_return_id is None and late.repack_return_id is None
    assert ret.repack_matched_at is None


@pytest.mark.asyncio
async def test_repack_matcher_mixed_lines_and_box_requirement(db_session, project, warehouse):
    """Смесь короба+россыпь в возврате МАТЧИТСЯ (живой кейс PVB-0000068: 87
    короб-строк и 3 россыпи одним возвратом); возврат ВООБЩЕ без короб-строк —
    возможно, РЕАЛЬНЫЙ возврат: не матчим."""
    box_guid, loose_guid, _base = await _mk_repack_sku(db_session, project, warehouse, 10)
    d = date(2026, 7, 30)
    ret = await _mk_repack_req(
        db_session, project, warehouse, kind="return",
        lines=[_mig_line(box_guid, 2), _mig_line(loose_guid, 5)], created=d,  # 20 + 5 россыпью
    )
    inb = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound",
        lines=[_mig_line(loose_guid, 25)], created=d,
    )

    assert await fulfillment_service._match_repack_pairs(db_session, project.id, warehouse.id) == 1
    await db_session.commit()  # commit — на вызывающем (контракт матчера)
    await db_session.refresh(inb)
    await db_session.refresh(ret)
    assert inb.repack_return_id == ret.id and ret.repack_matched_at is not None

    # Чисто-россыпной возврат при точно совпадающем поступлении — НЕ вскрытие.
    ret2 = await _mk_repack_req(
        db_session, project, warehouse, kind="return",
        lines=[_mig_line(loose_guid, 7)], created=d,
    )
    inb2 = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound",
        lines=[_mig_line(loose_guid, 7)], created=d,
    )
    assert await fulfillment_service._match_repack_pairs(db_session, project.id, warehouse.id) == 0
    await db_session.refresh(inb2)
    await db_session.refresh(ret2)
    assert inb2.repack_return_id is None and ret2.repack_matched_at is None


@pytest.mark.asyncio
async def test_sync_migfull_repack_pair_via_sync(db_session, project, warehouse, connected_mig_key, monkeypatch):
    """Интеграционно: синк зеркалит возврат и приёмку (строки приёмки — в raw
    через enrich) и сам помечает пару «вскрытия» в той же транзакции."""
    box_bc = "12043160330575"
    base_bc = fulfillment_service._itf14_to_ean13(box_bc)
    assert base_bc is not None
    pb, pl, rg, sg = _mig_guid(801), _mig_guid(802), _mig_guid(803), _mig_guid(804)

    _mock_mig_fetches(
        monkeypatch,
        products=[
            _mig_product(pb, name="Ковер короб 16 шт.", actual=5),
            _mig_product(pl, name="Ковер россыпь", actual=10),
        ],
        product_details={
            pb: {"barcodes": [{"value": box_bc, "is_primary": True}]},
            pl: {"barcodes": [{"value": base_bc, "is_primary": True}]},
        },
        submissions=[
            _mig_submission(sg, status="processing", reference="PVB-0000133", sub_date="2026-07-30")
        ],
        submission_lines={(sg, "incoming"): [_mig_line(pl, 48, name="Ковер россыпь")]},
        returns=[
            _mig_return(
                rg, reference="PVB-0000069", ret_date="2026-07-30",
                incoming_lines=[_mig_line(pb, 3, name="Ковер короб 16 шт.")],  # 3 × 16 = 48
            )
        ],
    )

    await fulfillment_service.sync_warehouse(db_session, project.id, warehouse.id)

    ret_row = (
        await db_session.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.project_id == project.id, FulfillmentRequest.external_id == rg
            )
        )
    ).scalar_one()
    inb_row = (
        await db_session.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.project_id == project.id, FulfillmentRequest.external_id == sg
            )
        )
    ).scalar_one()
    assert (inb_row.raw or {}).get("incoming_lines")  # enrich сложил состав в raw
    assert inb_row.repack_return_id == ret_row.id
    assert inb_row.repack_matched_at is not None and ret_row.repack_matched_at is not None


@pytest.mark.asyncio
async def test_inbound_locked_excludes_repack_receipt(db_session, project, warehouse):
    """Приёмка, привязанная к repack-поступлению, ИСКЛЮЧЕНА из резерва «в приёмке»
    (иначе штуки вскрытия повисли бы в резерве и занизили FBS-отдачу)."""
    bc_repack, bc_normal = f"BC-{_uid()}", f"BC-{_uid()}"
    nom_repack = await _make_nomenclature(db_session, project.id, bc_repack)
    nom_normal = await _make_nomenclature(db_session, project.id, bc_normal)
    d = date(2026, 7, 30)

    receipts = {}
    for key in ("repack", "normal"):
        receipt = InboundReceipt(
            project_id=project.id, warehouse_id=warehouse.id, number=f"IN-{_uid()[:6]}", status="EXPECTED"
        )
        db_session.add(receipt)
        await db_session.commit()
        await db_session.refresh(receipt)
        receipts[key] = receipt
    db_session.add_all(
        [
            InboundReceiptItem(
                project_id=project.id, receipt_id=receipts["repack"].id,
                nomenclature_id=nom_repack.id, barcode=bc_repack, expected_qty=398,
            ),
            InboundReceiptItem(
                project_id=project.id, receipt_id=receipts["normal"].id,
                nomenclature_id=nom_normal.id, barcode=bc_normal, expected_qty=30,
            ),
        ]
    )
    await db_session.commit()

    ret = await _mk_repack_req(db_session, project, warehouse, kind="return", lines=[], created=d)
    await _mk_repack_req(
        db_session, project, warehouse, kind="inbound", lines=[], created=d,
        inbound_receipt_id=receipts["repack"].id, repack_return_id=ret.id, repack_matched_at=utcnow(),
    )

    locked = await fulfillment_service._migfull_inbound_locked_by_barcode(
        db_session, project.id, warehouse.id
    )
    assert bc_repack not in locked  # repack-приёмка исключена
    assert locked.get(bc_normal) == 30  # обычная — считается


@pytest.mark.asyncio
async def test_accept_and_transfer_fact_guards_skip_repack(db_session, project, warehouse):
    """Авто-ACCEPT приёмок и transfer-fact пропускают repack-поступления
    (страховка от будущей ручной привязки)."""
    d = date(2026, 7, 30)
    receipt = InboundReceipt(
        project_id=project.id, warehouse_id=warehouse.id, number=f"IN-{_uid()[:6]}", status="EXPECTED"
    )
    db_session.add(receipt)
    await db_session.commit()
    await db_session.refresh(receipt)

    ret = await _mk_repack_req(db_session, project, warehouse, kind="return", lines=[], created=d)
    repack_inb = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound", lines=[], created=d, completed=True,
        inbound_receipt_id=receipt.id, repack_return_id=ret.id, repack_matched_at=utcnow(),
    )
    assert (
        await fulfillment_service._collect_inbound_accept_candidates(db_session, project.id, [repack_inb])
        == []
    )
    # Контроль: без repack-пометки та же заявка — кандидат
    repack_inb.repack_return_id = None
    assert await fulfillment_service._collect_inbound_accept_candidates(
        db_session, project.id, [repack_inb]
    ) == [receipt.id]
    repack_inb.repack_return_id = ret.id

    from backend.models.warehouse import StockTransfer

    tr = StockTransfer(
        project_id=project.id, from_warehouse_id=warehouse.id,
        to_warehouse_id=warehouse.id, number=f"TR-R-{project.id}", status="IN_TRANSIT",
    )
    db_session.add(tr)
    await db_session.commit()
    repack_inb.stock_transfer_id = tr.id
    repack_inb.inbound_receipt_id = None
    await db_session.commit()

    rows = await fulfillment_service._collect_transfer_fact_candidates(
        db_session, project.id, warehouse.id, "migfull"
    )
    assert repack_inb.id not in {r[0] for r in rows}


@pytest.mark.asyncio
async def test_repack_link_guards(db_session, project, warehouse):
    """kind=return не привязывается вовсе; repack-поступлению кандидаты/линк не предлагаются."""
    d = date(2026, 7, 30)
    receipt = InboundReceipt(
        project_id=project.id, warehouse_id=warehouse.id, number=f"IN-{_uid()[:6]}", status="EXPECTED"
    )
    db_session.add(receipt)
    await db_session.commit()
    await db_session.refresh(receipt)

    ret = await _mk_repack_req(db_session, project, warehouse, kind="return", lines=[], created=d)
    repack_inb = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound", lines=[], created=d,
        repack_return_id=ret.id, repack_matched_at=utcnow(),
    )

    with pytest.raises(ValueError, match="Возврат"):
        await fulfillment_service.link_request(
            db_session, project.id, ret.id, inbound_receipt_id=receipt.id
        )
    with pytest.raises(ValueError, match="вскрытия"):
        await fulfillment_service.link_request(
            db_session, project.id, repack_inb.id, inbound_receipt_id=receipt.id
        )
    with pytest.raises(ValueError):
        await fulfillment_service.get_link_candidates(db_session, project.id, warehouse.id, ret.id)
    with pytest.raises(ValueError, match="вскрытия"):
        await fulfillment_service.get_link_candidates(
            db_session, project.id, warehouse.id, repack_inb.id
        )
    # Связи не появились
    await db_session.refresh(ret)
    await db_session.refresh(repack_inb)
    assert ret.inbound_receipt_id is None and repack_inb.inbound_receipt_id is None


@pytest.mark.asyncio
async def test_return_detail_renders_from_raw_without_live_lines(
    db_session, project, warehouse
):
    """Деталка kind=return строится из raw.incoming_lines БЕЗ похода в API.

    Отдельного lines-ресурса у /returns нет, а до фикса ветка «else» деталки
    ходила в submissions/{guid}/lines с guid'ом ВОЗВРАТА: migfull отвечает 500
    «No query results for model [Submission]», и пять ретраев открывали circuit
    breaker на 120 с (прод-кейс PVB-0000068, 30.07). Тест работает без
    integration key вовсе — приёмочная ветка на этом падала бы «не подключён».
    """
    pg = _mig_guid(991)
    ret = await _mk_repack_req(
        db_session, project, warehouse, kind="return",
        lines=[_mig_line(pg, 3, name="Ковер короб 16 шт.")],
        created=date(2026, 7, 30),
    )

    detail = await fulfillment_service.get_request_detail(
        db_session, project.id, warehouse.id, ret.id
    )

    assert detail["kind"] == "return"
    products = detail.get("products") or []
    assert len(products) == 1
    # Состав из raw: 3 строки заявленного, факта нет.
    assert products[0]["qty"] == 3
    assert products[0]["accepted_qty"] == 0


@pytest.mark.asyncio
async def test_return_not_in_assembled_by_guid(db_session, project, warehouse):
    """kind=return не попадает в «Собрано» (учёт резерва идёт только по kind=assembly)."""
    guid = f"g-{_uid()}"
    req = FulfillmentRequest(
        project_id=project.id, warehouse_id=warehouse.id, provider="migfull",
        external_id=f"r-{_uid()}", kind="return", stage_code="uploaded",
        raw={"planned_lines": [{"product_guid": guid, "quantity": 7}]},
    )
    db_session.add(req)
    await db_session.commit()

    assembled = await fulfillment_service._migfull_assembled_by_guid(db_session, project.id, warehouse.id)
    assert guid not in assembled


@pytest.mark.asyncio
async def test_list_requests_repack_enrichment(db_session, project, warehouse):
    """Выдача: repack_pair_number зеркально у обеих сторон пары; repack_unpaired —
    только у kind=return старше 3 дней без пары и не отменённого."""
    today = utcnow().date()
    ret = await _mk_repack_req(
        db_session, project, warehouse, kind="return", number="PVB-R-1", lines=[],
        created=today - timedelta(days=1), repack_matched_at=utcnow(),
    )
    inb = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound", number="PVB-I-1", lines=[],
        created=today - timedelta(days=1), repack_return_id=ret.id, repack_matched_at=utcnow(),
    )
    old_unpaired = await _mk_repack_req(
        db_session, project, warehouse, kind="return", number="PVB-R-OLD", lines=[],
        created=today - timedelta(days=10),
    )
    fresh_unpaired = await _mk_repack_req(
        db_session, project, warehouse, kind="return", number="PVB-R-NEW", lines=[],
        created=today,
    )
    canceled = await _mk_repack_req(
        db_session, project, warehouse, kind="return", number="PVB-R-CANC", lines=[],
        created=today - timedelta(days=10), archived=True,
    )

    rows = {r["id"]: r for r in await fulfillment_service.list_requests(db_session, project.id, warehouse.id)}
    assert rows[inb.id]["repack_return_id"] == ret.id
    assert rows[inb.id]["repack_pair_number"] == "PVB-R-1"
    assert rows[inb.id]["repack_unpaired"] is False
    assert rows[ret.id]["repack_pair_number"] == "PVB-I-1"
    assert rows[ret.id]["repack_unpaired"] is False
    assert rows[old_unpaired.id]["repack_unpaired"] is True
    assert rows[old_unpaired.id]["repack_pair_number"] is None
    assert rows[fresh_unpaired.id]["repack_unpaired"] is False
    assert rows[canceled.id]["repack_unpaired"] is False

    # kind=return фильтруется параметром kind
    only_returns = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, kind="return")
    assert {r["id"] for r in only_returns} == {ret.id, old_unpaired.id, fresh_unpaired.id, canceled.id}


# ═══════════════════════════════════════════════════════════════════════════════
# Ручная связка пары «вскрытие коробов» (repack-candidates / repack-link)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_repack_candidates_ranking_and_filters(db_session, project, warehouse):
    """exact-кандидат первый (даже если дальше по дате), неточный — с посчитанным
    overlap_pct; связанное с нашим документом / уже спаренное / вне окна ±14 дней
    поступления — не кандидаты; неразрешённый guid НЕ выкидывает кандидата."""
    box_guid, loose_guid, _base = await _mk_repack_sku(db_session, project, warehouse, 10)
    d = date(2026, 7, 30)
    receipt = InboundReceipt(
        project_id=project.id, warehouse_id=warehouse.id, number=f"IN-{_uid()[:6]}", status="EXPECTED"
    )
    db_session.add(receipt)
    await db_session.commit()

    ret = await _mk_repack_req(
        db_session, project, warehouse, kind="return",
        lines=[_mig_line(box_guid, 5)], created=d,  # 5 коробов × 10 = 50 шт
    )
    exact_inb = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound",
        lines=[_mig_line(loose_guid, 50)], created=d + timedelta(days=2),
    )
    partial = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound",
        lines=[_mig_line(loose_guid, 45)], created=d,  # пересечение 45/50 = 90.0%
    )
    unresolved = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound",
        lines=[_mig_line(f"unknown-{_uid()}", 7)], created=d,  # guid без ШК: units=1
    )
    linked = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound",
        lines=[_mig_line(loose_guid, 50)], created=d, inbound_receipt_id=receipt.id,
    )
    other_ret = await _mk_repack_req(db_session, project, warehouse, kind="return", lines=[], created=d)
    paired = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound",
        lines=[_mig_line(loose_guid, 50)], created=d,
        repack_return_id=other_ret.id, repack_matched_at=utcnow(),
    )
    late = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound",
        lines=[_mig_line(loose_guid, 50)], created=d + timedelta(days=15),  # вне ±14
    )

    out = await fulfillment_service.get_repack_candidates(db_session, project.id, warehouse.id, ret.id)
    assert out["return_id"] == ret.id
    assert out["return_number"] == ret.number
    assert out["return_units"] == 50
    ids = [c["id"] for c in out["candidates"]]
    assert linked.id not in ids and paired.id not in ids and late.id not in ids
    assert ids == [exact_inb.id, partial.id, unresolved.id]
    exact_row, partial_row, unresolved_row = out["candidates"]
    assert exact_row["exact"] is True
    assert exact_row["overlap_pct"] == 100.0
    assert exact_row["units_sum"] == 50
    assert partial_row["exact"] is False
    assert partial_row["overlap_pct"] == 90.0
    assert partial_row["units_sum"] == 45
    assert unresolved_row["exact"] is False
    assert unresolved_row["units_sum"] == 7  # сырое qty с units=1
    assert unresolved_row["overlap_pct"] == 0

    # kind=inbound кандидатов не получает — только возврат
    with pytest.raises(ValueError, match="возврат"):
        await fulfillment_service.get_repack_candidates(db_session, project.id, warehouse.id, exact_inb.id)
    # несуществующая заявка
    with pytest.raises(ValueError, match="не найден"):
        await fulfillment_service.get_repack_candidates(db_session, project.id, warehouse.id, 999_999_999)


@pytest.mark.asyncio
async def test_repack_candidates_unresolved_return_units_none(db_session, project, warehouse):
    """Состав возврата не разрешился в ШК → return_units=None, overlap у всех 0."""
    _box_guid, loose_guid, _base = await _mk_repack_sku(db_session, project, warehouse, 10)
    d = date(2026, 7, 30)
    ret = await _mk_repack_req(
        db_session, project, warehouse, kind="return",
        lines=[_mig_line(f"unknown-{_uid()}", 3)], created=d,
    )
    inb = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound",
        lines=[_mig_line(loose_guid, 30)], created=d,
    )

    out = await fulfillment_service.get_repack_candidates(db_session, project.id, warehouse.id, ret.id)
    assert out["return_units"] is None
    row = next(c for c in out["candidates"] if c["id"] == inb.id)
    assert row["exact"] is False and row["overlap_pct"] == 0 and row["units_sum"] == 30


@pytest.mark.asyncio
async def test_repack_manual_link_success_and_double_link_guards(db_session, project, warehouse):
    """Успешная ручная связка помечает обе стороны; повторная связка любой из
    сторон — ValueError."""
    d = date(2026, 7, 30)
    ret = await _mk_repack_req(db_session, project, warehouse, kind="return", lines=[], created=d)
    inb = await _mk_repack_req(db_session, project, warehouse, kind="inbound", lines=[], created=d)

    row = await fulfillment_service.link_repack_pair(db_session, project.id, warehouse.id, ret.id, inb.id)
    await db_session.refresh(ret)
    await db_session.refresh(inb)
    assert inb.repack_return_id == ret.id
    assert inb.repack_matched_at is not None and ret.repack_matched_at is not None
    assert row["id"] == ret.id
    assert row["repack_pair_number"] == inb.number  # номер пары — сразу в ответе

    # возврат уже в паре
    inb2 = await _mk_repack_req(db_session, project, warehouse, kind="inbound", lines=[], created=d)
    with pytest.raises(ValueError, match="уже помечен"):
        await fulfillment_service.link_repack_pair(db_session, project.id, warehouse.id, ret.id, inb2.id)
    # поступление уже в паре
    ret2 = await _mk_repack_req(db_session, project, warehouse, kind="return", lines=[], created=d)
    with pytest.raises(ValueError, match="уже помечено"):
        await fulfillment_service.link_repack_pair(db_session, project.id, warehouse.id, ret2.id, inb.id)


@pytest.mark.asyncio
async def test_repack_manual_link_kind_warehouse_and_linked_guards(db_session, project, warehouse):
    """Чужой склад / чужой kind / поступление с нашим документом / отменённые —
    ValueError, ничего не помечается."""
    d = date(2026, 7, 30)
    other_wh = Warehouse(project_id=project.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(other_wh)
    await db_session.commit()
    receipt = InboundReceipt(
        project_id=project.id, warehouse_id=warehouse.id, number=f"IN-{_uid()[:6]}", status="EXPECTED"
    )
    db_session.add(receipt)
    await db_session.commit()

    ret = await _mk_repack_req(db_session, project, warehouse, kind="return", lines=[], created=d)
    inb = await _mk_repack_req(db_session, project, warehouse, kind="inbound", lines=[], created=d)
    inb_other = await _mk_repack_req(db_session, project, other_wh, kind="inbound", lines=[], created=d)

    # поступление с чужого склада не видно в скоупе склада возврата
    with pytest.raises(ValueError, match="не найдено"):
        await fulfillment_service.link_repack_pair(db_session, project.id, warehouse.id, ret.id, inb_other.id)
    # kind перепутан: в роли возврата — поступление и наоборот
    with pytest.raises(ValueError, match="возврат"):
        await fulfillment_service.link_repack_pair(db_session, project.id, warehouse.id, inb.id, ret.id)
    ret2 = await _mk_repack_req(db_session, project, warehouse, kind="return", lines=[], created=d)
    with pytest.raises(ValueError, match="поступление"):
        await fulfillment_service.link_repack_pair(db_session, project.id, warehouse.id, ret.id, ret2.id)
    # поступление связано с нашей приёмкой — реальный приход
    linked = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound", lines=[], created=d,
        inbound_receipt_id=receipt.id,
    )
    with pytest.raises(ValueError, match="нашим документом"):
        await fulfillment_service.link_repack_pair(db_session, project.id, warehouse.id, ret.id, linked.id)
    # отменённое (archived) поступление
    canceled = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound", lines=[], created=d, archived=True
    )
    with pytest.raises(ValueError, match="отменен"):
        await fulfillment_service.link_repack_pair(db_session, project.id, warehouse.id, ret.id, canceled.id)

    await db_session.refresh(ret)
    await db_session.refresh(inb)
    assert ret.repack_matched_at is None and inb.repack_return_id is None


@pytest.mark.asyncio
async def test_repack_manual_unlink_clears_both_sides(db_session, project, warehouse):
    """unlink снимает пометку с обеих сторон; unlink непомеченного — ValueError."""
    d = date(2026, 7, 30)
    ret = await _mk_repack_req(db_session, project, warehouse, kind="return", lines=[], created=d)
    inb = await _mk_repack_req(db_session, project, warehouse, kind="inbound", lines=[], created=d)
    await fulfillment_service.link_repack_pair(db_session, project.id, warehouse.id, ret.id, inb.id)

    row = await fulfillment_service.unlink_repack_pair(db_session, project.id, warehouse.id, ret.id)
    await db_session.refresh(ret)
    await db_session.refresh(inb)
    assert ret.repack_matched_at is None
    assert inb.repack_return_id is None and inb.repack_matched_at is None
    assert row["id"] == ret.id and row["repack_pair_number"] is None

    # повторный unlink — уже нечего снимать
    with pytest.raises(ValueError, match="не помечен"):
        await fulfillment_service.unlink_repack_pair(db_session, project.id, warehouse.id, ret.id)
    # unlink поступления (не возврата) — ValueError
    with pytest.raises(ValueError, match="возврат"):
        await fulfillment_service.unlink_repack_pair(db_session, project.id, warehouse.id, inb.id)


@pytest.mark.asyncio
async def test_repack_matcher_respects_manual_pair(db_session, project, warehouse):
    """Авто-матчер не перематчивает вручную связанную пару (обе стороны помечены),
    даже когда состав совпадает точно."""
    box_guid, loose_guid, _base = await _mk_repack_sku(db_session, project, warehouse, 10)
    d = date(2026, 7, 30)
    ret = await _mk_repack_req(
        db_session, project, warehouse, kind="return", lines=[_mig_line(box_guid, 2)], created=d
    )
    inb = await _mk_repack_req(
        db_session, project, warehouse, kind="inbound", lines=[_mig_line(loose_guid, 20)], created=d
    )
    await fulfillment_service.link_repack_pair(db_session, project.id, warehouse.id, ret.id, inb.id)
    manual_ts = inb.repack_matched_at

    assert await fulfillment_service._match_repack_pairs(db_session, project.id, warehouse.id) == 0
    await db_session.commit()  # commit — на вызывающем (контракт матчера)
    await db_session.refresh(ret)
    await db_session.refresh(inb)
    assert inb.repack_return_id == ret.id
    assert inb.repack_matched_at == manual_ts  # ручная пометка не перезаписана
