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

from backend.models.assembly import AssemblyDraft, AssemblyRequest
from backend.schemas.assembly_draft import (
    AssemblyDraftCreate,
    AssemblyDraftDistribution,
    AssemblyDraftMergeRequest,
    AssemblyDraftRow,
    AssemblyDraftUpdate,
    HandedUnit,
    HandedUnitItem,
)
from backend.services import assembly_draft_service, assembly_service

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
async def test_commit_splits_newcomer_from_regular(db_session, setup_newcomer_nomenclature):
    """Same (src, wb, pkg) but mixed newcomer/regular → 2 separate AssemblyRequests."""
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
    # Несмотря на одинаковые (src, wb, pkg) — две заявки: новинки отдельно.
    assert len(resp.created_request_ids) == 2

    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)))
    requests = list(res.scalars().all())
    comments = {r.comment for r in requests}
    # Одна заявка — новинки (comment начинается с маркера), вторая — обычная (auto-test).
    assert any(c and c.startswith("🆕 Новинки") for c in comments)
    assert "auto-test" in comments


@pytest.mark.asyncio
async def test_commit_splits_box_mono_newcomer_4_requests(db_session, setup_newcomer_nomenclature):
    """Same (src, wb) — оба SKU имеют BOX+MONOPALLET split → 4 заявки (newcomer × {BOX, MONO} + regular × {BOX, MONO})."""
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
    assert len(resp.created_request_ids) == 4

    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)))
    requests = list(res.scalars().all())
    pkg_types = {r.package_type for r in requests}
    assert pkg_types == {"BOX", "MONOPALLET"}
    newcomer_reqs = [r for r in requests if r.comment and r.comment.startswith("🆕")]
    regular_reqs = [r for r in requests if not r.comment or not r.comment.startswith("🆕")]
    assert len(newcomer_reqs) == 2  # BOX + MONOPALLET для новинки
    assert len(regular_reqs) == 2  # BOX + MONOPALLET для обычного
    # Каждый тип package_type есть и у новинки, и у обычного
    assert {r.package_type for r in newcomer_reqs} == {"BOX", "MONOPALLET"}
    assert {r.package_type for r in regular_reqs} == {"BOX", "MONOPALLET"}


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
async def test_commit_newcomer_filter_only_newcomers(db_session, setup_newcomer_nomenclature):
    """newcomer_filter='newcomer' коммитит только новинки; обычные остаются в черновике."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    payload = _build_payload([wh_a], ["Электросталь"], _mixed_rows(wh_a))
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id, newcomer_filter="newcomer")
    assert len(resp.created_request_ids) == 1
    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)))
    req = res.scalars().one()
    assert req.comment and req.comment.startswith("🆕 Новинки")

    # Обычный SKU остался в живом черновике (раздельная сборка).
    leftover = await assembly_draft_service.get_draft(db_session, PROJECT_ID, draft.id)
    assert leftover is not None
    assert {r["nm_id"] for r in leftover.distribution["rows"]} == {REGULAR_NM_ID}


@pytest.mark.asyncio
async def test_commit_newcomer_filter_only_regular(db_session, setup_newcomer_nomenclature):
    """newcomer_filter='regular' коммитит только обычные; новинки остаются в черновике."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    payload = _build_payload([wh_a], ["Электросталь"], _mixed_rows(wh_a))
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id, newcomer_filter="regular")
    assert len(resp.created_request_ids) == 1
    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)))
    req = res.scalars().one()
    assert not (req.comment and req.comment.startswith("🆕"))

    leftover = await assembly_draft_service.get_draft(db_session, PROJECT_ID, draft.id)
    assert leftover is not None
    assert {r["nm_id"] for r in leftover.distribution["rows"]} == {NEWCOMER_NM_ID}


