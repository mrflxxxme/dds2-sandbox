# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты серверного гейта ёмкости ФФ в update_draft (`_clamp_to_ff_capacity`).

Прод-кейс «храповик» (2026-07-22): SKU с available=0 выпадает из articles
расчёта на фронте → calc не владеет nm → его rows/prebook консервируются
навечно, а транзит-гейт (`_subtract_in_transit`) режет только ПРИРОСТ.
Экземпляры: divan_darkblue — сток Газпром 8 = заявка ASM-858 на 8, черновик
держал ещё 8; швабра EP-800 — 402 обещанных из 282 физических.

Гейт клампит план per (barcode, ФФ) к физике:
    cap = max(0, сток − в_сборке − резерв_других_черновиков)
БЕЗ baseline — применяется и к уже сохранённому плану (в этом весь смысл).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.models import Nomenclature, Warehouse
from backend.models.assembly import (
    AssemblyDraftEvent,
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
)
from backend.models.warehouse import WarehouseStock
from backend.schemas.assembly_draft import (
    AssemblyDraftCreate,
    AssemblyDraftDistribution,
    AssemblyDraftRow,
    AssemblyDraftUpdate,
)
from backend.services import assembly_draft_service


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
            barcode=f"20597579{_uid()[:5]}",
            article_seller=f"диван-{_uid()[:4]}",
            article_wb=nm_id,
        ),
    )


async def _stock(db_session, project_id: int, warehouse_id: int, nom: Nomenclature, qty: int) -> WarehouseStock:
    return await _add(
        db_session,
        WarehouseStock(
            project_id=project_id,
            warehouse_id=warehouse_id,
            nomenclature_id=nom.id,
            barcode=nom.barcode,
            quantity=qty,
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
            number=f"C-{_uid()[:6]}",
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


def _row(nom: Nomenclature, nm_id: int, ff_id: int, wb: str, qty: int, *, as_is: bool = False, pkg: str = "BOX") -> AssemblyDraftRow:
    return AssemblyDraftRow(
        nm_id=nm_id,
        barcode=nom.barcode,
        vendor_code="x",
        src={str(ff_id): qty},
        tgt={wb: qty},
        package_type=pkg,  # type: ignore[arg-type]
        as_is=as_is,
    )


async def _put(db_session, project_id: int, draft_id: int, dist: AssemblyDraftDistribution) -> AssemblyDraftDistribution:
    updated = await assembly_draft_service.update_draft(
        db_session, project_id, draft_id, AssemblyDraftUpdate(distribution=dist)
    )
    return AssemblyDraftDistribution.model_validate(updated.distribution)


async def _trim_events(db_session, project_id: int, draft_id: int) -> list[AssemblyDraftEvent]:
    result = await db_session.execute(
        select(AssemblyDraftEvent)
        .where(
            AssemblyDraftEvent.project_id == project_id,
            AssemblyDraftEvent.draft_id == draft_id,
            AssemblyDraftEvent.event_type == "CAPACITY_TRIM",
        )
        .limit(50)
    )
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_capacity_gate_cuts_plan_fully_reserved_by_assembly(db_session, project, ff_warehouse):
    """Прод-кейс divan_darkblue: сток 8, активная заявка 8 → весь сток уже в
    сборке → PUT плана 8 (rows 5 + prebook 3) срезается в 0. WB-склады заявки
    и плана РАЗНЫЕ — транзит-гейт тут ни при чём, режет именно гейт ёмкости."""
    nm_id = 898_000_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    await _stock(db_session, project.id, ff_warehouse.id, nom, 8)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.PENDING, wb_name="Казань", qty=8)

    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Храповик", distribution=AssemblyDraftDistribution())
    )
    dist = await _put(
        db_session,
        project.id,
        draft.id,
        AssemblyDraftDistribution(
            source_warehouse_ids=[ff_warehouse.id],
            target_warehouse_names=["Электросталь"],
            rows=[_row(nom, nm_id, ff_warehouse.id, "Электросталь", 5)],
            prebook=[_row(nom, nm_id, ff_warehouse.id, "Электросталь", 3)],
        ),
    )
    assert dist.rows == []
    assert dist.prebook == []


