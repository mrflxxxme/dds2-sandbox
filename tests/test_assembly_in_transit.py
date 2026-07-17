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
    """Server-side ДЕЛЬТА-гейт PUT, сценарий stale-возврата: черновик в БД
    очищен (baseline пустой), stale-вкладка PUT'ит старый план — весь он прирост
    → транзит вычитается полностью (прод-кейс: 7 PUT за 20с возвращали дубль
    после клиентской очистки)."""
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
    # Сарапул 12−24→0 (ключ выпал); ЕКБ не тронут — остаётся строкой.
    # Самара 32−30=2: хвост урезанного гейтом направления не может оставаться
    # строкой (целость паллеты потеряна) → уезжает в предбронь.
    assert row.tgt == {"Екатеринбург - Перспективная 14": 52}
    assert sum(row.src.values()) == 52
    assert len(dist.prebook) == 1
    assert dist.prebook[0].tgt == {"Самара (Новосемейкино)": 2}
    assert sum(dist.prebook[0].src.values()) == 2


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


@pytest.mark.asyncio
async def test_gate_idempotent_on_resave(db_session, project, ff_warehouse):
    """Идемпотентность дельта-гейта: PUT того же плана (автосейв матрицы на
    каждый клик) НИЧЕГО не вычитает — прирост относительно baseline равен 0.
    До дельта-гейта каждый PUT повторно урезал уже чистый план на транзит,
    и черновик «таял». Baseline считается по rows И prebook вместе."""
    from backend.schemas.assembly_draft import (
        AssemblyDraftCreate,
        AssemblyDraftDistribution,
        AssemblyDraftRow,
        AssemblyDraftUpdate,
    )
    from backend.services import assembly_draft_service

    nm_id = 896_500_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    # Едет 100 на Казань — больше, чем весь план черновика.
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.IN_PROGRESS, wb_name="Казань", qty=100)

    def _dist() -> AssemblyDraftDistribution:
        return AssemblyDraftDistribution(
            source_warehouse_ids=[ff_warehouse.id],
            target_warehouse_names=["Казань"],
            rows=[
                AssemblyDraftRow(nm_id=nm_id, barcode=nom.barcode, vendor_code="x", src={str(ff_warehouse.id): 30}, tgt={"Казань": 30})
            ],
            prebook=[
                AssemblyDraftRow(nm_id=nm_id, barcode=nom.barcode, vendor_code="x", src={str(ff_warehouse.id): 20}, tgt={"Казань": 20})
            ],
        )

    # Уже сохранённый ЧИСТЫЙ план (create_draft гейт не гоняет — как план,
    # прошедший гейт при прошлой записи).
    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Идемпотентность", distribution=_dist())
    )

    from backend.schemas.assembly_draft import AssemblyDraftDistribution as Dist

    # Два повторных автосейва того же плана — план не тает.
    for _ in range(2):
        updated = await assembly_draft_service.update_draft(
            db_session, project.id, draft.id, AssemblyDraftUpdate(distribution=_dist())
        )
        dist = Dist.model_validate(updated.distribution)
        assert len(dist.rows) == 1 and dist.rows[0].tgt == {"Казань": 30}
        assert len(dist.prebook) == 1 and dist.prebook[0].tgt == {"Казань": 20}


