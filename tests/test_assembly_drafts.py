"""
Tests for AssemblyDraft module — service layer + commit lifecycle.

Covers:
1. Create / list / get / update / soft-delete (CRUD happy paths + multi-tenancy)
2. Commit happy path: 1 source x 2 targets -> 2 AssemblyRequests
3. Commit pro-rata: 2 sources x 2 targets -> up to 4 AssemblyRequests
4. Commit balance validation: src sum != tgt sum -> 400
5. Commit atomic: failure during create -> rollback (draft remains, no requests)
6. Commit marks draft as soft-deleted on success
7. Merge: sum / union / project isolation / guards / schema validation
"""

from datetime import datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, text

from backend.models.assembly import AssemblyDraft, AssemblyRequest, AssemblyRequestItem
from backend.schemas.assembly_draft import (
    AssemblyDraftAddRows,
    AssemblyDraftCreate,
    AssemblyDraftDistribution,
    AssemblyDraftMergeRequest,
    AssemblyDraftRow,
    AssemblyDraftUpdate,
    DraftEventLog,
    HandedUnit,
    HandedUnitItem,
)
from backend.services import assembly_draft_service, assembly_service
from backend.services.assembly import draft_history

# PROJECT_ID / OTHER_PROJECT_ID are assigned per-test by the `setup_test_data`
# fixture from conftest's sequence-allocated `project` / `other_project`. Never
# hardcode a project id: a fixed value is a landmine — projects_id_seq on the
# local dev DB eventually climbs to it and auto-id INSERTs collide on projects_pkey.
PROJECT_ID = 0
OTHER_PROJECT_ID = 0
TEST_BARCODE_1 = "TEST_DRAFT_BC_001"
TEST_BARCODE_2 = "TEST_DRAFT_BC_002"


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def setup_test_data(db_session, project, other_project):
    """Allocate fresh projects and seed assembly-draft test fixtures.

    `project` / `other_project` (conftest) have sequence-allocated ids — no
    hardcoded project id to collide with projects_id_seq as it climbs on the
    local dev DB. Each test gets clean projects, so no cross-test cleanup needed.
    """
    global PROJECT_ID, OTHER_PROJECT_ID
    PROJECT_ID = project.id
    OTHER_PROJECT_ID = other_project.id

    # Ensure FULFILLMENT warehouses for PROJECT_ID (we need at least 2 sources for matrix tests)
    for slot in ("Source FF A", "Source FF B"):
        wh_result = await db_session.execute(
            text(
                "SELECT id FROM warehouses WHERE project_id = :pid AND name = :n "
                "AND (is_deleted = false OR is_deleted IS NULL)"
            ),
            {"pid": PROJECT_ID, "n": slot},
        )
        if wh_result.scalar() is None:
            await db_session.execute(
                text(
                    "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, "
                    "is_deleted, created_at, updated_at) "
                    "VALUES (:pid, :n, 'FULFILLMENT', 1, true, false, NOW(), NOW())"
                ),
                {"pid": PROJECT_ID, "n": slot},
            )
    await db_session.commit()

    # Ensure nomenclature for both barcodes
    for barcode in (TEST_BARCODE_1, TEST_BARCODE_2):
        nom_result = await db_session.execute(
            text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
            {"pid": PROJECT_ID, "bc": barcode},
        )
        if nom_result.scalar() is None:
            await db_session.execute(
                text("INSERT INTO nomenclature (project_id, barcode, updated_at) VALUES (:pid, :bc, NOW())"),
                {"pid": PROJECT_ID, "bc": barcode},
            )
    await db_session.commit()

    yield


async def _get_warehouse_ids(db_session) -> tuple[int, int]:
    """Return (wh_a_id, wh_b_id) for our two FULFILLMENT warehouses."""
    res_a = await db_session.execute(
        text("SELECT id FROM warehouses WHERE project_id = :pid AND name = 'Source FF A' LIMIT 1"),
        {"pid": PROJECT_ID},
    )
    res_b = await db_session.execute(
        text("SELECT id FROM warehouses WHERE project_id = :pid AND name = 'Source FF B' LIMIT 1"),
        {"pid": PROJECT_ID},
    )
    return res_a.scalar(), res_b.scalar()


def _build_payload(
    source_ids: list[int],
    targets: list[str],
    rows: list[AssemblyDraftRow],
    *,
    pallets: int = 1,
    weight: float = 100.0,
    eta: str | None = None,
) -> AssemblyDraftCreate:
    return AssemblyDraftCreate(
        name="Test Draft",
        distribution=AssemblyDraftDistribution(
            source_warehouse_ids=source_ids,
            target_warehouse_names=targets,
            rows=rows,
            pallets_count=pallets,
            pallet_weight_kg=weight,
            estimated_ready_date=eta,
        ),
        comment="auto-test",
    )


# ─── Tests: CRUD ────────────────────────────────────────────────────────────


def test_reconcile_handed_with_rows_drops_double_booked_draft_snapshots():
    """rows-побеждает: ручной снимок (status='draft') того же потока ФФ→склад→баркод,
    уже распределённого в rows, — устаревший дубль → вырезается; опустевший снимок
    удаляется. Не дубль (тот же баркод, но другой ФФ) и реальный handed-снимок цел."""
    dist = AssemblyDraftDistribution(
        rows=[
            AssemblyDraftRow(nm_id=1, barcode="BC-DUP", src={"5": 15}, tgt={"Воронеж": 15}),
            AssemblyDraftRow(nm_id=2, barcode="BC-CAZ", src={"5": 30}, tgt={"Казань": 30}),
            AssemblyDraftRow(nm_id=3, barcode="BC-HND", src={"5": 5}, tgt={"Пенза": 5}),
            AssemblyDraftRow(nm_id=4, barcode="BC-OTHERFF", src={"2": 10}, tgt={"Сарапул": 10}),
        ],
        handed_units=[
            # Дубль (5,Казань,BC-CAZ) есть в rows → снимок опустеет → удаляется.
            HandedUnit(
                source_ff_id=5, target_wb_name="Казань", package_type="BOX", status="draft",
                items=[HandedUnitItem(nm_id=2, barcode="BC-CAZ", qty=30)],
            ),
            # (5,Воронеж,BC-DUP) дубль → режется; (5,Воронеж,BC-KEEP) уникален → остаётся.
            HandedUnit(
                source_ff_id=5, target_wb_name="Воронеж", package_type="BOX", status="draft",
                items=[
                    HandedUnitItem(nm_id=1, barcode="BC-DUP", qty=15),
                    HandedUnitItem(nm_id=9, barcode="BC-KEEP", qty=12),
                ],
            ),
            # BC-OTHERFF в rows идёт из ФФ 2, а снимок — ФФ 5 → НЕ дубль, снимок цел.
            HandedUnit(
                source_ff_id=5, target_wb_name="Сарапул", package_type="BOX", status="draft",
                items=[HandedUnitItem(nm_id=4, barcode="BC-OTHERFF", qty=8)],
            ),
            # Реальная передача на ФФ (status='handed') — не трогаем даже при дубле (5,Пенза,BC-HND).
            HandedUnit(
                source_ff_id=5, target_wb_name="Пенза", package_type="BOX", status="handed",
                items=[HandedUnitItem(nm_id=3, barcode="BC-HND", qty=5)],
            ),
        ],
    )
    changed = assembly_draft_service._reconcile_handed_with_rows(dist)
    assert changed is True
    by_wb = {u.target_wb_name: u for u in dist.handed_units}
    assert "Казань" not in by_wb  # опустел → удалён
    assert [it.barcode for it in by_wb["Воронеж"].items] == ["BC-KEEP"]
    assert [it.barcode for it in by_wb["Сарапул"].items] == ["BC-OTHERFF"]  # другой ФФ — цел
    assert by_wb["Пенза"].status == "handed"  # реальная передача не тронута
    assert [it.barcode for it in by_wb["Пенза"].items] == ["BC-HND"]
    # Идемпотентность: повторный прогон уже ничего не меняет.
    assert assembly_draft_service._reconcile_handed_with_rows(dist) is False


@pytest.mark.asyncio
async def test_create_draft_happy_path(db_session):
    """POST /assembly/drafts equivalent — service creates a draft."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    payload = _build_payload(
        [wh_a],
        ["Электросталь"],
        [
            AssemblyDraftRow(
                nm_id=111,
                barcode=TEST_BARCODE_1,
                vendor_code="VC-1",
                src={str(wh_a): 5},
                tgt={"Электросталь": 5},
            )
        ],
    )
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    assert draft.id is not None
    assert draft.project_id == PROJECT_ID
    assert draft.name == "Test Draft"
    assert draft.is_deleted is False

    # Distribution round-trips through JSONB
    dist = AssemblyDraftDistribution.model_validate(draft.distribution)
    assert dist.source_warehouse_ids == [wh_a]
    assert dist.target_warehouse_names == ["Электросталь"]
    assert len(dist.rows) == 1
    assert dist.rows[0].barcode == TEST_BARCODE_1


@pytest.mark.asyncio
async def test_current_draft_creates_when_none(db_session):
    """get_or_create_current_draft: пусто → создаёт пустой; повторный вызов → тот же (не плодим)."""
    draft = await assembly_draft_service.get_or_create_current_draft(db_session, PROJECT_ID)
    assert draft.id is not None
    assert draft.project_id == PROJECT_ID
    assert draft.is_deleted is False

    again = await assembly_draft_service.get_or_create_current_draft(db_session, PROJECT_ID)
    assert again.id == draft.id  # один черновик → возвращается тот же


@pytest.mark.asyncio
async def test_current_draft_returns_single(db_session):
    """Один существующий черновик возвращается как есть (без merge)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    created = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Электросталь"],
            [AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, src={str(wh_a): 5}, tgt={"Электросталь": 5})],
        ),
    )
    current = await assembly_draft_service.get_or_create_current_draft(db_session, PROJECT_ID)
    assert current.id == created.id


@pytest.mark.asyncio
async def test_current_draft_merges_multiple(db_session):
    """Несколько черновиков → синглтон: остаётся ровно один активный со всеми строками."""
    wh_a, wh_b = await _get_warehouse_ids(db_session)
    d1 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Электросталь"],
            [AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, src={str(wh_a): 5}, tgt={"Электросталь": 5})],
        ),
    )
    d2 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_b],
            ["Казань"],
            [AssemblyDraftRow(nm_id=222, barcode=TEST_BARCODE_2, src={str(wh_b): 3}, tgt={"Казань": 3})],
        ),
    )
    current = await assembly_draft_service.get_or_create_current_draft(db_session, PROJECT_ID)

    # Ровно один активный черновик (остальные слиты и soft-deleted)
    actives = await assembly_draft_service.list_drafts(db_session, PROJECT_ID)
    assert len(actives) == 1
    assert actives[0].id == current.id
    assert current.id in {d1.id, d2.id}

    # Объединённый черновик держит строки ОБОИХ
    dist = AssemblyDraftDistribution.model_validate(current.distribution)
    assert {r.nm_id for r in dist.rows} == {111, 222}


@pytest.mark.asyncio
async def test_current_draft_merges_multiple_multitenancy(db_session):
    """Синглтон-консолидация не задевает чужой проект."""
    wh_a, wh_b = await _get_warehouse_ids(db_session)
    await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Электросталь"],
            [AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, src={str(wh_a): 1}, tgt={"Электросталь": 1})],
        ),
    )
    await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_b],
            ["Казань"],
            [AssemblyDraftRow(nm_id=222, barcode=TEST_BARCODE_2, src={str(wh_b): 1}, tgt={"Казань": 1})],
        ),
    )
    # Чужой проект со своим черновиком
    other = await assembly_draft_service.create_draft(db_session, OTHER_PROJECT_ID, _build_payload([], ["Тула"], []))

    await assembly_draft_service.get_or_create_current_draft(db_session, PROJECT_ID)

    other_actives = await assembly_draft_service.list_drafts(db_session, OTHER_PROJECT_ID)
    assert {d.id for d in other_actives} == {other.id}  # чужой проект нетронут


@pytest.mark.asyncio
async def test_list_drafts_excludes_deleted(db_session):
    """list_drafts must hide soft-deleted drafts."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    payload = _build_payload(
        [wh_a],
        ["Электросталь"],
        [
            AssemblyDraftRow(
                nm_id=111,
                barcode=TEST_BARCODE_1,
                src={str(wh_a): 1},
                tgt={"Электросталь": 1},
            )
        ],
    )
    d1 = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    d2 = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    # Delete one
    await assembly_draft_service.delete_draft(db_session, PROJECT_ID, d1.id)

    drafts = await assembly_draft_service.list_drafts(db_session, PROJECT_ID)
    ids = {d.id for d in drafts}
    assert d2.id in ids
    assert d1.id not in ids


@pytest.mark.asyncio
async def test_list_drafts_multi_tenancy(db_session):
    """list_drafts isolates by project_id."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    payload = _build_payload(
        [wh_a],
        ["Электросталь"],
        [
            AssemblyDraftRow(
                nm_id=111,
                barcode=TEST_BARCODE_1,
                src={str(wh_a): 1},
                tgt={"Электросталь": 1},
            )
        ],
    )
    d_p1 = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    # Create a draft directly in OTHER_PROJECT_ID (we don't need warehouses for the row)
    other_payload = _build_payload(
        [],
        ["Казань"],
        [],
    )
    # Empty rows is fine for a saved draft in another project
    d_other = AssemblyDraft(
        project_id=OTHER_PROJECT_ID,
        name="Other Project Draft",
        distribution=other_payload.distribution.model_dump(),
        comment=None,
    )
    db_session.add(d_other)
    await db_session.commit()

    drafts_p1 = await assembly_draft_service.list_drafts(db_session, PROJECT_ID)
    drafts_other = await assembly_draft_service.list_drafts(db_session, OTHER_PROJECT_ID)

    assert d_p1.id in {d.id for d in drafts_p1}
    assert d_other.id not in {d.id for d in drafts_p1}
    assert d_other.id in {d.id for d in drafts_other}
    assert d_p1.id not in {d.id for d in drafts_other}


