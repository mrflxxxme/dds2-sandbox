"""
Service + API tests — кандидаты модалки «Связать» (get_link_candidates)
и создание заявки на сборку из ФФ-заявки (create_assembly_from_ff).

wmscelicom: состав из зеркального raw — без HTTP. skladbot: живая деталка
мокается через monkeypatch SkladbotClient.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.integrations.skladbot_client import SkladbotApiError, SkladbotClient
from backend.integrations.wmscelicom_client import WmsCelicomClient
from backend.models import (
    FulfillmentRequest,
    FulfillmentStock,
    InboundReceipt,
    InboundReceiptItem,
    Nomenclature,
    Warehouse,
    WarehouseStock,
)
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus, AssemblyStatusHistory
from backend.models.wb_fbo import WbFboSupply
from backend.services import fulfillment_service

FAKE_TOKEN = "fake_skladbot_token_1234567890_abcdef"  # noqa: S105 — фейковый токен для тестов
WMS_TOKEN = "0fake0fake0fake0fake0fake0fake00"  # noqa: S105 — фейковый 32-hex


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _wms_raw(items):
    """raw отгрузки FBO wmscelicom: состав в packages → items [(barcode, count)]."""
    return {
        "shipment_fbo_id": 1,
        "status": "Новая",
        "packages": {
            "1": {
                "number": 1,
                "barcode": "WB_1",
                "items": [{"name": "Товар", "barcode": bc, "count": qty} for bc, qty in items],
            }
        },
    }


def _migfull_raw(lines):
    """raw отгрузки migfull: planned_lines [(guid, qty)] — ШК резолвятся из зеркала остатков."""
    return {
        "shipment_guid": _uid(),
        "planned_lines": [
            {"product_guid": guid, "quantity": qty, "product": {"guid": guid, "name": f"Товар {guid}"}}
            for guid, qty in lines
        ],
    }


def _mirror(
    project_id,
    warehouse_id,
    *,
    provider="wmscelicom",
    kind="assembly",
    created=None,
    number=None,
    raw=None,
    total_qty=None,
    assembly_request_id=None,
    inbound_receipt_id=None,
):
    """Зеркальная ФФ-заявка для прямой вставки в БД."""
    return FulfillmentRequest(
        project_id=project_id,
        warehouse_id=warehouse_id,
        provider=provider,
        external_id=_uid(),
        number=number,
        kind=kind,
        status="Новая",
        external_created_at=created,
        total_qty=total_qty,
        raw=raw,
        assembly_request_id=assembly_request_id,
        inbound_receipt_id=inbound_receipt_id,
    )


@pytest_asyncio.fixture
async def warehouse(db_session, project):
    wh = Warehouse(project_id=project.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(wh)
    await db_session.commit()
    await db_session.refresh(wh)
    return wh


async def _add(db_session, obj):
    db_session.add(obj)
    await db_session.commit()
    await db_session.refresh(obj)
    return obj


async def _make_nomenclature(db_session, project_id, barcode):
    return await _add(
        db_session, Nomenclature(project_id=project_id, barcode=barcode, article_seller=f"ART-{_uid()[:6]}")
    )


async def _make_assembly(
    db_session,
    project_id,
    warehouse_id,
    *,
    status=AssemblyStatus.IN_PROGRESS.value,
    created_at=None,
    items=(),
    wb_fbo_supply_id=None,
    wb_warehouse_name_manual=None,
):
    """Кандидат-заявка на сборку; items — [(barcode, nomenclature_id, qty)]."""
    doc = AssemblyRequest(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=f"A-{_uid()[:6]}",
        status=status,
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
        wb_fbo_supply_id=wb_fbo_supply_id,
        wb_warehouse_name_manual=wb_warehouse_name_manual,
    )
    if created_at is not None:
        doc.created_at = created_at
    await _add(db_session, doc)
    if items:
        db_session.add_all(
            [
                AssemblyRequestItem(
                    project_id=project_id,
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


async def _make_receipt(db_session, project_id, warehouse_id, *, created_at=None, items=()):
    """Приёмка-кандидат; items — [(barcode, nomenclature_id, expected_qty)]."""
    doc = InboundReceipt(project_id=project_id, warehouse_id=warehouse_id, number=f"IN-{_uid()[:6]}")
    if created_at is not None:
        doc.created_at = created_at
    await _add(db_session, doc)
    if items:
        db_session.add_all(
            [
                InboundReceiptItem(
                    project_id=project_id,
                    receipt_id=doc.id,
                    nomenclature_id=nom_id,
                    barcode=barcode,
                    expected_qty=qty,
                )
                for barcode, nom_id, qty in items
            ]
        )
        await db_session.commit()
    return doc


async def _seed_stock(db_session, project_id, warehouse_id, nom, barcode, qty):
    db_session.add(
        WarehouseStock(
            project_id=project_id,
            warehouse_id=warehouse_id,
            nomenclature_id=nom.id,
            barcode=barcode,
            quantity=qty,
        )
    )
    await db_session.commit()


# ─── get_link_candidates: wmscelicom assembly (состав из raw) ────────────────


@pytest.mark.asyncio
async def test_candidates_wms_assembly_scoring(db_session, project, warehouse):
    bc_a, bc_b, bc_c = f"20{_uid()}", f"21{_uid()}", f"22{_uid()}"
    nom_a = await _make_nomenclature(db_session, project.id, bc_a)
    nom_b = await _make_nomenclature(db_session, project.id, bc_b)
    nom_c = await _make_nomenclature(db_session, project.id, bc_c)

    ff = await _add(
        db_session,
        _mirror(
            project.id,
            warehouse.id,
            created=date(2026, 6, 8),
            number="ORD-1",
            raw=_wms_raw([(bc_a, 5), (bc_b, 5)]),  # итого 10 шт
        ),
    )

    supply = await _add(
        db_session,
        WbFboSupply(
            project_id=project.id,
            wb_supply_id="WB-123456",
            created_at_wb=datetime(2026, 6, 1, 10, 0),
            warehouse_name="Коледино",
        ),
    )

    # Полное совпадение ШК + qty ±10% + дата 0 дн: 60 + 20 + 20 = 100
    cand_full = await _make_assembly(
        db_session,
        project.id,
        warehouse.id,
        created_at=datetime(2026, 6, 8, 12, 0),
        items=[(bc_a, nom_a.id, 5), (bc_b, nom_b.id, 5)],
        wb_fbo_supply_id=supply.id,
    )
    # Половина ШК + дата ±2 дн: 30 + 10 = 40 (ровно порог)
    cand_half = await _make_assembly(
        db_session,
        project.id,
        warehouse.id,
        created_at=datetime(2026, 6, 6, 10, 0),
        items=[(bc_a, nom_a.id, 20)],
        wb_warehouse_name_manual="Тула",
    )
    # Нет пересечения ШК → score None (qty ±10% и дата ±1 дн не спасают)
    cand_none = await _make_assembly(
        db_session,
        project.id,
        warehouse.id,
        created_at=datetime(2026, 6, 9, 10, 0),
        items=[(bc_c, nom_c.id, 9)],
    )
    # Отбрасываемые: CANCELLED, soft-deleted, связанный с другой ФФ-заявкой
    await _make_assembly(
        db_session, project.id, warehouse.id, status=AssemblyStatus.CANCELLED.value, created_at=datetime(2026, 6, 8)
    )
    cand_deleted = await _make_assembly(db_session, project.id, warehouse.id, created_at=datetime(2026, 6, 8))
    cand_deleted.soft_delete()
    cand_linked = await _make_assembly(db_session, project.id, warehouse.id, created_at=datetime(2026, 6, 8))
    db_session.add(_mirror(project.id, warehouse.id, created=date(2026, 6, 8), assembly_request_id=cand_linked.id))
    await db_session.commit()

    data = await fulfillment_service.get_link_candidates(db_session, project.id, warehouse.id, ff.id)

    assert data is not None
    assert data["kind"] == "assembly"
    assert data["ff_number"] == "ORD-1"
    assert data["ff_total_qty"] == 10
    assert data["composition_available"] is True

    by_id = {c["doc_id"]: c for c in data["candidates"]}
    assert set(by_id) == {cand_full.id, cand_half.id, cand_none.id}
    # Порядок выборки — created_at desc
    assert [c["doc_id"] for c in data["candidates"]] == [cand_none.id, cand_full.id, cand_half.id]

    full = by_id[cand_full.id]
    assert full["score"] == 100
    assert full["reason"] == "ШК 100%, кол-во ±10%, дата совпадает"
    assert full["total_qty"] == 10
    assert full["number"] == cand_full.number
    assert full["status"] == AssemblyStatus.IN_PROGRESS.value
    assert full["fbo_supply_number"] == "WB-123456"
    assert full["dest_warehouse"] == "Коледино"  # из связанной ФБО-поставки

    half = by_id[cand_half.id]
    assert half["score"] == 40
    assert half["reason"] == "ШК 50%, дата ±2 дн"
    assert half["total_qty"] == 20
    assert half["dest_warehouse"] == "Тула"  # ручной склад приоритетнее
    assert half["fbo_supply_number"] is None

    none = by_id[cand_none.id]
    assert none["score"] is None
    assert none["reason"] is None
    assert none["total_qty"] == 9


# ─── get_link_candidates: inbound ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_candidates_inbound(db_session, project, warehouse):
    bc = f"23{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)

    ff = await _add(
        db_session,
        _mirror(
            project.id,
            warehouse.id,
            kind="inbound",
            created=date(2026, 6, 8),
            raw={"unloading_order_id": 1, "items": [{"barcode": bc, "count": 6}, None]},
        ),
    )

    # Полное совпадение состава + дата: 60 + 20 + 20 = 100
    receipt = await _make_receipt(
        db_session,
        project.id,
        warehouse.id,
        created_at=datetime(2026, 6, 8, 9, 0),
        items=[(bc, nom.id, 6)],
    )
    # Приёмка, связанная с другой ФФ-заявкой — исключается
    receipt_linked = await _make_receipt(db_session, project.id, warehouse.id)
    db_session.add(
        _mirror(
            project.id, warehouse.id, kind="inbound", created=date(2026, 6, 8), inbound_receipt_id=receipt_linked.id
        )
    )
    await db_session.commit()

    data = await fulfillment_service.get_link_candidates(db_session, project.id, warehouse.id, ff.id)

    assert data["kind"] == "inbound"
    assert data["ff_total_qty"] == 6  # null-элемент items пропущен
    assert data["composition_available"] is True
    by_id = {c["doc_id"]: c for c in data["candidates"]}
    assert set(by_id) == {receipt.id}
    row = by_id[receipt.id]
    assert row["score"] == 100
    assert row["reason"] == "ШК 100%, кол-во ±10%, дата совпадает"
    assert row["total_qty"] == 6
    assert row["fbo_supply_number"] is None
    assert row["dest_warehouse"] is None


# ─── get_link_candidates: фолбэк по датам (состав недоступен) ────────────────


@pytest.mark.asyncio
async def test_candidates_fallback_dates_when_no_composition(db_session, project, warehouse):
    """skladbot без подключённого ключа: деталку не получить → эвристика дат."""
    bc = f"24{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    ff = await _add(
        db_session,
        _mirror(project.id, warehouse.id, provider="skladbot", created=date(2026, 6, 8), total_qty=10),
    )

    c0 = await _make_assembly(
        db_session, project.id, warehouse.id, created_at=datetime(2026, 6, 8, 10, 0), items=[(bc, nom.id, 10)]
    )  # 70 + 10 (кол-во ±10%) = 80
    c1 = await _make_assembly(db_session, project.id, warehouse.id, created_at=datetime(2026, 6, 9, 10, 0))  # 55
    c_far = await _make_assembly(db_session, project.id, warehouse.id, created_at=datetime(2026, 6, 1, 10, 0))  # мимо

    data = await fulfillment_service.get_link_candidates(db_session, project.id, warehouse.id, ff.id)

    assert data["composition_available"] is False
    assert data["ff_total_qty"] == 10  # из зеркального total_qty
    by_id = {c["doc_id"]: c for c in data["candidates"]}
    assert by_id[c0.id]["score"] == 80
    assert by_id[c0.id]["reason"] == "дата совпадает, кол-во ±10%"
    assert by_id[c1.id]["score"] == 55
    assert by_id[c1.id]["reason"] == "дата ±1 дн"
    assert by_id[c_far.id]["score"] is None  # дата дальше ±2 дн — кандидат без оценки
    assert by_id[c_far.id]["reason"] is None


# ─── get_link_candidates: skladbot живая деталка ─────────────────────────────


@pytest_asyncio.fixture
async def connected_key(db_session, project, warehouse, monkeypatch):
    async def fake_test_connection(self):
        return {"id": 6282, "name": "ООО ТЕСТ ФФ"}

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_test_connection)
    await fulfillment_service.connect(db_session, project.id, warehouse.id, "skladbot", FAKE_TOKEN)
    return await fulfillment_service.get_integration(db_session, project.id, warehouse.id)


@pytest.mark.asyncio
async def test_candidates_skladbot_live_composition(db_session, project, warehouse, connected_key, monkeypatch):
    bc = f"25{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    ff = await _add(db_session, _mirror(project.id, warehouse.id, provider="skladbot", created=date(2026, 6, 8)))
    cand = await _make_assembly(
        db_session, project.id, warehouse.id, created_at=datetime(2026, 6, 8, 10, 0), items=[(bc, nom.id, 5)]
    )

    seen: list[str] = []

    async def fake_detail(self, external_id):
        seen.append(str(external_id))
        return {"products": [{"barcode": bc, "amount": 5}, {"barcode": "", "amount": 3}, "junk"]}

    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fake_detail)

    data = await fulfillment_service.get_link_candidates(db_session, project.id, warehouse.id, ff.id)

    assert seen == [ff.external_id]
    assert data["composition_available"] is True
    assert data["ff_total_qty"] == 5  # пустой barcode и мусор пропущены
    row = next(c for c in data["candidates"] if c["doc_id"] == cand.id)
    assert row["score"] == 100  # ШК 100% (60) + кол-во ±10% (20) + дата 0 (20)


@pytest.mark.asyncio
async def test_candidates_skladbot_detail_error_falls_back(db_session, project, warehouse, connected_key, monkeypatch):
    """Ошибка деталки провайдера НЕ валит эндпоинт — фолбэк на эвристику дат."""
    ff = await _add(
        db_session,
        _mirror(project.id, warehouse.id, provider="skladbot", created=date(2026, 6, 8), total_qty=7),
    )
    cand = await _make_assembly(db_session, project.id, warehouse.id, created_at=datetime(2026, 6, 8, 10, 0))

    async def fail_detail(self, external_id):
        raise SkladbotApiError("boom", status_code=500)

    monkeypatch.setattr(SkladbotClient, "fetch_request_detail", fail_detail)

    data = await fulfillment_service.get_link_candidates(db_session, project.id, warehouse.id, ff.id)
    assert data["composition_available"] is False
    row = next(c for c in data["candidates"] if c["doc_id"] == cand.id)
    assert row["score"] == 70  # только дата (кол-ва у кандидата нет)


# ─── get_link_candidates: 404 / kind=other / изоляция ────────────────────────


@pytest.mark.asyncio
async def test_candidates_not_found_kind_other_isolation(db_session, project, other_project, warehouse):
    assert await fulfillment_service.get_link_candidates(db_session, project.id, warehouse.id, 99999999) is None

    ff_other_kind = await _add(db_session, _mirror(project.id, warehouse.id, kind="other"))
    with pytest.raises(ValueError, match="сборка"):
        await fulfillment_service.get_link_candidates(db_session, project.id, warehouse.id, ff_other_kind.id)

    ff = await _add(db_session, _mirror(project.id, warehouse.id, created=date(2026, 6, 8)))
    assert await fulfillment_service.get_link_candidates(db_session, other_project.id, warehouse.id, ff.id) is None


# ─── create_assembly_from_ff ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_assembly_from_ff_happy(db_session, project, warehouse):
    bc_a, bc_b = f"26{_uid()}", f"27{_uid()}"
    nom_a = await _make_nomenclature(db_session, project.id, bc_a)
    nom_b = await _make_nomenclature(db_session, project.id, bc_b)
    await _seed_stock(db_session, project.id, warehouse.id, nom_a, bc_a, 10)
    await _seed_stock(db_session, project.id, warehouse.id, nom_b, bc_b, 10)

    ff = await _add(
        db_session,
        _mirror(
            project.id,
            warehouse.id,
            number="ORD-77",
            created=date(2026, 6, 8),
            raw=_wms_raw([(bc_a, 5), (bc_b, 3)]),
        ),
    )

    result = await fulfillment_service.create_assembly_from_ff(db_session, project.id, warehouse.id, ff.id)

    assert result is not None
    assert result["items_created"] == 2
    assert result["skipped_barcodes"] == []
    assert result["assembly_number"].startswith("ASM-")
    assert result["request"]["assembly_request_id"] == result["assembly_request_id"]
    assert result["request"]["linked_number"] == result["assembly_number"]
    assert result["request"]["linked_status"] == AssemblyStatus.IN_PROGRESS.value

    doc = await db_session.get(AssemblyRequest, result["assembly_request_id"])
    assert doc.project_id == project.id
    assert doc.warehouse_id == warehouse.id
    assert doc.status == AssemblyStatus.IN_PROGRESS.value
    assert "ORD-77" in (doc.comment or "")

    items_result = await db_session.execute(
        select(AssemblyRequestItem)
        .where(
            AssemblyRequestItem.project_id == project.id,
            AssemblyRequestItem.assembly_request_id == doc.id,
        )
        .limit(10)
    )
    items = list(items_result.scalars().all())
    assert {(i.barcode, i.quantity) for i in items} == {(bc_a, 5), (bc_b, 3)}
    assert all(i.nomenclature_id in (nom_a.id, nom_b.id) for i in items)

    await db_session.refresh(ff)
    assert ff.assembly_request_id == doc.id

    # Повторное создание по уже связанной заявке → ValueError
    with pytest.raises(ValueError, match="уже связана"):
        await fulfillment_service.create_assembly_from_ff(db_session, project.id, warehouse.id, ff.id)


@pytest.mark.asyncio
async def test_create_assembly_from_ff_skipped_barcodes(db_session, project, warehouse):
    bc_known, bc_unknown = f"28{_uid()}", f"29{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc_known)
    await _seed_stock(db_session, project.id, warehouse.id, nom, bc_known, 10)

    ff = await _add(
        db_session,
        _mirror(project.id, warehouse.id, created=date(2026, 6, 8), raw=_wms_raw([(bc_known, 4), (bc_unknown, 2)])),
    )

    result = await fulfillment_service.create_assembly_from_ff(db_session, project.id, warehouse.id, ff.id)

    assert result["items_created"] == 1
    assert result["skipped_barcodes"] == [bc_unknown]
    items_result = await db_session.execute(
        select(AssemblyRequestItem.barcode)
        .where(
            AssemblyRequestItem.project_id == project.id,
            AssemblyRequestItem.assembly_request_id == result["assembly_request_id"],
        )
        .limit(10)
    )
    assert [r[0] for r in items_result.all()] == [bc_known]


@pytest.mark.asyncio
async def test_create_assembly_from_ff_auto_ready(db_session, project, warehouse):
    """Стадия ФФ уже «готов» (is_completed) → созданная сборка сразу READY, как в link_request."""
    bc = f"30{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    await _seed_stock(db_session, project.id, warehouse.id, nom, bc, 10)
    ff = _mirror(project.id, warehouse.id, created=date(2026, 6, 8), raw=_wms_raw([(bc, 5)]))
    ff.is_completed = True
    ff = await _add(db_session, ff)

    result = await fulfillment_service.create_assembly_from_ff(db_session, project.id, warehouse.id, ff.id)

    assert result["request"]["linked_status"] == AssemblyStatus.READY.value
    doc = await db_session.get(AssemblyRequest, result["assembly_request_id"])
    assert doc.status == AssemblyStatus.READY.value
    history_result = await db_session.execute(
        select(AssemblyStatusHistory)
        .where(
            AssemblyStatusHistory.project_id == project.id,
            AssemblyStatusHistory.assembly_request_id == doc.id,
        )
        .order_by(AssemblyStatusHistory.id)
        .limit(10)
    )
    transitions = [(h.old_status, h.new_status, h.changed_by) for h in history_result.scalars().all()]
    assert (AssemblyStatus.IN_PROGRESS.value, AssemblyStatus.READY.value, "ff_sync") in transitions


@pytest.mark.asyncio
async def test_create_assembly_from_ff_migfull(db_session, project, warehouse):
    """migfull: состав из planned_lines + guid→barcode из зеркала остатков; неполный резолв → guard."""
    bc = f"31{_uid()}"
    guid_a, guid_b = f"g-{_uid()}", f"g-{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    await _seed_stock(db_session, project.id, warehouse.id, nom, bc, 10)
    await _add(
        db_session,
        FulfillmentStock(
            project_id=project.id,
            warehouse_id=warehouse.id,
            provider="migfull",
            barcode=bc,
            external_product_id=guid_a,
            qty_good=10,
        ),
    )

    # guid_b без ШК в зеркале остатков, total_qty больше резолвнутого → guard
    partial = _mirror(
        project.id,
        warehouse.id,
        provider="migfull",
        created=date(2026, 6, 8),
        raw=_migfull_raw([(guid_a, 6), (guid_b, 4)]),
        total_qty=10,
    )
    partial = await _add(db_session, partial)
    with pytest.raises(ValueError, match="не полностью"):
        await fulfillment_service.create_assembly_from_ff(db_session, project.id, warehouse.id, partial.id)

    # Полностью разрезолвленный состав → happy path
    full = _mirror(
        project.id,
        warehouse.id,
        provider="migfull",
        created=date(2026, 6, 8),
        raw=_migfull_raw([(guid_a, 6)]),
        total_qty=6,
    )
    full = await _add(db_session, full)
    result = await fulfillment_service.create_assembly_from_ff(db_session, project.id, warehouse.id, full.id)
    assert result["items_created"] == 1
    assert result["skipped_barcodes"] == []
    items_result = await db_session.execute(
        select(AssemblyRequestItem.barcode, AssemblyRequestItem.quantity)
        .where(
            AssemblyRequestItem.project_id == project.id,
            AssemblyRequestItem.assembly_request_id == result["assembly_request_id"],
        )
        .limit(10)
    )
    assert [(r[0], r[1]) for r in items_result.all()] == [(bc, 6)]


@pytest.mark.asyncio
async def test_create_assembly_from_ff_validation_errors(db_session, project, warehouse):
    # kind=inbound → 400
    ff_inbound = await _add(db_session, _mirror(project.id, warehouse.id, kind="inbound"))
    with pytest.raises(ValueError, match="типа «сборка»"):
        await fulfillment_service.create_assembly_from_ff(db_session, project.id, warehouse.id, ff_inbound.id)

    # Состав недоступен (wmscelicom без packages = «нет данных»)
    ff_empty = await _add(db_session, _mirror(project.id, warehouse.id, raw={"shipment_fbo_id": 2}))
    with pytest.raises(ValueError, match="недоступен"):
        await fulfillment_service.create_assembly_from_ff(db_session, project.id, warehouse.id, ff_empty.id)

    # Ни один ШК не найден в номенклатуре
    ff_unknown = await _add(db_session, _mirror(project.id, warehouse.id, raw=_wms_raw([(f"30{_uid()}", 3)])))
    with pytest.raises(ValueError, match="Ни один ШК"):
        await fulfillment_service.create_assembly_from_ff(db_session, project.id, warehouse.id, ff_unknown.id)

    # Не найдена / чужой проект → None
    assert await fulfillment_service.create_assembly_from_ff(db_session, project.id, warehouse.id, 99999999) is None


@pytest.mark.asyncio
async def test_create_assembly_from_ff_stock_deficit(db_session, project, warehouse):
    """Дефицит доступного стока пробрасывается из create_assembly_request; связь не ставится."""
    bc = f"31{_uid()}"
    nom = await _make_nomenclature(db_session, project.id, bc)
    await _seed_stock(db_session, project.id, warehouse.id, nom, bc, 1)  # нужно 5

    ff = await _add(db_session, _mirror(project.id, warehouse.id, raw=_wms_raw([(bc, 5)])))

    with pytest.raises(ValueError, match="Недостаточно"):
        await fulfillment_service.create_assembly_from_ff(db_session, project.id, warehouse.id, ff.id)
    await db_session.rollback()
    await db_session.refresh(ff)
    assert ff.assembly_request_id is None


# ─── API endpoints ───────────────────────────────────────────────────────────


def _mock_wms(monkeypatch, shipments=(), unloadings=()):
    async def fake_conn(self):
        return True

    async def fake_items(self):
        return []

    async def fake_shipments(self):
        return list(shipments)

    async def fake_unloadings(self):
        return list(unloadings)

    monkeypatch.setattr(WmsCelicomClient, "test_connection", fake_conn)
    monkeypatch.setattr(WmsCelicomClient, "fetch_all_items", fake_items)
    monkeypatch.setattr(WmsCelicomClient, "fetch_shipments_fbo", fake_shipments)
    monkeypatch.setattr(WmsCelicomClient, "fetch_unloading_orders", fake_unloadings)


@pytest.mark.asyncio
async def test_api_link_candidates_and_create_assembly(client, auth_headers, monkeypatch):
    shipment = {
        "shipment_fbo_id": 9001,
        "date_time": "2026-06-08 10:00:00",
        "status": "Новая",
        "external_order": "ORD-API",
        "shipped_target": "Склад МП",
        "packages": {"1": {"number": 1, "items": [{"barcode": "API-BC-1", "count": 2}]}},
    }
    unloading = {
        "unloading_order_id": 9002,
        "create_date_time": "2026-06-08 09:00:00",
        "status": "Новая",
        "unloading_close_date": None,
        "items": [{"barcode": "API-BC-1", "count": 2}],
    }
    _mock_wms(monkeypatch, shipments=[shipment], unloadings=[unloading])

    resp = await client.post("/api/v1/projects", json={"name": "FF Candidates API"}, headers=auth_headers)
    headers = {**auth_headers, "X-Project-Id": str(resp.json()["id"])}
    resp = await client.post(
        "/api/v1/warehouse",
        json={"name": "FF Cand", "warehouse_type": "FULFILLMENT"},
        headers=headers,
    )
    wh_id = resp.json()["id"]
    resp = await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/connect",
        json={"provider": "wmscelicom", "token": WMS_TOKEN, "base_url": "test-client.wmscelicom.ru"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    resp = await client.post(f"/api/v1/warehouse/{wh_id}/fulfillment/sync", headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.get(f"/api/v1/warehouse/{wh_id}/fulfillment/requests", headers=headers)
    by_kind = {r["kind"]: r for r in resp.json()}
    asm_id = by_kind["assembly"]["id"]
    inb_id = by_kind["inbound"]["id"]

    # link-candidates: состав из raw доступен, кандидатов на пустом складе нет
    resp = await client.get(
        f"/api/v1/warehouse/{wh_id}/fulfillment/requests/{asm_id}/link-candidates",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["kind"] == "assembly"
    assert data["ff_number"] == "ORD-API"
    assert data["ff_total_qty"] == 2
    assert data["composition_available"] is True
    assert data["candidates"] == []

    resp = await client.get(
        f"/api/v1/warehouse/{wh_id}/fulfillment/requests/99999999/link-candidates",
        headers=headers,
    )
    assert resp.status_code == 404

    # create-assembly: inbound → 400
    resp = await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/requests/{inb_id}/create-assembly",
        headers=headers,
    )
    assert resp.status_code == 400

    # create-assembly: ШК не заведён в номенклатуре → 400 с понятной ошибкой
    resp = await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/requests/{asm_id}/create-assembly",
        headers=headers,
    )
    assert resp.status_code == 400
    assert "ШК" in resp.json()["error"]["message"]

    resp = await client.post(
        f"/api/v1/warehouse/{wh_id}/fulfillment/requests/99999999/create-assembly",
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_candidates_require_auth(client):
    resp = await client.get("/api/v1/warehouse/1/fulfillment/requests/1/link-candidates")
    assert resp.status_code in (401, 403, 422)
    resp = await client.post("/api/v1/warehouse/1/fulfillment/requests/1/create-assembly")
    assert resp.status_code in (401, 403, 422)


# ─── get_ff_request_goods (кнопка «Состав» в Telegram) ───────────────────────


class TestGetFfRequestGoods:
    @pytest.mark.asyncio
    async def test_assembly_goods(self, db_session, project, warehouse):
        bc_a, bc_b = f"30{_uid()}", f"31{_uid()}"
        nom_a = await _make_nomenclature(db_session, project.id, bc_a)
        nom_b = await _make_nomenclature(db_session, project.id, bc_b)
        asm = await _make_assembly(
            db_session, project.id, warehouse.id, items=[(bc_a, nom_a.id, 5), (bc_b, nom_b.id, 3)]
        )
        ff = await _add(
            db_session,
            _mirror(project.id, warehouse.id, kind="assembly", number="ORD-9", assembly_request_id=asm.id),
        )
        text = await fulfillment_service.get_ff_request_goods(db_session, project.id, ff.id)
        assert text is not None
        assert "Состав заявки" in text
        assert bc_a in text and bc_b in text
        assert nom_a.article_seller in text and nom_b.article_seller in text
        assert "5 шт" in text and "3 шт" in text

    @pytest.mark.asyncio
    async def test_inbound_goods_uses_expected_qty(self, db_session, project, warehouse):
        bc = f"32{_uid()}"
        nom = await _make_nomenclature(db_session, project.id, bc)
        rcpt = await _make_receipt(db_session, project.id, warehouse.id, items=[(bc, nom.id, 12)])
        ff = await _add(db_session, _mirror(project.id, warehouse.id, kind="inbound", inbound_receipt_id=rcpt.id))
        text = await fulfillment_service.get_ff_request_goods(db_session, project.id, ff.id)
        assert text is not None
        assert bc in text and "12 шт" in text

    @pytest.mark.asyncio
    async def test_wrong_project_returns_none(self, db_session, project, other_project, warehouse):
        bc = f"33{_uid()}"
        nom = await _make_nomenclature(db_session, project.id, bc)
        asm = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc, nom.id, 1)])
        ff = await _add(db_session, _mirror(project.id, warehouse.id, kind="assembly", assembly_request_id=asm.id))
        # запрос под чужим проектом не должен раскрыть состав
        assert await fulfillment_service.get_ff_request_goods(db_session, other_project.id, ff.id) is None

    @pytest.mark.asyncio
    async def test_not_found_returns_none(self, db_session, project):
        assert await fulfillment_service.get_ff_request_goods(db_session, project.id, 99_999_999) is None

    @pytest.mark.asyncio
    async def test_unlinked_request_returns_none(self, db_session, project, warehouse):
        ff = await _add(db_session, _mirror(project.id, warehouse.id, kind="other"))
        assert await fulfillment_service.get_ff_request_goods(db_session, project.id, ff.id) is None

    @pytest.mark.asyncio
    async def test_empty_composition_returns_none(self, db_session, project, warehouse):
        asm = await _make_assembly(db_session, project.id, warehouse.id, items=[])
        ff = await _add(db_session, _mirror(project.id, warehouse.id, kind="assembly", assembly_request_id=asm.id))
        assert await fulfillment_service.get_ff_request_goods(db_session, project.id, ff.id) is None


# ─── build_ff_board_text (закреплённое авто-табло) ───────────────────────────


class TestBuildFfBoard:
    @pytest.mark.asyncio
    async def test_groups_active_by_status(self, db_session, project, warehouse):
        bc = f"40{_uid()}"
        nom = await _make_nomenclature(db_session, project.id, bc)
        await _make_assembly(
            db_session, project.id, warehouse.id, status=AssemblyStatus.IN_PROGRESS.value, items=[(bc, nom.id, 100)]
        )
        await _make_assembly(
            db_session, project.id, warehouse.id, status=AssemblyStatus.READY.value, items=[(bc, nom.id, 50)]
        )
        text = await fulfillment_service.build_ff_board_text(db_session, project.id)
        assert text is not None
        assert "Заявки ФФ" in text
        assert "В работе" in text and "Готово к отгрузке" in text
        assert warehouse.name in text
        assert "100 шт" in text and "50 шт" in text
        assert "<blockquote expandable>" in text

    @pytest.mark.asyncio
    async def test_excludes_non_active_statuses(self, db_session, project, warehouse):
        bc = f"41{_uid()}"
        nom = await _make_nomenclature(db_session, project.id, bc)
        await _make_assembly(
            db_session, project.id, warehouse.id, status=AssemblyStatus.SHIPPED.value, items=[(bc, nom.id, 10)]
        )
        await _make_assembly(
            db_session, project.id, warehouse.id, status=AssemblyStatus.CANCELLED.value, items=[(bc, nom.id, 10)]
        )
        assert await fulfillment_service.build_ff_board_text(db_session, project.id) is None

    @pytest.mark.asyncio
    async def test_empty_returns_none(self, db_session, project):
        assert await fulfillment_service.build_ff_board_text(db_session, project.id) is None

    @pytest.mark.asyncio
    async def test_project_scoped(self, db_session, project, other_project, warehouse):
        bc = f"42{_uid()}"
        nom = await _make_nomenclature(db_session, project.id, bc)
        await _make_assembly(
            db_session, project.id, warehouse.id, status=AssemblyStatus.READY.value, items=[(bc, nom.id, 7)]
        )
        assert await fulfillment_service.build_ff_board_text(db_session, other_project.id) is None
