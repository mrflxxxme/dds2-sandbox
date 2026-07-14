# ruff: noqa: RUF001, RUF002, RUF003
"""
Флаг расхождения наполнения связанной сборки с заявкой(ами) ФФ (все провайдеры).

compute_doc_ff_mismatch считает по ЗЕРКАЛУ (без HTTP): по ШК где состав есть
(wmscelicom из raw items, migfull из planned_lines + guid→barcode из остатков),
иначе по суммарному кол-ву (skladbot — total_qty). При N:1 — объединённый состав
всех привязанных заявок. None — определить нельзя.
"""

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio

from backend.models import FulfillmentRequest, FulfillmentStock, Nomenclature, Warehouse
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.services import fulfillment_service


def _uid() -> str:
    return uuid.uuid4().hex[:8]


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


async def _nom(db_session, project_id, barcode):
    return await _add(
        db_session, Nomenclature(project_id=project_id, barcode=barcode, article_seller=f"ART-{_uid()[:6]}")
    )


async def _make_assembly(db_session, project_id, warehouse_id, *, items=()):
    """items — [(barcode, qty)]; под каждый ШК создаётся номенклатура."""
    doc = await _add(
        db_session,
        AssemblyRequest(
            project_id=project_id,
            warehouse_id=warehouse_id,
            number=f"A-{_uid()[:6]}",
            status=AssemblyStatus.IN_PROGRESS.value,
            pallets_count=1,
            pallet_weight_kg=Decimal("10.00"),
        ),
    )
    if items:
        for bc, qty in items:
            nom = await _nom(db_session, project_id, bc)
            db_session.add(
                AssemblyRequestItem(
                    project_id=project_id,
                    assembly_request_id=doc.id,
                    nomenclature_id=nom.id,
                    barcode=bc,
                    quantity=qty,
                )
            )
        await db_session.commit()
    return doc


async def _make_ff(
    db_session, project_id, warehouse_id, *, provider, assembly_request_id, raw=None, total_qty=None, is_completed=False
):
    return await _add(
        db_session,
        FulfillmentRequest(
            project_id=project_id,
            warehouse_id=warehouse_id,
            provider=provider,
            external_id=_uid(),
            number=f"FF-{_uid()[:6]}",
            kind="assembly",
            status="Новый",
            assembly_request_id=assembly_request_id,
            raw=raw,
            total_qty=total_qty,
            is_completed=is_completed,
        ),
    )


def _wms_raw(items):
    """DispatchOrder raw: items [(barcode, count)]."""
    return {"items": [{"barcode": bc, "count": qty} for bc, qty in items]}


def _migfull_lines(rows):
    return [
        {"product_guid": guid, "quantity": qty, "product": {"guid": guid, "name": f"Товар {guid}"}}
        for guid, qty in rows
    ]


def _migfull_raw(lines, *, shipped=None):
    """planned_lines [(guid, qty)]; shipped — [(guid, qty)] для shipped_lines (факт)."""
    raw = {"planned_lines": _migfull_lines(lines)}
    if shipped is not None:
        raw["shipped_lines"] = _migfull_lines(shipped)
    return raw


async def _seed_migfull_stock(db_session, project_id, warehouse_id, guid, barcode):
    """Зеркало остатков migfull: guid→barcode (россыпь, units=1)."""
    await _add(
        db_session,
        FulfillmentStock(
            project_id=project_id,
            warehouse_id=warehouse_id,
            provider="migfull",
            barcode=barcode,
            external_product_id=guid,
            units_per_box=1,
        ),
    )


async def _verdict(db_session, project_id, assembly_id):
    mm = await fulfillment_service.get_assembly_ff_mismatch_map(db_session, project_id, {assembly_id})
    return mm.get(assembly_id)


# ─── wmscelicom: сверка по ШК ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wmscelicom_match_and_mismatch(db_session, project, warehouse):
    bc1, bc2 = f"20{_uid()}", f"20{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc1, 5), (bc2, 3)])
    ff = await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="wmscelicom",
        assembly_request_id=doc.id,
        raw=_wms_raw([(bc1, 5), (bc2, 3)]),
    )
    assert await _verdict(db_session, project.id, doc.id) is False  # совпадает

    ff.raw = _wms_raw([(bc1, 5), (bc2, 4)])  # qty разошлось
    await db_session.commit()
    assert await _verdict(db_session, project.id, doc.id) is True


