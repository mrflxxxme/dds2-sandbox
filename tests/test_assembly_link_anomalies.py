# ruff: noqa: RUF001, RUF002, RUF003
"""
Вкладка «Связи и расхождения» страницы «Анализ сборки» — get_link_anomalies.

Покрывает четыре блока ответа LinkAnomaliesResponse (read-only, по зеркалу):
  1. ff_composition_mismatch — расхождение состава сборки с привязанными ФФ;
  2. assemblies_without_ff — сборка на ФФ-складе без привязанной заявки ФФ;
  3. ff_without_assembly — заявка ФФ без привязанной нашей сборки;
  4. fbo — сводка аномалий FBO-поставок ВБ (недоприёмка / излишек / без заявки).

Плюс изоляция по project_id и пустой проект → пустой каркас + нули FBO.

NB: get_link_anomalies обёрнут @cached. Тесты ходят на «сырой» __wrapped__,
чтобы не зависеть от Redis и не ловить кросс-тестовые попадания в кэш.
"""

import uuid
from datetime import datetime
from decimal import Decimal

import pytest
import pytest_asyncio

from backend.models import FulfillmentRequest, Nomenclature, Warehouse
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.integrations import IntegrationKey
from backend.models.wb_fbo import WbFboSupply, WbFboSupplyItem, WbSupplyStatus
from backend.services.assembly.link_anomalies import get_link_anomalies

# «Сырая» (необёрнутая) функция — без Redis-кэша.
_raw = get_link_anomalies.__wrapped__


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _add(db_session, obj):
    db_session.add(obj)
    await db_session.commit()
    await db_session.refresh(obj)
    return obj


@pytest_asyncio.fixture
async def warehouse(db_session, project):
    return await _add(
        db_session,
        Warehouse(project_id=project.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT"),
    )


async def _nom(db_session, project_id, barcode):
    return await _add(
        db_session,
        Nomenclature(project_id=project_id, barcode=barcode, article_seller=f"ART-{_uid()[:6]}"),
    )


async def _make_assembly(db_session, project_id, warehouse_id, *, status=AssemblyStatus.IN_PROGRESS, items=()):
    """items — [(barcode, qty)]; под каждый ШК создаётся номенклатура."""
    doc = await _add(
        db_session,
        AssemblyRequest(
            project_id=project_id,
            warehouse_id=warehouse_id,
            number=f"A-{_uid()[:6]}",
            status=status.value,
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


def _wms_raw(items):
    """wmscelicom DispatchOrder raw: items [(barcode, count)]."""
    return {"items": [{"barcode": bc, "count": qty} for bc, qty in items]}


async def _make_ff(
    db_session,
    project_id,
    warehouse_id,
    *,
    provider="wmscelicom",
    kind="assembly",
    assembly_request_id=None,
    raw=None,
    total_qty=None,
    number=None,
    archived=False,
    local_archived=False,
    is_completed=False,
    expired=False,
    stage_code=None,
    stage_title=None,
):
    return await _add(
        db_session,
        FulfillmentRequest(
            project_id=project_id,
            warehouse_id=warehouse_id,
            provider=provider,
            external_id=_uid(),
            number=number or f"FF-{_uid()[:6]}",
            kind=kind,
            status="Новый",
            assembly_request_id=assembly_request_id,
            raw=raw,
            total_qty=total_qty,
            archived=archived,
            local_archived=local_archived,
            is_completed=is_completed,
            expired=expired,
            stage_code=stage_code,
            stage_title=stage_title,
        ),
    )


async def _make_supply(
    db_session,
    project_id,
    *,
    status=WbSupplyStatus.ACCEPTED,
    total_qty=0,
    accepted_qty=0,
    warehouse_name="Коледино",
):
    return await _add(
        db_session,
        WbFboSupply(
            project_id=project_id,
            wb_supply_id=f"WB-{_uid()}",
            wb_status=status,
            warehouse_name=warehouse_name,
            total_qty=total_qty,
            accepted_qty=accepted_qty,
            created_at_wb=datetime(2026, 3, 20),
        ),
    )


# ─── (f) пустой проект ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_project(db_session, project):
    res = await _raw(db_session, project.id)
    assert res["ff_composition_mismatch"] == []
    assert res["assemblies_without_ff"] == []
    assert res["ff_without_assembly"] == []
    assert res["fbo"] == {
        "without_assembly_count": 0,
        "under_accepted_count": 0,
        "under_accepted_qty": 0,
        "excess_count": 0,
        "excess_qty": 0,
    }


# ─── (b) ff_composition_mismatch ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_composition_mismatch_detected(db_session, project, warehouse):
    bc1, bc2 = f"20{_uid()}", f"20{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc1, 5), (bc2, 3)])
    # ФФ-заявка с расходящимся составом: bc2 = 7 вместо 3 → ff_total 12 vs our 8.
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="wmscelicom",
        assembly_request_id=doc.id,
        raw=_wms_raw([(bc1, 5), (bc2, 7)]),
    )
    res = await _raw(db_session, project.id)
    rows = res["ff_composition_mismatch"]
    assert len(rows) == 1
    row = rows[0]
    assert row["assembly_id"] == doc.id
    assert row["number"] == doc.number
    assert row["status"] == AssemblyStatus.IN_PROGRESS.value
    assert row["warehouse_id"] == warehouse.id
    assert row["warehouse_name"] == warehouse.name
    assert row["our_total"] == 8
    assert row["ff_total"] == 12
    assert row["diff"] == 4
    assert row["mode"] == "barcode"


@pytest.mark.asyncio
async def test_composition_matching_not_flagged(db_session, project, warehouse):
    bc = f"20{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[(bc, 5)])
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="wmscelicom",
        assembly_request_id=doc.id,
        raw=_wms_raw([(bc, 5)]),  # совпадает
    )
    res = await _raw(db_session, project.id)
    assert res["ff_composition_mismatch"] == []


@pytest.mark.asyncio
async def test_shipped_assembly_excluded_from_mismatch(db_session, project, warehouse):
    """SHIPPED-сборка в блок расхождения состава не попадает (только активные)."""
    bc = f"20{_uid()}"
    doc = await _make_assembly(db_session, project.id, warehouse.id, status=AssemblyStatus.SHIPPED, items=[(bc, 5)])
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="wmscelicom",
        assembly_request_id=doc.id,
        raw=_wms_raw([(bc, 9)]),  # расхождение, но статус SHIPPED
    )
    res = await _raw(db_session, project.id)
    assert res["ff_composition_mismatch"] == []


