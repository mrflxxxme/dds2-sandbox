# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты создания ПОСТАВКИ у Натали из нашего ПЕРЕМЕЩЕНИЯ (StockTransfer).

Второй источник того же контура, что и поставка из приёмки машины: перемещение
наш склад → Натали своей приёмки не создаёт (приход у ФФ заводит именно эта
поставка), поэтому состав/шапка берутся из строк переезда TR-….

Портал-клиент замокан (никаких живых POST на боевой портал!) — переиспользуем
``FakePortalClient`` из соседнего модуля. Проверяем: состав и prefill draft'а,
цепочку кратности, send с packing, анти-дубль 409/force_resend, чужой склад →
400, принятый переезд → 400 и автосвязь ``FulfillmentRequest.stock_transfer_id``.
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.models import (
    FulfillmentRequest,
    FulfillmentStock,
    InboundReceipt,
    IntegrationKey,
    MigfullShipmentOrder,
    MigfullShipmentStatus,
    Nomenclature,
    StockTransfer,
    StockTransferItem,
    Warehouse,
)
from backend.models.fulfillment import FfRequestKind
from backend.models.warehouse import InboundStatus, TransferStatus
from backend.schemas.migfull_portal import MigfullInboundSendRequest, MigfullPackingLine
from backend.services import migfull_portal_inbound as svc
from backend.services.migfull_portal_service import MigfullPortalServiceError
from backend.utils.crypto import encrypt
from backend.utils.time import utcnow
from tests.test_migfull_portal_inbound import EAN, GUID, ITF, FakePortalClient, _use_fake_client

# Второй SKU переезда: карты Натали нет, кратность придёт из SKU-дефолта.
EAN2 = "2052922000042"


def _transfer_number() -> str:
    """Номер переезда уникален в пределах прогона (номер попадает в prefill/имя описи)."""
    return f"TR-{uuid.uuid4().hex[:6]}"


async def _make_receipt(db_session, env) -> InboundReceipt:
    """Реальная приёмка на складе Натали — для проверок «чужой источник» (FK-совместимо)."""
    receipt = InboundReceipt(
        project_id=env.project_id,
        warehouse_id=env.warehouse.id,
        number=f"IN-{uuid.uuid4().hex[:5]}",
        status=InboundStatus.EXPECTED.value,
    )
    db_session.add(receipt)
    await db_session.flush()
    return receipt


@pytest_asyncio.fixture
async def env(db_session, project):
    """Склад «Натали» + портальный ключ + переезд наш склад → Натали (40 шт короб по 5 + 9 шт)."""
    natali = Warehouse(project_id=project.id, name="натали", warehouse_type="FULFILLMENT")
    source_wh = Warehouse(project_id=project.id, name="транзит спб", warehouse_type="OWN")
    db_session.add_all([natali, source_wh])
    await db_session.flush()

    db_session.add(
        IntegrationKey(
            project_id=project.id,
            service="migfull_portal",
            encrypted_key=encrypt("portal-pass"),
            config={"login": "test@example.com"},
            warehouse_id=natali.id,
            is_active=True,
        )
    )
    nom = Nomenclature(project_id=project.id, barcode=EAN, article_seller="ELKA")
    # SKU-дефолт кратности: карты Натали у EAN2 нет — сработает НАША кратность.
    nom2 = Nomenclature(project_id=project.id, barcode=EAN2, article_seller="KOVER-DEF", box_qty_override=3)
    db_session.add_all([nom, nom2])
    await db_session.flush()

    transfer = StockTransfer(
        project_id=project.id,
        from_warehouse_id=source_wh.id,
        to_warehouse_id=natali.id,
        number=_transfer_number(),
        status=TransferStatus.IN_TRANSIT.value,
        delivery_date=date(2026, 8, 12),
    )
    db_session.add(transfer)
    await db_session.flush()
    db_session.add_all([
        StockTransferItem(
            project_id=project.id, transfer_id=transfer.id,
            nomenclature_id=nom.id, barcode=EAN, quantity=40,
        ),
        StockTransferItem(
            project_id=project.id, transfer_id=transfer.id,
            nomenclature_id=nom2.id, barcode=EAN2, quantity=9,
        ),
    ])
    # Зеркало остатков ФФ: короб (ITF14, 5 шт) → россыпь (EAN13) только для EAN.
    db_session.add(
        FulfillmentStock(
            project_id=project.id,
            warehouse_id=natali.id,
            provider="migfull",
            barcode=ITF,
            name="ELKA короб 5 шт.",
            base_barcode=EAN,
            units_per_box=5,
        )
    )
    await db_session.commit()

    from types import SimpleNamespace

    return SimpleNamespace(
        project_id=project.id, warehouse=natali, source_wh=source_wh, transfer=transfer
    )