@pytest.mark.asyncio
async def test_get_draft_404_other_project(db_session):
    """get_draft scoped to project_id — other project sees None."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    payload = _build_payload(
        [wh_a],
        ["Электросталь"],
        [
            AssemblyDraftRow(
                nm_id=111,
                barcode=TEST_BARCODE_1,
                src={str(wh_a): 1},
                tgt={"Электросталь": 1},
            )
        ],
    )
    d = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    found_other = await assembly_draft_service.get_draft(db_session, OTHER_PROJECT_ID, d.id)
    assert found_other is None
    found_self = await assembly_draft_service.get_draft(db_session, PROJECT_ID, d.id)
    assert found_self is not None


@pytest.mark.asyncio
async def test_update_draft(db_session):
    """update_draft mutates name + distribution + comment."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    payload = _build_payload(
        [wh_a],
        ["Электросталь"],
        [
            AssemblyDraftRow(
                nm_id=111,
                barcode=TEST_BARCODE_1,
                src={str(wh_a): 1},
                tgt={"Электросталь": 1},
            )
        ],
    )
    d = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    new_dist = AssemblyDraftDistribution(
        source_warehouse_ids=[wh_a],
        target_warehouse_names=["Казань"],
        rows=[
            AssemblyDraftRow(
                nm_id=222,
                barcode=TEST_BARCODE_2,
                src={str(wh_a): 3},
                tgt={"Казань": 3},
            )
        ],
        pallets_count=2,
        pallet_weight_kg=200.0,
    )
    update = AssemblyDraftUpdate(
        name="Обновлённый черновик",
        distribution=new_dist,
        comment="Updated comment",
    )
    updated = await assembly_draft_service.update_draft(db_session, PROJECT_ID, d.id, update)
    assert updated.name == "Обновлённый черновик"
    assert updated.comment == "Updated comment"
    parsed = AssemblyDraftDistribution.model_validate(updated.distribution)
    assert parsed.target_warehouse_names == ["Казань"]
    assert parsed.rows[0].nm_id == 222


@pytest.mark.asyncio
async def test_update_draft_404(db_session):
    """update_draft on missing/deleted -> HTTPException(404)."""
    update = AssemblyDraftUpdate(name="anything")
    with pytest.raises(HTTPException) as ei:
        await assembly_draft_service.update_draft(db_session, PROJECT_ID, 999_999_999, update)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_draft_is_soft(db_session):
    """delete_draft marks is_deleted + deleted_at, doesn't remove the row."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    payload = _build_payload(
        [wh_a],
        ["Электросталь"],
        [
            AssemblyDraftRow(
                nm_id=111,
                barcode=TEST_BARCODE_1,
                src={str(wh_a): 1},
                tgt={"Электросталь": 1},
            )
        ],
    )
    d = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    await assembly_draft_service.delete_draft(db_session, PROJECT_ID, d.id)

    raw_q = await db_session.execute(select(AssemblyDraft).where(AssemblyDraft.id == d.id))
    raw = raw_q.scalar_one()
    assert raw.is_deleted is True
    assert raw.deleted_at is not None
    assert isinstance(raw.deleted_at, datetime)

    # Service-level get returns None
    assert await assembly_draft_service.get_draft(db_session, PROJECT_ID, d.id) is None


@pytest.mark.asyncio
async def test_delete_draft_404(db_session):
    """delete_draft on missing -> 404."""
    with pytest.raises(HTTPException) as ei:
        await assembly_draft_service.delete_draft(db_session, PROJECT_ID, 999_999_999)
    assert ei.value.status_code == 404


# ─── Tests: Commit ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_commit_draft_one_source_two_targets(db_session):
    """1 source x 2 targets → 2 AssemblyRequests, each with manual WB warehouse."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=111,
            barcode=TEST_BARCODE_1,
            src={str(wh_a): 10},
            tgt={"Электросталь": 6, "Казань": 4},
        )
    ]
    payload = _build_payload(
        [wh_a],
        ["Электросталь", "Казань"],
        rows,
        pallets=2,
        weight=120.0,
        eta="2026-06-15",
    )
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert resp.draft_id == draft.id
    assert len(resp.created_request_ids) == 2

    # Reload requests
    res = await db_session.execute(
        select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)).order_by(AssemblyRequest.id)
    )
    requests = list(res.scalars().all())
    assert len(requests) == 2

    by_wb = {r.wb_warehouse_name_manual: r for r in requests}
    assert set(by_wb.keys()) == {"Электросталь", "Казань"}

    for r in requests:
        assert r.warehouse_id == wh_a
        assert r.project_id == PROJECT_ID
        assert r.wb_fbo_supply_id is None
        assert r.pallets_count == 2
        assert r.pallet_weight_kg == Decimal("120.00") or float(r.pallet_weight_kg) == 120.0
        assert r.estimated_ready_date is not None and r.estimated_ready_date.isoformat() == "2026-06-15"

    # Items: each request should have one item with the correct quantity
    item_q = await db_session.execute(
        text(
            "SELECT assembly_request_id, barcode, quantity "
            "FROM assembly_request_items WHERE assembly_request_id = ANY(:ids) "
            "ORDER BY assembly_request_id"
        ),
        {"ids": resp.created_request_ids},
    )
    items_by_req: dict[int, list[tuple[str, int]]] = {}
    for row in item_q.all():
        items_by_req.setdefault(row.assembly_request_id, []).append((row.barcode, row.quantity))

    qty_by_wb = {by_wb["Электросталь"].id: 6, by_wb["Казань"].id: 4}
    for req_id, expected_qty in qty_by_wb.items():
        assert items_by_req[req_id] == [(TEST_BARCODE_1, expected_qty)]


@pytest.mark.asyncio
async def test_commit_draft_pallet_counts_per_request(db_session):
    """pallet_counts проставляет паллеты per-request по ключу «ff::wb::pkg»;
    отсутствующий ключ → плоский distribution.pallets_count (fallback)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=111,
            barcode=TEST_BARCODE_1,
            src={str(wh_a): 10},
            tgt={"Электросталь": 6, "Казань": 4},
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь", "Казань"], rows, pallets=1)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    # Казань намеренно опущена → должна упасть на плоский pallets_count=1.
    pallet_counts = {f"{wh_a}::Электросталь::BOX": 3}
    resp = await assembly_draft_service.commit_draft(
        db_session, PROJECT_ID, draft.id, None, pallet_counts
    )
    assert len(resp.created_request_ids) == 2

    res = await db_session.execute(
        select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids))
    )
    by_wb = {r.wb_warehouse_name_manual: r for r in res.scalars().all()}
    assert by_wb["Электросталь"].pallets_count == 3  # из map
    assert by_wb["Казань"].pallets_count == 1  # fallback на плоский pallets_count


@pytest.mark.asyncio
async def test_commit_draft_two_sources_two_targets_pro_rata(db_session):
    """2 sources x 2 targets, pro-rata distribution stays balanced + total preserved."""
    wh_a, wh_b = await _get_warehouse_ids(db_session)
    # 6 from A + 4 from B → 5 to Электросталь, 5 to Казань. Total = 10.
    rows = [
        AssemblyDraftRow(
            nm_id=111,
            barcode=TEST_BARCODE_1,
            src={str(wh_a): 6, str(wh_b): 4},
            tgt={"Электросталь": 5, "Казань": 5},
        )
    ]
    payload = _build_payload(
        [wh_a, wh_b],
        ["Электросталь", "Казань"],
        rows,
    )
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    # Could be up to 4 pairs (2 sources × 2 targets); each pair gets a request
    assert 1 <= len(resp.created_request_ids) <= 4

    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)))
    requests = list(res.scalars().all())

    # Sum of items quantities equals row total (10)
    item_q = await db_session.execute(
        text("SELECT SUM(quantity) FROM assembly_request_items " "WHERE assembly_request_id = ANY(:ids)"),
        {"ids": resp.created_request_ids},
    )
    total_qty = item_q.scalar()
    assert total_qty == 10

    # Per-source totals
    src_totals: dict[int, int] = {}
    tgt_totals: dict[str, int] = {}
    for r in requests:
        item_sum_q = await db_session.execute(
            text("SELECT SUM(quantity) FROM assembly_request_items WHERE assembly_request_id = :rid"),
            {"rid": r.id},
        )
        s = item_sum_q.scalar() or 0
        src_totals[r.warehouse_id] = src_totals.get(r.warehouse_id, 0) + s
        tgt_totals[r.wb_warehouse_name_manual] = tgt_totals.get(r.wb_warehouse_name_manual, 0) + s

    assert src_totals.get(wh_a) == 6
    assert src_totals.get(wh_b) == 4
    assert tgt_totals.get("Электросталь") == 5
    assert tgt_totals.get("Казань") == 5


@pytest.mark.asyncio
async def test_commit_draft_by_source_ff_partial(db_session):
    """Партиальный коммит по складу-ФФ: заявки только из порций выбранного ФФ,
    порции других ФФ (даже внутри ОДНОЙ строки) остаются в черновике."""
    from backend.schemas.assembly_draft import AssemblyDraftDistribution

    wh_a, wh_b = await _get_warehouse_ids(db_session)
    # Одна строка сорсит с ДВУХ ФФ: 6 с A + 4 с B → 5 Казань, 5 Электросталь.
    rows = [
        AssemblyDraftRow(
            nm_id=111,
            barcode=TEST_BARCODE_1,
            src={str(wh_a): 6, str(wh_b): 4},
            tgt={"Электросталь": 5, "Казань": 5},
        )
    ]
    payload = _build_payload([wh_a, wh_b], ["Электросталь", "Казань"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    # Коммитим ТОЛЬКО ФФ A.
    resp = await assembly_draft_service.commit_draft(
        db_session, PROJECT_ID, draft.id, source_ff_id=wh_a
    )
    res = await db_session.execute(
        select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids))
    )
    requests = list(res.scalars().all())
    # Все заявки — только со склада A, суммарно ровно src[A] = 6.
    assert requests
    assert {r.warehouse_id for r in requests} == {wh_a}
    item_q = await db_session.execute(
        text("SELECT SUM(quantity) FROM assembly_request_items WHERE assembly_request_id = ANY(:ids)"),
        {"ids": resp.created_request_ids},
    )
    assert item_q.scalar() == 6

    # Черновик НЕ удалён: остаток ФФ B (4 шт) остался в rows, сбалансирован.
    raw = await db_session.execute(select(AssemblyDraft).where(AssemblyDraft.id == draft.id))
    raw_draft = raw.scalar_one()
    assert raw_draft.is_deleted is False
    dist = AssemblyDraftDistribution.model_validate(raw_draft.distribution)
    left_src = sum(int(v) for r in dist.rows for v in r.src.values())
    left_tgt = sum(int(v) for r in dist.rows for v in r.tgt.values())
    assert left_src == 4 and left_tgt == 4  # ровно порция B, баланс сохранён
    # Остаток сорсится ТОЛЬКО с B (порция A ушла в заявки).
    assert all(str(wh_a) not in r.src for r in dist.rows)


@pytest.mark.asyncio
async def test_commit_draft_source_ff_not_in_draft_400(db_session):
    """source_ff_id, которого нет среди источников черновика → 400 (нечего коммитить)."""
    wh_a, wh_b = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=111, barcode=TEST_BARCODE_1,
            src={str(wh_a): 4}, tgt={"Электросталь": 4},
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    with pytest.raises(HTTPException) as exc:
        await assembly_draft_service.commit_draft(
            db_session, PROJECT_ID, draft.id, source_ff_id=wh_b
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_commit_draft_explicit_supplies(db_session):
    """supplies → заявки строятся РОВНО из явных отгрузок ФФ→склад (минуя pro-rata).
    Режим «только целые паллеты»: фронт прислал одну целую отгрузку, остальное снято."""
    from backend.schemas.assembly_draft import CommitSupply

    wh_a, wh_b = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=111,
            barcode=TEST_BARCODE_1,
            src={str(wh_a): 100, str(wh_b): 60},
            tgt={"Электросталь": 100, "Казань": 60},
        )
    ]
    payload = _build_payload([wh_a, wh_b], ["Электросталь", "Казань"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    # Явно: только wh_a→Электросталь 80 шт (целое). Остальное (80) — снято, не едет.
    supplies = [
        CommitSupply(
            source_ff_id=wh_a, target_wb_name="Электросталь", package_type="BOX",
            items={TEST_BARCODE_1: 80},
        )
    ]
    resp = await assembly_draft_service.commit_draft(
        db_session, PROJECT_ID, draft.id, "BOX", None, supplies
    )
    assert len(resp.created_request_ids) == 1

    res = await db_session.execute(
        select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids))
    )
    req = res.scalar_one()
    assert req.warehouse_id == wh_a
    assert req.wb_warehouse_name_manual == "Электросталь"
    item_q = await db_session.execute(
        text("SELECT SUM(quantity) FROM assembly_request_items WHERE assembly_request_id = :rid"),
        {"rid": req.id},
    )
    assert item_q.scalar() == 80  # ровно из supplies, не pro-rata 160


@pytest.mark.asyncio
async def test_commit_draft_supplies_reject_inflation(db_session):
    """supplies с Σ по баркоду больше, чем в черновике → 400 (нельзя раздуть отгрузку)."""
    from backend.schemas.assembly_draft import CommitSupply

    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=111, barcode=TEST_BARCODE_1,
            src={str(wh_a): 100}, tgt={"Электросталь": 100},
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    supplies = [
        CommitSupply(
            source_ff_id=wh_a, target_wb_name="Электросталь", package_type="BOX",
            items={TEST_BARCODE_1: 140},  # > 100 в черновике
        )
    ]
    with pytest.raises(HTTPException) as ei:
        await assembly_draft_service.commit_draft(
            db_session, PROJECT_ID, draft.id, "BOX", None, supplies
        )
    assert ei.value.status_code == 400
    assert "exceeds draft" in ei.value.detail


@pytest.mark.asyncio
async def test_commit_draft_validates_balance(db_session):
    """Row where sum(src) != sum(tgt) -> HTTPException(400)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=111,
            barcode=TEST_BARCODE_1,
            src={str(wh_a): 5},
            tgt={"Электросталь": 3},  # mismatch
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    with pytest.raises(HTTPException) as ei:
        await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert ei.value.status_code == 400
    assert "src sum" in ei.value.detail.lower()

    # Draft NOT marked deleted
    raw = await db_session.execute(select(AssemblyDraft).where(AssemblyDraft.id == draft.id))
    raw_draft = raw.scalar_one()
    assert raw_draft.is_deleted is False