# ─── (c) assemblies_without_ff ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assembly_without_ff_on_ff_warehouse(db_session, project, warehouse):
    # Склад становится «ФФ-интегрированным» благодаря наличию заявки ФФ на нём.
    await _make_ff(db_session, project.id, warehouse.id, provider="skladbot", assembly_request_id=None)
    # IntegrationKey задаёт провайдера склада.
    await _add(
        db_session,
        IntegrationKey(
            project_id=project.id,
            service="skladbot",
            warehouse_id=warehouse.id,
            encrypted_key="enc",
            label=f"k-{_uid()}",
        ),
    )
    # Наша активная сборка на этом складе без привязки к ФФ.
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[("20a", 4), ("20b", 6)])

    res = await _raw(db_session, project.id)
    rows = [r for r in res["assemblies_without_ff"] if r["assembly_id"] == doc.id]
    assert len(rows) == 1
    row = rows[0]
    assert row["warehouse_id"] == warehouse.id
    assert row["warehouse_name"] == warehouse.name
    assert row["provider"] == "skladbot"
    assert row["total_qty"] == 10
    assert row["created_at"] is not None
    assert row["age_days"] >= 0


@pytest.mark.asyncio
async def test_linked_assembly_not_in_without_ff(db_session, project, warehouse):
    """Сборка с привязанной заявкой ФФ не попадает в assemblies_without_ff."""
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[("20a", 4)])
    await _make_ff(db_session, project.id, warehouse.id, provider="skladbot", assembly_request_id=doc.id)
    res = await _raw(db_session, project.id)
    assert all(r["assembly_id"] != doc.id for r in res["assemblies_without_ff"])


@pytest.mark.asyncio
async def test_assembly_on_non_ff_warehouse_not_flagged(db_session, project, warehouse):
    """Склад без ФФ-интеграции (нет ни одной заявки ФФ) → ручную сборку не флагаем."""
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[("20a", 4)])
    res = await _raw(db_session, project.id)
    assert all(r["assembly_id"] != doc.id for r in res["assemblies_without_ff"])


# ─── (d) ff_without_assembly ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ff_without_assembly(db_session, project, warehouse):
    ff = await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="skladbot",
        kind="assembly",
        assembly_request_id=None,
        total_qty=42,
        number="WH-R-777",
    )
    res = await _raw(db_session, project.id)
    rows = [r for r in res["ff_without_assembly"] if r["ff_request_id"] == ff.id]
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "skladbot"
    assert row["number"] == "WH-R-777"
    assert row["warehouse_id"] == warehouse.id
    assert row["warehouse_name"] == warehouse.name
    assert row["total_qty"] == 42


@pytest.mark.asyncio
async def test_ff_with_assembly_or_archived_excluded(db_session, project, warehouse):
    """Привязанная либо архивная заявка ФФ в ff_without_assembly не попадает."""
    doc = await _make_assembly(db_session, project.id, warehouse.id, items=[("20a", 1)])
    linked = await _make_ff(db_session, project.id, warehouse.id, assembly_request_id=doc.id)
    archived = await _make_ff(db_session, project.id, warehouse.id, assembly_request_id=None, archived=True)
    local_arch = await _make_ff(db_session, project.id, warehouse.id, assembly_request_id=None, local_archived=True)
    inbound = await _make_ff(db_session, project.id, warehouse.id, kind="inbound", assembly_request_id=None)

    res = await _raw(db_session, project.id)
    ids = {r["ff_request_id"] for r in res["ff_without_assembly"]}
    assert linked.id not in ids
    assert archived.id not in ids
    assert local_arch.id not in ids
    assert inbound.id not in ids  # kind != assembly


