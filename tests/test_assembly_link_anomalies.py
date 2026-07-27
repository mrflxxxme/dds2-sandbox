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
from backend.models.fulfillment import FulfillmentStock
from backend.models.integrations import IntegrationKey
from backend.models.warehouse import WarehouseStock
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
    assert res["stock_mismatch"] == []
    assert res["fbo"] == {
        "without_assembly_count": 0,
        "under_accepted_count": 0,
        "under_accepted_qty": 0,
        "excess_count": 0,
        "excess_qty": 0,
        "without_assembly_supplies": [],
        "under_accepted_supplies": [],
        "excess_supplies": [],
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
async def test_linked_assembly_archived_completed_ff_not_flagged(db_session, project, warehouse):
    """Регресс (сборка ASM-455 / id 561): SHIPPED-сборка, чья заявка ФФ ЗАВЕРШЕНА и
    заархивирована (skladbot сдал заявку в архив после отгрузки), — НЕ аномалия:
    связь была и отработала штатно. archived+is_completed считается живой связью."""
    doc = await _make_assembly(
        db_session, project.id, warehouse.id, status=AssemblyStatus.SHIPPED, items=[("20a", 4)]
    )
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="skladbot",
        assembly_request_id=doc.id,
        archived=True,
        is_completed=True,
    )
    res = await _raw(db_session, project.id)
    assert all(r["assembly_id"] != doc.id for r in res["assemblies_without_ff"])


@pytest.mark.asyncio
async def test_linked_assembly_annulled_ff_still_flagged(db_session, project, warehouse):
    """Заявка ФФ аннулирована (archived, но НЕ completed — напр. wmscelicom «Аннулирована»):
    живой связи у сборки больше нет → она остаётся аномалией (нужна новая привязка)."""
    doc = await _make_assembly(
        db_session, project.id, warehouse.id, status=AssemblyStatus.READY, items=[("20a", 4)]
    )
    await _make_ff(
        db_session,
        project.id,
        warehouse.id,
        provider="wmscelicom",
        assembly_request_id=doc.id,
        archived=True,
        is_completed=False,
    )
    res = await _raw(db_session, project.id)
    assert any(r["assembly_id"] == doc.id for r in res["assemblies_without_ff"])


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
async def test_ff_without_assembly_shows_all_non_archived(db_session, project, warehouse):
    """Показываем ВСЁ незвязанное, кроме архивных — стадия/завершённость не фильтруют."""
    active_sklad = await _make_ff(db_session, project.id, warehouse.id, provider="skladbot")
    mig_wip = await _make_ff(db_session, project.id, warehouse.id, provider="migfull", stage_code="new")
    mig_ready = await _make_ff(db_session, project.id, warehouse.id, provider="migfull", stage_code="ready")
    mig_closed = await _make_ff(db_session, project.id, warehouse.id, provider="migfull", stage_code="closed")
    completed = await _make_ff(db_session, project.id, warehouse.id, provider="skladbot", is_completed=True)
    wms_ready = await _make_ff(
        db_session, project.id, warehouse.id, provider="wmscelicom", stage_title="Ожидает отгрузки"
    )
    archived = await _make_ff(db_session, project.id, warehouse.id, provider="skladbot", archived=True)

    res = await _raw(db_session, project.id)
    ids = {r["ff_request_id"] for r in res["ff_without_assembly"]}
    # стадия/завершённость не фильтруют — всё незвязанное и не в архиве показывается
    for ff in (active_sklad, mig_wip, mig_ready, mig_closed, completed, wms_ready):
        assert ff.id in ids
    assert archived.id not in ids  # архив — скрыт


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


# ─── (g) stock_mismatch — расхождение остатков наш склад vs ФФ-зеркало ────────


async def _integrate(db_session, project_id, warehouse_id, service="skladbot"):
    """Сделать склад «ФФ-интегрированным» (IntegrationKey задаёт провайдера)."""
    return await _add(
        db_session,
        IntegrationKey(
            project_id=project_id,
            service=service,
            warehouse_id=warehouse_id,
            encrypted_key="enc",
            label=f"warehouse:{warehouse_id}-{_uid()}",
        ),
    )