# ─── Draft (превью для confirm-модалки) ───────────────────────────────────────


async def test_transfer_draft_composition_and_prefill(db_session, env):
    """Состав — строки переезда; шапка — TR-…, плановая дата, «источник → Натали»."""
    draft = await svc.build_transfer_draft(db_session, env.project_id, env.transfer.id)

    assert draft.eligible is True
    assert draft.already_sent is False
    assert {it.barcode for it in draft.items} == {EAN, EAN2}
    assert {it.barcode: it.qty for it in draft.items} == {EAN: 40, EAN2: 9}

    assert draft.prefill.number == env.transfer.number
    assert draft.prefill.transfer_number == env.transfer.number
    # Источник — переезд, а не машина: инфо-поля приёмки пусты.
    assert draft.prefill.vehicle_order_no is None
    assert draft.prefill.receipt_number is None
    assert draft.prefill.submission_date == date(2026, 8, 12)
    notes = draft.prefill.notes or ""
    assert env.transfer.number in notes
    assert "перемещение" in notes
    assert "транзит спб" in notes
    assert "натали" in notes


async def test_transfer_draft_multiplicity_chain(db_session, env):
    """Кратность: карта Натали (EAN, 5) → НАША кратность (EAN2, SKU-дефолт 3)."""
    draft = await svc.build_transfer_draft(db_session, env.project_id, env.transfer.id)
    by_bc = {it.barcode: it for it in draft.items}

    assert by_bc[EAN].pack_source == "natali"
    assert by_bc[EAN].units_per_box == 5
    assert by_bc[EAN].box_barcode == ITF
    assert by_bc[EAN].box_barcode_source == "natali"

    # Машины-источника у переезда нет — наша кратность берётся из SKU-дефолта.
    assert by_bc[EAN2].pack_source == "ours"
    assert by_bc[EAN2].units_per_box == 3
    assert by_bc[EAN2].units_ours == 3
    assert by_bc[EAN2].units_natali is None
    assert by_bc[EAN2].box_barcode == svc.ean13_to_itf14(EAN2)
    assert by_bc[EAN2].box_barcode_source == "derived"

    # Дефолтная опись: 8 коробов по 5 (EAN) + 3 короба по 3 (EAN2).
    by_line = {ln.barcode: ln for ln in draft.opis_lines}
    assert by_line[ITF].is_box is True
    assert by_line[ITF].quantity == 8
    assert by_line[svc.ean13_to_itf14(EAN2)].quantity == 3
    assert draft.total_boxes == 11
    assert draft.total_pieces == 49


async def test_transfer_draft_missing_is_404(db_session, env):
    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.build_transfer_draft(db_session, env.project_id, 987654321)
    assert exc.value.status_code == 404


async def test_transfer_isolated_by_project(db_session, env, other_project):
    """Изоляция по проекту: чужой переезд не виден загрузчику даже по прямому id."""
    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc._source_from_transfer(db_session, other_project.id, env.transfer.id)
    assert exc.value.status_code == 404

    # Через публичный вход у чужого проекта первым сработает гейт интеграции.
    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.build_transfer_draft(db_session, other_project.id, env.transfer.id)
    assert exc.value.status_code == 400
    assert "не настроена" in str(exc.value)


# ─── Send (реальное создание — портал замокан) ────────────────────────────────


