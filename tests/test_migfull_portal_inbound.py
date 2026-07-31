# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты создания ПОСТАВКИ (приёмки) в портале ФФ «Натали» из нашей InboundReceipt.

Портал-клиент замокан (никаких живых POST на боевой портал!): проверяем сервис —
превью описи, happy-path create+upload, идемпотентность (409/force_resend),
исходы ошибок портала (FAILED/UNCERTAIN) и автосвязь FulfillmentRequest —
плюс маппинг ошибок роутера.
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.integrations.migfull_portal_client import MigfullCreateResult, MigfullPortalError, MigfullUploadResult
from backend.models import (
    CostOrder,
    FulfillmentRequest,
    FulfillmentStock,
    InboundReceipt,
    IntegrationKey,
    MigfullShipmentOrder,
    MigfullShipmentStatus,
    Nomenclature,
    Warehouse,
)
from backend.models.warehouse import InboundReceiptItem, InboundStatus
from backend.schemas.migfull_portal import MigfullInboundSendRequest
from backend.services import migfull_portal_inbound as svc
from backend.services.migfull_portal_service import MigfullPortalServiceError
from backend.utils.crypto import encrypt

GUID = "4dfc6d57-acda-432c-be18-de8f4236a323"
EAN = "2049985828273"
ITF = "12049985828273"


class FakePortalClient:
    """Мок портального клиента: пишет вызовы, отдаёт настроенные исходы."""

    def __init__(
        self,
        create_result: MigfullCreateResult | None = None,
        upload_result: MigfullUploadResult | None = None,
        create_exc: Exception | None = None,
        upload_exc: Exception | None = None,
    ):
        self.create_result = create_result or MigfullCreateResult(guid=GUID)
        self.upload_result = upload_result or MigfullUploadResult(ok=True, reference="PVB-0000401")
        self.create_exc = create_exc
        self.upload_exc = upload_exc
        self.created_headers: list[dict] = []
        self.uploads: list[tuple[str, str, str]] = []

    async def __aenter__(self) -> "FakePortalClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def create_submission(self, header: dict) -> MigfullCreateResult:
        self.created_headers.append(header)
        if self.create_exc:
            raise self.create_exc
        return self.create_result

    async def upload_submission_opis(
        self, guid: str, filename: str, content: bytes, content_type: str
    ) -> MigfullUploadResult:
        assert content  # опись собрана
        self.uploads.append((guid, filename, content_type))
        if self.upload_exc:
            raise self.upload_exc
        return self.upload_result


def _use_fake_client(monkeypatch, fake: FakePortalClient) -> None:
    monkeypatch.setattr(svc, "_client_from_key", lambda key: fake)

    async def _no_session(client, fn):
        return await fn()

    monkeypatch.setattr(svc, "_with_portal_session", _no_session)


@pytest_asyncio.fixture
async def env(db_session, project):
    """Склад «Натали» + портальный ключ + машина V-… с приёмкой (40 шт, короб по 5)."""
    wh = Warehouse(project_id=project.id, name="натали", warehouse_type="FULFILLMENT")
    db_session.add(wh)
    await db_session.flush()

    db_session.add(
        IntegrationKey(
            project_id=project.id,
            service="migfull_portal",
            encrypted_key=encrypt("portal-pass"),
            config={"login": "test@example.com"},
            warehouse_id=wh.id,
            is_active=True,
        )
    )
    nom = Nomenclature(project_id=project.id, barcode=EAN, article_seller="ELKA")
    db_session.add(nom)
    vehicle = CostOrder(project_id=project.id, order_no=f"V-T{uuid.uuid4().hex[:6]}")
    db_session.add(vehicle)
    await db_session.flush()

    receipt = InboundReceipt(
        project_id=project.id,
        warehouse_id=wh.id,
        number="IN-777",
        status=InboundStatus.EXPECTED.value,
        planned_date=date(2026, 8, 5),
        cost_order_id=vehicle.id,
    )
    db_session.add(receipt)
    await db_session.flush()
    db_session.add(
        InboundReceiptItem(
            project_id=project.id,
            receipt_id=receipt.id,
            nomenclature_id=nom.id,
            barcode=EAN,
            expected_qty=40,
        )
    )
    # Зеркало остатков ФФ: сопоставление короб (ITF14, 5 шт) → россыпь (EAN13)
    db_session.add(
        FulfillmentStock(
            project_id=project.id,
            warehouse_id=wh.id,
            provider="migfull",
            barcode=ITF,
            name="ELKA короб 5 шт.",
            base_barcode=EAN,
            units_per_box=5,
        )
    )
    await db_session.commit()

    from types import SimpleNamespace

    return SimpleNamespace(project_id=project.id, warehouse=wh, vehicle=vehicle, receipt=receipt)