@pytest.mark.asyncio
async def test_capacity_gate_respects_other_draft_reserve(db_session, project, ff_warehouse):
    """Прод-кейс швабры EP-800: сток 282, другой черновик уже резервирует все
    282 → план текущего черновика срезается в 0 (кросс-черновичный дубль)."""
    nm_id = 898_100_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    await _stock(db_session, project.id, ff_warehouse.id, nom, 282)

    await assembly_draft_service.create_draft(
        db_session,
        project.id,
        AssemblyDraftCreate(
            name="Скоупнутый (владеет стоком)",
            distribution=AssemblyDraftDistribution(
                source_warehouse_ids=[ff_warehouse.id],
                target_warehouse_names=["Казань"],
                rows=[_row(nom, nm_id, ff_warehouse.id, "Казань", 282)],
            ),
        ),
    )
    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Основной", distribution=AssemblyDraftDistribution())
    )
    dist = await _put(
        db_session,
        project.id,
        draft.id,
        AssemblyDraftDistribution(
            source_warehouse_ids=[ff_warehouse.id],
            target_warehouse_names=["Электросталь"],
            rows=[_row(nom, nm_id, ff_warehouse.id, "Электросталь", 282)],
        ),
    )
    assert dist.rows == []
    assert dist.prebook == []


@pytest.mark.asyncio
async def test_capacity_gate_partial_cut_prebook_before_rows(db_session, project, ff_warehouse):
    """Частичный срез: сток 10, заявка 8 → cap 2. План prebook 3 + rows 5 = 8,
    излишек 6: сначала под нож предбронь (3, целиком), потом rows (5 → 2).
    Остаток 2 урезанного rows-направления — уже НЕ целая паллета → демотируется
    в предбронь (канон «rows = целые паллеты»)."""
    nm_id = 898_200_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    await _stock(db_session, project.id, ff_warehouse.id, nom, 10)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.IN_PROGRESS, wb_name="Казань", qty=8)

    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Частичный", distribution=AssemblyDraftDistribution())
    )
    dist = await _put(
        db_session,
        project.id,
        draft.id,
        AssemblyDraftDistribution(
            source_warehouse_ids=[ff_warehouse.id],
            target_warehouse_names=["Электросталь"],
            rows=[_row(nom, nm_id, ff_warehouse.id, "Электросталь", 5)],
            prebook=[_row(nom, nm_id, ff_warehouse.id, "Электросталь", 3)],
        ),
    )
    assert dist.rows == []
    assert len(dist.prebook) == 1
    assert dist.prebook[0].tgt == {"Электросталь": 2}
    assert dist.prebook[0].src == {str(ff_warehouse.id): 2}


@pytest.mark.asyncio
async def test_capacity_gate_cuts_as_is_last(db_session, project, ff_warehouse):
    """as_is («Оставить так») — под нож ПОСЛЕДНИМ: cap 2, rows авто 4 + as_is 4
    → авто-строка срезается целиком, as_is доживает с 2."""
    nm_id = 898_300_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    await _stock(db_session, project.id, ff_warehouse.id, nom, 10)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.READY, wb_name="Казань", qty=8)

    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="as_is", distribution=AssemblyDraftDistribution())
    )
    dist = await _put(
        db_session,
        project.id,
        draft.id,
        AssemblyDraftDistribution(
            source_warehouse_ids=[ff_warehouse.id],
            target_warehouse_names=["Электросталь"],
            rows=[
                _row(nom, nm_id, ff_warehouse.id, "Электросталь", 4),
                _row(nom, nm_id, ff_warehouse.id, "Электросталь", 4, as_is=True),
            ],
        ),
    )
    assert len(dist.rows) == 1
    assert dist.rows[0].as_is is True
    assert dist.rows[0].tgt == {"Электросталь": 2}
    assert dist.rows[0].src == {str(ff_warehouse.id): 2}


@pytest.mark.asyncio
async def test_capacity_gate_cuts_manual_too(db_session, project, ff_warehouse):
    """✋ manual_nms НЕ спасает от физики (в отличие от транзит-гейта): сток 8,
    заявка 8 → cap 0 → ручной план 8 срезается в 0. Фантом с ✋ — всё равно
    фантом: товара физически нет."""
    nm_id = 898_400_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    await _stock(db_session, project.id, ff_warehouse.id, nom, 8)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.VEHICLE_ASSIGNED, wb_name="Казань", qty=8)

    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Ручной фантом", distribution=AssemblyDraftDistribution())
    )
    dist = await _put(
        db_session,
        project.id,
        draft.id,
        AssemblyDraftDistribution(
            source_warehouse_ids=[ff_warehouse.id],
            target_warehouse_names=["Электросталь"],
            rows=[_row(nom, nm_id, ff_warehouse.id, "Электросталь", 8)],
            manual_nms=[nm_id],
        ),
    )
    assert dist.rows == []