async def test_transfer_send_with_packing(db_session, env, monkeypatch):
    """Опись строится ПО packing из модалки; audit и ФФ-связка ссылаются на переезд."""
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)

    res = await svc.send_transfer_submission(
        db_session,
        env.project_id,
        env.transfer.id,
        MigfullInboundSendRequest(
            packing=[
                # 6 коробов по 5 = 30 шт, остаток 10 — россыпью (явный сплит, без warning)
                MigfullPackingLine(barcode=EAN, qty=40, units_per_box=5, boxes=6),
                MigfullPackingLine(barcode=EAN2, qty=9, units_per_box=None),
            ]
        ),
    )

    assert res.ok is True
    assert res.shipment_guid == GUID
    assert res.shipment_number == "PVB-0000401"
    assert len(fake.created_headers) == 1
    header = fake.created_headers[0]
    assert header["number"] == env.transfer.number
    assert header["submission_date"] == "2026-08-12"
    assert env.transfer.number in header["notes"]
    # Имя файла описи — по номеру переезда (TR-… проходит фильтр «alnum + -_» как есть).
    assert fake.uploads[0][1] == f"opis_{env.transfer.number}.xlsx"

    order = (
        await db_session.execute(
            select(MigfullShipmentOrder).where(MigfullShipmentOrder.project_id == env.project_id)
        )
    ).scalar_one()
    assert order.status == MigfullShipmentStatus.SENT
    assert order.stock_transfer_id == env.transfer.id
    assert order.inbound_receipt_id is None
    lines = order.payload["opis_lines"]
    by_bc = {ln["barcode"]: ln for ln in lines}
    assert by_bc[ITF]["quantity"] == 6          # ровно заказанные короба
    assert by_bc[ITF]["is_box"] is True
    assert by_bc[EAN]["quantity"] == 10         # остаток россыпью
    assert by_bc[EAN2]["quantity"] == 9         # россыпь (units_per_box не задан)
    assert order.payload["warnings"] == []      # явный сплит warning не даёт


async def test_transfer_send_autolinks_ff_request(db_session, env, monkeypatch):
    """Созданная PVB сразу получает stock_transfer_id — синк сольёт номер в ту же строку."""
    _use_fake_client(monkeypatch, FakePortalClient())
    res = await svc.send_transfer_submission(
        db_session, env.project_id, env.transfer.id, MigfullInboundSendRequest()
    )
    assert res.ok is True

    ff = (
        await db_session.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.project_id == env.project_id,
                FulfillmentRequest.external_id == GUID,
            )
        )
    ).scalar_one()
    assert ff.stock_transfer_id == env.transfer.id
    assert ff.inbound_receipt_id is None
    assert ff.kind == FfRequestKind.INBOUND.value
    assert ff.provider == "migfull"
    assert ff.warehouse_id == env.warehouse.id
    assert ff.number == "PVB-0000401"
    assert ff.total_qty == 49


# ─── Анти-дубль ───────────────────────────────────────────────────────────────


async def test_transfer_resend_blocked_then_forced(db_session, env, monkeypatch):
    """Повтор без force → 409; с force_resend — вторая отправка проходит."""
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    first = await svc.send_transfer_submission(
        db_session, env.project_id, env.transfer.id, MigfullInboundSendRequest()
    )
    assert first.ok is True

    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.send_transfer_submission(
            db_session, env.project_id, env.transfer.id, MigfullInboundSendRequest()
        )
    assert exc.value.status_code == 409
    assert "перемещения" in str(exc.value)
    assert len(fake.created_headers) == 1  # второй POST на портал не ушёл

    forced = await svc.send_transfer_submission(
        db_session, env.project_id, env.transfer.id, MigfullInboundSendRequest(force_resend=True)
    )
    assert forced.ok is True
    assert len(fake.created_headers) == 2


async def test_transfer_draft_reports_already_sent(db_session, env, monkeypatch):
    _use_fake_client(monkeypatch, FakePortalClient())
    await svc.send_transfer_submission(
        db_session, env.project_id, env.transfer.id, MigfullInboundSendRequest()
    )
    draft = await svc.build_transfer_draft(db_session, env.project_id, env.transfer.id)
    assert draft.already_sent is True
    assert draft.sent_guid == GUID
    assert draft.sent_number == "PVB-0000401"


async def test_manually_linked_pvb_blocks_send(db_session, env, monkeypatch):
    """Дубль без audit-строки: PVB завела сама Натали и её привязали вручную к переезду."""
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    db_session.add(
        FulfillmentRequest(
            project_id=env.project_id,
            warehouse_id=env.warehouse.id,
            provider="migfull",
            external_id="manual-guid",
            number="PVB-0000500",
            kind=FfRequestKind.INBOUND.value,
            status="new",
            stock_transfer_id=env.transfer.id,
        )
    )
    await db_session.commit()

    draft = await svc.build_transfer_draft(db_session, env.project_id, env.transfer.id)
    assert draft.already_sent is True
    assert draft.sent_number == "PVB-0000500"

    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.send_transfer_submission(
            db_session, env.project_id, env.transfer.id, MigfullInboundSendRequest()
        )
    assert exc.value.status_code == 409
    assert "PVB-0000500" in str(exc.value)
    assert fake.created_headers == []