async def _ff_stock(
    db_session,
    project_id,
    warehouse_id,
    barcode,
    qty_good,
    *,
    provider="skladbot",
    base_barcode=None,
    units_per_box=1,
    nomenclature_id=None,
    name=None,
):
    return await _add(
        db_session,
        FulfillmentStock(
            project_id=project_id,
            warehouse_id=warehouse_id,
            provider=provider,
            barcode=barcode,
            base_barcode=base_barcode,
            units_per_box=units_per_box,
            qty_good=qty_good,
            nomenclature_id=nomenclature_id,
            name=name,
        ),
    )


async def _our_stock(db_session, project_id, warehouse_id, nomenclature_id, barcode, quantity, defect_quantity=0):
    return await _add(
        db_session,
        WarehouseStock(
            project_id=project_id,
            warehouse_id=warehouse_id,
            nomenclature_id=nomenclature_id,
            barcode=barcode,
            quantity=quantity,
            defect_quantity=defect_quantity,
        ),
    )


def _wh_row(res, warehouse_id):
    rows = [r for r in res["stock_mismatch"] if r["warehouse_id"] == warehouse_id]
    return rows[0] if rows else None


@pytest.mark.asyncio
async def test_stock_mismatch_ff_more(db_session, project, warehouse):
    """У ФФ больше, чем у нас (diff > 0)."""
    await _integrate(db_session, project.id, warehouse.id)
    bc = f"20{_uid()}"
    nom = await _nom(db_session, project.id, bc)
    await _ff_stock(db_session, project.id, warehouse.id, bc, 100, nomenclature_id=nom.id, name="Товар A")
    await _our_stock(db_session, project.id, warehouse.id, nom.id, bc, 60)

    res = await _raw(db_session, project.id)
    row = _wh_row(res, warehouse.id)
    assert row is not None
    assert row["provider"] == "skladbot"
    assert row["surplus_ff_qty"] == 40
    assert row["surplus_ff_sku"] == 1
    assert row["surplus_our_qty"] == 0
    assert row["surplus_our_sku"] == 0
    assert row["net_diff"] == 40
    assert row["sku_total"] == 1
    assert row["truncated"] is False
    assert len(row["rows"]) == 1
    sku = row["rows"][0]
    assert sku["barcode"] == bc
    assert sku["ff_good"] == 100
    assert sku["our_quantity"] == 60
    assert sku["diff"] == 40
    assert sku["article_seller"] == nom.article_seller


@pytest.mark.asyncio
async def test_stock_mismatch_our_more(db_session, project, warehouse):
    """У нас больше, чем у ФФ (diff < 0)."""
    await _integrate(db_session, project.id, warehouse.id)
    bc = f"20{_uid()}"
    nom = await _nom(db_session, project.id, bc)
    await _ff_stock(db_session, project.id, warehouse.id, bc, 30, nomenclature_id=nom.id)
    await _our_stock(db_session, project.id, warehouse.id, nom.id, bc, 80)

    res = await _raw(db_session, project.id)
    row = _wh_row(res, warehouse.id)
    assert row is not None
    assert row["surplus_our_qty"] == 50
    assert row["surplus_our_sku"] == 1
    assert row["surplus_ff_qty"] == 0
    assert row["net_diff"] == -50
    assert row["rows"][0]["diff"] == -50