@pytest.mark.asyncio
async def test_commit_draft_empty_rows(db_session):
    """Empty distribution rows -> 400."""
    payload = _build_payload([], [], [])
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    with pytest.raises(HTTPException) as ei:
        await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_commit_atomic_rollback_on_unknown_barcode(db_session):
    """Failure during commit -> rollback (draft remains, no AssemblyRequests)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=111,
            barcode="UNKNOWN_BARCODE_DOES_NOT_EXIST",
            src={str(wh_a): 5},
            tgt={"Электросталь": 5},
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    with pytest.raises(HTTPException) as ei:
        await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert ei.value.status_code == 400
    assert "barcode" in ei.value.detail.lower()

    # Draft NOT marked deleted, no AssemblyRequests created
    raw = await db_session.execute(select(AssemblyDraft).where(AssemblyDraft.id == draft.id))
    raw_draft = raw.scalar_one()
    assert raw_draft.is_deleted is False

    req_q = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.project_id == PROJECT_ID))
    assert req_q.scalars().all() == []


@pytest.mark.asyncio
async def test_commit_marks_draft_deleted(db_session):
    """After successful commit the draft is soft-deleted."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=111,
            barcode=TEST_BARCODE_1,
            src={str(wh_a): 4},
            tgt={"Электросталь": 4},
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert len(resp.created_request_ids) == 1

    raw = await db_session.execute(select(AssemblyDraft).where(AssemblyDraft.id == draft.id))
    raw_draft = raw.scalar_one()
    assert raw_draft.is_deleted is True
    assert raw_draft.deleted_at is not None

    # And list_drafts no longer returns it
    drafts = await assembly_draft_service.list_drafts(db_session, PROJECT_ID)
    assert draft.id not in {d.id for d in drafts}


@pytest.mark.asyncio
async def test_commit_404_when_draft_missing(db_session):
    """commit_draft on missing/deleted draft -> 404."""
    with pytest.raises(HTTPException) as ei:
        await assembly_draft_service.commit_draft(db_session, PROJECT_ID, 999_999_999)
    assert ei.value.status_code == 404


# ─── Tests: newcomer split (короб/моно/новинки идут отдельными заявками) ────


NEWCOMER_BARCODE = "TEST_DRAFT_BC_NEW_001"
REGULAR_BARCODE = "TEST_DRAFT_BC_OLD_001"
NEWCOMER_NM_ID = 9_001_001
REGULAR_NM_ID = 9_001_002


@pytest_asyncio.fixture
async def setup_newcomer_nomenclature(db_session):
    """UPSERT two SKU: one «новинка» (first_sale_date NULL), one «обычный» (-100d).

    Без teardown — autouse `setup_test_data` следующего теста чистит зависимые
    assembly_request_items/requests перед удалением nomenclature невозможно
    (FK violation), поэтому UPSERT идемпотентен и safe для re-use между тестами.
    """
    from datetime import date, timedelta

    await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, article_wb, first_sale_date, updated_at) "
            "VALUES (:pid, :bc, :nm, NULL, NOW()) "
            "ON CONFLICT (project_id, barcode) DO UPDATE "
            "SET article_wb = EXCLUDED.article_wb, first_sale_date = NULL, updated_at = NOW()"
        ),
        {"pid": PROJECT_ID, "bc": NEWCOMER_BARCODE, "nm": NEWCOMER_NM_ID},
    )
    await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, article_wb, first_sale_date, updated_at) "
            "VALUES (:pid, :bc, :nm, :fsd, NOW()) "
            "ON CONFLICT (project_id, barcode) DO UPDATE "
            "SET article_wb = EXCLUDED.article_wb, first_sale_date = EXCLUDED.first_sale_date, updated_at = NOW()"
        ),
        {
            "pid": PROJECT_ID,
            "bc": REGULAR_BARCODE,
            "nm": REGULAR_NM_ID,
            "fsd": date.today() - timedelta(days=100),
        },
    )
    await db_session.commit()
    yield


@pytest.mark.asyncio
async def test_commit_merges_newcomer_and_regular(db_session, setup_newcomer_nomenclature):
    """Same (src, wb, pkg) mixed newcomer/regular → ОДНА AssemblyRequest (новинки и
    обычные едут вместе); comment получает префикс 🆕 (в заявке есть новинка)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=NEWCOMER_NM_ID,
            barcode=NEWCOMER_BARCODE,
            src={str(wh_a): 5},
            tgt={"Электросталь": 5},
            package_type="BOX",
        ),
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 7},
            tgt={"Электросталь": 7},
            package_type="BOX",
        ),
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    # Один и тот же (src, wb, pkg) → ОДНА заявка (новинки + обычные вместе).
    assert len(resp.created_request_ids) == 1

    req_id = resp.created_request_ids[0]
    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id == req_id))
    req = res.scalars().one()
    # Содержит новинку → префикс 🆕 в комментарии (auto-test — базовый comment).
    assert req.comment and req.comment.startswith("🆕 Новинки")
    assert "auto-test" in req.comment

    items_res = await db_session.execute(
        select(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == req_id)
    )
    items = list(items_res.scalars().all())
    assert {it.barcode for it in items} == {NEWCOMER_BARCODE, REGULAR_BARCODE}
    assert sum(it.quantity for it in items) == 12  # 5 (новинка) + 7 (обычный)


@pytest.mark.asyncio
async def test_commit_box_mono_2_requests(db_session, setup_newcomer_nomenclature):
    """Same (src, wb), упаковки BOX+MONOPALLET, в каждой смесь новинка+обычный →
    2 заявки (по одной на упаковку); обе помечены 🆕 (в каждой есть новинка)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=NEWCOMER_NM_ID,
            barcode=NEWCOMER_BARCODE,
            src={str(wh_a): 3},
            tgt={"Электросталь": 3},
            package_type="BOX",
        ),
        AssemblyDraftRow(
            nm_id=NEWCOMER_NM_ID,
            barcode=NEWCOMER_BARCODE,
            src={str(wh_a): 4},
            tgt={"Электросталь": 4},
            package_type="MONOPALLET",
        ),
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 5},
            tgt={"Электросталь": 5},
            package_type="BOX",
        ),
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 6},
            tgt={"Электросталь": 6},
            package_type="MONOPALLET",
        ),
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    # Группировка по (src, wb, pkg): BOX и MONOPALLET — 2 заявки (новинки и обычные слиты).
    assert len(resp.created_request_ids) == 2

    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)))
    requests = list(res.scalars().all())
    assert {r.package_type for r in requests} == {"BOX", "MONOPALLET"}
    # В каждой упаковке есть новинка → обе заявки помечены 🆕.
    assert all(r.comment and r.comment.startswith("🆕") for r in requests)


@pytest.mark.asyncio
async def test_commit_box_tab_includes_supersafe_excludes_mono(db_session, setup_newcomer_nomenclature):
    """«Короб»-коммит (package_type=BOX) = вкладка «не-MONOPALLET»: BOX и SUPERSAFE
    коммитятся (отдельными заявками со своим типом), MONOPALLET остаётся в черновике."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 5},
            tgt={"Электросталь": 5},
            package_type="BOX",
        ),
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 3},
            tgt={"Электросталь": 3},
            package_type="SUPERSAFE",
        ),
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 4},
            tgt={"Электросталь": 4},
            package_type="MONOPALLET",
        ),
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id, package_type="BOX")
    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)))
    pkgs = {r.package_type for r in res.scalars().all()}
    assert pkgs == {"BOX", "SUPERSAFE"}  # SUPERSAFE больше не выпадает из «Короб»-коммита
    # MONOPALLET остался → черновик не удалён, в нём 1 строка.
    leftover = await assembly_draft_service.get_draft(db_session, PROJECT_ID, draft.id)
    assert leftover is not None
    leftover_rows = leftover.distribution["rows"]
    assert len(leftover_rows) == 1
    assert (leftover_rows[0].get("package_type") or "BOX") == "MONOPALLET"


def _mixed_rows(wh_a: int) -> list[AssemblyDraftRow]:
    return [
        AssemblyDraftRow(
            nm_id=NEWCOMER_NM_ID,
            barcode=NEWCOMER_BARCODE,
            src={str(wh_a): 5},
            tgt={"Электросталь": 5},
            package_type="BOX",
        ),
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 7},
            tgt={"Электросталь": 7},
            package_type="BOX",
        ),
    ]


@pytest.mark.asyncio
async def test_commit_mixed_merges_one_request_and_clears_draft(db_session, setup_newcomer_nomenclature):
    """Смешанный склад (новинка + обычный, один src/wb/pkg) → ОДНА заявка с 🆕;
    весь черновик закоммичен → soft-delete."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    payload = _build_payload([wh_a], ["Электросталь"], _mixed_rows(wh_a))
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert len(resp.created_request_ids) == 1
    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)))
    req = res.scalars().one()
    assert req.comment and req.comment.startswith("🆕 Новинки")
    # Черновик полностью закоммичен → удалён.
    assert await assembly_draft_service.get_draft(db_session, PROJECT_ID, draft.id) is None


@pytest.mark.asyncio
async def test_commit_regular_only_no_newcomer_marker(db_session, setup_newcomer_nomenclature):
    """Склад только из обычных товаров → заявка без префикса 🆕."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 7},
            tgt={"Электросталь": 7},
            package_type="BOX",
        ),
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert len(resp.created_request_ids) == 1
    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)))
    req = res.scalars().one()
    assert not (req.comment and req.comment.startswith("🆕"))


@pytest.mark.asyncio
async def test_to_read_model_returns_newcomer_nm_ids(db_session, setup_newcomer_nomenclature):
    """to_read_model добавляет newcomer_nm_ids — UI использует для бейджа 🆕 и счётчика заявок."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=NEWCOMER_NM_ID,
            barcode=NEWCOMER_BARCODE,
            src={str(wh_a): 1},
            tgt={"Электросталь": 1},
        ),
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 1},
            tgt={"Электросталь": 1},
        ),
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    read = await assembly_draft_service.to_read_model(db_session, PROJECT_ID, draft)
    assert NEWCOMER_NM_ID in read.newcomer_nm_ids
    assert REGULAR_NM_ID not in read.newcomer_nm_ids
    assert read.id == draft.id


# ─── Tests: per-unit lifecycle (передан на ФФ → в сборке) ───────────────────