def test_source_filters_target_their_own_column():
    """Скоуп анти-дубля — по КОЛОНКЕ источника, а не по числу id.

    id приёмок и id переездов живут в разных последовательностях и легко
    совпадают численно; если бы фильтр смотрел «не ту» колонку, поставка из
    переезда залипла бы в 409 из-за чужой приёмки с тем же числом.
    """
    common = {
        "id": 777, "warehouse_id": 1, "qty_by_bc": {}, "nom_by_bc": {}, "vehicle": None,
        "prefill_number": None, "prefill_date": None, "notes": "", "filename_base": "x",
    }
    receipt_src = svc.InboundSource(kind="receipt", **common)
    transfer_src = svc.InboundSource(kind="transfer", **common)

    assert "inbound_receipt_id" in str(svc._order_source_filter(receipt_src))
    assert "stock_transfer_id" in str(svc._order_source_filter(transfer_src))
    assert "inbound_receipt_id" in str(svc._ff_source_filter(receipt_src))
    assert "stock_transfer_id" in str(svc._ff_source_filter(transfer_src))


async def test_receipt_audit_does_not_block_transfer(db_session, env, monkeypatch):
    """SENT-поставка по ПРИЁМКЕ не гасит поставку по ПЕРЕМЕЩЕНИЮ (разные источники)."""
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    receipt = await _make_receipt(db_session, env)
    db_session.add(
        MigfullShipmentOrder(
            project_id=env.project_id,
            inbound_receipt_id=receipt.id,
            stock_transfer_id=None,
            status=MigfullShipmentStatus.SENT,
            shipment_guid="other-guid",
            shipment_number="PVB-0000999",
        )
    )
    await db_session.commit()

    draft = await svc.build_transfer_draft(db_session, env.project_id, env.transfer.id)
    assert draft.already_sent is False

    res = await svc.send_transfer_submission(
        db_session, env.project_id, env.transfer.id, MigfullInboundSendRequest()
    )
    assert res.ok is True


async def test_assembly_side_ff_link_does_not_block(db_session, env, monkeypatch):
    """Отгрузочная сторона переезда (kind=assembly) — штатное состояние, не дубль.

    У переезда между ФФ есть ЗАЯВКА на отгрузку с тем же stock_transfer_id.
    Если бы анти-дубль не фильтровал kind=inbound, поставка залипла бы в 409.
    """
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    db_session.add(
        FulfillmentRequest(
            project_id=env.project_id,
            warehouse_id=env.source_wh.id,
            provider="migfull",
            external_id="assembly-side-guid",
            number="ЗАК-0000777",
            kind=FfRequestKind.ASSEMBLY.value,
            status="new",
            stock_transfer_id=env.transfer.id,
        )
    )
    await db_session.commit()

    draft = await svc.build_transfer_draft(db_session, env.project_id, env.transfer.id)
    assert draft.already_sent is False

    res = await svc.send_transfer_submission(
        db_session, env.project_id, env.transfer.id, MigfullInboundSendRequest()
    )
    assert res.ok is True


async def test_ff_link_claims_single_source_slot(db_session, env, monkeypatch):
    """Автосвязь выставляет РОВНО одну ссылку: соседний слот обнуляется.

    Гонка с read-sync: строка по тому же guid уже есть и держит чужой
    inbound_receipt_id. Читатели связок берут stock_transfer_id первым — две
    заполненные ссылки означали бы молчаливую подмену документа.
    """
    _use_fake_client(monkeypatch, FakePortalClient())
    foreign = await _make_receipt(db_session, env)
    db_session.add(
        FulfillmentRequest(
            project_id=env.project_id,
            warehouse_id=env.warehouse.id,
            provider="migfull",
            external_id=GUID,                  # тот же guid, что вернёт портал
            kind=FfRequestKind.INBOUND.value,
            status="new",
            inbound_receipt_id=foreign.id,     # «чужая» ссылка от прошлой жизни строки
        )
    )
    await db_session.commit()

    res = await svc.send_transfer_submission(
        db_session, env.project_id, env.transfer.id, MigfullInboundSendRequest()
    )
    assert res.ok is True

    ff = (
        await db_session.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.project_id == env.project_id,
                FulfillmentRequest.external_id == GUID,
            )
        )
    ).scalar_one()
    assert ff.stock_transfer_id == env.transfer.id
    assert ff.inbound_receipt_id is None  # соседний слот очищен


# ─── Валидация источника ──────────────────────────────────────────────────────


