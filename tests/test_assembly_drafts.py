"""
Tests for AssemblyDraft module — service layer + commit lifecycle.

Covers:
1. Create / list / get / update / soft-delete (CRUD happy paths + multi-tenancy)
2. Commit happy path: 1 source x 2 targets -> 2 AssemblyRequests
3. Commit pro-rata: 2 sources x 2 targets -> up to 4 AssemblyRequests
4. Commit balance validation: src sum != tgt sum -> 400
5. Commit atomic: failure during create -> rollback (draft remains, no requests)
6. Commit marks draft as soft-deleted on success
"""

from datetime import datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text

from backend.models.assembly import AssemblyDraft, AssemblyRequest
from backend.schemas.assembly_draft import (
    AssemblyDraftCreate,
    AssemblyDraftDistribution,
    AssemblyDraftRow,
    AssemblyDraftUpdate,
)
from backend.services import assembly_draft_service

PROJECT_ID = 77711
OTHER_PROJECT_ID = 77712
TEST_BARCODE_1 = "TEST_DRAFT_BC_001"
TEST_BARCODE_2 = "TEST_DRAFT_BC_002"


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest_asyncio.fixture(autouse=True)
async def setup_test_data(db_session):
    """Clean assembly_drafts and ensure test fixtures exist."""
    # Clean test projects (in dependency order)
    for pid in (PROJECT_ID, OTHER_PROJECT_ID):
        await db_session.execute(
            text(
                "DELETE FROM assembly_request_items WHERE assembly_request_id IN "
                "(SELECT id FROM assembly_requests WHERE project_id = :pid)"
            ),
            {"pid": pid},
        )
        await db_session.execute(text("DELETE FROM assembly_status_history WHERE project_id = :pid"), {"pid": pid})
        await db_session.execute(text("DELETE FROM assembly_requests WHERE project_id = :pid"), {"pid": pid})
        await db_session.execute(text("DELETE FROM assembly_drafts WHERE project_id = :pid"), {"pid": pid})
    await db_session.commit()

    # Ensure projects exist
    for pid, name, slug in (
        (PROJECT_ID, "Draft Test Project", "draft-test"),
        (OTHER_PROJECT_ID, "Draft Other Project", "draft-other"),
    ):
        result = await db_session.execute(text("SELECT id FROM projects WHERE id = :pid"), {"pid": pid})
        if result.scalar() is None:
            user_q = await db_session.execute(text("SELECT id FROM users WHERE username = 'draft_test_user'"))
            user_id = user_q.scalar()
            if user_id is None:
                await db_session.execute(
                    text(
                        "INSERT INTO users (username, email, password_hash, is_active, created_at) "
                        "VALUES (:u, :e, :p, true, NOW())"
                    ),
                    {"u": "draft_test_user", "e": "draft_test@test.com", "p": "nohash"},
                )
                user_q = await db_session.execute(text("SELECT id FROM users WHERE username = 'draft_test_user'"))
                user_id = user_q.scalar()
            await db_session.execute(
                text(
                    "INSERT INTO projects (id, name, slug, owner_id, created_at) "
                    "VALUES (:pid, :n, :s, :o, NOW()) ON CONFLICT (id) DO NOTHING"
                ),
                {"pid": pid, "n": name, "s": slug, "o": user_id},
            )
    await db_session.commit()

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