@pytest.mark.asyncio
async def test_wmscelicom_extra_barcode_is_mismatch(db_session, project, warehouse):
    bc1, bc2 = f"20{_uid()}", f"20{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc1, 5)])
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="wmscelicom",
        assembly_request_id=doc.id,
        raw=_wms_raw([(bc1, 5), (bc2, 1)]),  # лишний ШК у ФФ
    )
    assert await _verdict(db_session, project.id, doc.id) is True


@pytest.mark.asyncio
async def test_zero_qty_barcode_not_false_positive(db_session, project, warehouse):
    """Регресс: позиция сборки с qty 0, которой нет в составе ФФ, НЕ должна давать
    ложное расхождение в бейдже (verdict), раз детальная панель его не показывает.
    Раньше `combined != our_comp` ловил лишний ключ bc0:0 → бейдж «расхождение»
    горел, а при переходе в заявку панель пустая (our_qty==ff_qty==0)."""
    bc1, bc0 = f"20{_uid()}", f"20{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc1, 5), (bc0, 0)])
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="wmscelicom",
        assembly_request_id=doc.id,
        raw=_wms_raw([(bc1, 5)]),  # bc0 (qty 0) у ФФ отсутствует
    )
    # Бейдж: расхождения нет (0-позиция игнорируется)
    assert await _verdict(db_session, project.id, doc.id) is False
    # Панель: тоже нет расхождения — итоги сходятся, строк нет (консистентно с бейджем)
    detail = await fulfillment_service.get_assembly_ff_mismatch_detail(db_session, project.id, doc.id)
    assert detail["mode"] == "barcode"
    assert detail["our_total"] == detail["ff_total"] == 5
    assert detail["rows"] == []


# ─── migfull N:1: объединённый состав двух заявок ────────────────────────────


@pytest.mark.asyncio
async def test_migfull_combined_n1_match_and_mismatch(db_session, project, warehouse):
    bc1, bc2 = f"20{_uid()}", f"20{_uid()}"
    guid_a, guid_b = _uid(), _uid()
    await _seed_migfull_stock(db_session, project.id, warehouse.id, guid_a, bc1)
    await _seed_migfull_stock(db_session, project.id, warehouse.id, guid_b, bc2)
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc1, 6), (bc2, 4)])

    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="migfull",
        assembly_request_id=doc.id,
        raw=_migfull_raw([(guid_a, 6)]),
    )
    ff2 = await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="migfull",
        assembly_request_id=doc.id,
        raw=_migfull_raw([(guid_b, 4)]),
    )
    # объединённо {bc1:6, bc2:4} == наш состав
    assert await _verdict(db_session, project.id, doc.id) is False

    ff2.raw = _migfull_raw([(guid_b, 3)])  # недодали по второй заявке
    await db_session.commit()
    assert await _verdict(db_session, project.id, doc.id) is True


# ─── migfull: закрытая (отгружена) заявка сверяется по ФАКТУ shipped_lines ────


@pytest.mark.asyncio
async def test_closed_migfull_compares_shipped_not_planned(db_session, project, warehouse):
    """Закрытая (отгружена, is_completed) migfull-заявка сверяется с ФАКТОМ
    shipped_lines, а НЕ с предварительным планом planned_lines. План закрытой
    заявки неактуален: ФФ отгрузил не весь план → сверка с планом давала ложное
    расхождение (прод-кейс PVB-0000306)."""
    bc1 = f"20{_uid()}"
    guid_a = _uid()
    await _seed_migfull_stock(db_session, project.id, warehouse.id, guid_a, bc1)
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc1, 5)])
    ff = await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="migfull",
        assembly_request_id=doc.id,
        raw=_migfull_raw([(guid_a, 3)], shipped=[(guid_a, 5)]),  # план 3, факт 5
        is_completed=True,
    )
    # закрыта → сверка по факту 5 == наш 5 → совпадает (по плану 3≠5 дало бы ⚠️)
    assert await _verdict(db_session, project.id, doc.id) is False
    detail = await fulfillment_service.get_assembly_ff_mismatch_detail(db_session, project.id, doc.id)
    assert detail["mode"] == "barcode"
    assert detail["ff_total"] == 5 and detail["our_total"] == 5
    assert detail["rows"] == []

    # та же заявка ОТКРЫТА → сверка по плану 3 ≠ наш 5 → расхождение
    ff.is_completed = False
    await db_session.commit()
    assert await _verdict(db_session, project.id, doc.id) is True


