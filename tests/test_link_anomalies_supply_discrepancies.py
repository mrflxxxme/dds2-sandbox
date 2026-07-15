# ruff: noqa: RUF001, RUF002, RUF003
"""
Блок «Расхождение поставок ФФ» (get_link_anomalies → supply_discrepancies).

Сборки с назначенной машиной / в пути, чья WB-поставка расходится по:
  • дате сдачи (окно ±1 дня от брони WB, 72ч);
  • паллетам (наши pallets_count ≠ pass_pallets пропуска);
  • неоформленному пропуску (нет реплея / sync_status != PASSED / pass_pallets пуст).

NB: тестируем «сырую» __wrapped__ (без Redis-кэша).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio

from backend.models import Warehouse
from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.assembly_wb import AssemblyWbSupply, WbSupplySyncStatus
from backend.models.wb_fbo import WbFboSupply, WbSupplyStatus
from backend.services.assembly.link_anomalies import get_link_anomalies

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


async def _make_supply(
    db_session,
    project_id,
    *,
    wb_status=WbSupplyStatus.ON_DELIVERY,
    planned_date=None,
    warehouse_name="Коледино",
):
    return await _add(
        db_session,
        WbFboSupply(
            project_id=project_id,
            wb_supply_id=f"WB-{_uid()}",
            wb_status=wb_status,
            planned_date=planned_date,
            warehouse_name=warehouse_name,
            created_at_wb=datetime(2026, 3, 20),
        ),
    )


async def _make_assembly(
    db_session,
    project_id,
    warehouse_id,
    *,
    status=AssemblyStatus.VEHICLE_ASSIGNED,
    pallets_count=2,
    delivery_date=None,
    wb_fbo_supply_id=None,
):
    return await _add(
        db_session,
        AssemblyRequest(
            project_id=project_id,
            warehouse_id=warehouse_id,
            number=f"A-{_uid()[:6]}",
            status=status.value,
            pallets_count=pallets_count,
            pallet_weight_kg=Decimal("10.00"),
            delivery_date=delivery_date,
            wb_fbo_supply_id=wb_fbo_supply_id,
        ),
    )


async def _make_pass(
    db_session,
    project_id,
    assembly_request_id,
    *,
    sync_status=WbSupplySyncStatus.PASSED,
    pass_pallets=2,
):
    return await _add(
        db_session,
        AssemblyWbSupply(
            project_id=project_id,
            assembly_request_id=assembly_request_id,
            sync_status=sync_status.value,
            pass_pallets=pass_pallets,
        ),
    )


def _rows(res, assembly_id):
    return [r for r in res["supply_discrepancies"] if r["assembly_id"] == assembly_id]


# ─── Дата сдачи vs бронь WB (окно ±1 дня) ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [-1, 0, 1])
async def test_date_within_window_not_flagged(db_session, project, warehouse, delta):
    """Сдать можно за сутки до и сутки после брони — расхождением НЕ считается."""
    supply = await _make_supply(db_session, project.id, planned_date=date(2026, 7, 15))
    from datetime import timedelta

    doc = await _make_assembly(
        db_session,
        project.id,
        warehouse.id,
        delivery_date=date(2026, 7, 15) + timedelta(days=delta),
        wb_fbo_supply_id=supply.id,
    )
    await _make_pass(db_session, project.id, doc.id, pass_pallets=doc.pallets_count)
    res = await _raw(db_session, project.id)
    assert _rows(res, doc.id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("delta", [2, -2, 5])
async def test_date_outside_window_flagged(db_session, project, warehouse, delta):
    """Разница > 1 дня → date_mismatch."""
    from datetime import timedelta

    supply = await _make_supply(db_session, project.id, planned_date=date(2026, 7, 15))
    doc = await _make_assembly(
        db_session,
        project.id,
        warehouse.id,
        delivery_date=date(2026, 7, 15) + timedelta(days=delta),
        wb_fbo_supply_id=supply.id,
    )
    await _make_pass(db_session, project.id, doc.id, pass_pallets=doc.pallets_count)
    res = await _raw(db_session, project.id)
    rows = _rows(res, doc.id)
    assert len(rows) == 1
    assert rows[0]["date_mismatch"] is True
    assert rows[0]["date_diff_days"] == delta
    assert rows[0]["pallet_mismatch"] is False
    assert rows[0]["pass_missing"] is False


# ─── Паллеты ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pallet_mismatch_flagged(db_session, project, warehouse):
    supply = await _make_supply(db_session, project.id, planned_date=date(2026, 7, 15))
    doc = await _make_assembly(
        db_session, project.id, warehouse.id, pallets_count=3, delivery_date=date(2026, 7, 15), wb_fbo_supply_id=supply.id
    )
    await _make_pass(db_session, project.id, doc.id, pass_pallets=5)  # 5 ≠ 3
    res = await _raw(db_session, project.id)
    rows = _rows(res, doc.id)
    assert len(rows) == 1
    assert rows[0]["pallet_mismatch"] is True
    assert rows[0]["pallets_count"] == 3
    assert rows[0]["pass_pallets"] == 5
    assert rows[0]["date_mismatch"] is False
    assert rows[0]["pass_missing"] is False


# ─── Пропуск не оформлен ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pass_missing_no_replay(db_session, project, warehouse):
    """Машина назначена, но AssemblyWbSupply нет → pass_missing."""
    supply = await _make_supply(db_session, project.id, planned_date=date(2026, 7, 15))
    doc = await _make_assembly(
        db_session, project.id, warehouse.id, delivery_date=date(2026, 7, 15), wb_fbo_supply_id=supply.id
    )
    res = await _raw(db_session, project.id)
    rows = _rows(res, doc.id)
    assert len(rows) == 1
    assert rows[0]["pass_missing"] is True
    assert rows[0]["pass_pallets"] is None
    assert rows[0]["sync_status"] is None


@pytest.mark.asyncio
async def test_pass_not_passed_stage_flagged(db_session, project, warehouse):
    """Реплей есть, но не дошёл до PASSED → pass_missing."""
    supply = await _make_supply(db_session, project.id, planned_date=date(2026, 7, 15))
    doc = await _make_assembly(
        db_session, project.id, warehouse.id, delivery_date=date(2026, 7, 15), wb_fbo_supply_id=supply.id
    )
    await _make_pass(db_session, project.id, doc.id, sync_status=WbSupplySyncStatus.BOXED, pass_pallets=None)
    res = await _raw(db_session, project.id)
    rows = _rows(res, doc.id)
    assert len(rows) == 1
    assert rows[0]["pass_missing"] is True


# ─── Чистая строка / исключения ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clean_row_not_flagged(db_session, project, warehouse):
    """Дата в окне, паллеты сходятся, пропуск оформлен → строки нет."""
    supply = await _make_supply(db_session, project.id, planned_date=date(2026, 7, 15))
    doc = await _make_assembly(
        db_session, project.id, warehouse.id, pallets_count=4, delivery_date=date(2026, 7, 15), wb_fbo_supply_id=supply.id
    )
    await _make_pass(db_session, project.id, doc.id, pass_pallets=4)
    res = await _raw(db_session, project.id)
    assert _rows(res, doc.id) == []


@pytest.mark.asyncio
async def test_accepted_supply_excluded(db_session, project, warehouse):
    """Закрытая (ACCEPTED) WB-поставка исключается даже при расхождении."""
    supply = await _make_supply(
        db_session, project.id, wb_status=WbSupplyStatus.ACCEPTED, planned_date=date(2026, 7, 15)
    )
    doc = await _make_assembly(
        db_session,
        project.id,
        warehouse.id,
        status=AssemblyStatus.SHIPPED,
        delivery_date=date(2026, 7, 20),  # +5 дней
        wb_fbo_supply_id=supply.id,
    )
    await _make_pass(db_session, project.id, doc.id, pass_pallets=doc.pallets_count)
    res = await _raw(db_session, project.id)
    assert _rows(res, doc.id) == []


@pytest.mark.asyncio
async def test_out_of_scope_status_excluded(db_session, project, warehouse):
    """READY/IN_PROGRESS (машина не назначена) в блок не попадают."""
    supply = await _make_supply(db_session, project.id, planned_date=date(2026, 7, 15))
    doc = await _make_assembly(
        db_session,
        project.id,
        warehouse.id,
        status=AssemblyStatus.READY,
        delivery_date=date(2026, 7, 25),  # заведомо расходится
        wb_fbo_supply_id=supply.id,
    )
    res = await _raw(db_session, project.id)
    assert _rows(res, doc.id) == []


@pytest.mark.asyncio
async def test_shipped_status_in_scope(db_session, project, warehouse):
    """SHIPPED (в пути) — в scope блока."""
    supply = await _make_supply(db_session, project.id, planned_date=date(2026, 7, 15))
    doc = await _make_assembly(
        db_session,
        project.id,
        warehouse.id,
        status=AssemblyStatus.SHIPPED,
        delivery_date=date(2026, 7, 15),
        wb_fbo_supply_id=supply.id,
    )
    # пропуск не оформлен → строка есть
    res = await _raw(db_session, project.id)
    assert len(_rows(res, doc.id)) == 1


# ─── Фильтры / изоляция ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_warehouse_ids_filter(db_session, project, warehouse):
    supply = await _make_supply(db_session, project.id, planned_date=date(2026, 7, 15))
    doc = await _make_assembly(
        db_session, project.id, warehouse.id, delivery_date=date(2026, 7, 20), wb_fbo_supply_id=supply.id
    )
    await _make_pass(db_session, project.id, doc.id, pass_pallets=doc.pallets_count)
    res_other = await _raw(db_session, project.id, warehouse_ids=[warehouse.id + 99999])
    assert res_other["supply_discrepancies"] == []
    res_self = await _raw(db_session, project.id, warehouse_ids=[warehouse.id])
    assert len(_rows(res_self, doc.id)) == 1


@pytest.mark.asyncio
async def test_project_isolation(db_session, project, other_project):
    other_wh = await _add(
        db_session,
        Warehouse(project_id=other_project.id, name=f"OTH-{_uid()}", warehouse_type="FULFILLMENT"),
    )
    supply = await _make_supply(db_session, other_project.id, planned_date=date(2026, 7, 15))
    await _make_assembly(
        db_session, other_project.id, other_wh.id, delivery_date=date(2026, 7, 25), wb_fbo_supply_id=supply.id
    )
    res = await _raw(db_session, project.id)
    assert res["supply_discrepancies"] == []


@pytest.mark.asyncio
async def test_source_and_wb_warehouse_names(db_session, project, warehouse):
    """Отдельно: склад-источник (наш ФФ) и склад ВБ (город сдачи)."""
    supply = await _make_supply(db_session, project.id, planned_date=date(2026, 7, 15), warehouse_name="Коледино")
    doc = await _make_assembly(
        db_session, project.id, warehouse.id, delivery_date=date(2026, 7, 15), wb_fbo_supply_id=supply.id
    )
    res = await _raw(db_session, project.id)  # pass_missing → строка есть
    rows = _rows(res, doc.id)
    assert len(rows) == 1
    assert rows[0]["source_warehouse_name"] == warehouse.name  # откуда забрали (наш склад)
    assert rows[0]["warehouse_name"] == "Коледино"  # склад ВБ
    assert rows[0]["wb_supply_id"] == supply.wb_supply_id
    assert rows[0]["wb_status"] == WbSupplyStatus.ON_DELIVERY.value


@pytest.mark.asyncio
async def test_wb_warehouse_none_when_no_wb_name(db_session, project, warehouse):
    """Склад ВБ = None, если у поставки нет имени; источник всё равно заполнен."""
    supply = await _make_supply(db_session, project.id, planned_date=date(2026, 7, 15), warehouse_name=None)
    doc = await _make_assembly(
        db_session, project.id, warehouse.id, delivery_date=date(2026, 7, 15), wb_fbo_supply_id=supply.id
    )
    res = await _raw(db_session, project.id)
    rows = _rows(res, doc.id)
    assert len(rows) == 1
    assert rows[0]["warehouse_name"] is None
    assert rows[0]["source_warehouse_name"] == warehouse.name