@pytest.mark.asyncio
async def test_gate_subtracts_only_increase(db_session, project, ff_warehouse):
    """Дельта-гейт вычитает транзит только из ПРИРОСТА: в БД план 50 по
    (nm, Казань) с транзитом 100, PUT приносит 110 → прирост 60, вычет
    min(100, 60)=60 → сохраняется 50 (а не 10, как при вычете из всего плана)."""
    from backend.schemas.assembly_draft import (
        AssemblyDraftCreate,
        AssemblyDraftDistribution,
        AssemblyDraftRow,
        AssemblyDraftUpdate,
    )
    from backend.services import assembly_draft_service

    nm_id = 896_600_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.IN_PROGRESS, wb_name="Казань", qty=100)

    draft = await assembly_draft_service.create_draft(
        db_session,
        project.id,
        AssemblyDraftCreate(
            name="Прирост",
            distribution=AssemblyDraftDistribution(
                source_warehouse_ids=[ff_warehouse.id],
                target_warehouse_names=["Казань"],
                rows=[
                    AssemblyDraftRow(nm_id=nm_id, barcode=nom.barcode, vendor_code="x", src={str(ff_warehouse.id): 50}, tgt={"Казань": 50})
                ],
            ),
        ),
    )

    incoming = AssemblyDraftDistribution(
        source_warehouse_ids=[ff_warehouse.id],
        target_warehouse_names=["Казань"],
        rows=[
            AssemblyDraftRow(nm_id=nm_id, barcode=nom.barcode, vendor_code="x", src={str(ff_warehouse.id): 110}, tgt={"Казань": 110})
        ],
    )
    updated = await assembly_draft_service.update_draft(
        db_session, project.id, draft.id, AssemblyDraftUpdate(distribution=incoming)
    )

    from backend.schemas.assembly_draft import AssemblyDraftDistribution as Dist

    dist = Dist.model_validate(updated.distribution)
    # Направление урезано гейтом (110 → 50) → остаток больше не гарантированно
    # целая паллета → уезжает в предбронь (Σ плана сохраняется: 50).
    assert dist.rows == []
    assert len(dist.prebook) == 1
    assert dist.prebook[0].tgt == {"Казань": 50}
    # src ужат до Σtgt (carve): 110 → 50.
    assert sum(dist.prebook[0].src.values()) == 50


@pytest.mark.asyncio
async def test_gate_growth_without_transit_untouched(db_session, project, ff_warehouse):
    """Прирост на складе БЕЗ транзита не вычитается: транзит только по Казани,
    PUT добавляет 30 на ЕКБ — ЕКБ сохраняется как есть, Казань без прироста
    не трогается."""
    from backend.schemas.assembly_draft import (
        AssemblyDraftCreate,
        AssemblyDraftDistribution,
        AssemblyDraftRow,
        AssemblyDraftUpdate,
    )
    from backend.services import assembly_draft_service

    nm_id = 896_700_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.IN_PROGRESS, wb_name="Казань", qty=100)

    draft = await assembly_draft_service.create_draft(
        db_session,
        project.id,
        AssemblyDraftCreate(
            name="Без транзита",
            distribution=AssemblyDraftDistribution(
                source_warehouse_ids=[ff_warehouse.id],
                target_warehouse_names=["Казань"],
                rows=[
                    AssemblyDraftRow(nm_id=nm_id, barcode=nom.barcode, vendor_code="x", src={str(ff_warehouse.id): 50}, tgt={"Казань": 50})
                ],
            ),
        ),
    )

    incoming = AssemblyDraftDistribution(
        source_warehouse_ids=[ff_warehouse.id],
        target_warehouse_names=["Казань", "Екатеринбург - Перспективная 14"],
        rows=[
            AssemblyDraftRow(
                nm_id=nm_id,
                barcode=nom.barcode,
                vendor_code="x",
                src={str(ff_warehouse.id): 80},
                tgt={"Казань": 50, "Екатеринбург - Перспективная 14": 30},
            )
        ],
    )
    updated = await assembly_draft_service.update_draft(
        db_session, project.id, draft.id, AssemblyDraftUpdate(distribution=incoming)
    )

    from backend.schemas.assembly_draft import AssemblyDraftDistribution as Dist

    dist = Dist.model_validate(updated.distribution)
    assert len(dist.rows) == 1
    assert dist.rows[0].tgt == {"Казань": 50, "Екатеринбург - Перспективная 14": 30}
    assert dist.rows[0].src == {str(ff_warehouse.id): 80}