@pytest.mark.asyncio
async def test_commit_newcomer_filter_all_commits_everything(db_session, setup_newcomer_nomenclature):
    """newcomer_filter='all' эквивалентен отсутствию фильтра: коммитит всё, черновик удаляется."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    payload = _build_payload([wh_a], ["Электросталь"], _mixed_rows(wh_a))
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)

    resp = await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id, newcomer_filter="all")
    assert len(resp.created_request_ids) == 2  # новинка + обычный = раздельные заявки
    assert await assembly_draft_service.get_draft(db_session, PROJECT_ID, draft.id) is None


@pytest.mark.asyncio
async def test_commit_invalid_newcomer_filter_400(db_session, setup_newcomer_nomenclature):
    """Некорректный newcomer_filter → 400, черновик не тронут."""
    wh_a, _ = await _get_warehouse_ids(db_session)
    payload = _build_payload([wh_a], ["Электросталь"], _mixed_rows(wh_a))
    draft = await assembly_draft_service.create_draft(db_session, PROJECT_ID, payload)
    with pytest.raises(HTTPException) as exc:
        await assembly_draft_service.commit_draft(db_session, PROJECT_ID, draft.id, newcomer_filter="bogus")
    assert exc.value.status_code == 400


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

    read = await assembly_draft_service.hand_off_unit(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False
    )
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

    await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False)
    read = await assembly_draft_service.revert_unit(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False
    )
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

    await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False)
    resp = await assembly_draft_service.commit_unit(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False
    )
    assert len(resp.created_request_ids) == 1
    res = await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(resp.created_request_ids)))
    req = res.scalars().one()
    assert req.warehouse_id == wh_a
    assert req.wb_warehouse_name_manual == "Электросталь"
    assert req.status == "IN_PROGRESS"
    # Черновик опустел (нет rows и handed_units) → soft-deleted.
    assert await assembly_draft_service.get_draft(db_session, PROJECT_ID, draft.id) is None


@pytest.mark.asyncio
async def test_commit_unit_404_when_not_handed(db_session, setup_newcomer_nomenclature):
    """commit_unit без предварительного hand-off → 404 (юнит не заморожен)."""
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
        await assembly_draft_service.commit_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False)
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
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False, [_item(10)]
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
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False, [_item(5)]
    )
    read = await assembly_draft_service.set_unit_items(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False, [_item(9)]
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
    await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False)
    with pytest.raises(HTTPException) as exc:
        await assembly_draft_service.set_unit_items(
            db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False, [_item(9)]
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
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False, [_item(9)]
    )
    read = await assembly_draft_service.hand_off_unit(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False
    )
    u = read.distribution.handed_units[0]
    assert u.status == "handed"
    assert sum(it.qty for it in u.items) == 9
    resp = await assembly_draft_service.commit_unit(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False
    )
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
    await assembly_draft_service.delete_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False)
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
    await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False)
    with pytest.raises(HTTPException) as exc:
        await assembly_draft_service.delete_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False)
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

    read = await assembly_draft_service.move_unit(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False, "Тула"
    )
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
    await assembly_draft_service.set_unit_items(
        db_session, PROJECT_ID, draft.id, wh_a, "Казань", "BOX", False, [_item(5)]
    )

    read = await assembly_draft_service.move_unit(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False, "Казань"
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
            db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False, "Электросталь"
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
    await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False)
    with pytest.raises(HTTPException) as exc:
        await assembly_draft_service.move_unit(
            db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False, "Казань"
        )
    assert exc.value.status_code == 400


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
    await assembly_draft_service.hand_off_unit(db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False)
    resp = await assembly_draft_service.commit_unit(
        db_session, PROJECT_ID, draft.id, wh_a, "Электросталь", "BOX", False
    )
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


@pytest.mark.asyncio
async def test_merge_drafts_blocks_handed_units(db_session):
    """Слияние черновика с handed_units → 400."""
    wh_a, _ = await _get_warehouse_ids(db_session)

    # Черновик с handed_units в distribution
    d_handed = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        AssemblyDraftCreate(
            name="Handed Draft",
            distribution=AssemblyDraftDistribution(
                source_warehouse_ids=[wh_a],
                target_warehouse_names=["Электросталь"],
                rows=[],
                handed_units=[
                    HandedUnit(
                        source_ff_id=wh_a,
                        target_wb_name="Электросталь",
                        package_type="BOX",
                        is_newcomer=False,
                        status="handed",
                        items=[HandedUnitItem(nm_id=1, barcode="X", vendor_code="", qty=10)],
                    )
                ],
            ),
        ),
    )
    d_normal = await assembly_draft_service.create_draft(
        db_session,
        PROJECT_ID,
        _build_payload(
            [wh_a],
            ["Казань"],
            [
                AssemblyDraftRow(nm_id=50, barcode="MRG-I", src={str(wh_a): 5}, tgt={"Казань": 5}),
            ],
        ),
    )

    with pytest.raises(HTTPException) as exc:
        await assembly_draft_service.merge_drafts(db_session, PROJECT_ID, [d_handed.id, d_normal.id])
    assert exc.value.status_code == 400
    assert "handed" in exc.value.detail.lower() or "юниты" in exc.value.detail


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