@pytest.mark.asyncio
async def test_closed_migfull_planned_only_position_dropped(db_session, project, warehouse):
    """Позиция есть в плане, но НЕ отгружена (нет в shipped_lines) у закрытой
    заявки → не показывается как расхождение ФФ. Реальный кейс: винтаж был в
    плане PVB-0000306, отгружено 0 → панель ложно рисовала +25/+10/+10."""
    bc1, bc2 = f"20{_uid()}", f"20{_uid()}"
    guid_a, guid_b = _uid(), _uid()
    await _seed_migfull_stock(db_session, project.id, warehouse.id, guid_a, bc1)
    await _seed_migfull_stock(db_session, project.id, warehouse.id, guid_b, bc2)
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc1, 5)])
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="migfull",
        assembly_request_id=doc.id,
        # план: bc1 5 + bc2 10 (винтаж); факт отгружено: только bc1 5
        raw=_migfull_raw([(guid_a, 5), (guid_b, 10)], shipped=[(guid_a, 5)]),
        is_completed=True,
    )
    assert await _verdict(db_session, project.id, doc.id) is False  # bc2 не отгружен → не расхождение
    detail = await fulfillment_service.get_assembly_ff_mismatch_detail(db_session, project.id, doc.id)
    assert detail["ff_total"] == 5 and detail["our_total"] == 5
    assert detail["rows"] == []


@pytest.mark.asyncio
async def test_closed_migfull_empty_shipped_falls_back_to_planned(db_session, project, warehouse):
    """Закрыта, но shipped_lines пуст (нет данных отгрузки) → фолбэк на план,
    чтобы не потерять состав целиком."""
    bc1 = f"20{_uid()}"
    guid_a = _uid()
    await _seed_migfull_stock(db_session, project.id, warehouse.id, guid_a, bc1)
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc1, 5)])
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="migfull",
        assembly_request_id=doc.id,
        raw=_migfull_raw([(guid_a, 5)], shipped=[]),  # план есть, отгрузки нет
        is_completed=True,
    )
    # пустой shipped → сверяем по плану 5 == наш 5
    assert await _verdict(db_session, project.id, doc.id) is False


# ─── skladbot: фолбэк по суммарному кол-ву ───────────────────────────────────


@pytest.mark.asyncio
async def test_skladbot_total_fallback(db_session, project, warehouse):
    bc = f"20{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc, 10)])
    ff = await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="skladbot",
        assembly_request_id=doc.id,
        total_qty=10,
    )
    assert await _verdict(db_session, project.id, doc.id) is False  # 10 == 10

    ff.total_qty = 12
    await db_session.commit()
    assert await _verdict(db_session, project.id, doc.id) is True  # 12 != 10


@pytest.mark.asyncio
async def test_skladbot_unknown_total_is_none(db_session, project, warehouse):
    bc = f"20{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc, 10)])
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="skladbot",
        assembly_request_id=doc.id,
        total_qty=None,
    )
    assert await _verdict(db_session, project.id, doc.id) is None  # определить нельзя


# ─── surfacing: list_requests + map ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_requests_exposes_linked_mismatch(db_session, project, warehouse):
    bc = f"20{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc, 5)])
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="wmscelicom",
        assembly_request_id=doc.id,
        raw=_wms_raw([(bc, 9)]),  # расхождение
    )
    rows = await fulfillment_service.list_requests(db_session, project.id, warehouse.id, "assembly")
    assert len(rows) == 1
    assert rows[0]["linked_mismatch"] is True


@pytest.mark.asyncio
async def test_unlinked_assembly_absent_from_map(db_session, project, warehouse):
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[("20x", 5)])
    mm = await fulfillment_service.get_assembly_ff_mismatch_map(db_session, project.id, {doc.id})
    assert doc.id not in mm  # нет привязанных заявок → нет вердикта


# ─── граничные случаи (по адверсариал-ревью) ─────────────────────────────────