# ─── Draft (превью для confirm-модалки) ───────────────────────────────────────


async def test_draft_preview_boxes_and_prefill(db_session, env):
    draft = await svc.build_inbound_draft(db_session, env.project_id, env.receipt.id)
    assert draft.eligible is True
    assert draft.already_sent is False
    assert len(draft.opis_lines) == 1
    line = draft.opis_lines[0]
    assert line.is_box is True
    assert line.barcode == ITF
    assert line.quantity == 8  # 40 шт / 5 в коробе
    assert draft.total_boxes == 8
    assert draft.total_pieces == 40
    assert draft.prefill.number == env.vehicle.order_no
    assert draft.prefill.vehicle_order_no == env.vehicle.order_no
    assert draft.prefill.receipt_number == "IN-777"
    assert draft.prefill.submission_date == date(2026, 8, 5)
    assert env.vehicle.order_no in (draft.prefill.notes or "")
    assert "IN-777" in (draft.prefill.notes or "")


async def test_draft_receipt_not_found(db_session, env):
    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.build_inbound_draft(db_session, env.project_id, 99_999_999)
    assert exc.value.status_code == 404


# ─── Send: happy path ─────────────────────────────────────────────────────────


async def test_send_creates_submission_and_autolinks(db_session, env, monkeypatch):
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)

    res = await svc.send_submission(db_session, env.project_id, env.receipt.id, MigfullInboundSendRequest())
    assert res.ok is True
    assert res.shipment_guid == GUID
    assert res.shipment_number == "PVB-0000401"

    # Шапка ушла с нашим номером машины и примечанием для оператора
    assert fake.created_headers[0]["number"] == env.vehicle.order_no
    assert env.vehicle.order_no in str(fake.created_headers[0]["notes"])
    assert fake.uploads and fake.uploads[0][0] == GUID

    # Audit SENT с маркером идемпотентности (inbound_receipt_id)
    order = (
        await db_session.execute(
            select(MigfullShipmentOrder).where(
                MigfullShipmentOrder.project_id == env.project_id,
                MigfullShipmentOrder.inbound_receipt_id == env.receipt.id,
            )
        )
    ).scalar_one()
    assert order.status == MigfullShipmentStatus.SENT
    assert order.shipment_guid == GUID
    assert order.assembly_request_id is None

    # Автосвязь: FulfillmentRequest kind=inbound уже указывает на нашу приёмку
    req = (
        await db_session.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.project_id == env.project_id,
                FulfillmentRequest.provider == "migfull",
                FulfillmentRequest.external_id == GUID,
            )
        )
    ).scalar_one()
    assert req.kind == "inbound"
    assert req.inbound_receipt_id == env.receipt.id
    assert req.number == "PVB-0000401"
    assert req.total_qty == 40


# ─── Идемпотентность ─────────────────────────────────────────────────────────


async def test_send_repeat_is_409_until_forced(db_session, env, monkeypatch):
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)

    res = await svc.send_submission(db_session, env.project_id, env.receipt.id, MigfullInboundSendRequest())
    assert res.ok is True

    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.send_submission(db_session, env.project_id, env.receipt.id, MigfullInboundSendRequest())
    assert exc.value.status_code == 409
    assert len(fake.created_headers) == 1  # второй create на портал НЕ ушёл

    res2 = await svc.send_submission(
        db_session, env.project_id, env.receipt.id, MigfullInboundSendRequest(force_resend=True)
    )
    assert res2.ok is True
    assert len(fake.created_headers) == 2


async def test_manually_linked_pvb_also_blocks(db_session, env, monkeypatch):
    """Натали уже завела PVB и её привязали вручную → создание из DDS = дубль (409)."""
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    db_session.add(
        FulfillmentRequest(
            project_id=env.project_id,
            warehouse_id=env.warehouse.id,
            provider="migfull",
            external_id=str(uuid.uuid4()),
            number="PVB-0000124",
            kind="inbound",
            inbound_receipt_id=env.receipt.id,
        )
    )
    await db_session.commit()

    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.send_submission(db_session, env.project_id, env.receipt.id, MigfullInboundSendRequest())
    assert exc.value.status_code == 409
    assert "PVB-0000124" in str(exc.value)
    assert fake.created_headers == []


# ─── Ошибки портала ──────────────────────────────────────────────────────────


