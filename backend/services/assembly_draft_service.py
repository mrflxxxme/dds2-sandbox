"""
Assembly Draft service — CRUD + commit (turn draft into N AssemblyRequests).

A draft holds a planned NxM distribution (RF source warehouses x WB target
warehouses) and is persisted in DB so the user can reopen across devices.
On commit, it spawns one AssemblyRequest per (source_ff, target_wb) pair
that has any non-zero quantity, then soft-deletes itself.

See backend/DOMAIN_ASSEMBLY.md for context (assembly module).
"""

import logging
from datetime import date

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import (
    AssemblyDraft,
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
)
from backend.models.cost import Nomenclature
from backend.schemas.assembly_draft import (
    AssemblyDraftCommitResponse,
    AssemblyDraftCreate,
    AssemblyDraftDistribution,
    AssemblyDraftUpdate,
)
from backend.services.warehouse_stock_engine import _next_number

logger = logging.getLogger(__name__)


# ─── List / Get / CRUD ──────────────────────────────────────────────────────


async def list_drafts(
    db: AsyncSession,
    project_id: int,
) -> list[AssemblyDraft]:
    """List all non-deleted drafts of a project, newest-updated first."""
    result = await db.execute(
        select(AssemblyDraft)
        .where(
            AssemblyDraft.project_id == project_id,
            AssemblyDraft.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
        )
        .order_by(AssemblyDraft.updated_at.desc())
        .limit(500)
    )
    return list(result.scalars().all())


async def get_draft(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
) -> AssemblyDraft | None:
    """Fetch a single non-deleted draft scoped to project_id."""
    result = await db.execute(
        select(AssemblyDraft).where(
            AssemblyDraft.id == draft_id,
            AssemblyDraft.project_id == project_id,
            AssemblyDraft.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
        )
    )
    return result.scalar_one_or_none()