@pytest.mark.asyncio
async def test_hand_off_unit_carves_from_rows(db_session, setup_newcomer_nomenclature):
    """hand_off_unit вырезает поток ff→wb из rows в handed_units, уменьшая остаток."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 12},
            tgt={"Электросталь": 7, "Казань": 5},
            package_type="BOX",
        ),
    ]
    payload = _build_payload([wh_a], ["Электросталь", "Казань"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    read = await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    assert len(read.distribution.handed_units) == 1
    unit = read.distribution.handed_units[0]
    assert unit.source_ff_id == wh_a and unit.target_wb_name == "Электросталь"
    assert sum(it.qty for it in unit.items) == 7
    # В rows остался только поток на Казань (5 шт).
    assert len(read.distribution.rows) == 1
    rem = read.distribution.rows[0]
    assert sum(rem.src.values()) == 5
    assert rem.tgt == {"Казань": 5}


@pytest.mark.asyncio
async def test_hand_off_merges_rows_into_existing_snapshot(db_session, setup_newcomer_nomenclature):
    """Edge (черновик «в полёте»): обычные уже переданы на ФФ (снимок), а новинки
    ещё в rows на тот же (ff, wb, pkg). hand_off вырезает rows и сливает их в
    снимок → один handed-юнит со всеми позициями, rows пустеют."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    handed = [
        HandedUnit(
            source_ff_id=wh_a,
            target_wb_name="Электросталь",
            package_type="BOX",
            status="handed",
            items=[HandedUnitItem(nm_id=REGULAR_NM_ID, barcode=REGULAR_BARCODE, vendor_code="", qty=7)],
        )
    ]
    rows = [
        AssemblyDraftRow(
            nm_id=NEWCOMER_NM_ID,
            barcode=NEWCOMER_BARCODE,
            src={str(wh_a): 5},
            tgt={"Электросталь": 5},
            package_type="BOX",
        )
    ]
    draft = await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, _draft_with_handed(wh_a, "Inflight", rows, handed)
    )

    read = await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    assert len(read.distribution.handed_units) == 1
    u = read.distribution.handed_units[0]
    assert u.status == "handed"
    by_bc = {it.barcode: it.qty for it in u.items}
    assert by_bc == {REGULAR_BARCODE: 7, NEWCOMER_BARCODE: 5}
    assert read.distribution.rows == []


@pytest.mark.asyncio
async def test_revert_unit_merges_back(db_session, setup_newcomer_nomenclature):
    """revert_unit возвращает позиции замороженного юнита обратно в rows."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 7},
            tgt={"Электросталь": 7},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    read = await assembly_draft_service.revert_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    assert read.distribution.handed_units == []
    assert len(read.distribution.rows) == 1
    assert read.distribution.rows[0].src == {str(wh_a): 7}
    assert read.distribution.rows[0].tgt == {"Электросталь": 7}


@pytest.mark.asyncio
async def test_commit_unit_creates_request_and_clears_draft(db_session, setup_newcomer_nomenclature):
    """commit_unit создаёт AssemblyRequest из замороженного юнита; пустой черновик удаляется."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 7},
            tgt={"Электросталь": 7},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    resp = await assembly_draft_service.commit_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    assert len(resp.created_request_ids) == 1
    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)))
    req = res.scalars().one()
    assert req.warehouse_id == wh_a
    assert req.wb_warehouse_name_manual == "Электросталь"
    assert req.status == "IN_PROGRESS"
    # Черновик опустел (нет rows и handed_units) → soft-deleted.
    assert await assembly_draft_service.get_draft(db_session, PROJECT_ID, draft.id) is None


@pytest.mark.asyncio
async def test_commit_unit_direct_from_rows(db_session, setup_newcomer_nomenclature):
    """commit_unit без предварительного hand-off: юнит замораживается неявно
    и заявка создаётся сразу (один клик «В сборку»)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 12},
            tgt={"Электросталь": 7, "Казань": 5},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь", "Казань"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    resp = await assembly_draft_service.commit_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")

    assert len(resp.created_request_ids) == 1
    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)))
    req = res.scalars().one()
    assert req.status == "IN_PROGRESS"
    items_res = await db_session.execute(
        select(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == req.id)
    )
    assert sum(it.quantity for it in items_res.scalars().all()) == 7
    # Поток на Казань остался в rows, черновик жив.
    read = await assembly_draft_service.get_draft(db_session, PROJECT_ID, draft.id)
    assert read is not None
    await db_session.refresh(read)
    dist = AssemblyDraftDistribution.model_validate(read.distribution)
    assert dist.handed_units == []
    assert len(dist.rows) == 1
    assert dist.rows[0].tgt == {"Казань": 5}


@pytest.mark.asyncio
async def test_commit_unit_404_when_unit_missing(db_session, setup_newcomer_nomenclature):
    """commit_unit по несуществующему направлению (нет ни в rows, ни в handed) → 404."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 7},
            tgt={"Электросталь": 7},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    with pytest.raises(HTTPException) as exc:
        await assembly_draft_service.commit_unit(db_session, PROJECT_ID, draft.id, wh_a, "Несуществующий", "BOX")
    assert exc.value.status_code == 404


def _item(qty: int) -> HandedUnitItem:
    return HandedUnitItem(nm_id=REGULAR_NM_ID, barcode=REGULAR_BARCODE, vendor_code="", qty=qty)


@pytest.mark.asyncio
async def test_set_unit_items_freezes_auto_draft(db_session, setup_newcomer_nomenclature):
    """Правка авто-черновика: фиксирует юнит (вырез из rows) + новый состав (status=draft)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 12},
            tgt={"Электросталь": 7, "Казань": 5},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь", "Казань"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    read = await assembly_draft_service.set_unit_items(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", [_item(10)]
    )
    assert len(read.distribution.handed_units) == 1
    u = read.distribution.handed_units[0]
    assert u.status == "draft"
    assert sum(it.qty for it in u.items) == 10
    # Из rows вырезан исходный поток на Электросталь, остался Казань (5).
    assert sum(r.tgt.get("Электросталь", 0) for r in read.distribution.rows) == 0
    assert sum(r.tgt.get("Казань", 0) for r in read.distribution.rows) == 5


@pytest.mark.asyncio
async def test_set_unit_items_replaces_frozen(db_session, setup_newcomer_nomenclature):
    """Повторная правка замороженного черновика заменяет состав, не плодит юниты."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 7},
            tgt={"Электросталь": 7},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    await assembly_draft_service.set_unit_items(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", [_item(5)]
    )
    read = await assembly_draft_service.set_unit_items(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", [_item(9)]
    )
    assert len(read.distribution.handed_units) == 1
    assert sum(it.qty for it in read.distribution.handed_units[0].items) == 9


@pytest.mark.asyncio
async def test_set_unit_items_blocked_after_handoff(db_session, setup_newcomer_nomenclature):
    """После «передан на ФФ» правка наполнения запрещена (400)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 7},
            tgt={"Электросталь": 7},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    with pytest.raises(HTTPException) as exc:
        await assembly_draft_service.set_unit_items(
            db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", [_item(9)]
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_edit_then_handoff_then_commit(db_session, setup_newcomer_nomenclature):
    """Правка → передать на ФФ (handed, состав отредактированный) → в сборку."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 7},
            tgt={"Электросталь": 7},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    await assembly_draft_service.set_unit_items(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", [_item(9)]
    )
    read = await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    u = read.distribution.handed_units[0]
    assert u.status == "handed"
    assert sum(it.qty for it in u.items) == 9
    resp = await assembly_draft_service.commit_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    assert len(resp.created_request_ids) == 1


@pytest.mark.asyncio
async def test_delete_unit_auto_clears_empty_draft(db_session, setup_newcomer_nomenclature):
    """Удаление единственной авто-заявки → поток вырезан, пустой черновик soft-delete."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 7},
            tgt={"Электросталь": 7},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    await assembly_draft_service.delete_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    assert await assembly_draft_service.get_draft(db_session, PROJECT_ID, draft.id) is None


@pytest.mark.asyncio
async def test_delete_unit_handed_blocked(db_session, setup_newcomer_nomenclature):
    """Удалить переданную на ФФ заявку нельзя (400) — сначала вернуть в черновик."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 7},
            tgt={"Электросталь": 7},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    with pytest.raises(HTTPException) as exc:
        await assembly_draft_service.delete_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    assert exc.value.status_code == 400


# ─── Tests: move_unit («сменить склад WB» — перенос для этого ФФ) ─────────────


@pytest.mark.asyncio
async def test_move_unit_auto_relabels_to_new_wb(db_session, setup_newcomer_nomenclature):
    """Перенос авто-юнита на новый WB: поток этого ФФ переезжает в tgt[new_wb],
    остальные направления и src нетронуты, баланс сохраняется."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 12},
            tgt={"Электросталь": 7, "Казань": 5},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь", "Казань"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    read = await assembly_draft_service.move_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", "Тула")
    assert read.distribution.handed_units == []
    assert len(read.distribution.rows) == 1
    row = read.distribution.rows[0]
    assert row.src == {str(wh_a): 12}
    assert row.tgt == {"Казань": 5, "Тула": 7}
    assert "Тула" in read.distribution.target_warehouse_names


@pytest.mark.asyncio
async def test_move_unit_merges_into_existing_draft_at_dest(db_session, setup_newcomer_nomenclature):
    """Если на складе-получателе уже есть ручной черновик-юнит — позиции
    сливаются по баркоду в один снимок, исходный поток исчезает."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 12},
            tgt={"Электросталь": 7, "Казань": 5},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь", "Казань"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    # Зафиксировать Казань как ручной черновик (5 шт), Электросталь остаётся авто.
    await assembly_draft_service.set_unit_items(db_session, PROJECT_ID, draft.id, wh_a, "Казань", "BOX", [_item(5)])

    read = await assembly_draft_service.move_unit(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", "Казань"
    )
    assert read.distribution.rows == []
    assert len(read.distribution.handed_units) == 1
    unit = read.distribution.handed_units[0]
    assert unit.target_wb_name == "Казань"
    assert unit.status == "draft"
    assert sum(it.qty for it in unit.items) == 12  # 5 (был) + 7 (перенос)


@pytest.mark.asyncio
async def test_move_unit_same_wb_rejected(db_session, setup_newcomer_nomenclature):
    """Перенос на тот же склад → 400."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 7},
            tgt={"Электросталь": 7},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    with pytest.raises(HTTPException) as exc:
        await assembly_draft_service.move_unit(
            db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", "Электросталь"
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_move_unit_handed_source_blocked(db_session, setup_newcomer_nomenclature):
    """Переданный на ФФ юнит переносить нельзя (400) — сначала вернуть в черновик."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 7},
            tgt={"Электросталь": 7},
            package_type="BOX",
        )
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    with pytest.raises(HTTPException) as exc:
        await assembly_draft_service.move_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", "Казань")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_move_unit_mixed_snapshot_and_rows(db_session, setup_newcomer_nomenclature):
    """Legacy «в полёте»: ДРАФТ-снимок + остаточный авто-поток на тот же (ff,wb,pkg).
    move переносит ВСЁ (снимок + rows) → на исходном складе ничего не остаётся."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    handed = [
        HandedUnit(
            source_ff_id=wh_a,
            target_wb_name="Электросталь",
            package_type="BOX",
            status="draft",
            items=[HandedUnitItem(nm_id=REGULAR_NM_ID, barcode=REGULAR_BARCODE, vendor_code="", qty=7)],
        )
    ]
    rows = [
        AssemblyDraftRow(
            nm_id=NEWCOMER_NM_ID,
            barcode=NEWCOMER_BARCODE,
            src={str(wh_a): 5},
            tgt={"Электросталь": 5},
            package_type="BOX",
        )
    ]
    draft = await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, _draft_with_handed(wh_a, "Mixed", rows, handed)
    )

    read = await assembly_draft_service.move_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", "Тула")
    # На исходном складе не осталось ни снимка, ни авто-потока.
    assert all(u.target_wb_name != "Электросталь" for u in read.distribution.handed_units)
    assert all("Электросталь" not in r.tgt for r in read.distribution.rows)
    # Всё (7 снимок + 5 rows) уехало на Тулу (нет снимка-получателя → вернулось в rows).
    assert sum(r.tgt.get("Тула", 0) for r in read.distribution.rows) == 12


# ─── Tests: source_draft_id linkage («История — в сборке» per draft) ──────────


@pytest.mark.asyncio
async def test_commit_draft_sets_source_draft_id(db_session):
    """commit_draft проставляет source_draft_id = draft.id на все созданные заявки."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=111,
            barcode=TEST_BARCODE_1,
            src={str(wh_a): 10},
            tgt={"Электросталь": 6, "Казань": 4},
        )
    ]
    draft = await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, _build_payload([wh_a], ["Электросталь", "Казань"], rows)
    )
    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)))
    requests = list(res.scalars().all())
    assert requests and all(r.source_draft_id == draft.id for r in requests)


