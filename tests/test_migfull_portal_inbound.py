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
    CostOrderItem,
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
from backend.schemas.migfull_portal import MigfullInboundSendRequest, MigfullPackingLine
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


async def test_draft_items_and_pack_sources(db_session, env):
    """Фолбэк кратности: карта Натали → НАША кратность (строки машины / SKU-дефолт) → россыпь."""
    # EAN2 — новинка без карты Натали, кратность 6 со строк самой машины V-…
    ean2 = "2052922000011"
    # EAN3 — ни карты Натали, ни нашей кратности → россыпь
    ean3 = "2052922000028"
    # EAN4 — SKU-дефолт кратности (Nomenclature.box_qty_override=4)
    ean4 = "2052922000035"
    nom2 = Nomenclature(project_id=env.project_id, barcode=ean2, article_seller="KOVER-NEW")
    nom3 = Nomenclature(project_id=env.project_id, barcode=ean3, article_seller="KOVER-LOOSE")
    nom4 = Nomenclature(project_id=env.project_id, barcode=ean4, article_seller="KOVER-DEF", box_qty_override=4)
    db_session.add_all([nom2, nom3, nom4])
    db_session.add(
        CostOrderItem(
            project_id=env.project_id, order_no=env.vehicle.order_no,
            barcode=ean2, qty=25, pcs_per_box_override=6,
        )
    )
    await db_session.flush()
    for bc, q, nid in ((ean2, 25, nom2.id), (ean3, 7, nom3.id), (ean4, 8, nom4.id)):
        db_session.add(
            InboundReceiptItem(
                project_id=env.project_id, receipt_id=env.receipt.id,
                nomenclature_id=nid, barcode=bc, expected_qty=q,
            )
        )
    await db_session.commit()

    draft = await svc.build_inbound_draft(db_session, env.project_id, env.receipt.id)
    by_bc = {it.barcode: it for it in draft.items}
    assert len(draft.items) == 4

    # Карта Натали побеждает
    assert by_bc[EAN].pack_source == "natali"
    assert by_bc[EAN].units_per_box == 5
    assert by_bc[EAN].box_barcode == ITF
    assert by_bc[EAN].box_barcode_source == "natali"
    assert by_bc[EAN].units_natali == 5
    assert by_bc[EAN].units_ours is None
    assert by_bc[EAN].article_seller == "ELKA"
    # Наша кратность: строки машины-источника (едущей, не принятой)
    assert by_bc[ean2].pack_source == "ours"
    assert by_bc[ean2].units_per_box == 6
    assert by_bc[ean2].box_barcode == svc.ean13_to_itf14(ean2)
    assert by_bc[ean2].box_barcode_source == "derived"
    assert by_bc[ean2].units_natali is None
    assert by_bc[ean2].units_ours == 6
    assert by_bc[ean2].article_seller == "KOVER-NEW"
    # Наша кратность: SKU-дефолт box_qty_override
    assert by_bc[ean4].pack_source == "ours"
    assert by_bc[ean4].units_per_box == 4
    assert by_bc[ean4].units_ours == 4
    # Ничего не известно → россыпь
    assert by_bc[ean3].pack_source == "none"
    assert by_bc[ean3].units_per_box is None
    assert by_bc[ean3].units_natali is None
    assert by_bc[ean3].units_ours is None

    # Превью описи по дефолтному packing: короба нашей кратности + некратный остаток россыпью
    itf2 = svc.ean13_to_itf14(ean2)
    box2 = next(line for line in draft.opis_lines if line.is_box and line.barcode == itf2)
    assert box2.quantity == 4  # 25 // 6
    assert box2.units_per_box == 6
    assert box2.pieces == 24
    loose2 = next(line for line in draft.opis_lines if not line.is_box and line.barcode == ean2)
    assert loose2.quantity == 1  # 25 % 6
    assert any("не кратно коробу (6)" in w for w in draft.warnings)
    loose3 = next(line for line in draft.opis_lines if line.barcode == ean3)
    assert loose3.is_box is False and loose3.quantity == 7
    box4 = next(line for line in draft.opis_lines if line.is_box and line.barcode == svc.ean13_to_itf14(ean4))
    assert box4.quantity == 2 and box4.units_per_box == 4
    assert draft.total_boxes == 8 + 4 + 2
    assert draft.total_pieces == 40 + 25 + 7 + 8


def test_ean13_to_itf14_matches_natali_box_barcode():
    """Выведенный GTIN-14 совпадает с реальным ШК короба Натали для того же EAN13."""
    assert svc.ean13_to_itf14(EAN) == ITF
    assert svc.ean13_to_itf14("не-шк") is None