async def create_draft(
    db: AsyncSession,
    project_id: int,
    payload: AssemblyDraftCreate,
) -> AssemblyDraft:
    """Create a new draft from payload."""
    draft = AssemblyDraft(
        project_id=project_id,
        name=payload.name or "Черновик сборки",
        distribution=payload.distribution.model_dump(),
        comment=payload.comment,
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def update_draft(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    payload: AssemblyDraftUpdate,
) -> AssemblyDraft:
    """Update mutable fields of a draft. Raises 404 if missing/deleted."""
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    if payload.name is not None:
        draft.name = payload.name
    if payload.distribution is not None:
        draft.distribution = payload.distribution.model_dump()
    if payload.comment is not None:
        draft.comment = payload.comment

    await db.commit()
    await db.refresh(draft)
    return draft


async def delete_draft(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
) -> None:
    """Soft-delete a draft. Raises 404 if missing/already deleted."""
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    draft.soft_delete()
    await db.commit()


# ─── Commit (draft -> N AssemblyRequests) ───────────────────────────────────


def _allocate_pairs(
    src: dict[str, int],
    tgt: dict[str, int],
) -> dict[tuple[int, str], int]:
    """
    Distribute one row's quantities across (source_warehouse_id, target_wb_name)
    pairs using deterministic largest-remainder rounding.

    Inputs:
      src = {warehouse_id_str: qty}  (sum == row total)
      tgt = {wb_warehouse_name: qty}  (sum == row total)

    Returns: {(source_id_int, target_name): qty}, each qty >= 0,
    sum equals min(sum(src), sum(tgt)) which equals row total when balanced.

    Algorithm: pro-rata (X * Y / total) with floor + remainder distribution
    to the pairs with the largest fractional residue (ties broken by
    deterministic key ordering).
    """
    src_items = [(int(k), int(v)) for k, v in src.items() if int(v or 0) > 0]
    tgt_items = [(str(k), int(v)) for k, v in tgt.items() if int(v or 0) > 0]
    if not src_items or not tgt_items:
        return {}

    total = sum(v for _, v in src_items)
    # If src/tgt are balanced (caller guarantees), total == sum(tgt) too.

    # Floor allocation + fractional residues
    raw: list[tuple[tuple[int, str], int, float]] = []  # (pair, floor_q, residue)
    floor_sum = 0
    for sid, sv in src_items:
        for tname, tv in tgt_items:
            num = sv * tv
            q = num // total
            residue = (num - q * total) / total
            raw.append(((sid, tname), q, residue))
            floor_sum += q

    remainder = total - floor_sum
    # Distribute remainder to highest residue first; deterministic tie-break
    raw.sort(key=lambda x: (-x[2], x[0][0], x[0][1]))

    allocation: dict[tuple[int, str], int] = {}
    for i, (pair, q, _residue) in enumerate(raw):
        bonus = 1 if i < remainder else 0
        if q + bonus > 0:
            allocation[pair] = q + bonus

    return allocation


async def commit_draft(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
) -> AssemblyDraftCommitResponse:
    """
    Validate the distribution, then create one AssemblyRequest per
    (source_ff_warehouse, target_wb_name) pair with non-zero qty.
    On any failure rolls back. On success soft-deletes the draft.
    """
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    try:
        distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid distribution: {e}") from None

    if not distribution.rows:
        raise HTTPException(status_code=400, detail="Distribution has no rows")

    # 1. Validate balance per row (Σ src == Σ tgt > 0)
    for row in distribution.rows:
        src_sum = sum(int(v or 0) for v in row.src.values())
        tgt_sum = sum(int(v or 0) for v in row.tgt.values())
        if src_sum <= 0 or tgt_sum <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Row {row.nm_id}: src sum = {src_sum}, tgt sum = {tgt_sum} (must be > 0)",
            )
        if src_sum != tgt_sum:
            raise HTTPException(
                status_code=400,
                detail=f"Row {row.nm_id}: src sum != tgt sum ({src_sum} != {tgt_sum})",
            )

    # 2. Group items per (source_ff_id, target_wb_name) pair
    # pair_items[pair] -> {barcode: total_qty}
    pair_items: dict[tuple[int, str], dict[str, int]] = {}
    for row in distribution.rows:
        if not row.barcode:
            raise HTTPException(
                status_code=400,
                detail=f"Row {row.nm_id}: barcode is required",
            )
        alloc = _allocate_pairs(row.src, row.tgt)
        for pair, qty in alloc.items():
            if qty <= 0:
                continue
            bucket = pair_items.setdefault(pair, {})
            bucket[row.barcode] = bucket.get(row.barcode, 0) + qty

    if not pair_items:
        raise HTTPException(status_code=400, detail="No (source, target) pairs with non-zero quantity")

    # 3. Resolve barcodes -> nomenclature in one batch
    all_barcodes = {bc for items in pair_items.values() for bc in items}
    nom_result = await db.execute(
        select(Nomenclature).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(all_barcodes),
        )
    )
    nom_map = {n.barcode: n for n in nom_result.scalars().all()}

    missing = [bc for bc in all_barcodes if bc not in nom_map]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Barcode not found: {missing[0]}",
        )

    # 4. Parse estimated_ready_date once
    eta: date | None = None
    if distribution.estimated_ready_date:
        try:
            eta = date.fromisoformat(distribution.estimated_ready_date)
        except ValueError:
            raise HTTPException(  # noqa: B904
                status_code=400,
                detail=f"Invalid estimated_ready_date: {distribution.estimated_ready_date}",
            )

    pallets = max(1, int(distribution.pallets_count or 1))
    pallet_weight = distribution.pallet_weight_kg or 0.0

    # 5. Create AssemblyRequest per pair (atomic — single transaction)
    created_ids: list[int] = []
    try:
        for (source_ff_id, target_wb_name), barcodes in pair_items.items():
            number = await _next_number(db, project_id, "ASM", AssemblyRequest)

            assembly_req = AssemblyRequest(
                project_id=project_id,
                warehouse_id=source_ff_id,
                number=number,
                status=AssemblyStatus.IN_PROGRESS,
                wb_fbo_supply_id=None,  # not linked — manual WB warehouse
                wb_warehouse_name_manual=target_wb_name,
                estimated_ready_date=eta,
                pallets_count=pallets,
                pallet_weight_kg=pallet_weight,
                comment=draft.comment,
            )
            db.add(assembly_req)
            await db.flush()

            for barcode, qty in barcodes.items():
                if qty <= 0:
                    continue
                nom = nom_map[barcode]
                item = AssemblyRequestItem(
                    project_id=project_id,
                    assembly_request_id=assembly_req.id,
                    nomenclature_id=nom.id,
                    barcode=barcode,
                    quantity=qty,
                )
                db.add(item)

            await db.flush()
            created_ids.append(assembly_req.id)

        # 6. Soft-delete draft on success
        draft.soft_delete()
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.exception("commit_draft failed for draft_id=%s", draft_id)
        raise HTTPException(status_code=400, detail=f"Failed to commit draft: {e}") from None

    return AssemblyDraftCommitResponse(
        created_request_ids=created_ids,
        draft_id=draft_id,
    )