@pytest.mark.asyncio
async def test_commit_unit_links_draft_and_list_filter(db_session):
    """commit_unit проставляет source_draft_id; list?draft_id= возвращает только заявки этого черновика."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=111,
            barcode=TEST_BARCODE_1,
            src={str(wh_a): 5},
            tgt={"Электросталь": 5},
            package_type="BOX",
        )
    ]
    draft = await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, _build_payload([wh_a], ["Электросталь"], rows)
    )
    await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    resp = await assembly_draft_service.commit_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX")
    req_id = resp.created_request_ids[0]

    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id == req_id))
    assert res.scalar_one().source_draft_id == draft.id

    # list по своему draft_id — только эта заявка
    items, total = await assembly_service.list_assembly_requests(db_session, PROJECT_ID, draft_id=draft.id)
    assert [r.id for r in items] == [req_id]
    assert total == 1

    # list по чужому draft_id — пусто
    items2, total2 = await assembly_service.list_assembly_requests(db_session, PROJECT_ID, draft_id=draft.id + 999_999)
    assert items2 == [] and total2 == 0


# ─── Tests: created-groups («Предпросмотр созданных») ────────────────────────


@pytest.mark.asyncio
async def test_get_created_groups_groups_by_draft(db_session, setup_newcomer_nomenclature):
    """get_created_groups группирует IN_PROGRESS-заявки по source_draft_id; имя
    черновика резолвится даже после soft-delete (полный коммит). Изоляция по project."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(
            nm_id=REGULAR_NM_ID,
            barcode=REGULAR_BARCODE,
            src={str(wh_a): 10},
            tgt={"Электросталь": 6, "Казань": 4},
            package_type="BOX",
        ),
    ]
    payload = _build_payload([wh_a], ["Электросталь", "Казань"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert len(resp.created_request_ids) == 2  # Электросталь + Казань

    groups = await assembly_service.get_created_groups(db_session, PROJECT_ID)
    grp = next(g for g in groups if g["draft_id"] == draft.id)
    assert grp["request_count"] == 2
    assert grp["total_qty"] == 10
    assert grp["total_sku"] == 1  # один nm_id на обе заявки
    assert grp["draft_name"]  # имя резолвится после soft-delete черновика
    assert {r["wb_name"] for r in grp["requests"]} == {"Электросталь", "Казань"}

    # Изоляция: в другом проекте этой группы нет.
    other = await assembly_service.get_created_groups(db_session, OTHER_PROJECT_ID)
    assert all(g["draft_id"] != draft.id for g in other)


# ─── Tests: Merge ────────────────────────────────────────────────────────────


def test_merge_request_schema_validates():
    """AssemblyDraftMergeRequest enforces ≥2 distinct ids at schema level."""
    # Valid
    req = AssemblyDraftMergeRequest(draft_ids=[1, 2, 3])
    assert req.draft_ids == [1, 2, 3]

    # Single id → 422 via min_length
    with pytest.raises(ValidationError):
        AssemblyDraftMergeRequest(draft_ids=[1])

    # Duplicates deduplicated → only 1 unique → ValueError → ValidationError
    with pytest.raises(ValidationError):
        AssemblyDraftMergeRequest(draft_ids=[5, 5])

    # Duplicates with ≥2 unique: dedup preserves order, keeps all distinct
    req2 = AssemblyDraftMergeRequest(draft_ids=[3, 1, 3, 2])
    assert req2.draft_ids == [3, 1, 2]


@pytest.mark.asyncio
async def test_merge_drafts_sums_overlapping_rows(db_session):
    """Строки с совпадающим (nm_id, package_type) суммируются поэлементно."""
    wh_a, _ = await _get_warehouse_ids(db_session)

    d1 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Электросталь"],
            [
                AssemblyDraftRow(nm_id=1, barcode="MRG-A", src={str(wh_a): 100}, tgt={"Электросталь": 100}),
            ],
        ),
    )
    d2 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Электросталь"],
            [
                AssemblyDraftRow(nm_id=1, barcode="MRG-A", src={str(wh_a): 50}, tgt={"Электросталь": 50}),
            ],
        ),
    )

    # Both 1 row → tie → survivor = d1 (lower id)
    merged = await assembly_draft_service.merge_drafts(db_session, PROJECT_ID, [d1.id, d2.id])

    assert merged.id == d1.id
    dist = AssemblyDraftDistribution.model_validate(merged.distribution)
    assert len(dist.rows) == 1
    row = dist.rows[0]
    assert row.nm_id == 1
    assert row.src[str(wh_a)] == 150  # 100 + 50
    assert row.tgt["Электросталь"] == 150

    # d2 soft-deleted, d1 still alive
    await db_session.refresh(d2)
    assert d2.is_deleted is True
    await db_session.refresh(merged)
    assert merged.is_deleted is False


@pytest.mark.asyncio
async def test_merge_drafts_unions_distinct_rows(db_session):
    """Строки с разными nm_id просто объединяются (не суммируются)."""
    wh_a, _ = await _get_warehouse_ids(db_session)

    d1 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Электросталь"],
            [
                AssemblyDraftRow(nm_id=10, barcode="MRG-B1", src={str(wh_a): 20}, tgt={"Электросталь": 20}),
            ],
        ),
    )
    d2 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Казань"],
            [
                AssemblyDraftRow(nm_id=20, barcode="MRG-B2", src={str(wh_a): 30}, tgt={"Казань": 30}),
            ],
        ),
    )

    merged = await assembly_draft_service.merge_drafts(db_session, PROJECT_ID, [d1.id, d2.id])

    dist = AssemblyDraftDistribution.model_validate(merged.distribution)
    assert len(dist.rows) == 2
    nm_ids = {r.nm_id for r in dist.rows}
    assert nm_ids == {10, 20}


@pytest.mark.asyncio
async def test_merge_drafts_preserves_package_type_split(db_session):
    """Одинаковый nm_id с разным package_type → отдельные строки, не суммируются."""
    wh_a, _ = await _get_warehouse_ids(db_session)

    d1 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Электросталь"],
            [
                AssemblyDraftRow(
                    nm_id=5, barcode="MRG-C", package_type="BOX", src={str(wh_a): 10}, tgt={"Электросталь": 10}
                ),
            ],
        ),
    )
    d2 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Электросталь"],
            [
                AssemblyDraftRow(
                    nm_id=5, barcode="MRG-C", package_type="MONOPALLET", src={str(wh_a): 10}, tgt={"Электросталь": 10}
                ),
            ],
        ),
    )

    merged = await assembly_draft_service.merge_drafts(db_session, PROJECT_ID, [d1.id, d2.id])

    dist = AssemblyDraftDistribution.model_validate(merged.distribution)
    assert len(dist.rows) == 2
    pkgs = {r.package_type for r in dist.rows}
    assert pkgs == {"BOX", "MONOPALLET"}


@pytest.mark.asyncio
async def test_merge_drafts_unions_source_and_target(db_session):
    """source_warehouse_ids и target_warehouse_names объединяются (union)."""
    wh_a, wh_b = await _get_warehouse_ids(db_session)

    d1 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Электросталь"],
            [
                AssemblyDraftRow(nm_id=7, barcode="MRG-D", src={str(wh_a): 5}, tgt={"Электросталь": 5}),
            ],
        ),
    )
    d2 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_b],
            ["Казань"],
            [
                AssemblyDraftRow(nm_id=8, barcode="MRG-E", src={str(wh_b): 5}, tgt={"Казань": 5}),
            ],
        ),
    )

    merged = await assembly_draft_service.merge_drafts(db_session, PROJECT_ID, [d1.id, d2.id])

    dist = AssemblyDraftDistribution.model_validate(merged.distribution)
    assert set(dist.source_warehouse_ids) == {wh_a, wh_b}
    assert set(dist.target_warehouse_names) == {"Электросталь", "Казань"}


@pytest.mark.asyncio
async def test_merge_drafts_drops_cold_start_shares(db_session):
    """cold_start_shares сбрасывается до None при слиянии."""
    wh_a, _ = await _get_warehouse_ids(db_session)

    # Создаём черновик с cold_start_shares
    d1 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        AssemblyDraftCreate(
            name="Cold Start Draft",
            distribution=AssemblyDraftDistribution(
                source_warehouse_ids=[wh_a],
                target_warehouse_names=["Электросталь", "Казань"],
                rows=[AssemblyDraftRow(nm_id=9, barcode="MRG-F", src={str(wh_a): 10}, tgt={"Электросталь": 10})],
                cold_start_shares={"Электросталь": 0.7, "Казань": 0.3},
            ),
        ),
    )
    d2 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Казань"],
            [
                AssemblyDraftRow(nm_id=11, barcode="MRG-G", src={str(wh_a): 5}, tgt={"Казань": 5}),
            ],
        ),
    )

    merged = await assembly_draft_service.merge_drafts(db_session, PROJECT_ID, [d1.id, d2.id])

    dist = AssemblyDraftDistribution.model_validate(merged.distribution)
    assert dist.cold_start_shares is None


@pytest.mark.asyncio
async def test_merge_drafts_survivor_has_most_rows(db_session):
    """Survivor = черновик с наибольшим числом строк (tie-break: наименьший id)."""
    wh_a, _ = await _get_warehouse_ids(db_session)

    d1 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Электросталь"],
            [
                AssemblyDraftRow(nm_id=41, barcode="MRG-H1", src={str(wh_a): 1}, tgt={"Электросталь": 1}),
            ],
        ),
    )
    d2 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Казань"],
            [
                AssemblyDraftRow(nm_id=42, barcode="MRG-H2", src={str(wh_a): 1}, tgt={"Казань": 1}),
                AssemblyDraftRow(nm_id=43, barcode="MRG-H3", src={str(wh_a): 1}, tgt={"Казань": 1}),
                AssemblyDraftRow(nm_id=44, barcode="MRG-H4", src={str(wh_a): 1}, tgt={"Казань": 1}),
            ],
        ),
    )

    merged = await assembly_draft_service.merge_drafts(db_session, PROJECT_ID, [d1.id, d2.id])

    # d2 has more rows → survivor
    assert merged.id == d2.id
    dist = AssemblyDraftDistribution.model_validate(merged.distribution)
    assert len(dist.rows) == 4  # 3 from d2 + 1 from d1

    await db_session.refresh(d1)
    assert d1.is_deleted is True


def _draft_with_handed(
    wh: int, name: str, rows: list[AssemblyDraftRow], units: list[HandedUnit]
) -> AssemblyDraftCreate:
    """Payload-хелпер: черновик с rows и handed_units."""
    return AssemblyDraftCreate(
        name=name,
        distribution=AssemblyDraftDistribution(
            source_warehouse_ids=[wh],
            target_warehouse_names=[u.target_wb_name for u in units] or ["Электросталь"],
            rows=rows,
            handed_units=units,
        ),
    )


@pytest.mark.asyncio
async def test_merge_drafts_carries_handed_units(db_session):
    """handed_units не-survivor черновика переносятся в survivor, не теряются."""
    wh_a, _ = await _get_warehouse_ids(db_session)

    # survivor (больше строк) без handed_units
    d_survivor = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Казань"],
            [
                AssemblyDraftRow(nm_id=50, barcode="MRG-I", src={str(wh_a): 5}, tgt={"Казань": 5}),
                AssemblyDraftRow(nm_id=51, barcode="MRG-I2", src={str(wh_a): 7}, tgt={"Казань": 7}),
            ],
        ),
    )
    # не-survivor с handed-юнитом
    d_handed = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _draft_with_handed(
            wh_a,
            "Handed Draft",
            [],
            [
                HandedUnit(
                    source_ff_id=wh_a,
                    target_wb_name="Электросталь",
                    package_type="BOX",
                    status="handed",
                    items=[HandedUnitItem(nm_id=1, barcode="X", vendor_code="V", qty=10)],
                )
            ],
        ),
    )

    merged = await assembly_draft_service.merge_drafts(db_session, PROJECT_ID, [d_survivor.id, d_handed.id])

    assert merged.id == d_survivor.id  # survivor = больше строк
    dist = AssemblyDraftDistribution.model_validate(merged.distribution)
    assert len(dist.handed_units) == 1
    u = dist.handed_units[0]
    assert u.status == "handed"
    assert u.target_wb_name == "Электросталь"
    assert sum(it.qty for it in u.items) == 10


@pytest.mark.asyncio
async def test_merge_drafts_dedups_handed_units_same_key(db_session):
    """Юниты с совпадающим (ff, wb, pkg) сливаются, позиции суммируются по баркоду."""
    wh_a, _ = await _get_warehouse_ids(db_session)

    def _unit(qty_x: int, qty_y: int) -> HandedUnit:
        return HandedUnit(
            source_ff_id=wh_a,
            target_wb_name="Казань",
            package_type="BOX",
            status="handed",
            items=[
                HandedUnitItem(nm_id=1, barcode="X", vendor_code="V", qty=qty_x),
                HandedUnitItem(nm_id=2, barcode="Y", vendor_code="V", qty=qty_y),
            ],
        )

    d1 = await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, _draft_with_handed(wh_a, "H1", [], [_unit(10, 3)])
    )
    d2 = await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, _draft_with_handed(wh_a, "H2", [], [_unit(5, 0)])
    )

    merged = await assembly_draft_service.merge_drafts(db_session, PROJECT_ID, [d1.id, d2.id])

    dist = AssemblyDraftDistribution.model_validate(merged.distribution)
    assert len(dist.handed_units) == 1  # один ключ → один юнит
    by_bc = {it.barcode: it.qty for it in dist.handed_units[0].items}
    assert by_bc == {"X": 15, "Y": 3}  # X: 10+5, Y: 3+0