@pytest.mark.asyncio
async def test_capacity_gate_fail_open_on_fetch_error(db_session, project, ff_warehouse, monkeypatch):
    """Сбой выборки стока → гейт пропущен (fail-open), план сохраняется
    нетронутым — как транзит-гейт: автосейв важнее гейта."""
    nm_id = 898_500_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    # Стока НЕТ вовсе: живой гейт срезал бы план в 0.

    async def _boom(*args, **kwargs):
        raise RuntimeError("stock fetch failed")

    monkeypatch.setattr(assembly_draft_service, "_fetch_ff_stock_by_barcode", _boom)

    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Fail-open", distribution=AssemblyDraftDistribution())
    )
    dist = await _put(
        db_session,
        project.id,
        draft.id,
        AssemblyDraftDistribution(
            source_warehouse_ids=[ff_warehouse.id],
            target_warehouse_names=["Электросталь"],
            rows=[_row(nom, nm_id, ff_warehouse.id, "Электросталь", 8)],
        ),
    )
    assert len(dist.rows) == 1
    assert dist.rows[0].tgt == {"Электросталь": 8}


@pytest.mark.asyncio
async def test_capacity_gate_cuts_baseline_phantom_without_grandfathering(db_session, project, ff_warehouse):
    """УЖЕ СОХРАНЁННЫЙ фантом срезается при любом следующем PUT: черновик в БД
    держит план 8, PUT приносит ТОТ ЖЕ план (прирост 0 → транзит-гейт молчит),
    но сток 8 целиком в активной заявке → гейт ёмкости режет в 0. Никакого
    baseline/grandfathering — в этом весь смысл."""
    nm_id = 898_600_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    await _stock(db_session, project.id, ff_warehouse.id, nom, 8)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.IN_PROGRESS, wb_name="Казань", qty=8)

    def _dist() -> AssemblyDraftDistribution:
        return AssemblyDraftDistribution(
            source_warehouse_ids=[ff_warehouse.id],
            target_warehouse_names=["Электросталь"],
            rows=[_row(nom, nm_id, ff_warehouse.id, "Электросталь", 8)],
        )

    # Фантом уже в БД (create_draft гейт не гоняет — легаси-план).
    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Легаси-фантом", distribution=_dist())
    )
    dist = await _put(db_session, project.id, draft.id, _dist())
    assert dist.rows == []
    assert dist.prebook == []


@pytest.mark.asyncio
async def test_capacity_gate_neutral_when_stock_sufficient(db_session, project, ff_warehouse):
    """Стока хватает (cap ≥ план) → гейт нейтрален, план сохраняется как есть:
    сток 20, заявка 8, резерв другого черновика 5 → cap 7, план 7 не тронут."""
    nm_id = 898_700_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    await _stock(db_session, project.id, ff_warehouse.id, nom, 20)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.PENDING, wb_name="Казань", qty=8)
    await assembly_draft_service.create_draft(
        db_session,
        project.id,
        AssemblyDraftCreate(
            name="Сосед",
            distribution=AssemblyDraftDistribution(
                source_warehouse_ids=[ff_warehouse.id],
                target_warehouse_names=["Казань"],
                rows=[_row(nom, nm_id, ff_warehouse.id, "Казань", 5)],
            ),
        ),
    )

    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Нейтральный", distribution=AssemblyDraftDistribution())
    )
    dist = await _put(
        db_session,
        project.id,
        draft.id,
        AssemblyDraftDistribution(
            source_warehouse_ids=[ff_warehouse.id],
            target_warehouse_names=["Электросталь"],
            rows=[_row(nom, nm_id, ff_warehouse.id, "Электросталь", 7)],
        ),
    )
    assert len(dist.rows) == 1
    assert dist.rows[0].tgt == {"Электросталь": 7}
    assert dist.rows[0].src == {str(ff_warehouse.id): 7}
    # Среза не было → события CAPACITY_TRIM в истории НЕТ.
    assert await _trim_events(db_session, project.id, draft.id) == []