async def test_transfer_to_foreign_warehouse_blocked(db_session, env, monkeypatch):
    """Получатель — не склад Натали → 400, на портал ничего не уходит."""
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    other_wh = Warehouse(project_id=env.project_id, name="другой фф", warehouse_type="FULFILLMENT")
    db_session.add(other_wh)
    await db_session.flush()
    foreign = StockTransfer(
        project_id=env.project_id,
        from_warehouse_id=env.source_wh.id,
        to_warehouse_id=other_wh.id,
        number=_transfer_number(),
        status=TransferStatus.DRAFT.value,
    )
    db_session.add(foreign)
    await db_session.commit()

    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.send_transfer_submission(
            db_session, env.project_id, foreign.id, MigfullInboundSendRequest()
        )
    assert exc.value.status_code == 400
    assert "не на склад ФФ" in str(exc.value)
    assert fake.created_headers == []

    # Draft не падает — просто помечает документ неподходящим.
    draft = await svc.build_transfer_draft(db_session, env.project_id, foreign.id)
    assert draft.eligible is False


async def test_completed_transfer_blocked(db_session, env, monkeypatch):
    """Переезд уже принят — товар оприходован, поставка у ФФ не нужна → 400."""
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    env.transfer.status = TransferStatus.COMPLETED.value
    await db_session.commit()

    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.send_transfer_submission(
            db_session, env.project_id, env.transfer.id, MigfullInboundSendRequest()
        )
    assert exc.value.status_code == 400
    assert "уже принято" in str(exc.value)
    assert fake.created_headers == []


async def test_empty_transfer_blocked(db_session, env, monkeypatch):
    """Переезд без строк — описи не из чего строить → 400, на портал не идём."""
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    empty = StockTransfer(
        project_id=env.project_id,
        from_warehouse_id=env.source_wh.id,
        to_warehouse_id=env.warehouse.id,
        number=_transfer_number(),
        status=TransferStatus.DRAFT.value,
    )
    db_session.add(empty)
    await db_session.commit()

    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.send_transfer_submission(
            db_session, env.project_id, empty.id, MigfullInboundSendRequest()
        )
    assert exc.value.status_code == 400
    assert "нет позиций" in str(exc.value)
    assert fake.created_headers == []


async def test_prefill_date_falls_back_to_pickup_then_today(db_session, env):
    """Дата шапки: delivery_date → pickup_date → сегодня."""
    env.transfer.delivery_date = None
    env.transfer.pickup_date = date(2026, 8, 3)
    await db_session.commit()
    draft = await svc.build_transfer_draft(db_session, env.project_id, env.transfer.id)
    assert draft.prefill.submission_date == date(2026, 8, 3)

    env.transfer.pickup_date = None
    await db_session.commit()
    draft = await svc.build_transfer_draft(db_session, env.project_id, env.transfer.id)
    assert draft.prefill.submission_date == utcnow().date()


async def test_deleted_transfer_is_404(db_session, env):
    env.transfer.soft_delete()
    await db_session.commit()
    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.build_transfer_draft(db_session, env.project_id, env.transfer.id)
    assert exc.value.status_code == 404


async def test_packing_beyond_transfer_composition_is_400(db_session, env, monkeypatch):
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.send_transfer_submission(
            db_session,
            env.project_id,
            env.transfer.id,
            MigfullInboundSendRequest(
                packing=[
                    MigfullPackingLine(barcode=EAN, qty=41),  # больше состава переезда (40)
                    MigfullPackingLine(barcode=EAN2, qty=9),
                ]
            ),
        )
    assert exc.value.status_code == 400
    assert "перемещения" in str(exc.value)
    assert fake.created_headers == []


# ─── Роутер ───────────────────────────────────────────────────────────────────


async def test_transfer_router_maps_service_errors(client, auth_headers):
    """Интеграция не настроена → 400 с внятным сообщением (и draft, и send)."""
    resp = await client.post("/api/v1/projects", json={"name": "MP Transfer"}, headers=auth_headers)
    assert resp.status_code in (200, 201)
    headers = {**auth_headers, "X-Project-Id": str(resp.json()["id"])}

    resp = await client.get("/api/v1/migfull-portal/transfer/1/draft", headers=headers)
    assert resp.status_code == 400
    assert "не настроена" in resp.json()["error"]["message"]

    resp = await client.post(
        "/api/v1/migfull-portal/transfer/1/send", json={"force_resend": False}, headers=headers
    )
    assert resp.status_code == 400
    assert "не настроена" in resp.json()["error"]["message"]