@pytest.mark.asyncio
async def test_merge_drafts_handed_status_wins_over_draft(db_session):
    """При слиянии юнитов одного ключа статус 'handed' побеждает 'draft'."""
    wh_a, _ = await _get_warehouse_ids(db_session)

    def _unit(status: str) -> HandedUnit:
        return HandedUnit(
            source_ff_id=wh_a,
            target_wb_name="Казань",
            package_type="BOX",
            status=status,
            items=[HandedUnitItem(nm_id=1, barcode="X", vendor_code="V", qty=4)],
        )

    # survivor с замороженным draft-снимком (1 строка), other — handed того же ключа (0 строк)
    d_survivor = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _draft_with_handed(
            wh_a,
            "S",
            [AssemblyDraftRow(nm_id=99, barcode="R", src={str(wh_a): 1}, tgt={"Казань": 1})],
            [_unit("draft")],
        ),
    )
    d_other = await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, _draft_with_handed(wh_a, "O", [], [_unit("handed")])
    )

    merged = await assembly_draft_service.merge_drafts(db_session, PROJECT_ID, [d_survivor.id, d_other.id])

    assert merged.id == d_survivor.id
    dist = AssemblyDraftDistribution.model_validate(merged.distribution)
    assert len(dist.handed_units) == 1
    assert dist.handed_units[0].status == "handed"
    assert sum(it.qty for it in dist.handed_units[0].items) == 8  # 4 + 4


@pytest.mark.asyncio
async def test_merge_drafts_404_if_draft_missing(db_session):
    """Хоть один несуществующий id → 404."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    d1 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Электросталь"],
            [
                AssemblyDraftRow(nm_id=60, barcode="MRG-J", src={str(wh_a): 1}, tgt={"Электросталь": 1}),
            ],
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await assembly_draft_service.merge_drafts(db_session, PROJECT_ID, [d1.id, 999_999_999])
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_merge_drafts_project_isolation(db_session):
    """Нельзя объединить черновик из другого проекта — 404."""
    wh_a, _ = await _get_warehouse_ids(db_session)

    d_p1 = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Электросталь"],
            [
                AssemblyDraftRow(nm_id=70, barcode="MRG-K", src={str(wh_a): 1}, tgt={"Электросталь": 1}),
            ],
        ),
    )

    # Черновик в OTHER_PROJECT_ID
    d_other = AssemblyDraft(
        project_id=OTHER_PROJECT_ID,
        name="Other Draft",
        distribution=AssemblyDraftDistribution(
            source_warehouse_ids=[],
            target_warehouse_names=["Казань"],
            rows=[],
        ).model_dump(),
        comment=None,
    )
    db_session.add(d_other)
    await db_session.commit()
    await db_session.refresh(d_other)

    with pytest.raises(HTTPException) as exc:
        await assembly_draft_service.merge_drafts(db_session, PROJECT_ID, [d_p1.id, d_other.id])
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_merge_drafts_survivor_rows_none(db_session):
    """_row_count не падает когда distribution['rows'] == null (а не отсутствует)."""
    wh_a, _ = await _get_warehouse_ids(db_session)

    # Черновик с явным rows=None в JSON
    d_null = AssemblyDraft(
        project_id=PROJECT_ID,
        name="Null-rows draft",
        distribution={"source_warehouse_ids": [], "target_warehouse_names": [], "rows": None},
        comment=None,
    )
    d_normal = AssemblyDraft(
        project_id=PROJECT_ID,
        name="Normal draft",
        distribution=AssemblyDraftDistribution(
            source_warehouse_ids=[wh_a],
            target_warehouse_names=["Электросталь"],
            rows=[AssemblyDraftRow(nm_id=99, barcode="NULL-R", src={str(wh_a): 5}, tgt={"Электросталь": 5})],
        ).model_dump(mode="json"),
        comment=None,
    )
    db_session.add_all([d_null, d_normal])
    await db_session.commit()
    await db_session.refresh(d_null)
    await db_session.refresh(d_normal)

    # Should not raise — d_normal wins as survivor (1 row > 0)
    merged = await assembly_draft_service.merge_drafts(db_session, PROJECT_ID, [d_null.id, d_normal.id])
    assert merged.id == d_normal.id


# ─── Tests: duplicate-row dedupe ────────────────────────────────────────────


def test_dedupe_rows_keeps_first_per_nm_pkg():
    """_dedupe_rows схлопывает дубли (nm_id, package_type), оставляя первую строку."""
    rows = [
        AssemblyDraftRow(nm_id=1, barcode="b1", src={"10": 5}, tgt={"WB": 5}),
        AssemblyDraftRow(nm_id=1, barcode="b1", src={"10": 2}, tgt={"WB": 2}),  # dup → drop
        AssemblyDraftRow(nm_id=1, barcode="b1", src={"10": 3}, tgt={"WB": 3}, package_type="MONOPALLET"),
        AssemblyDraftRow(nm_id=2, barcode="b2", src={"10": 1}, tgt={"WB": 1}),
    ]
    out = assembly_draft_service._dedupe_rows(rows)
    assert {(r.nm_id, r.package_type or "BOX") for r in out} == {(1, "BOX"), (1, "MONOPALLET"), (2, "BOX")}
    box1 = next(r for r in out if r.nm_id == 1 and (r.package_type or "BOX") == "BOX")
    assert box1.src == {"10": 5}  # keep-first (не вторая строка с src 2)


@pytest.mark.asyncio
async def test_create_draft_dedupes_duplicate_rows(db_session):
    """create_draft схлопывает дубли строк — в БД одна строка на (nm_id, package_type)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    row = AssemblyDraftRow(nm_id=222, barcode=TEST_BARCODE_1, src={str(wh_a): 10}, tgt={"Казань": 10})
    payload = _build_payload([wh_a], ["Казань"], [row, row.model_copy(deep=True)])
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    dist = AssemblyDraftDistribution.model_validate(draft.distribution)
    assert len(dist.rows) == 1


@pytest.mark.asyncio
async def test_commit_draft_dedupes_stale_duplicate_rows(db_session):
    """commit не задваивает отгрузку для черновика с уже сохранёнными дублями.

    Старые черновики (до фикса генерации) хранят задвоенные строки; commit
    складывает qty по баркоду в корзину (ФФ, WB, упаковка) → без дедупа отгрузка
    удвоилась бы. Дубль вписываем через ORM напрямую, минуя дедуп create/update.
    """
    wh_a, _ = await _get_warehouse_ids(db_session)
    row = AssemblyDraftRow(nm_id=333, barcode=TEST_BARCODE_1, src={str(wh_a): 10}, tgt={"Казань": 10})
    payload = _build_payload([wh_a], ["Казань"], [row])
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    # Впишем дубль напрямую (как в битом черновике), минуя дедуп сервиса.
    draft_obj = await assembly_draft_service.get_draft(db_session, PROJECT_ID, draft.id)
    dist = AssemblyDraftDistribution.model_validate(draft_obj.distribution)
    dist.rows = [row, row.model_copy(deep=True)]
    draft_obj.distribution = dist.model_dump(mode="json")
    await db_session.commit()

    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert len(resp.created_request_ids) == 1

    item_q = await db_session.execute(
        text("SELECT barcode, quantity FROM assembly_request_items " "WHERE assembly_request_id = ANY(:ids)"),
        {"ids": resp.created_request_ids},
    )
    items = item_q.all()
    assert len(items) == 1
    assert items[0].quantity == 10  # дубль схлопнут (НЕ 20)


# ─── Tests: add_rows_to_draft (дозалив строк в существующий черновик) ─────────


@pytest.mark.asyncio
async def test_add_rows_appends_new_nm_pkg(db_session):
    """Дозалив строки с НОВЫМ (nm_id, package_type) → она появляется в черновике,
    исходные строки сохраняются, source/target склады объединяются."""
    wh_a, wh_b = await _get_warehouse_ids(db_session)
    payload = _build_payload(
        [wh_a],
        ["Электросталь"],
        [
            AssemblyDraftRow(
                nm_id=111,
                barcode=TEST_BARCODE_1,
                src={str(wh_a): 5},
                tgt={"Электросталь": 5},
                package_type="BOX",
            )
        ],
    )
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    new_rows = AssemblyDraftAddRows(
        rows=[
            AssemblyDraftRow(
                nm_id=222,
                barcode=TEST_BARCODE_2,
                src={str(wh_b): 3},
                tgt={"Казань": 3},
                package_type="BOX",
            )
        ]
    )
    updated = await assembly_draft_service.add_rows_to_draft(db_session, PROJECT_ID, draft.id, new_rows.rows)
    dist = AssemblyDraftDistribution.model_validate(updated.distribution)
    by_key = {(r.nm_id, r.package_type or "BOX"): r for r in dist.rows}
    assert set(by_key) == {(111, "BOX"), (222, "BOX")}
    # Исходная строка нетронута, новая добавлена.
    assert by_key[(111, "BOX")].src == {str(wh_a): 5}
    assert by_key[(222, "BOX")].tgt == {"Казань": 3}
    # Склады объединены.
    assert set(dist.source_warehouse_ids) == {wh_a, wh_b}
    assert set(dist.target_warehouse_names) == {"Электросталь", "Казань"}


@pytest.mark.asyncio
async def test_add_rows_sums_existing_nm_pkg(db_session):
    """Дозалив строки с СУЩЕСТВУЮЩИМ (nm_id, package_type) → src/tgt СУММИРУЮТСЯ
    поэлементно (не отброшены keep-first, не заменены full-replace)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    payload = _build_payload(
        [wh_a],
        ["Электросталь"],
        [
            AssemblyDraftRow(
                nm_id=111,
                barcode=TEST_BARCODE_1,
                src={str(wh_a): 100},
                tgt={"Электросталь": 100},
                package_type="BOX",
            )
        ],
    )
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    rows = [
        AssemblyDraftRow(
            nm_id=111,
            barcode=TEST_BARCODE_1,
            src={str(wh_a): 50},
            tgt={"Электросталь": 30, "Казань": 20},
            package_type="BOX",
        )
    ]
    updated = await assembly_draft_service.add_rows_to_draft(db_session, PROJECT_ID, draft.id, rows)
    dist = AssemblyDraftDistribution.model_validate(updated.distribution)
    assert len(dist.rows) == 1  # тот же ключ → одна строка
    row = dist.rows[0]
    assert row.nm_id == 111
    assert row.src[str(wh_a)] == 150  # 100 + 50 (просуммировано)
    assert row.tgt["Электросталь"] == 130  # 100 + 30
    assert row.tgt["Казань"] == 20  # новый wb-ключ добавлен
    assert "Казань" in dist.target_warehouse_names


@pytest.mark.asyncio
async def test_add_rows_distinct_barcodes_same_nm_not_collapsed(db_session):
    """Один nm_id с РАЗНЫМИ баркодами (размерные варианты / nm_id=0 у карточек без
    article_wb) НЕ схлопывается в одну строку: каждый баркод остаётся отдельной
    строкой со своим qty. Иначе commit отгрузил бы чужой физический товар.
    Ключ merge/dedupe = (nm_id, package_type, barcode)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    payload = _build_payload(
        [wh_a],
        ["Электросталь"],
        [
            AssemblyDraftRow(
                nm_id=111,
                barcode=TEST_BARCODE_1,
                src={str(wh_a): 50},
                tgt={"Электросталь": 50},
                package_type="BOX",
            )
        ],
    )
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    rows = [
        AssemblyDraftRow(
            nm_id=111,  # тот же nm_id…
            barcode=TEST_BARCODE_2,  # …но ДРУГОЙ баркод
            src={str(wh_a): 30},
            tgt={"Электросталь": 30},
            package_type="BOX",
        )
    ]
    updated = await assembly_draft_service.add_rows_to_draft(db_session, PROJECT_ID, draft.id, rows)
    dist = AssemblyDraftDistribution.model_validate(updated.distribution)
    assert len(dist.rows) == 2  # два баркода → две строки, НЕ схлопнуты
    by_bc = {r.barcode: r for r in dist.rows}
    assert by_bc[TEST_BARCODE_1].src[str(wh_a)] == 50
    assert by_bc[TEST_BARCODE_1].tgt["Электросталь"] == 50
    assert by_bc[TEST_BARCODE_2].src[str(wh_a)] == 30
    assert by_bc[TEST_BARCODE_2].tgt["Электросталь"] == 30