@pytest.mark.asyncio
async def test_ff_without_assembly_only_active(db_session, project, warehouse):
    """Только активные заявки ФФ: собранные/закрытые/завершённые исключаются."""
    active_sklad = await _make_ff(db_session, project.id, warehouse.id, provider="skladbot")
    mig_wip = await _make_ff(db_session, project.id, warehouse.id, provider="migfull", stage_code="new")
    mig_ready = await _make_ff(db_session, project.id, warehouse.id, provider="migfull", stage_code="ready")
    mig_closed = await _make_ff(db_session, project.id, warehouse.id, provider="migfull", stage_code="closed")
    completed = await _make_ff(db_session, project.id, warehouse.id, provider="skladbot", is_completed=True)
    wms_ready = await _make_ff(
        db_session, project.id, warehouse.id, provider="wmscelicom", stage_title="Ожидает отгрузки"
    )

    res = await _raw(db_session, project.id)
    ids = {r["ff_request_id"] for r in res["ff_without_assembly"]}
    assert active_sklad.id in ids  # skladbot WIP
    assert mig_wip.id in ids  # migfull «Новая»
    assert mig_ready.id not in ids  # migfull «Собран» (ready)
    assert mig_closed.id not in ids  # migfull «Закрыт»
    assert completed.id not in ids  # is_completed
    assert wms_ready.id not in ids  # wmscelicom «Ожидает отгрузки»


# ─── (e) fbo сводка ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fbo_under_accepted(db_session, project):
    """ACCEPTED-поставка с accepted_qty < total_qty → недоприёмка в сводке."""
    supply = await _make_supply(db_session, project.id, status=WbSupplyStatus.ACCEPTED, total_qty=10, accepted_qty=7)
    await _add(
        db_session,
        WbFboSupplyItem(
            project_id=project.id,
            supply_id=supply.id,
            wb_order_id=f"ORD-{_uid()}",
            barcode=f"30{_uid()}",
            quantity=10,
            accepted_qty=7,
        ),
    )
    res = await _raw(db_session, project.id)
    fbo = res["fbo"]
    assert fbo["under_accepted_count"] >= 1
    assert fbo["under_accepted_qty"] >= 3
    # Эта поставка не привязана к сборке → также аномалия «без заявки».
    assert fbo["without_assembly_count"] >= 1


@pytest.mark.asyncio
async def test_fbo_excess(db_session, project):
    """ACCEPTED-поставка с accepted_qty > total_qty → излишек в сводке."""
    await _make_supply(db_session, project.id, status=WbSupplyStatus.ACCEPTED, total_qty=5, accepted_qty=8)
    res = await _raw(db_session, project.id)
    fbo = res["fbo"]
    assert fbo["excess_count"] >= 1
    assert fbo["excess_qty"] >= 3


# ─── (a) изоляция по project_id ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_isolation(db_session, project, other_project):
    """Данные чужого проекта не видны в ответе."""
    other_wh = await _add(
        db_session,
        Warehouse(project_id=other_project.id, name=f"OTH-{_uid()}", warehouse_type="FULFILLMENT"),
    )
    # Чужая сборка с расхождением состава.
    bc = f"20{_uid()}"
    other_doc = await _make_assembly(db_session, other_project.id, other_wh.id, items=[(bc, 5)])
    await _make_ff(
        db_session,
        other_project.id,
        other_wh.id,
        provider="wmscelicom",
        assembly_request_id=other_doc.id,
        raw=_wms_raw([(bc, 9)]),
    )
    # Чужая ФФ-заявка без привязки.
    await _make_ff(db_session, other_project.id, other_wh.id, assembly_request_id=None)
    # Чужая FBO-поставка с недоприёмкой.
    await _make_supply(db_session, other_project.id, status=WbSupplyStatus.ACCEPTED, total_qty=10, accepted_qty=4)

    res = await _raw(db_session, project.id)
    assert res["ff_composition_mismatch"] == []
    assert res["assemblies_without_ff"] == []
    assert res["ff_without_assembly"] == []
    assert res["fbo"]["under_accepted_count"] == 0
    assert res["fbo"]["under_accepted_qty"] == 0
    assert res["fbo"]["without_assembly_count"] == 0
    assert res["fbo"]["excess_count"] == 0
    assert res["fbo"]["excess_qty"] == 0


@pytest.mark.asyncio
async def test_warehouse_ids_filter(db_session, project, warehouse):
    """warehouse_ids фильтрует сборочные блоки (но не FBO)."""
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
    # Фильтр по другому складу → расхождение не должно показаться.
    res = await _raw(db_session, project.id, warehouse_ids=[warehouse.id + 99999])
    assert res["ff_composition_mismatch"] == []
    # Фильтр по правильному складу → показывается.
    res2 = await _raw(db_session, project.id, warehouse_ids=[warehouse.id])
    assert any(r["assembly_id"] == doc.id for r in res2["ff_composition_mismatch"])