@pytest.mark.asyncio
async def test_migfull_partial_guid_resolution_falls_back_not_false_positive(db_session, project, warehouse):
    """Часть guid не в зеркале остатков (cold-start) → НЕ ложное расхождение по ШК.

    Один guid разрезолвлен (bc1), второй — нет. Состав по ШК неполный → вердикт
    уходит в фолбэк по total_qty; при совпадающем тотале расхождения нет.
    """
    bc1, bc2 = f"20{_uid()}", f"20{_uid()}"
    guid_a, guid_b = _uid(), _uid()
    await _seed_migfull_stock(db_session, project.id, warehouse.id, guid_a, bc1)  # только A в остатках
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc1, 6), (bc2, 4)])
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="migfull",
        assembly_request_id=doc.id,
        raw=_migfull_raw([(guid_a, 6), (guid_b, 4)]),
        total_qty=10,
    )
    # тотал 10 == наш итог 10 → совпадает (не ложный True из-за неразрезолвленного guid_b)
    assert await _verdict(db_session, project.id, doc.id) is False


@pytest.mark.asyncio
async def test_verdict_fallback_migfull_counts_units_not_boxes(db_session, project, warehouse):
    """Флаг расхождения в фолбэке: ФФ migfull в ШТУКАХ (короба×штук), не коробах.
    Раньше total_qty=коробá сравнивался с нашим итогом в штуках → ложный ⚠️."""
    base_a, box_a = f"20{_uid()}", f"10{_uid()}"
    guid_a, guid_b = f"g{_uid()}", f"g{_uid()}"
    # guid_a — короб 10 шт в зеркале; guid_b НЕ в зеркале → состав неполный → фолбэк
    await _add(
        db_session,
        FulfillmentStock(
            project_id=project.id, warehouse_id=warehouse.id, provider="migfull",
            barcode=box_a, base_barcode=base_a, units_per_box=10, external_product_id=guid_a,
        ),
    )
    bc_b = f"20{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(base_a, 30), (bc_b, 2)])
    await _make_ff(
        db_session, project.id, warehouse.id, provider="migfull",
        assembly_request_id=doc.id, raw=_migfull_raw([(guid_a, 3), (guid_b, 2)]), total_qty=5,
    )
    # ФФ в штуках: 3 кор ×10 + 2 ×1 = 32 == наш итог 32 → НЕ расхождение (старо: 5 коробов != 32 → ⚠️)
    assert await _verdict(db_session, project.id, doc.id) is False


@pytest.mark.asyncio
async def test_archived_ff_excluded_from_verdict(db_session, project, warehouse):
    """Локально-архивная заявка ФФ не должна раздувать сводный состав (ложное расхождение)."""
    bc = f"20{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc, 5)])
    # живая заявка совпадает по составу
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="wmscelicom",
        assembly_request_id=doc.id,
        raw=_wms_raw([(bc, 5)]),
    )
    # вторая привязана, но локально заархивирована (superseded) + лишний ШК
    archived = await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="wmscelicom",
        assembly_request_id=doc.id,
        raw=_wms_raw([(bc, 5), (f"20{_uid()}", 9)]),
    )
    archived.local_archived = True
    await db_session.commit()
    assert await _verdict(db_session, project.id, doc.id) is False  # архивную не учитываем


# ─── разбивка расхождения (модалка «что не так») ─────────────────────────────


@pytest.mark.asyncio
async def test_mismatch_detail_barcode_rows(db_session, project, warehouse):
    bc1, bc2 = f"20{_uid()}", f"20{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc1, 5), (bc2, 3)])
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="wmscelicom",
        assembly_request_id=doc.id,
        raw=_wms_raw([(bc1, 5), (bc2, 7)]),  # bc2: ФФ 7 vs наш 3
    )
    detail = await fulfillment_service.get_assembly_ff_mismatch_detail(db_session, project.id, doc.id)
    assert detail is not None
    assert detail["mode"] == "barcode"
    assert detail["our_total"] == 8 and detail["ff_total"] == 12
    # только расходящаяся позиция bc2 (diff = 7-3 = +4); совпавшая bc1 не в rows
    assert [(r["barcode"], r["our_qty"], r["ff_qty"], r["diff"]) for r in detail["rows"]] == [(bc2, 3, 7, 4)]