async def test_draft_units_both_sides_mismatch(db_session, env):
    """Draft несёт ОБЕ кратности (наша 20 vs у Натали 18) — блок нестыковок в модалке.

    Prefill при этом остаётся за Натали; наш артикул и источник ШК короба — в строке.
    """
    ean = "2052922000042"
    natali_box = "98052922000045"  # реальный ШК короба Натали ≠ выведенный GTIN-14
    nom = Nomenclature(project_id=env.project_id, barcode=ean, article_seller="KOVER-MIX")
    db_session.add(nom)
    db_session.add(
        FulfillmentStock(
            project_id=env.project_id,
            warehouse_id=env.warehouse.id,
            provider="migfull",
            barcode=natali_box,
            name="KOVER короб 18 шт.",
            base_barcode=ean,
            units_per_box=18,
        )
    )
    # НАША кратность — 20 (строка машины-источника)
    db_session.add(
        CostOrderItem(
            project_id=env.project_id, order_no=env.vehicle.order_no,
            barcode=ean, qty=40, pcs_per_box_override=20,
        )
    )
    await db_session.flush()
    db_session.add(
        InboundReceiptItem(
            project_id=env.project_id, receipt_id=env.receipt.id,
            nomenclature_id=nom.id, barcode=ean, expected_qty=40,
        )
    )
    # Строка без EAN13 и без карты Натали: ШК короба не выводится → source None;
    # артикула в номенклатуре нет → article_seller None
    nom_other = Nomenclature(project_id=env.project_id, barcode="CODE128-XYZ", article_seller=None)
    db_session.add(nom_other)
    await db_session.flush()
    db_session.add(
        InboundReceiptItem(
            project_id=env.project_id, receipt_id=env.receipt.id,
            nomenclature_id=nom_other.id, barcode="CODE128-XYZ", expected_qty=3,
        )
    )
    await db_session.commit()

    draft = await svc.build_inbound_draft(db_session, env.project_id, env.receipt.id)
    by_bc = {it.barcode: it for it in draft.items}

    it = by_bc[ean]
    assert it.article_seller == "KOVER-MIX"
    assert it.units_natali == 18
    assert it.units_ours == 20
    assert it.units_per_box == 18  # prefill — за Натали
    assert it.pack_source == "natali"
    assert it.box_barcode == natali_box
    assert it.box_barcode_source == "natali"

    other = by_bc["CODE128-XYZ"]
    assert other.box_barcode is None
    assert other.box_barcode_source is None
    assert other.article_seller is None
    assert svc.ean13_to_itf14("123") is None


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


# ─── Send: per-line packing из модалки ───────────────────────────────────────


async def test_send_with_packing_builds_opis(db_session, env, monkeypatch):
    """Опись строится ПО ручному packing: N кор. × M шт + некратный остаток россыпью."""
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)

    req = MigfullInboundSendRequest(packing=[MigfullPackingLine(barcode=EAN, qty=40, units_per_box=12)])
    res = await svc.send_submission(db_session, env.project_id, env.receipt.id, req)
    assert res.ok is True
    assert fake.uploads and fake.uploads[0][0] == GUID

    order = (
        await db_session.execute(
            select(MigfullShipmentOrder).where(MigfullShipmentOrder.inbound_receipt_id == env.receipt.id)
        )
    ).scalar_one()
    ols = order.payload["opis_lines"]
    box = next(line for line in ols if line["is_box"])
    loose = next(line for line in ols if not line["is_box"])
    # Короб — ШК короба Натали (ITF14), кол-во = 40 // 12
    assert box["barcode"] == ITF
    assert box["quantity"] == 3
    assert box["units_per_box"] == 12
    assert box["pieces"] == 36
    # Остаток россыпью — ШК товара (EAN13)
    assert loose["barcode"] == EAN
    assert loose["quantity"] == 4
    assert any("не кратно коробу (12)" in w for w in order.payload["warnings"])
    # Ручная кратность != карточки Натали (5) → предупреждение
    assert any("в карточке Натали короб 5" in w for w in order.payload["warnings"])
    # Packing зафиксирован в audit-payload
    assert order.payload["packing"] == [{"barcode": EAN, "qty": 40, "units_per_box": 12, "boxes": None}]
    # Автосвязь несёт суммарные штуки
    req_row = (
        await db_session.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.project_id == env.project_id,
                FulfillmentRequest.external_id == GUID,
            )
        )
    ).scalar_one()
    assert req_row.total_qty == 40


async def test_send_with_packing_loose_overrides_natali(db_session, env, monkeypatch):
    """Ручной выбор «россыпью» побеждает карту Натали — коробов в описи нет."""
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)

    req = MigfullInboundSendRequest(packing=[MigfullPackingLine(barcode=EAN, qty=40, units_per_box=None)])
    res = await svc.send_submission(db_session, env.project_id, env.receipt.id, req)
    assert res.ok is True
    order = (
        await db_session.execute(
            select(MigfullShipmentOrder).where(MigfullShipmentOrder.inbound_receipt_id == env.receipt.id)
        )
    ).scalar_one()
    ols = order.payload["opis_lines"]
    assert len(ols) == 1
    assert ols[0]["is_box"] is False
    assert ols[0]["barcode"] == EAN
    assert ols[0]["quantity"] == 40


# ─── Явный сплит: boxes — сколько коробов, остаток россыпью (чистая функция) ─