async def test_create_error_records_failed_and_allows_retry(db_session, env, monkeypatch):
    fake = FakePortalClient(create_exc=MigfullPortalError("портал недоступен", status_code=502))
    _use_fake_client(monkeypatch, fake)

    res = await svc.send_submission(db_session, env.project_id, env.receipt.id, MigfullInboundSendRequest())
    assert res.ok is False
    assert res.shipment_guid is None
    assert "портал недоступен" in (res.message or "")

    order = (
        await db_session.execute(
            select(MigfullShipmentOrder).where(MigfullShipmentOrder.inbound_receipt_id == env.receipt.id)
        )
    ).scalar_one()
    assert order.status == MigfullShipmentStatus.FAILED
    # FAILED (ничего не создано) НЕ блокирует повтор
    fake2 = FakePortalClient()
    _use_fake_client(monkeypatch, fake2)
    res2 = await svc.send_submission(db_session, env.project_id, env.receipt.id, MigfullInboundSendRequest())
    assert res2.ok is True


async def test_upload_error_is_uncertain_and_blocks_resend(db_session, env, monkeypatch):
    """Шапка создана (guid есть), опись упала → UNCERTAIN + автосвязь; повтор — 409."""
    fake = FakePortalClient(upload_exc=MigfullPortalError("файл отклонён", status_code=502))
    _use_fake_client(monkeypatch, fake)

    res = await svc.send_submission(db_session, env.project_id, env.receipt.id, MigfullInboundSendRequest())
    assert res.ok is False
    assert res.shipment_guid == GUID

    order = (
        await db_session.execute(
            select(MigfullShipmentOrder).where(MigfullShipmentOrder.inbound_receipt_id == env.receipt.id)
        )
    ).scalar_one()
    assert order.status == MigfullShipmentStatus.UNCERTAIN
    # Автосвязь есть даже при UNCERTAIN (шапка на портале уже существует)
    req = (
        await db_session.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.project_id == env.project_id,
                FulfillmentRequest.external_id == GUID,
            )
        )
    ).scalar_one()
    assert req.inbound_receipt_id == env.receipt.id

    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.send_submission(db_session, env.project_id, env.receipt.id, MigfullInboundSendRequest())
    assert exc.value.status_code == 409


# ─── Гейты ───────────────────────────────────────────────────────────────────


async def test_not_configured_clear_message(db_session, other_project, monkeypatch):
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.send_submission(db_session, other_project.id, 1, MigfullInboundSendRequest())
    assert exc.value.status_code == 400
    assert "не настроена" in str(exc.value)
    assert fake.created_headers == []


async def test_wrong_warehouse_blocked(db_session, env, monkeypatch):
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    other_wh = Warehouse(project_id=env.project_id, name="другой", warehouse_type="OWN")
    db_session.add(other_wh)
    await db_session.flush()
    receipt = InboundReceipt(
        project_id=env.project_id,
        warehouse_id=other_wh.id,
        number="IN-778",
        status=InboundStatus.EXPECTED.value,
    )
    db_session.add(receipt)
    await db_session.commit()

    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.send_submission(db_session, env.project_id, receipt.id, MigfullInboundSendRequest())
    assert exc.value.status_code == 400
    assert fake.created_headers == []


async def test_accepted_and_cancelled_receipt_blocked(db_session, env, monkeypatch):
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    for status in (InboundStatus.ACCEPTED.value, InboundStatus.CANCELLED.value):
        env.receipt.status = status
        await db_session.commit()
        with pytest.raises(MigfullPortalServiceError) as exc:
            await svc.send_submission(db_session, env.project_id, env.receipt.id, MigfullInboundSendRequest())
        assert exc.value.status_code == 400
    assert fake.created_headers == []


async def test_empty_receipt_no_lines(db_session, env, monkeypatch):
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    empty = InboundReceipt(
        project_id=env.project_id,
        warehouse_id=env.warehouse.id,
        number="IN-779",
        status=InboundStatus.EXPECTED.value,
    )
    db_session.add(empty)
    await db_session.commit()
    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.send_submission(db_session, env.project_id, empty.id, MigfullInboundSendRequest())
    assert exc.value.status_code == 400
    assert "нет позиций" in str(exc.value)


# ─── Роутер: маппинг ошибок сервиса в HTTP ───────────────────────────────────


async def test_router_maps_service_errors(client, auth_headers):
    resp = await client.post("/api/v1/projects", json={"name": "MP Inbound"}, headers=auth_headers)
    assert resp.status_code in (200, 201)
    project_id = resp.json()["id"]
    headers = {**auth_headers, "X-Project-Id": str(project_id)}

    # Интеграция не настроена → 400 с внятным сообщением (и для draft, и для send).
    # Глобальный error-envelope приложения: {"error": {"message": …}}.
    resp = await client.get("/api/v1/migfull-portal/inbound/1/draft", headers=headers)
    assert resp.status_code == 400
    assert "не настроена" in resp.json()["error"]["message"]

    resp = await client.post(
        "/api/v1/migfull-portal/inbound/1/send", json={"force_resend": False}, headers=headers
    )
    assert resp.status_code == 400
    assert "не настроена" in resp.json()["error"]["message"]
