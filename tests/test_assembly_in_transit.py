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


@pytest.mark.asyncio
async def test_update_draft_gate_subtracts_in_transit(db_session, project, ff_warehouse):
    """Server-side гейт PUT: stale-вкладка не может записать план, дублирующий
    уже едущее (прод-кейс: 7 PUT за 20с возвращали дубль после клиентской очистки)."""
    from backend.schemas.assembly_draft import (
        AssemblyDraftCreate,
        AssemblyDraftDistribution,
        AssemblyDraftRow,
        AssemblyDraftUpdate,
    )
    from backend.services import assembly_draft_service

    nm_id = 896_300_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    # Уже едет: 30 на Самару (имя заявки — «Новосемейкино»), 24 на Сарапул (резерв машины).
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.IN_PROGRESS, wb_name="Новосемейкино", qty=30)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.PRE_DISTRIBUTED, wb_name="Сарапул", qty=24)

    draft = await assembly_draft_service.create_draft(
        db_session,
        project.id,
        AssemblyDraftCreate(name="Гейт", distribution=AssemblyDraftDistribution()),
    )

    # Stale-вкладка присылает план с дублем: Самара 32 (едет 30), Сарапул 12 (едет 24), ЕКБ 52.
    stale = AssemblyDraftDistribution(
        source_warehouse_ids=[ff_warehouse.id],
        target_warehouse_names=["Самара (Новосемейкино)", "Сарапул", "Екатеринбург - Перспективная 14"],
        rows=[
            AssemblyDraftRow(
                nm_id=nm_id,
                barcode=nom.barcode,
                vendor_code="швабра",
                src={str(ff_warehouse.id): 96},
                tgt={"Самара (Новосемейкино)": 32, "Сарапул": 12, "Екатеринбург - Перспективная 14": 52},
            )
        ],
    )
    updated = await assembly_draft_service.update_draft(
        db_session, project.id, draft.id, AssemblyDraftUpdate(distribution=stale)
    )

    from backend.schemas.assembly_draft import AssemblyDraftDistribution as Dist

    dist = Dist.model_validate(updated.distribution)
    assert len(dist.rows) == 1
    row = dist.rows[0]
    # Самара 32−30=2, Сарапул 12−24→0 (ключ выпал), ЕКБ не тронут.
    assert row.tgt == {"Самара (Новосемейкино)": 2, "Екатеринбург - Перспективная 14": 52}
    # src ужат до Σtgt (carve): 96 → 54.
    assert sum(row.src.values()) == 54


@pytest.mark.asyncio
async def test_update_draft_gate_no_transit_untouched(db_session, project, ff_warehouse):
    """Без активных заявок PUT сохраняет план как есть (гейт нейтрален)."""
    from backend.schemas.assembly_draft import (
        AssemblyDraftCreate,
        AssemblyDraftDistribution,
        AssemblyDraftRow,
        AssemblyDraftUpdate,
    )
    from backend.services import assembly_draft_service

    nm_id = 896_400_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Чисто", distribution=AssemblyDraftDistribution())
    )
    dist_in = AssemblyDraftDistribution(
        source_warehouse_ids=[ff_warehouse.id],
        target_warehouse_names=["Казань"],
        rows=[
            AssemblyDraftRow(nm_id=nm_id, barcode=nom.barcode, vendor_code="x", src={str(ff_warehouse.id): 10}, tgt={"Казань": 10})
        ],
    )
    updated = await assembly_draft_service.update_draft(
        db_session, project.id, draft.id, AssemblyDraftUpdate(distribution=dist_in)
    )
    from backend.schemas.assembly_draft import AssemblyDraftDistribution as Dist

    dist = Dist.model_validate(updated.distribution)
    assert dist.rows[0].tgt == {"Казань": 10}
    assert dist.rows[0].src == {str(ff_warehouse.id): 10}