@pytest.mark.asyncio
async def test_gate_respects_manual_nms(db_session, project, ff_warehouse):
    """✋ Ручной SKU (manual_nms) гейт НЕ режет: человек добирает ПОВЕРХ уже
    едущего осознанно — колонка «В сборке» видна в матрице. Прод-кейс
    2026-07-15: +4 короба 150х200 в Электросталь (транзит есть) молча
    вырезались до нуля → правка «не сохранялась»."""
    from backend.schemas.assembly_draft import (
        AssemblyDraftCreate,
        AssemblyDraftDistribution,
        AssemblyDraftRow,
        AssemblyDraftUpdate,
    )
    from backend.services import assembly_draft_service

    nm_id = 896_800_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    # Уже едет 26 в Электросталь.
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.IN_PROGRESS, wb_name="Электросталь", qty=26)

    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Ручной", distribution=AssemblyDraftDistribution())
    )
    incoming = AssemblyDraftDistribution(
        source_warehouse_ids=[ff_warehouse.id],
        target_warehouse_names=["Электросталь"],
        rows=[
            AssemblyDraftRow(nm_id=nm_id, barcode=nom.barcode, vendor_code="x", src={str(ff_warehouse.id): 52}, tgt={"Электросталь": 52})
        ],
        manual_nms=[nm_id],
    )
    updated = await assembly_draft_service.update_draft(
        db_session, project.id, draft.id, AssemblyDraftUpdate(distribution=incoming)
    )
    from backend.schemas.assembly_draft import AssemblyDraftDistribution as Dist

    dist = Dist.model_validate(updated.distribution)
    assert len(dist.rows) == 1
    assert dist.rows[0].tgt == {"Электросталь": 52}
    assert dist.rows[0].src == {str(ff_warehouse.id): 52}


@pytest.mark.asyncio
async def test_gate_manual_exemption_is_per_nm(db_session, project, ff_warehouse):
    """Изъятие из гейта — точечное, per nm: ручной SKU не режется, соседний
    НЕручной с тем же складом режется как раньше (stale-защита жива)."""
    from backend.schemas.assembly_draft import (
        AssemblyDraftCreate,
        AssemblyDraftDistribution,
        AssemblyDraftRow,
        AssemblyDraftUpdate,
    )
    from backend.services import assembly_draft_service

    nm_manual = 896_900_000 + int(_uid()[:4], 16)
    nm_auto = nm_manual + 1
    nom_m = await _nom(db_session, project.id, nm_manual)
    nom_a = await _nom(db_session, project.id, nm_auto)
    await _make_request(db_session, project.id, ff_warehouse.id, nom_m, status=AssemblyStatus.IN_PROGRESS, wb_name="Электросталь", qty=26)
    await _make_request(db_session, project.id, ff_warehouse.id, nom_a, status=AssemblyStatus.IN_PROGRESS, wb_name="Электросталь", qty=26)

    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Смешанный", distribution=AssemblyDraftDistribution())
    )
    incoming = AssemblyDraftDistribution(
        source_warehouse_ids=[ff_warehouse.id],
        target_warehouse_names=["Электросталь"],
        rows=[
            AssemblyDraftRow(nm_id=nm_manual, barcode=nom_m.barcode, vendor_code="m", src={str(ff_warehouse.id): 52}, tgt={"Электросталь": 52}),
            AssemblyDraftRow(nm_id=nm_auto, barcode=nom_a.barcode, vendor_code="a", src={str(ff_warehouse.id): 52}, tgt={"Электросталь": 52}),
        ],
        manual_nms=[nm_manual],
    )
    updated = await assembly_draft_service.update_draft(
        db_session, project.id, draft.id, AssemblyDraftUpdate(distribution=incoming)
    )
    from backend.schemas.assembly_draft import AssemblyDraftDistribution as Dist

    dist = Dist.model_validate(updated.distribution)
    by_nm = {r.nm_id: r for r in dist.rows}
    # ручной — цел (и от вычета, и от демоции в предбронь)
    assert by_nm[nm_manual].tgt == {"Электросталь": 52}
    # обычный — урезан на транзит 26; хвост урезанного направления → предбронь
    assert nm_auto not in by_nm
    pre_by_nm = {r.nm_id: r for r in dist.prebook}
    assert pre_by_nm[nm_auto].tgt == {"Электросталь": 26}
    assert sum(pre_by_nm[nm_auto].src.values()) == 26


