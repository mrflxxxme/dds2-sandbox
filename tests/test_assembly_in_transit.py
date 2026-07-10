# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты fetch_in_transit_by_nm / include_pre_distributed — «один мир» черновика.

Прод-кейс «швабры апл» (2026-07-10): заявки pre-dist (PRE_DISTRIBUTED) и
IN_PROGRESS не видны черновику → он предлагал повторную отправку уже едущего.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio

from backend.models import Nomenclature, Warehouse
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.services.cold_start_distribution_service import (
    fetch_active_assemblies_for_sku,
    fetch_in_transit_by_nm,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _add(db_session, obj):
    db_session.add(obj)
    await db_session.commit()
    await db_session.refresh(obj)
    return obj


@pytest_asyncio.fixture
async def ff_warehouse(db_session, project):
    return await _add(
        db_session,
        Warehouse(project_id=project.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT"),
    )


async def _nom(db_session, project_id: int, nm_id: int) -> Nomenclature:
    return await _add(
        db_session,
        Nomenclature(
            project_id=project_id,
            barcode=f"20497579{_uid()[:5]}",
            article_seller=f"швабра-{_uid()[:4]}",
            article_wb=nm_id,
        ),
    )


async def _make_request(
    db_session,
    project_id: int,
    warehouse_id: int,
    nom: Nomenclature,
    *,
    status: AssemblyStatus,
    wb_name: str,
    qty: int,
) -> AssemblyRequest:
    doc = await _add(
        db_session,
        AssemblyRequest(
            project_id=project_id,
            warehouse_id=warehouse_id,
            number=f"A-{_uid()[:6]}",
            status=status.value,
            wb_warehouse_name_manual=wb_name,
            pallets_count=1,
            pallet_weight_kg=Decimal("10.00"),
        ),
    )
    await _add(
        db_session,
        AssemblyRequestItem(
            project_id=project_id,
            assembly_request_id=doc.id,
            nomenclature_id=nom.id,
            barcode=nom.barcode,
            quantity=qty,
        ),
    )
    return doc


@pytest.mark.asyncio
async def test_in_transit_includes_pd_and_active_canonized(db_session, project, ff_warehouse):
    """PRE_DISTRIBUTED + IN_PROGRESS считаются; DELIVERED/CANCELLED — нет;
    имена складов канонизируются (Новосемейкино → Самара (Новосемейкино))."""
    nm_id = 896_000_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)

    # прод-кейс: едет 30 на Самару (IN_PROGRESS, имя из заявки — «Новосемейкино»)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.IN_PROGRESS, wb_name="Новосемейкино", qty=30)
    # резерв машины: 24 на Сарапул (PRE_DISTRIBUTED)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.PRE_DISTRIBUTED, wb_name="Сарапул", qty=24)
    # уже на WB / отменено — не «едет»
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.DELIVERED, wb_name="Казань", qty=100)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.CANCELLED, wb_name="Казань", qty=50)

    out = await fetch_in_transit_by_nm(db_session, project.id, [nm_id])
    assert out == {nm_id: {"Самара (Новосемейкино)": 30, "Сарапул": 24}}


@pytest.mark.asyncio
async def test_in_transit_project_isolation_and_empty(db_session, project, other_project, ff_warehouse):
    nm_id = 896_100_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.PENDING, wb_name="Казань", qty=7)

    # Чужой проект не видит
    assert await fetch_in_transit_by_nm(db_session, other_project.id, [nm_id]) == {}
    # Пустой список nm — пустой ответ без SQL-ошибок
    assert await fetch_in_transit_by_nm(db_session, project.id, []) == {}
    # Свой — видит
    assert (await fetch_in_transit_by_nm(db_session, project.id, [nm_id]))[nm_id] == {"Казань": 7}


@pytest.mark.asyncio
async def test_active_asm_pd_flag(db_session, project, ff_warehouse):
    """include_pre_distributed: False (дефолт, вычет из свободного ФФ) не видит
    резерв машины; True (per-склад цели) — видит."""
    nm_id = 896_200_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.PRE_DISTRIBUTED, wb_name="Сарапул", qty=24)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.IN_PROGRESS, wb_name="Сарапул", qty=6)

    without_pd = await fetch_active_assemblies_for_sku(db_session, project.id, nom.id)
    with_pd = await fetch_active_assemblies_for_sku(db_session, project.id, nom.id, include_pre_distributed=True)
    assert without_pd == {"Сарапул": 6}
    assert with_pd == {"Сарапул": 30}