@pytest.mark.asyncio
async def test_mismatch_detail_total_mode_for_skladbot(db_session, project, warehouse):
    bc = f"20{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc, 10)])
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="skladbot",
        assembly_request_id=doc.id,
        total_qty=14,
    )
    detail = await fulfillment_service.get_assembly_ff_mismatch_detail(db_session, project.id, doc.id)
    assert detail is not None
    assert detail["mode"] == "total"
    assert detail["our_total"] == 10 and detail["ff_total"] == 14
    assert detail["rows"] == []  # по позициям состав недоступен


@pytest.mark.asyncio
async def test_mismatch_detail_total_mode_migfull_counts_units_not_boxes(db_session, project, warehouse):
    """migfull-отгрузка в фолбэке mode=total: ФФ-итог в ШТУКАХ (короба × штук в коробе),
    а НЕ в коробах из total_qty (баг: показывало кол-во коробов)."""
    guid_a, guid_b = f"g{_uid()}", f"g{_uid()}"
    base_a, box_a = f"20{_uid()}", f"10{_uid()}"
    # зеркало: guid_a — короб 10 шт (units=10); guid_b НЕ в зеркале → состав неполный → mode=total
    await _add(
        db_session,
        FulfillmentStock(
            project_id=project.id, warehouse_id=warehouse.id, provider="migfull",
            barcode=box_a, base_barcode=base_a, units_per_box=10, external_product_id=guid_a,
        ),
    )
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(base_a, 30)])
    await _make_ff(
        db_session, project.id, warehouse.id, provider="migfull",
        assembly_request_id=doc.id, raw=_migfull_raw([(guid_a, 3), (guid_b, 2)]), total_qty=5,
    )
    detail = await fulfillment_service.get_assembly_ff_mismatch_detail(db_session, project.id, doc.id)
    assert detail["mode"] == "total"  # guid_b не разрезолвлен → состав неполный
    # ФФ в ШТУКАХ: guid_a 3 кор ×10 = 30 + guid_b 2 ×1 = 2 → 32; НЕ 5 (коробов из total_qty)
    assert detail["ff_total"] == 32
    assert detail["our_total"] == 30


@pytest.mark.asyncio
async def test_mismatch_detail_live_resolves_migfull_units(db_session, project, warehouse, monkeypatch):
    """live=True: отгрузка с ШК короба в карточке, но guid НЕ в зеркале → дотягиваем
    живой карточкой → mode=barcode, ФФ-итог в штуках (короб × шт), а не фолбэк в коробах."""
    from backend.integrations.migfull_client import MigfullClient

    async def _ok(self):
        return True

    monkeypatch.setattr(MigfullClient, "test_connection", _ok)
    await fulfillment_service.connect(
        db_session, project.id, warehouse.id, "migfull", "tok", tenant_guid=str(uuid.uuid4())
    )
    box = "12046928659864"  # ШК короба (ITF14)
    base = fulfillment_service._itf14_to_ean13(box)  # россыпь EAN13

    async def _card(self, guid):
        return {"name": "YY короб 15 шт.", "barcodes": [{"value": box, "is_primary": True}]}

    monkeypatch.setattr(MigfullClient, "fetch_product", _card)

    guid = f"g{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(base, 15)])
    await _make_ff(
        db_session, project.id, warehouse.id, provider="migfull",
        assembly_request_id=doc.id, raw=_migfull_raw([(guid, 1)]), total_qty=1,
    )
    # без live: guid не в зеркале → фолбэк mode=total
    d0 = await fulfillment_service.get_assembly_ff_mismatch_detail(db_session, project.id, doc.id)
    assert d0["mode"] == "total"
    # live: дотянули карточку → mode=barcode, ФФ = 1 кор × 15 = 15 шт, сошлось с нашим 15
    d1 = await fulfillment_service.get_assembly_ff_mismatch_detail(db_session, project.id, doc.id, live=True)
    assert d1["mode"] == "barcode"
    assert d1["ff_total"] == 15 and d1["our_total"] == 15
    assert d1["rows"] == []  # совпало по позициям


@pytest.mark.asyncio
async def test_mismatch_detail_404_other_project(db_session, project, other_project, warehouse):
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[("20x", 5)])
    assert await fulfillment_service.get_assembly_ff_mismatch_detail(db_session, other_project.id, doc.id) is None