@pytest.mark.asyncio
async def test_stock_mismatch_migfull_subtracts_our_defect(db_session, project, warehouse):
    """migfull: ФФ годный включает брак → сверяем с нашим итогом (годный+брак).
    Расхождение брак↔годный (итог сходится) не флагается; реальная недостача — да."""
    await _integrate(db_session, project.id, warehouse.id, service="migfull")
    # SKU1: ФФ 939 = наш годный 421 + наш брак 518 → diff 0 (только реклассификация)
    bc1 = f"20{_uid()}"
    nom1 = await _nom(db_session, project.id, bc1)
    await _ff_stock(db_session, project.id, warehouse.id, bc1, 939, provider="migfull", nomenclature_id=nom1.id)
    await _our_stock(db_session, project.id, warehouse.id, nom1.id, bc1, 421, defect_quantity=518)
    # SKU2: реальная недостача — ФФ 100, наш итог 30+0 → diff +70
    bc2 = f"20{_uid()}"
    nom2 = await _nom(db_session, project.id, bc2)
    await _ff_stock(db_session, project.id, warehouse.id, bc2, 100, provider="migfull", nomenclature_id=nom2.id)
    await _our_stock(db_session, project.id, warehouse.id, nom2.id, bc2, 30, defect_quantity=0)

    res = await _raw(db_session, project.id)
    row = _wh_row(res, warehouse.id)
    assert row is not None
    assert row["provider"] == "migfull"
    # SKU1 не во флагах (итог сошёлся), показан только SKU2
    bcs = {s["barcode"] for s in row["rows"]}
    assert bc1 not in bcs and bc2 in bcs
    assert row["surplus_ff_qty"] == 70 and row["surplus_our_qty"] == 0
    assert row["net_diff"] == 70
    sku2 = next(s for s in row["rows"] if s["barcode"] == bc2)
    assert sku2["our_defect"] == 0 and sku2["diff"] == 70


@pytest.mark.asyncio
async def test_stock_mismatch_non_migfull_ignores_our_defect(db_session, project, warehouse):
    """Не-migfull (skladbot): наш брак НЕ вычитается — diff = ФФ годный − наш годный."""
    await _integrate(db_session, project.id, warehouse.id, service="skladbot")
    bc = f"20{_uid()}"
    nom = await _nom(db_session, project.id, bc)
    await _ff_stock(db_session, project.id, warehouse.id, bc, 100, provider="skladbot", nomenclature_id=nom.id)
    await _our_stock(db_session, project.id, warehouse.id, nom.id, bc, 60, defect_quantity=30)

    res = await _raw(db_session, project.id)
    row = _wh_row(res, warehouse.id)
    assert row is not None
    sku = row["rows"][0]
    assert sku["diff"] == 40  # 100 − 60, брак 30 НЕ вычтен
    assert sku["our_defect"] == 0  # для не-migfull брак в строке не показываем


@pytest.mark.asyncio
async def test_stock_mismatch_equal_not_flagged(db_session, project, warehouse):
    """Совпадающий остаток не флагается; склад без расхождений в ответе отсутствует."""
    await _integrate(db_session, project.id, warehouse.id)
    bc = f"20{_uid()}"
    nom = await _nom(db_session, project.id, bc)
    await _ff_stock(db_session, project.id, warehouse.id, bc, 50, nomenclature_id=nom.id)
    await _our_stock(db_session, project.id, warehouse.id, nom.id, bc, 50)

    res = await _raw(db_session, project.id)
    assert _wh_row(res, warehouse.id) is None


@pytest.mark.asyncio
async def test_stock_mismatch_box_to_loose(db_session, project, warehouse):
    """Остаток короба сводится к россыпи (qty_good × units_per_box) перед diff."""
    await _integrate(db_session, project.id, warehouse.id, service="migfull")
    loose_bc = f"20{_uid()}"
    box_bc = f"14{_uid()}"
    nom = await _nom(db_session, project.id, loose_bc)
    # Короб: 5 коробов × 10 = 50 штук россыпи под ШК россыпи loose_bc.
    await _ff_stock(
        db_session,
        project.id,
        warehouse.id,
        box_bc,
        5,
        provider="migfull",
        base_barcode=loose_bc,
        units_per_box=10,
        nomenclature_id=nom.id,
    )
    await _our_stock(db_session, project.id, warehouse.id, nom.id, loose_bc, 30)

    res = await _raw(db_session, project.id)
    row = _wh_row(res, warehouse.id)
    assert row is not None
    assert row["rows"][0]["barcode"] == loose_bc
    assert row["rows"][0]["ff_good"] == 50
    assert row["rows"][0]["diff"] == 20