@pytest.mark.asyncio
async def test_capacity_trim_event_logged_on_cut(db_session, project, ff_warehouse):
    """Аудит молчаливого среза: гейт реально урезал план → в истории черновика
    событие CAPACITY_TRIM с Σ среза, разбивкой по артикулам и снапшотом ДО среза
    (before_distribution несёт полный несрезанный план)."""
    nm_id = 898_800_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    await _stock(db_session, project.id, ff_warehouse.id, nom, 10)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.IN_PROGRESS, wb_name="Казань", qty=8)

    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Аудит среза", distribution=AssemblyDraftDistribution())
    )
    await _put(
        db_session,
        project.id,
        draft.id,
        AssemblyDraftDistribution(
            source_warehouse_ids=[ff_warehouse.id],
            target_warehouse_names=["Электросталь"],
            rows=[_row(nom, nm_id, ff_warehouse.id, "Электросталь", 5)],
            prebook=[_row(nom, nm_id, ff_warehouse.id, "Электросталь", 3)],
        ),
    )

    events = await _trim_events(db_session, project.id, draft.id)
    assert len(events) == 1
    ev = events[0]
    assert "⚖ Ёмкость ФФ" in (ev.summary or "")
    assert "срезано 6 шт" in (ev.summary or "")
    assert "x ×6" in (ev.summary or "")  # vendor_code строки-хелпера = "x"
    # Снапшот ДО среза: полный входящий план 8 шт (rows 5 + prebook 3).
    before = ev.before_distribution or {}
    total_before = sum(
        sum((r.get("src") or {}).values())
        for r in list(before.get("rows") or []) + list(before.get("prebook") or [])
    )
    assert total_before == 8
    # Токен версии выставлен — событие откатываемо (снапшотный revert-гейт).
    assert ev.draft_updated_at_after is not None


@pytest.mark.asyncio
async def test_capacity_trim_demotes_cut_rows_direction_to_prebook(db_session, project, ff_warehouse):
    """Канон целых паллет: срез rows-направления оставляет НЕ целую паллету →
    остаток направления демотируется в предбронь (как после транзит-гейта),
    в rows частичная паллета не живёт."""
    nm_id = 898_900_000 + int(_uid()[:4], 16)
    nom = await _nom(db_session, project.id, nm_id)
    await _stock(db_session, project.id, ff_warehouse.id, nom, 10)
    await _make_request(db_session, project.id, ff_warehouse.id, nom, status=AssemblyStatus.PENDING, wb_name="Казань", qty=8)

    draft = await assembly_draft_service.create_draft(
        db_session, project.id, AssemblyDraftCreate(name="Демоция", distribution=AssemblyDraftDistribution())
    )
    dist = await _put(
        db_session,
        project.id,
        draft.id,
        AssemblyDraftDistribution(
            source_warehouse_ids=[ff_warehouse.id],
            target_warehouse_names=["Электросталь"],
            rows=[_row(nom, nm_id, ff_warehouse.id, "Электросталь", 5)],
        ),
    )
    # cap 2: rows 5 → 2, но 2 — не целая паллета → уезжает в prebook.
    assert dist.rows == []
    assert len(dist.prebook) == 1
    assert dist.prebook[0].barcode == nom.barcode
    assert dist.prebook[0].tgt == {"Электросталь": 2}
    assert dist.prebook[0].src == {str(ff_warehouse.id): 2}


@pytest.mark.asyncio
async def test_capacity_gate_fast_path_empty_plan_zero_queries(project):
    """Fast-path: пустой planned (нет rows/prebook src) → гейт выходит ДО
    единого запроса к БД. db=None упал бы AttributeError на первом же
    обращении к сессии — сам проход теста доказывает отсутствие запросов."""
    total, by_article = await assembly_draft_service._clamp_to_ff_capacity(
        None,  # type: ignore[arg-type]
        project.id,
        999,
        AssemblyDraftDistribution(),
    )
    assert (total, by_article) == (0, {})

    # Строки есть, но src пустые/нулевые — тоже fast-path (planned пуст).
    dist = AssemblyDraftDistribution(
        rows=[AssemblyDraftRow(nm_id=1, barcode="2000000000001", vendor_code="x", src={"5": 0}, tgt={})],
        prebook=[AssemblyDraftRow(nm_id=2, barcode="", vendor_code="y", src={"5": 3}, tgt={})],
    )
    total, by_article = await assembly_draft_service._clamp_to_ff_capacity(
        None,  # type: ignore[arg-type]
        project.id,
        999,
        dist,
    )
    assert (total, by_article) == (0, {})