@pytest.mark.asyncio
async def test_gate_demotes_partial_direction_remains_to_prebook(db_session, project, ff_warehouse):
    """Прод-кейс draft 62 (2026-07-17, ЕКБ×BOX): фронт прислал направление ЦЕЛОЙ
    смешанной паллетой (SKU A 52 шт + SKU B 18 шт), транзит покрывал только A →
    гейт вычитал строки A, а остаток направления (B, < паллеты) оставался в rows —
    канон «rows = целые паллеты» ломался (превью честно показывало «⚠ ~50%»).
    Теперь остаток урезанного гейтом направления уезжает в ПРЕДБРОНЬ; чужие
    направления той же строки (Казань) и as_is-строки остаются в rows."""
    from backend.schemas.assembly_draft import (
        AssemblyDraftCreate,
        AssemblyDraftDistribution,
        AssemblyDraftRow,
        AssemblyDraftUpdate,
    )
    from backend.services import assembly_draft_service

    nm_a = 897_000_000 + int(_uid()[:4], 16)
    nm_b = nm_a + 1
    nm_c = nm_a + 2
    nom_a = await _nom(db_session, project.id, nm_a)
    nom_b = await _nom(db_session, project.id, nm_b)
    nom_c = await _nom(db_session, project.id, nm_c)
    # Транзит покрывает ВЕСЬ план SKU A на ЕКБ.
    await _make_request(
        db_session, project.id, ff_warehouse.id, nom_a,
        status=AssemblyStatus.IN_PROGRESS, wb_name="Екатеринбург - Перспективная 14", qty=52,
    )

    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Демоция", distribution=AssemblyDraftDistribution())
    )
    incoming = AssemblyDraftDistribution(
        source_warehouse_ids=[ff_warehouse.id],
        target_warehouse_names=["Екатеринбург - Перспективная 14", "Казань"],
        rows=[
            # A: целиком покрыт транзитом → строка выпадает.
            AssemblyDraftRow(nm_id=nm_a, barcode=nom_a.barcode, vendor_code="a", src={str(ff_warehouse.id): 52}, tgt={"Екатеринбург - Перспективная 14": 52}),
            # B: транзита нет, но направление ЕКБ урезано гейтом (через A) →
            # его ЕКБ-порция больше не гарантированно целая паллета → предбронь.
            AssemblyDraftRow(nm_id=nm_b, barcode=nom_b.barcode, vendor_code="b", src={str(ff_warehouse.id): 38}, tgt={"Екатеринбург - Перспективная 14": 18, "Казань": 20}),
            # C: as_is («Оставить так») — юзер осознанно везёт частичную паллету.
            AssemblyDraftRow(nm_id=nm_c, barcode=nom_c.barcode, vendor_code="c", src={str(ff_warehouse.id): 6}, tgt={"Екатеринбург - Перспективная 14": 6}, as_is=True),
        ],
    )
    updated = await assembly_draft_service.update_draft(
        db_session, project.id, draft.id, AssemblyDraftUpdate(distribution=incoming)
    )

    from backend.schemas.assembly_draft import AssemblyDraftDistribution as Dist

    dist = Dist.model_validate(updated.distribution)
    rows_by_nm = {r.nm_id: r for r in dist.rows}
    # A вычтен целиком (весь план уже едет).
    assert nm_a not in rows_by_nm
    # B: ЕКБ-порция демотирована, Казань осталась строкой (Σsrc == Σtgt).
    assert rows_by_nm[nm_b].tgt == {"Казань": 20}
    assert sum(rows_by_nm[nm_b].src.values()) == 20
    # C: as_is освобождён от паллет-инварианта — остаётся в rows.
    assert rows_by_nm[nm_c].tgt == {"Екатеринбург - Перспективная 14": 6}
    pre_by_nm = {r.nm_id: r for r in dist.prebook}
    assert pre_by_nm[nm_b].tgt == {"Екатеринбург - Перспективная 14": 18}
    assert sum(pre_by_nm[nm_b].src.values()) == 18