@pytest.mark.asyncio
async def test_stock_mismatch_only_ff_integrated_warehouses(db_session, project):
    """Склад без ФФ-интеграции (нет IntegrationKey) в расхождении остатков не участвует."""
    wh = await _add(
        db_session,
        Warehouse(project_id=project.id, name=f"OWN-{_uid()}", warehouse_type="OWN"),
    )
    bc = f"20{_uid()}"
    nom = await _nom(db_session, project.id, bc)
    await _ff_stock(db_session, project.id, wh.id, bc, 100, nomenclature_id=nom.id)
    await _our_stock(db_session, project.id, wh.id, nom.id, bc, 10)

    res = await _raw(db_session, project.id)
    assert _wh_row(res, wh.id) is None


@pytest.mark.asyncio
async def test_stock_mismatch_warehouse_filter(db_session, project, warehouse):
    """warehouse_ids фильтрует расхождение остатков."""
    await _integrate(db_session, project.id, warehouse.id)
    bc = f"20{_uid()}"
    nom = await _nom(db_session, project.id, bc)
    await _ff_stock(db_session, project.id, warehouse.id, bc, 100, nomenclature_id=nom.id)
    await _our_stock(db_session, project.id, warehouse.id, nom.id, bc, 60)

    res_other = await _raw(db_session, project.id, warehouse_ids=[warehouse.id + 99999])
    assert res_other["stock_mismatch"] == []
    res_self = await _raw(db_session, project.id, warehouse_ids=[warehouse.id])
    assert _wh_row(res_self, warehouse.id) is not None


@pytest.mark.asyncio
async def test_stock_mismatch_project_isolation(db_session, project, other_project):
    """Расхождение остатков чужого проекта не видно."""
    other_wh = await _add(
        db_session,
        Warehouse(project_id=other_project.id, name=f"OTH-{_uid()}", warehouse_type="FULFILLMENT"),
    )
    await _integrate(db_session, other_project.id, other_wh.id)
    bc = f"20{_uid()}"
    other_nom = await _add(
        db_session, Nomenclature(project_id=other_project.id, barcode=bc, article_seller=f"ART-{_uid()[:6]}")
    )
    await _ff_stock(db_session, other_project.id, other_wh.id, bc, 100, nomenclature_id=other_nom.id)
    await _our_stock(db_session, other_project.id, other_wh.id, other_nom.id, bc, 10)

    res = await _raw(db_session, project.id)
    assert res["stock_mismatch"] == []


# ─── (g2) stock_mismatch — досчёт логистики в транзите (паритет с list_stocks) ─


async def _logistics_transit(
    db_session,
    project_id,
    warehouse_id,
    barcode,
    qty,
    nomenclature_id,
    *,
    status=AssemblyStatus.IN_PROGRESS,
    stage_code="logistics_works",
    is_completed=False,
):
    """Сборка в пред-отгрузочном статусе + привязанная ФФ-заявка на стадии списания
    логистики: skladbot уже списал ff_good, но груз физически на складе и наш сток
    его держит. Такой объём list_stocks досчитывает к ff_good — stock_mismatch тоже.
    """
    doc = await _make_assembly(db_session, project_id, warehouse_id, status=status, items=())
    db_session.add(
        AssemblyRequestItem(
            project_id=project_id,
            assembly_request_id=doc.id,
            nomenclature_id=nomenclature_id,
            barcode=barcode,
            quantity=qty,
        )
    )
    await db_session.commit()
    await _make_ff(
        db_session,
        project_id,
        warehouse_id,
        provider="skladbot",
        kind="assembly",
        assembly_request_id=doc.id,
        stage_code=stage_code,
        is_completed=is_completed,
    )
    return doc