@pytest.mark.asyncio
async def test_add_rows_preserves_handed_units(db_session):
    """Черновик с handed_units → дозалив строк не трогает замороженные юниты,
    cold_start_shares и pallets_count тоже не изменяются."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    handed = [
        HandedUnit(
            source_ff_id=wh_a,
            target_wb_name="Электросталь",
            package_type="BOX",
            status="handed",
            items=[HandedUnitItem(nm_id=1, barcode="HX", vendor_code="V", qty=10)],
        )
    ]
    rows = [
        AssemblyDraftRow(
            nm_id=222,
            barcode=TEST_BARCODE_2,
            src={str(wh_a): 4},
            tgt={"Казань": 4},
            package_type="BOX",
        )
    ]
    create = AssemblyDraftCreate(
        name="With Handed",
        distribution=AssemblyDraftDistribution(
            source_warehouse_ids=[wh_a],
            target_warehouse_names=["Электросталь", "Казань"],
            rows=rows,
            handed_units=handed,
            cold_start_shares={"Электросталь": 0.6, "Казань": 0.4},
            pallets_count=3,
        ),
    )
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, create)

    new_rows = [
        AssemblyDraftRow(
            nm_id=333,
            barcode=TEST_BARCODE_1,
            src={str(wh_a): 7},
            tgt={"Казань": 7},
            package_type="BOX",
        )
    ]
    updated = await assembly_draft_service.add_rows_to_draft(db_session, PROJECT_ID, draft.id, new_rows)
    dist = AssemblyDraftDistribution.model_validate(updated.distribution)

    # handed_units нетронуты.
    assert len(dist.handed_units) == 1
    u = dist.handed_units[0]
    assert u.status == "handed"
    assert u.target_wb_name == "Электросталь"
    assert sum(it.qty for it in u.items) == 10
    # cold_start_shares и pallets_count сохранены.
    assert dist.cold_start_shares == {"Электросталь": 0.6, "Казань": 0.4}
    assert dist.pallets_count == 3
    # Новая строка добавлена к существующей.
    assert {(r.nm_id, r.package_type or "BOX") for r in dist.rows} == {(222, "BOX"), (333, "BOX")}


# ─── Tests: _allocate_pairs сохраняет ОБА маргинала ──────────────────────────


def _assert_marginals(src: dict[str, int], tgt: dict[str, int]) -> None:
    """Раскладка по парам (ФФ→WB) сохраняет per-ФФ и per-склад суммы (балансовая)."""
    alloc = assembly_draft_service._allocate_pairs(src, tgt)
    per_src: dict[int, int] = {}
    per_tgt: dict[str, int] = {}
    for (sid, tname), q in alloc.items():
        assert q > 0
        per_src[sid] = per_src.get(sid, 0) + q
        per_tgt[tname] = per_tgt.get(tname, 0) + q
    assert per_src == {int(k): v for k, v in src.items() if v > 0}
    assert per_tgt == {k: v for k, v in tgt.items() if v > 0}


def test_allocate_pairs_preserves_both_marginals():
    """Регресс: каждый ФФ отгружает ровно свой запас, каждый склад получает ровно
    свою потребность. Старая joint-pro-rata на src={1:1,2:1}, tgt={A:1,B:1} давала
    ФФ1=2, ФФ2=0 → commit создавал заявку на склад без остатка."""
    # Контрпример старого алгоритма: оба ФФ должны отгрузить по 1.
    alloc = assembly_draft_service._allocate_pairs({"1": 1, "2": 1}, {"A": 1, "B": 1})
    per_src: dict[int, int] = {}
    for (sid, _), q in alloc.items():
        per_src[sid] = per_src.get(sid, 0) + q
    assert per_src == {1: 1, 2: 1}

    # Прочие сбалансированные сетки — оба маргинала точны.
    _assert_marginals({"1": 1, "2": 1}, {"A": 1, "B": 1})
    _assert_marginals({"10": 6, "20": 4}, {"Электросталь": 5, "Казань": 5})
    _assert_marginals({"10": 10}, {"A": 6, "B": 4})
    _assert_marginals({"1": 7, "2": 11, "3": 5}, {"X": 9, "Y": 9, "Z": 5})
    _assert_marginals({"5": 3, "1": 100}, {"Z": 50, "A": 53})  # ключи не отсортированы


def test_allocate_pairs_empty():
    """Пустые src/tgt → пустая раскладка."""
    assert assembly_draft_service._allocate_pairs({}, {"A": 5}) == {}
    assert assembly_draft_service._allocate_pairs({"1": 5}, {}) == {}


# ─── Tests: идемпотентность синглтона и коммита ──────────────────────────────


@pytest.mark.asyncio
async def test_current_draft_idempotent_after_merge(db_session):
    """После консолидации повторный вход возвращает тот же survivor, без новых
    мутаций: ровно один активный, тот же id, строки обоих исходных сохранены."""
    wh_a, wh_b = await _get_warehouse_ids(db_session)
    await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Электросталь"],
            [AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, src={str(wh_a): 5}, tgt={"Электросталь": 5})],
        ),
    )
    await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_b],
            ["Казань"],
            [AssemblyDraftRow(nm_id=222, barcode=TEST_BARCODE_2, src={str(wh_b): 3}, tgt={"Казань": 3})],
        ),
    )
    first = await assembly_draft_service.get_or_create_current_draft(db_session, PROJECT_ID)
    second = await assembly_draft_service.get_or_create_current_draft(db_session, PROJECT_ID)
    assert second.id == first.id  # стабильный синглтон, второй вход не плодит/не сливает заново

    actives = await assembly_draft_service.list_drafts(db_session, PROJECT_ID)
    assert len(actives) == 1
    dist = AssemblyDraftDistribution.model_validate(second.distribution)
    assert {r.nm_id for r in dist.rows} == {111, 222}  # строки не потеряны на 2-м входе


@pytest.mark.asyncio
async def test_remove_rows_by_nm(db_session):
    """remove_rows_by_nm убирает строки указанных SKU, остальные сохраняет."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, src={str(wh_a): 5}, tgt={"Электросталь": 5}),
        AssemblyDraftRow(nm_id=222, barcode=TEST_BARCODE_2, src={str(wh_a): 3}, tgt={"Казань": 3}),
    ]
    draft = await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, _build_payload([wh_a], ["Электросталь", "Казань"], rows)
    )

    updated = await assembly_draft_service.remove_rows_by_nm(db_session, PROJECT_ID, draft.id, [111])
    dist = AssemblyDraftDistribution.model_validate(updated.distribution)
    assert {r.nm_id for r in dist.rows} == {222}  # 111 убран, 222 остался


@pytest.mark.asyncio
async def test_remove_rows_clears_handed_units(db_session):
    """remove_rows_by_nm чистит handed-юниты от удаляемых SKU; пустые юниты дропаются."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    handed = [
        HandedUnit(
            source_ff_id=wh_a, target_wb_name="Электросталь", package_type="BOX", status="handed",
            items=[
                HandedUnitItem(nm_id=111, barcode=TEST_BARCODE_1, vendor_code="", qty=5),
                HandedUnitItem(nm_id=222, barcode=TEST_BARCODE_2, vendor_code="", qty=3),
            ],
        )
    ]
    create = AssemblyDraftCreate(
        name="H", distribution=AssemblyDraftDistribution(
            source_warehouse_ids=[wh_a], target_warehouse_names=["Электросталь"], rows=[], handed_units=handed,
        ),
    )
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, create)

    updated = await assembly_draft_service.remove_rows_by_nm(db_session, PROJECT_ID, draft.id, [111])
    dist = AssemblyDraftDistribution.model_validate(updated.distribution)
    assert len(dist.handed_units) == 1  # юнит остался (есть 222)
    assert {it.nm_id for it in dist.handed_units[0].items} == {222}  # 111 вычищен


@pytest.mark.asyncio
async def test_remove_rows_clears_prebook(db_session):
    """remove_rows_by_nm чистит и предбронь удаляемых SKU.

    Кейс «швабры» 2026-07-10: rows вычищались, а предбронь (348 шт) оставалась
    жить в черновике навсегда — её не трогала ни матрица (владеет только своими
    SKU), ни этот endpoint."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, src={str(wh_a): 5}, tgt={"Электросталь": 5})]
    prebook = [
        AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, src={str(wh_a): 8}, tgt={"Электросталь": 8}),
        AssemblyDraftRow(nm_id=222, barcode=TEST_BARCODE_2, src={str(wh_a): 3}, tgt={"Казань": 3}),
    ]
    create = AssemblyDraftCreate(
        name="P",
        distribution=AssemblyDraftDistribution(
            source_warehouse_ids=[wh_a],
            target_warehouse_names=["Электросталь", "Казань"],
            rows=rows,
            prebook=prebook,
        ),
    )
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, create)

    updated = await assembly_draft_service.remove_rows_by_nm(db_session, PROJECT_ID, draft.id, [111])
    dist = AssemblyDraftDistribution.model_validate(updated.distribution)
    assert dist.rows == []
    assert {r.nm_id for r in dist.prebook} == {222}


@pytest.mark.asyncio
async def test_remove_rows_clears_prebook_origin_and_logs_event(db_session):
    """remove_rows_by_nm чистит бейджи prebook_origin (ключ nmId::wb) удаляемых SKU
    и пишет событие REMOVE_ROWS со снапшотом для отката (аудит 2026-07-11)."""
    from sqlalchemy import select

    from backend.models.assembly import AssemblyDraftEvent

    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, src={str(wh_a): 5}, tgt={"Электросталь": 5})]
    create = AssemblyDraftCreate(
        name="PO",
        distribution=AssemblyDraftDistribution(
            source_warehouse_ids=[wh_a],
            target_warehouse_names=["Электросталь"],
            rows=rows,
            prebook_origin=["111::Электросталь", "222::Казань"],
        ),
    )
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, create)

    updated = await assembly_draft_service.remove_rows_by_nm(db_session, PROJECT_ID, draft.id, [111])
    dist = AssemblyDraftDistribution.model_validate(updated.distribution)
    assert dist.prebook_origin == ["222::Казань"]  # бейдж удалённого SKU вычищен

    events = (await db_session.execute(
        select(AssemblyDraftEvent).where(AssemblyDraftEvent.draft_id == draft.id)
    )).scalars().all()
    assert any(e.event_type == "REMOVE_ROWS" for e in events)
    ev = next(e for e in events if e.event_type == "REMOVE_ROWS")
    assert ev.before_distribution and len(ev.before_distribution.get("rows", [])) == 1  # снапшот для отката


@pytest.mark.asyncio
async def test_manual_nms_persist_through_update_and_remove(db_session):
    """manual_nms (ручные SKU авто-синка матрицы) персистятся через update_draft
    и переживают remove_rows_by_nm (модель прогоняется через model_validate)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, src={str(wh_a): 4}, tgt={"Электросталь": 4})]
    draft = await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, _build_payload([wh_a], ["Электросталь"], rows)
    )
    dist = AssemblyDraftDistribution.model_validate(draft.distribution)
    dist.manual_nms = [111, 222]
    updated = await assembly_draft_service.update_draft(
        db_session, PROJECT_ID, draft.id, AssemblyDraftUpdate(distribution=dist)
    )
    assert AssemblyDraftDistribution.model_validate(updated.distribution).manual_nms == [111, 222]

    after_remove = await assembly_draft_service.remove_rows_by_nm(db_session, PROJECT_ID, draft.id, [111])
    assert AssemblyDraftDistribution.model_validate(after_remove.distribution).manual_nms == [111, 222]


@pytest.mark.asyncio
async def test_remove_rows_404(db_session):
    """remove_rows_by_nm на несуществующем черновике → 404."""
    with pytest.raises(HTTPException) as ei:
        await assembly_draft_service.remove_rows_by_nm(db_session, PROJECT_ID, 999_999_999, [1])
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_commit_draft_twice_is_404(db_session):
    """Повторный commit того же черновика → 404 (первый его soft-delete'нул);
    заявки не задваиваются (идемпотентный контракт, под FOR UPDATE — без гонки)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    rows = [
        AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, src={str(wh_a): 4}, tgt={"Электросталь": 4}),
    ]
    payload = _build_payload([wh_a], ["Электросталь"], rows)
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert len(resp.created_request_ids) == 1

    with pytest.raises(HTTPException) as ei:
        await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert ei.value.status_code == 404


# ─── _draft_nm_ids: rows + prebook (гистерезис признака новинки) ─────────────


def test_draft_nm_ids_includes_prebook():
    """nm_id собираются и из rows, И из prebook.

    SKU, лежащий только в предброни, — часть черновика: без него новинка из
    prebook выпадала из newcomer_nm_ids → фронт терял бейдж 🆕 и newcomer-логику
    («одинаковые» товары вели себя по-разному в rows vs prebook).
    """
    draft = AssemblyDraft(
        project_id=PROJECT_ID,
        name="t",
        distribution={
            "rows": [{"nm_id": 111, "src": {}, "tgt": {}}],
            "prebook": [{"nm_id": 222, "src": {}, "tgt": {}}, {"nm_id": "мусор"}],
        },
    )
    assert assembly_draft_service._draft_nm_ids(draft) == {111, 222}


def test_draft_nm_ids_tolerates_missing_parts():
    """Отсутствующие/null rows и prebook не роняют чтение."""
    draft = AssemblyDraft(project_id=PROJECT_ID, name="t", distribution={"rows": None})
    assert assembly_draft_service._draft_nm_ids(draft) == set()


# ─── Tests: commit-дедуп (фикс дубля «дозабор из предброни») ─────────────────