def test_build_opis_explicit_boxes_partial_split():
    """120 шт × кратность 20, boxes=4 → 4 короба (80 шт) + 40 россыпью БЕЗ warning."""
    lines, warnings = svc.build_opis_lines_from_packing(
        [MigfullPackingLine(barcode=EAN, qty=120, units_per_box=20, boxes=4)],
        box_for_piece={},
        name_for_barcode={EAN: "ELKA"},
    )
    assert warnings == []  # осознанный сплит — остаток россыпью не ворнится
    box = next(line for line in lines if line.is_box)
    loose = next(line for line in lines if not line.is_box)
    assert box.barcode == svc.ean13_to_itf14(EAN)
    assert box.quantity == 4
    assert box.units_per_box == 20
    assert box.pieces == 80
    assert loose.barcode == EAN
    assert loose.quantity == 40
    assert loose.pieces == 40


def test_build_opis_explicit_boxes_zero_all_loose():
    """boxes=0 → вся строка россыпью, даже при заданной кратности и карте Натали."""
    lines, warnings = svc.build_opis_lines_from_packing(
        [MigfullPackingLine(barcode=EAN, qty=40, units_per_box=5, boxes=0)],
        box_for_piece={EAN: (ITF, 5, "ELKA короб 5 шт.")},
        name_for_barcode={EAN: "ELKA"},
    )
    assert warnings == []
    assert len(lines) == 1
    assert lines[0].is_box is False
    assert lines[0].barcode == EAN
    assert lines[0].quantity == 40


def test_build_opis_boxes_none_keeps_legacy_behavior():
    """Регресс: boxes=None — максимум коробов + некратный остаток россыпью с warning."""
    lines, warnings = svc.build_opis_lines_from_packing(
        [MigfullPackingLine(barcode=EAN, qty=40, units_per_box=12, boxes=None)],
        box_for_piece={},
        name_for_barcode={EAN: "ELKA"},
    )
    box = next(line for line in lines if line.is_box)
    loose = next(line for line in lines if not line.is_box)
    assert box.quantity == 3  # 40 // 12 — максимум
    assert loose.quantity == 4
    assert any("не кратно коробу (12)" in w for w in warnings)


async def test_send_with_explicit_boxes_split(db_session, env, monkeypatch):
    """Send с явным сплитом: ровно N коробов + остаток россыпью, без warning о некратности."""
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)

    req = MigfullInboundSendRequest(packing=[MigfullPackingLine(barcode=EAN, qty=40, units_per_box=5, boxes=4)])
    res = await svc.send_submission(db_session, env.project_id, env.receipt.id, req)
    assert res.ok is True

    order = (
        await db_session.execute(
            select(MigfullShipmentOrder).where(MigfullShipmentOrder.inbound_receipt_id == env.receipt.id)
        )
    ).scalar_one()
    ols = order.payload["opis_lines"]
    box = next(line for line in ols if line["is_box"])
    loose = next(line for line in ols if not line["is_box"])
    assert box["barcode"] == ITF
    assert box["quantity"] == 4  # ровно 4 короба, не максимум (8)
    assert box["pieces"] == 20
    assert loose["barcode"] == EAN
    assert loose["quantity"] == 20  # 40 − 4×5 россыпью
    assert not any("не кратно коробу" in w for w in order.payload["warnings"])
    assert order.payload["packing"] == [{"barcode": EAN, "qty": 40, "units_per_box": 5, "boxes": 4}]


@pytest.mark.parametrize(
    "packing",
    [
        [{"barcode": EAN, "qty": 0, "units_per_box": 5}],  # qty <= 0
        [{"barcode": EAN, "qty": 40, "units_per_box": 0}],  # units < 1
        # лишний ШК, которого нет в приёмке
        [{"barcode": "9999999999999", "qty": 1, "units_per_box": None},
         {"barcode": EAN, "qty": 40, "units_per_box": None}],
        [],  # не покрывает состав приёмки
        # дубль ШК
        [{"barcode": EAN, "qty": 20, "units_per_box": None},
         {"barcode": EAN, "qty": 20, "units_per_box": None}],
        [{"barcode": EAN, "qty": 40, "units_per_box": 5, "boxes": -1}],  # boxes < 0
        [{"barcode": EAN, "qty": 40, "units_per_box": 12, "boxes": 4}],  # 4×12=48 > 40
        [{"barcode": EAN, "qty": 40, "units_per_box": None, "boxes": 2}],  # короба без кратности
    ],
)
async def test_send_invalid_packing_is_400(db_session, env, monkeypatch, packing):
    fake = FakePortalClient()
    _use_fake_client(monkeypatch, fake)
    req = MigfullInboundSendRequest(packing=[MigfullPackingLine(**p) for p in packing])
    with pytest.raises(MigfullPortalServiceError) as exc:
        await svc.send_submission(db_session, env.project_id, env.receipt.id, req)
    assert exc.value.status_code == 400
    assert fake.created_headers == []  # на портал ничего не ушло


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