@pytest.mark.asyncio
async def test_stock_mismatch_logistics_in_transit_added_to_ff(db_session, project, warehouse):
    """Товар в стадии списания логистики (skladbot) досчитывается к ff_good → ложная
    недостача «у нас больше» гасится; остаётся только реальное расхождение."""
    await _integrate(db_session, project.id, warehouse.id)
    # SKU A: ff-зеркало просело на 40 из-за списания логистики (наш сток держит).
    bc_a = f"20{_uid()}"
    nom_a = await _nom(db_session, project.id, bc_a)
    await _ff_stock(db_session, project.id, warehouse.id, bc_a, 60, nomenclature_id=nom_a.id)
    await _our_stock(db_session, project.id, warehouse.id, nom_a.id, bc_a, 100)
    await _logistics_transit(db_session, project.id, warehouse.id, bc_a, 40, nom_a.id)
    # SKU B: реальное расхождение (у ФФ больше на 10) — должно остаться.
    bc_b = f"20{_uid()}"
    nom_b = await _nom(db_session, project.id, bc_b)
    await _ff_stock(db_session, project.id, warehouse.id, bc_b, 30, nomenclature_id=nom_b.id)
    await _our_stock(db_session, project.id, warehouse.id, nom_b.id, bc_b, 20)

    res = await _raw(db_session, project.id)
    row = _wh_row(res, warehouse.id)
    assert row is not None
    # SKU A сошёлся (60 + 40 логистики == 100) → фантом «у нас больше» ушёл.
    assert row["surplus_our_qty"] == 0
    assert row["surplus_our_sku"] == 0
    assert row["surplus_ff_qty"] == 10
    assert row["net_diff"] == 10
    assert row["sku_total"] == 1
    assert [r["barcode"] for r in row["rows"]] == [bc_b]


@pytest.mark.asyncio
async def test_stock_mismatch_logistics_in_transit_partial(db_session, project, warehouse):
    """Досчёт меньше разрыва → остаётся честный остаточный дифф (у нас больше)."""
    await _integrate(db_session, project.id, warehouse.id)
    bc = f"20{_uid()}"
    nom = await _nom(db_session, project.id, bc)
    await _ff_stock(db_session, project.id, warehouse.id, bc, 60, nomenclature_id=nom.id)
    await _our_stock(db_session, project.id, warehouse.id, nom.id, bc, 100)
    await _logistics_transit(db_session, project.id, warehouse.id, bc, 25, nom.id)

    res = await _raw(db_session, project.id)
    row = _wh_row(res, warehouse.id)
    assert row is not None
    assert row["rows"][0]["ff_good"] == 85  # 60 + 25 логистики
    assert row["rows"][0]["diff"] == -15
    assert row["surplus_our_qty"] == 15


@pytest.mark.asyncio
async def test_stock_mismatch_logistics_clamped_to_shortfall(db_session, project, warehouse):
    """Провайдер списывает ПОЗИЦИОННО: досчёт состава «как есть» родил бы ложный
    профицит ФФ. Кламп по наблюдаемой недостаче — паритет с list_stocks."""
    await _integrate(db_session, project.id, warehouse.id)
    # A: зеркало позицию НЕ списало (ff == наш) → досчёт обязан быть 0, не +40.
    bc_a = f"20{_uid()}"
    nom_a = await _nom(db_session, project.id, bc_a)
    await _ff_stock(db_session, project.id, warehouse.id, bc_a, 100, nomenclature_id=nom_a.id)
    await _our_stock(db_session, project.id, warehouse.id, nom_a.id, bc_a, 100)
    await _logistics_transit(db_session, project.id, warehouse.id, bc_a, 40, nom_a.id)
    # B: списало частично (10 из 40) → досчитываем ровно 10.
    bc_b = f"20{_uid()}"
    nom_b = await _nom(db_session, project.id, bc_b)
    await _ff_stock(db_session, project.id, warehouse.id, bc_b, 90, nomenclature_id=nom_b.id)
    await _our_stock(db_session, project.id, warehouse.id, nom_b.id, bc_b, 100)
    await _logistics_transit(db_session, project.id, warehouse.id, bc_b, 40, nom_b.id)

    res = await _raw(db_session, project.id)
    row = _wh_row(res, warehouse.id)
    # Обе строки сходятся в 0 → в вывод (только diff != 0) не попадают вовсе.
    assert row is None or row["surplus_ff_qty"] == 0
    assert row is None or bc_a not in {r["barcode"] for r in row["rows"]}
    assert row is None or bc_b not in {r["barcode"] for r in row["rows"]}


