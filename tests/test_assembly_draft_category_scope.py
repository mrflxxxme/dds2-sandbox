# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests: категорийные черновики сборки.

1. get_drafts_reserved — резерв стока всеми не-удалёнными черновиками проекта
   (rows[].src + prebook[].src + handed_units[].items), exclude_draft_id,
   изоляция project_id, устойчивость к explicit null в JSONB.
2. merge_drafts guard — разный категорийный скоуп (как множества; None ≡ []) → ValueError.
3. get_or_create_current_draft — скоупленные черновики не участвуют в
   синглтон-консолидации (иначе категорийный черновик молча сливался бы в общий).
"""

import pytest

from backend.schemas.assembly_draft import (
    AssemblyDraftCreate,
    AssemblyDraftDistribution,
    AssemblyDraftRow,
    HandedUnit,
    HandedUnitItem,
)
from backend.services import assembly_draft_service

BC1 = "SCOPE_BC_001"
BC2 = "SCOPE_BC_002"


def _payload(
    *,
    rows: list[AssemblyDraftRow] | None = None,
    prebook: list[AssemblyDraftRow] | None = None,
    handed_units: list[HandedUnit] | None = None,
    category_scope: list[str] | None = None,
    name: str = "Тестовый черновик",
) -> AssemblyDraftCreate:
    return AssemblyDraftCreate(
        name=name,
        distribution=AssemblyDraftDistribution(
            rows=rows or [],
            prebook=prebook or [],
            handed_units=handed_units or [],
            category_scope=category_scope,
        ),
    )


# ─── get_drafts_reserved ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reserved_aggregates_rows_prebook_handed(db_session, project):
    """Резерв суммирует ТРИ источника: rows[].src + prebook[].src + handed_units[].items.
    Нулевые/отрицательные qty и пустые баркоды пропускаются."""
    await assembly_draft_service.create_draft(
        db_session,
        project.id,
        _payload(
            rows=[
                AssemblyDraftRow(nm_id=1, barcode=BC1, src={"5": 10, "7": 4, "9": 0}, tgt={"Казань": 14}),
                AssemblyDraftRow(nm_id=3, barcode="", src={"5": 99}, tgt={"Казань": 99}),  # без баркода — мимо
            ],
            prebook=[AssemblyDraftRow(nm_id=2, barcode=BC2, src={"5": 3}, tgt={})],
        ),
    )
    await assembly_draft_service.create_draft(
        db_session,
        project.id,
        _payload(
            handed_units=[
                HandedUnit(
                    source_ff_id=5,
                    target_wb_name="Казань",
                    items=[
                        HandedUnitItem(nm_id=1, barcode=BC1, qty=6),
                        HandedUnitItem(nm_id=2, barcode=BC2, qty=2),
                    ],
                )
            ],
        ),
    )

    reserved = await assembly_draft_service.get_drafts_reserved(db_session, project.id)

    assert reserved[BC1] == {"5": 16, "7": 4}  # 10 (rows d1) + 6 (handed d2); "9": 0 отброшен
    assert reserved[BC2] == {"5": 5}  # 3 (prebook d1) + 2 (handed d2)
    assert "" not in reserved


@pytest.mark.asyncio
async def test_reserved_exclude_draft_id(db_session, project):
    """exclude_draft_id вырезает собственный черновик из резерва."""
    d1 = await assembly_draft_service.create_draft(
        db_session,
        project.id,
        _payload(rows=[AssemblyDraftRow(nm_id=1, barcode=BC1, src={"5": 10}, tgt={"Казань": 10})]),
    )
    await assembly_draft_service.create_draft(
        db_session,
        project.id,
        _payload(rows=[AssemblyDraftRow(nm_id=1, barcode=BC1, src={"5": 7}, tgt={"Тула": 7})]),
    )

    reserved = await assembly_draft_service.get_drafts_reserved(db_session, project.id, exclude_draft_id=d1.id)
    assert reserved[BC1] == {"5": 7}


@pytest.mark.asyncio
async def test_reserved_skips_soft_deleted(db_session, project):
    """Soft-deleted черновик не резервирует сток."""
    d1 = await assembly_draft_service.create_draft(
        db_session,
        project.id,
        _payload(rows=[AssemblyDraftRow(nm_id=1, barcode=BC1, src={"5": 10}, tgt={"Казань": 10})]),
    )
    await assembly_draft_service.delete_draft(db_session, project.id, d1.id)

    reserved = await assembly_draft_service.get_drafts_reserved(db_session, project.id)
    assert reserved == {}


@pytest.mark.asyncio
async def test_reserved_project_isolation(db_session, project, other_project):
    """Черновики чужого проекта не попадают в резерв."""
    await assembly_draft_service.create_draft(
        db_session,
        other_project.id,
        _payload(rows=[AssemblyDraftRow(nm_id=1, barcode=BC1, src={"5": 10}, tgt={"Казань": 10})]),
    )

    reserved = await assembly_draft_service.get_drafts_reserved(db_session, project.id)
    assert reserved == {}


@pytest.mark.asyncio
async def test_reserved_tolerates_explicit_nulls_in_jsonb(db_session, project):
    """Explicit null в JSONB (prebook/handed_units/src/items) не роняет резерв."""
    d = await assembly_draft_service.create_draft(
        db_session,
        project.id,
        _payload(rows=[AssemblyDraftRow(nm_id=1, barcode=BC1, src={"5": 4}, tgt={"Казань": 4})]),
    )
    draft = await assembly_draft_service.get_draft(db_session, project.id, d.id)
    draft.distribution = {
        "rows": [
            {"nm_id": 1, "barcode": BC1, "src": {"5": 4}, "tgt": {"Казань": 4}},
            {"nm_id": 2, "barcode": BC2, "src": None, "tgt": None},  # null-src
        ],
        "prebook": None,  # explicit null вместо []
        "handed_units": [
            {"source_ff_id": 7, "target_wb_name": "Тула", "items": None},  # null-items
            {"source_ff_id": 8, "target_wb_name": "Тула", "items": [{"nm_id": 1, "barcode": BC1, "qty": 2}]},
        ],
    }
    await db_session.commit()

    reserved = await assembly_draft_service.get_drafts_reserved(db_session, project.id)
    assert reserved[BC1] == {"5": 4, "8": 2}
    assert BC2 not in reserved


# ─── merge_drafts: guard категорийного скоупа ───────────────────────────────


@pytest.mark.asyncio
async def test_merge_different_scope_raises(db_session, project):
    """Черновики с разным категорийным скоупом объединять нельзя → ValueError, оба живы."""
    d1 = await assembly_draft_service.create_draft(
        db_session, project.id, _payload(category_scope=["Коврики"])
    )
    d2 = await assembly_draft_service.create_draft(db_session, project.id, _payload(category_scope=None))

    with pytest.raises(ValueError, match="категорийным скоупом"):
        await assembly_draft_service.merge_drafts(db_session, project.id, [d1.id, d2.id])

    actives = await assembly_draft_service.list_drafts(db_session, project.id)
    assert {d.id for d in actives} == {d1.id, d2.id}  # ничего не слито и не удалено


@pytest.mark.asyncio
async def test_merge_same_scope_as_set_ok(db_session, project):
    """Одинаковый скоуп сравнивается как множество (порядок не важен) → merge ок."""
    d1 = await assembly_draft_service.create_draft(
        db_session,
        project.id,
        _payload(
            rows=[AssemblyDraftRow(nm_id=1, barcode=BC1, src={"5": 2}, tgt={"Казань": 2})],
            category_scope=["Коврики", "Полки"],
        ),
    )
    d2 = await assembly_draft_service.create_draft(
        db_session, project.id, _payload(category_scope=["Полки", "Коврики"])
    )

    survivor = await assembly_draft_service.merge_drafts(db_session, project.id, [d1.id, d2.id])
    assert survivor.id in {d1.id, d2.id}
    actives = await assembly_draft_service.list_drafts(db_session, project.id)
    assert len(actives) == 1


@pytest.mark.asyncio
async def test_merge_none_and_empty_scope_equivalent(db_session, project):
    """None и [] — эквивалентный (пустой) скоуп → merge ок."""
    d1 = await assembly_draft_service.create_draft(db_session, project.id, _payload(category_scope=None))
    d2 = await assembly_draft_service.create_draft(db_session, project.id, _payload(category_scope=[]))

    survivor = await assembly_draft_service.merge_drafts(db_session, project.id, [d1.id, d2.id])
    assert survivor.id in {d1.id, d2.id}
    actives = await assembly_draft_service.list_drafts(db_session, project.id)
    assert len(actives) == 1


# ─── get_or_create_current_draft: скоупленные черновики вне синглтона ────────


@pytest.mark.asyncio
async def test_current_draft_ignores_scoped_drafts(db_session, project):
    """Синглтон-консолидация «текущего» черновика не трогает категорийные:
    без этого POST /current либо 500 (guard), либо молча сливал бы категорийный
    черновик в общий (потеря фичи)."""
    normal = await assembly_draft_service.create_draft(
        db_session,
        project.id,
        _payload(rows=[AssemblyDraftRow(nm_id=1, barcode=BC1, src={"5": 2}, tgt={"Казань": 2})]),
    )
    scoped = await assembly_draft_service.create_draft(
        db_session, project.id, _payload(category_scope=["Коврики"])
    )

    current = await assembly_draft_service.get_or_create_current_draft(db_session, project.id)
    assert current.id == normal.id

    actives = await assembly_draft_service.list_drafts(db_session, project.id)
    assert {d.id for d in actives} == {normal.id, scoped.id}  # категорийный жив


@pytest.mark.asyncio
async def test_current_draft_merges_only_unscoped_guard_untouched(db_session, project):
    """Два обычных + один категорийный: current-путь мержит ТОЛЬКО бесскоупные
    (merge-guard не срабатывает), категорийный жив и не выбран текущим."""
    n1 = await assembly_draft_service.create_draft(
        db_session,
        project.id,
        _payload(rows=[AssemblyDraftRow(nm_id=1, barcode=BC1, src={"5": 2}, tgt={"Казань": 2})]),
    )
    n2 = await assembly_draft_service.create_draft(
        db_session,
        project.id,
        _payload(rows=[AssemblyDraftRow(nm_id=2, barcode=BC2, src={"5": 3}, tgt={"Тула": 3})]),
    )
    scoped = await assembly_draft_service.create_draft(
        db_session, project.id, _payload(category_scope=["Коврики"])
    )

    current = await assembly_draft_service.get_or_create_current_draft(db_session, project.id)

    assert current.id in {n1.id, n2.id}
    assert current.id != scoped.id
    dist = AssemblyDraftDistribution.model_validate(current.distribution)
    assert {r.nm_id for r in dist.rows} == {1, 2}  # оба обычных слиты

    actives = await assembly_draft_service.list_drafts(db_session, project.id)
    assert {d.id for d in actives} == {current.id, scoped.id}  # категорийный не тронут


@pytest.mark.asyncio
async def test_current_draft_scoped_with_explicit_null_scope_is_unscoped(db_session, project):
    """Explicit null в category_scope (JSONB) = бесскоупный: участвует в синглтоне."""
    d = await assembly_draft_service.create_draft(db_session, project.id, _payload())
    draft = await assembly_draft_service.get_draft(db_session, project.id, d.id)
    draft.distribution = {"rows": [], "category_scope": None}  # explicit null
    await db_session.commit()

    current = await assembly_draft_service.get_or_create_current_draft(db_session, project.id)
    assert current.id == d.id


@pytest.mark.asyncio
async def test_current_draft_creates_unscoped_when_only_scoped_exists(db_session, project):
    """Есть только категорийный черновик → «текущим» становится НОВЫЙ пустой без скоупа."""
    scoped = await assembly_draft_service.create_draft(
        db_session, project.id, _payload(category_scope=["Коврики"])
    )

    current = await assembly_draft_service.get_or_create_current_draft(db_session, project.id)
    assert current.id != scoped.id
    dist = AssemblyDraftDistribution.model_validate(current.distribution)
    assert not dist.category_scope