@pytest.mark.asyncio
async def test_commit_draft_dedup_folds_second_commit_same_direction(db_session):
    """Повторный commit ТОГО ЖЕ черновика на то же направление доливает позиции в
    уже созданную сборку, а не плодит дубль (регресс: авто-раскладку закоммитили,
    потом «дозабили из предброни» и закоммитили снова тем же черновиком)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    # Предбронь → черновик переживает первый commit (rows очистятся, prebook сохранится).
    dist = AssemblyDraftDistribution(
        source_warehouse_ids=[wh_a],
        target_warehouse_names=["Тула"],
        rows=[AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, src={str(wh_a): 6}, tgt={"Тула": 6})],
        prebook=[AssemblyDraftRow(nm_id=222, barcode=TEST_BARCODE_2, src={str(wh_a): 3}, tgt={"Тула": 3})],
        pallets_count=1,
        pallet_weight_kg=100.0,
    )
    draft = await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, AssemblyDraftCreate(name="D", distribution=dist, comment="t")
    )
    resp1 = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert len(resp1.created_request_ids) == 1
    first_id = resp1.created_request_ids[0]

    # «Дозабор из предброни»: доливаем строки на ТО ЖЕ направление в тот же черновик.
    await assembly_draft_service.add_rows_to_draft(
        db_session,
        PROJECT_ID,
        draft.id,
        [AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, src={str(wh_a): 4}, tgt={"Тула": 4})],
    )
    resp2 = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    # Дедуп: тот же request, не новый.
    assert resp2.created_request_ids == [first_id]

    # Ровно одна не-удалённая сборка на Тулу из этого черновика.
    res = await db_session.execute(
        select(AssemblyRequest).where(
            AssemblyRequest.source_draft_id == draft.id,
            AssemblyRequest.wb_warehouse_name_manual == "Тула",
            AssemblyRequest.is_deleted == False,  # noqa: E712
        )
    )
    reqs = list(res.scalars().all())
    assert len(reqs) == 1 and reqs[0].id == first_id

    # TEST_BARCODE_1 просуммирован: 6 (1-й commit) + 4 (дозабор) = 10.
    item_q = await db_session.execute(
        text("SELECT barcode, quantity FROM assembly_request_items WHERE assembly_request_id = :id"),
        {"id": first_id},
    )
    qty_by_bc = {r.barcode: r.quantity for r in item_q.all()}
    assert qty_by_bc[TEST_BARCODE_1] == 10


# ─── Tests: merge_assembly_requests (объединение созданных сборок) ───────────


async def _commit_one(db_session, wh: int, wb: str, items: dict[str, int], *, pallets: int = 1) -> int:
    """Создать одну сборку через commit отдельного черновика (без валидации стока).
    Возвращает id созданной AssemblyRequest."""
    nm_by_bc = {TEST_BARCODE_1: 111, TEST_BARCODE_2: 222}
    total = sum(items.values())
    rows = [
        AssemblyDraftRow(nm_id=nm_by_bc[bc], barcode=bc, src={str(wh): q}, tgt={wb: q}) for bc, q in items.items()
    ]
    dist = AssemblyDraftDistribution(
        source_warehouse_ids=[wh],
        target_warehouse_names=[wb],
        rows=rows,
        pallets_count=pallets,
        pallet_weight_kg=100.0,
    )
    assert total > 0
    draft = await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, AssemblyDraftCreate(name="D", distribution=dist, comment="t")
    )
    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert len(resp.created_request_ids) == 1
    return resp.created_request_ids[0]


@pytest.mark.asyncio
async def test_merge_assembly_requests_sums_and_soft_deletes(db_session):
    """merge: survivor — с наибольшим числом позиций; позиции суммируются, паллеты
    складываются, losers → soft-delete, число не-удалённых сборок падает."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    a = await _commit_one(db_session, wh_a, "Тула", {TEST_BARCODE_1: 6}, pallets=6)
    b = await _commit_one(db_session, wh_a, "Тула", {TEST_BARCODE_1: 4, TEST_BARCODE_2: 5}, pallets=1)

    survivor = await assembly_service.merge_assembly_requests(db_session, PROJECT_ID, [a, b])
    # Survivor — b (2 позиции > 1).
    assert survivor.id == b
    assert survivor.pallets_count == 7  # 6 + 1

    item_q = await db_session.execute(
        text("SELECT barcode, quantity FROM assembly_request_items WHERE assembly_request_id = :id"),
        {"id": b},
    )
    qty_by_bc = {r.barcode: r.quantity for r in item_q.all()}
    assert qty_by_bc[TEST_BARCODE_1] == 10  # 6 + 4
    assert qty_by_bc[TEST_BARCODE_2] == 5

    # Loser a — soft-deleted; его позиции удалены.
    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id == a))
    loser = res.scalar_one()
    assert loser.is_deleted is True
    cnt = await db_session.execute(
        text("SELECT count(*) FROM assembly_request_items WHERE assembly_request_id = :id"), {"id": a}
    )
    assert cnt.scalar() == 0


@pytest.mark.asyncio
async def test_merge_assembly_requests_different_direction_rejected(db_session):
    """Разные направления → 400 (нельзя объединять Тулу с Казанью)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    a = await _commit_one(db_session, wh_a, "Тула", {TEST_BARCODE_1: 6})
    c = await _commit_one(db_session, wh_a, "Казань", {TEST_BARCODE_1: 4})
    with pytest.raises(ValueError, match="одного склада"):
        await assembly_service.merge_assembly_requests(db_session, PROJECT_ID, [a, c])


@pytest.mark.asyncio
async def test_merge_assembly_requests_missing_id_rejected(db_session):
    """Несуществующий id (в т.ч. чужой проект) → «не найдены»."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    a = await _commit_one(db_session, wh_a, "Тула", {TEST_BARCODE_1: 6})
    with pytest.raises(ValueError, match="не найдены"):
        await assembly_service.merge_assembly_requests(db_session, PROJECT_ID, [a, 999_999_999])


# ─── Tests: история черновика + откат (draft_history) ───────────────────────


async def _make_draft(db_session, wh: int, wb: str, items: dict[str, int]):
    """Создать черновик (без коммита) с одним ФФ→WB. Возвращает объект AssemblyDraft."""
    nm_by_bc = {TEST_BARCODE_1: 111, TEST_BARCODE_2: 222}
    rows = [
        AssemblyDraftRow(nm_id=nm_by_bc[bc], barcode=bc, vendor_code=f"v-{bc}", src={str(wh): q}, tgt={wb: q})
        for bc, q in items.items()
    ]
    return await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, _build_payload([wh], [wb], rows)
    )


@pytest.mark.asyncio
async def test_commit_logs_event_and_revert_restores(db_session):
    """Коммит логирует COMMIT_REQUEST; откат удаляет заявку и возвращает строки в черновик."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    draft = await _make_draft(db_session, wh_a, "Электросталь", {TEST_BARCODE_1: 10})
    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    assert len(resp.created_request_ids) == 1

    hist = await draft_history.get_draft_history(db_session, PROJECT_ID, draft.id)
    assert len(hist.events) == 1
    ev = hist.events[0]
    assert ev.event_type == "COMMIT_REQUEST"
    assert ev.can_revert is True
    assert ev.created_request_ids == resp.created_request_ids

    rev = await draft_history.revert_draft_event(db_session, PROJECT_ID, draft.id, ev.id)
    assert rev.deleted_request_ids == resp.created_request_ids

    req = (
        await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id == resp.created_request_ids[0]))
    ).scalar_one()
    assert req.is_deleted is True

    # Черновик был soft-delete'нут после полного коммита → откат его восстановил + вернул строку.
    draft2 = await assembly_draft_service.get_draft(db_session, PROJECT_ID, draft.id)
    assert draft2 is not None
    dist = AssemblyDraftDistribution.model_validate(draft2.distribution)
    assert any(r.barcode == TEST_BARCODE_1 and sum(r.tgt.values()) == 10 for r in dist.rows)

    hist2 = await draft_history.get_draft_history(db_session, PROJECT_ID, draft.id)
    assert hist2.events[0].reverted_at is not None
    assert hist2.events[0].can_revert is False


@pytest.mark.asyncio
async def test_commit_revert_blocked_when_shipped(db_session):
    """Откат создания заявки заблокирован, если заявка уже WB-поставка (SHIPPED)."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    draft = await _make_draft(db_session, wh_a, "Тула", {TEST_BARCODE_1: 6})
    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id)
    rid = resp.created_request_ids[0]

    req = (await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id == rid))).scalar_one()
    req.status = "SHIPPED"
    await db_session.commit()

    hist = await draft_history.get_draft_history(db_session, PROJECT_ID, draft.id)
    ev = hist.events[0]
    assert ev.can_revert is False
    assert ev.revert_blocked_reason and "WB" in ev.revert_blocked_reason

    with pytest.raises(HTTPException) as exc:
        await draft_history.revert_draft_event(db_session, PROJECT_ID, draft.id, ev.id)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_topup_event_revert_and_version_guard(db_session):
    """PUT с event логирует PREBOOK_TOPUP; откат возвращает снапшот; изменение черновика блокирует откат."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    draft = await _make_draft(db_session, wh_a, "Электросталь", {TEST_BARCODE_1: 10})

    # «Дозабор»: PUT нового distribution (20 шт) с маркером события.
    bumped = AssemblyDraftDistribution(
        source_warehouse_ids=[wh_a],
        target_warehouse_names=["Электросталь"],
        rows=[AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, vendor_code="v", src={str(wh_a): 20}, tgt={"Электросталь": 20})],
        pallets_count=1,
        pallet_weight_kg=100.0,
    )
    await assembly_draft_service.update_draft(
        db_session, PROJECT_ID, draft.id,
        AssemblyDraftUpdate(distribution=bumped, event=DraftEventLog(event_type="PREBOOK_TOPUP", summary="дозабор")),
        changed_by="user",
    )
    hist = await draft_history.get_draft_history(db_session, PROJECT_ID, draft.id)
    ev = hist.events[0]
    assert ev.event_type == "PREBOOK_TOPUP"
    assert ev.can_revert is True

    # Откат возвращает 10 шт (снапшот before).
    await draft_history.revert_draft_event(db_session, PROJECT_ID, draft.id, ev.id)
    d = await assembly_draft_service.get_draft(db_session, PROJECT_ID, draft.id)
    dist = AssemblyDraftDistribution.model_validate(d.distribution)
    assert sum(dist.rows[0].tgt.values()) == 10

    # Новый top-up, затем ПОСТОРОННЕЕ изменение черновика → его откат становится недоступен.
    await assembly_draft_service.update_draft(
        db_session, PROJECT_ID, draft.id,
        AssemblyDraftUpdate(distribution=bumped, event=DraftEventLog(event_type="PREBOOK_TOPUP", summary="дозабор 2")),
        changed_by="user",
    )
    hist2 = await draft_history.get_draft_history(db_session, PROJECT_ID, draft.id)
    topup2 = hist2.events[0]
    assert topup2.can_revert is True

    # autosave без event — меняет updated_at.
    await assembly_draft_service.update_draft(
        db_session, PROJECT_ID, draft.id, AssemblyDraftUpdate(comment="touched"), changed_by="user",
    )
    hist3 = await draft_history.get_draft_history(db_session, PROJECT_ID, draft.id)
    topup2b = next(e for e in hist3.events if e.id == topup2.id)
    assert topup2b.can_revert is False
    assert topup2b.revert_blocked_reason and "изменил" in topup2b.revert_blocked_reason


@pytest.mark.asyncio
async def test_commit_revert_lifo_guard(db_session):
    """Стек частичных коммитов: откат НЕ новейшего события заблокирован (LIFO) —
    иначе restore/merge портит количества (потеря+задвоение)."""
    wh_a, wh_b = await _get_warehouse_ids(db_session)
    # Черновик с одним nm, источник размазан по двум ФФ (src wh_a=5, wh_b=5).
    rows = [AssemblyDraftRow(nm_id=111, barcode=TEST_BARCODE_1, vendor_code="v", src={str(wh_a): 5, str(wh_b): 5}, tgt={"Электросталь": 10})]
    draft = await assembly_draft_service.create_draft(
        db_session, PROJECT_ID, _build_payload([wh_a, wh_b], ["Электросталь"], rows)
    )
    # Частичный коммит по ФФ wh_a (порция wh_b карвится в leftover, черновик выживает).
    r1 = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id, source_ff_id=wh_a)
    assert len(r1.created_request_ids) == 1
    # Второй коммит по wh_b (черновик пустеет → soft-delete).
    r2 = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id, source_ff_id=wh_b)
    assert len(r2.created_request_ids) == 1

    hist = await draft_history.get_draft_history(db_session, PROJECT_ID, draft.id)
    assert len(hist.events) == 2
    newest, older = hist.events[0], hist.events[1]  # desc по id
    assert older.created_request_ids == r1.created_request_ids
    assert newest.created_request_ids == r2.created_request_ids
    # LIFO: старое событие откатить нельзя, новое — можно.
    assert older.can_revert is False
    assert older.revert_blocked_reason and "более новое" in older.revert_blocked_reason
    assert newest.can_revert is True

    # Прямой откат старого события → 409.
    with pytest.raises(HTTPException) as exc:
        await draft_history.revert_draft_event(db_session, PROJECT_ID, draft.id, older.id)
    assert exc.value.status_code == 409

    # Откат новейшего проходит; после него старое становится откатываемым.
    await draft_history.revert_draft_event(db_session, PROJECT_ID, draft.id, newest.id)
    hist2 = await draft_history.get_draft_history(db_session, PROJECT_ID, draft.id)
    older2 = next(e for e in hist2.events if e.id == older.id)
    assert older2.can_revert is True