@pytest.mark.asyncio
async def test_stock_mismatch_logistics_not_counted_when_shipped(db_session, project, warehouse):
    """SHIPPED-сборка (наш сток уже списан) НЕ досчитывается — иначе ложный излишек."""
    await _integrate(db_session, project.id, warehouse.id)
    bc = f"20{_uid()}"
    nom = await _nom(db_session, project.id, bc)
    await _ff_stock(db_session, project.id, warehouse.id, bc, 60, nomenclature_id=nom.id)
    await _our_stock(db_session, project.id, warehouse.id, nom.id, bc, 100)
    await _logistics_transit(db_session, project.id, warehouse.id, bc, 40, nom.id, status=AssemblyStatus.SHIPPED)

    res = await _raw(db_session, project.id)
    row = _wh_row(res, warehouse.id)
    assert row is not None
    assert row["surplus_our_qty"] == 40  # без досчёта


@pytest.mark.asyncio
async def test_stock_mismatch_logistics_not_counted_wrong_stage(db_session, project, warehouse):
    """Заявка не на стадии списания логистики не досчитывается (migfull/wmscelicom без
    стадии logistics_works — пустой досчёт, их расхождения фикс не трогает)."""
    await _integrate(db_session, project.id, warehouse.id)
    bc = f"20{_uid()}"
    nom = await _nom(db_session, project.id, bc)
    await _ff_stock(db_session, project.id, warehouse.id, bc, 60, nomenclature_id=nom.id)
    await _our_stock(db_session, project.id, warehouse.id, nom.id, bc, 100)
    await _logistics_transit(db_session, project.id, warehouse.id, bc, 40, nom.id, stage_code="new")

    res = await _raw(db_session, project.id)
    row = _wh_row(res, warehouse.id)
    assert row is not None
    assert row["surplus_our_qty"] == 40  # стадия не logistics_works → без досчёта


# ─── (h) fbo списки поставок (разворот) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_fbo_supply_lists_populated(db_session, project):
    """Под счётчиками лежат сами поставки: недоприёмка / излишек / без заявки."""
    under = await _make_supply(db_session, project.id, status=WbSupplyStatus.ACCEPTED, total_qty=10, accepted_qty=7)
    excess = await _make_supply(db_session, project.id, status=WbSupplyStatus.ACCEPTED, total_qty=5, accepted_qty=8)

    res = await _raw(db_session, project.id)
    fbo = res["fbo"]

    under_ids = {s["supply_id"] for s in fbo["under_accepted_supplies"]}
    assert under.id in under_ids
    under_row = next(s for s in fbo["under_accepted_supplies"] if s["supply_id"] == under.id)
    assert under_row["wb_supply_id"] == under.wb_supply_id
    assert under_row["total_qty"] == 10
    assert under_row["accepted_qty"] == 7
    assert under_row["diff"] == -3

    excess_ids = {s["supply_id"] for s in fbo["excess_supplies"]}
    assert excess.id in excess_ids
    excess_row = next(s for s in fbo["excess_supplies"] if s["supply_id"] == excess.id)
    assert excess_row["diff"] == 3

    # Обе поставки не привязаны к сборке → в «без заявки».
    no_asm_ids = {s["supply_id"] for s in fbo["without_assembly_supplies"]}
    assert under.id in no_asm_ids
    assert excess.id in no_asm_ids
