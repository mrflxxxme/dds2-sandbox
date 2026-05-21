# ruff: noqa: RUF001, RUF002, RUF003
"""
Assembly Draft service — CRUD + commit (turn draft into N AssemblyRequests).

A draft holds a planned NxM distribution (RF source warehouses x WB target
warehouses) and is persisted in DB so the user can reopen across devices.
On commit, it spawns one AssemblyRequest per (source_ff, target_wb) pair
that has any non-zero quantity, then soft-deletes itself.

See backend/DOMAIN_ASSEMBLY.md for context (assembly module).
"""

import logging
from datetime import date, timedelta
from typing import cast

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
    AssemblyDraftRead,
    AssemblyDraftRow,
    AssemblyDraftUpdate,
    HandedUnit,
    HandedUnitItem,
    PackageTypeStr,
)
from backend.services.warehouse_stock_engine import _next_number

logger = logging.getLogger(__name__)

# Окно "новинка" — синхронно с cold_start_distribution_service.fetch_cold_start_segment:
# nm_id считается новинкой если first_sale_date IS NULL ИЛИ ≥ today-14d.
NEWCOMER_DAYS = 14
NEWCOMER_COMMENT_PREFIX = "🆕 Новинки"


async def fetch_newcomer_nm_ids(
    db: AsyncSession,
    project_id: int,
    nm_ids: set[int],
) -> set[int]:
    """Из переданного множества nm_id выбрать те, что считаются «новинками».

    Новинка = `Nomenclature.first_sale_date` IS NULL ИЛИ first_sale_date ≥ today-14d.
    Этот же критерий использует backend cold-start (fetch_cold_start_segment),
    поэтому при commit_draft новинки группируются в отдельные AssemblyRequests.
    """
    if not nm_ids:
        return set()
    cutoff = date.today() - timedelta(days=NEWCOMER_DAYS)
    result = await db.execute(
        select(Nomenclature.article_wb).where(
            Nomenclature.project_id == project_id,
            Nomenclature.article_wb.in_(nm_ids),
            (Nomenclature.first_sale_date.is_(None)) | (Nomenclature.first_sale_date >= cutoff),
        )
    )
    return {nm for (nm,) in result.fetchall() if nm is not None}


def _draft_nm_ids(draft: AssemblyDraft) -> set[int]:
    """Извлечь все nm_id из draft.distribution.rows (toJSON-friendly чтение)."""
    rows = (draft.distribution or {}).get("rows", []) if isinstance(draft.distribution, dict) else []
    out: set[int] = set()
    for r in rows:
        nm = r.get("nm_id")
        if isinstance(nm, int):
            out.add(nm)
    return out


async def to_read_model(
    db: AsyncSession,
    project_id: int,
    draft: AssemblyDraft,
) -> AssemblyDraftRead:
    """Преобразовать ORM-модель в схему ответа с обогащённым newcomer_nm_ids."""
    newcomer = await fetch_newcomer_nm_ids(db, project_id, _draft_nm_ids(draft))
    read = AssemblyDraftRead.model_validate(draft)
    read.newcomer_nm_ids = sorted(newcomer)
    return read


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
    package_type: str | None = None,
    newcomer_filter: str | None = None,
) -> AssemblyDraftCommitResponse:
    """
    Validate the distribution, then create one AssemblyRequest per
    (source_ff_warehouse, target_wb_name) pair with non-zero qty.
    On any failure rolls back.

    Партиальный коммит по двум независимым осям (всё, что не выбрано,
    остаётся в черновике для последующих сборок):
    - `package_type` (BOX/MONOPALLET/...) — короб/моно раздельно;
    - `newcomer_filter` (newcomer/regular/all) — новинки/обычные раздельно.
    Без обоих фильтров коммитит весь черновик и soft-delete'ит его.
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

    # Партиальный коммит по двум осям: тип упаковки (короб/моно) и тип товара
    # (новинки/обычные). newcomer-set считаем по ВСЕМ строкам ДО фильтрации —
    # он нужен и для фильтра по новизне, и далее для группировки заявок.
    norm_pkg = (package_type or "").strip().upper() or None
    norm_nc = (newcomer_filter or "").strip().lower() or None
    if norm_nc == "all":
        norm_nc = None
    if norm_nc not in (None, "newcomer", "regular"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid newcomer_filter: {newcomer_filter!r} (expected newcomer|regular|all)",
        )

    newcomer_nm_ids = await fetch_newcomer_nm_ids(db, project_id, {row.nm_id for row in distribution.rows})

    def _is_selected(r: AssemblyDraftRow) -> bool:
        if norm_pkg and (r.package_type or "BOX") != norm_pkg:
            return False
        if norm_nc == "newcomer" and r.nm_id not in newcomer_nm_ids:
            return False
        if norm_nc == "regular" and r.nm_id in newcomer_nm_ids:
            return False
        return True

    commit_rows = [r for r in distribution.rows if _is_selected(r)]
    leftover_rows = [r for r in distribution.rows if not _is_selected(r)]
    if (norm_pkg or norm_nc) and not commit_rows:
        raise HTTPException(
            status_code=400,
            detail="Нет строк выбранного типа (упаковка/новизна) для сборки",
        )

    # 1. Validate balance per row (Σ src == Σ tgt > 0)
    for row in commit_rows:
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

    # 2. newcomer_nm_ids уже посчитан выше (до фильтрации строк) — используем его
    # для группировки: новинки идут отдельными AssemblyRequests от обычных.
    # 3. Group items per (source_ff_id, target_wb_name, package_type, is_newcomer) tuple.
    # One AssemblyRequest = one transport unit = one package_type, новинки отдельно.
    # pair_items[(src_id, wb_name, pkg, is_new)] -> {barcode: total_qty}
    pair_items: dict[tuple[int, str, str, bool], dict[str, int]] = {}
    for row in commit_rows:
        if not row.barcode:
            raise HTTPException(
                status_code=400,
                detail=f"Row {row.nm_id}: barcode is required",
            )
        alloc = _allocate_pairs(row.src, row.tgt)
        pkg = row.package_type or "BOX"
        is_new = row.nm_id in newcomer_nm_ids
        for (src_id, wb_name), qty in alloc.items():
            if qty <= 0:
                continue
            bucket = pair_items.setdefault((src_id, wb_name, pkg, is_new), {})
            bucket[row.barcode] = bucket.get(row.barcode, 0) + qty

    if not pair_items:
        raise HTTPException(status_code=400, detail="No (source, target) pairs with non-zero quantity")

    # 4. Resolve barcodes -> nomenclature in one batch
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

    # 5. Parse estimated_ready_date once
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
    base_comment = draft.comment

    # 6. Create AssemblyRequest per pair (atomic — single transaction)
    created_ids: list[int] = []
    try:
        for (source_ff_id, target_wb_name, package_type, is_newcomer), barcodes in pair_items.items():
            number = await _next_number(db, project_id, "ASM", AssemblyRequest)
            if is_newcomer:
                pieces = [NEWCOMER_COMMENT_PREFIX]
                if base_comment:
                    pieces.append(base_comment)
                req_comment: str | None = " | ".join(pieces)
            else:
                req_comment = base_comment

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
                comment=req_comment,
                package_type=package_type,
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

        # 7. Если остались строки другого типа упаковки ИЛИ замороженные
        # (передан на ФФ) юниты — оставляем черновик; иначе soft-delete.
        if leftover_rows or distribution.handed_units:
            distribution.rows = leftover_rows
            draft.distribution = distribution.model_dump(mode="json")
        else:
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


# ─── Per-unit lifecycle (черновик → передан на ФФ → в сборке) ────────────────
# «Заявка-юнит» = (source_ff × target_wb × package_type × newcomer). «Передать
# на ФФ» вырезает юнит из rows в замороженный handed_units (правки распределения
# его больше не трогают). «В сборку» создаёт из снимка AssemblyRequest.


def _norm_pkg(package_type: str | None) -> PackageTypeStr:
    p = (package_type or "BOX").strip().upper() or "BOX"
    if p not in ("BOX", "MONOPALLET", "SUPERSAFE"):
        raise HTTPException(status_code=400, detail=f"Invalid package_type: {package_type!r}")
    return cast(PackageTypeStr, p)


def _find_handed_index(units: list[HandedUnit], ff: int, wb: str, pkg: str, newcomer: bool) -> int | None:
    for i, u in enumerate(units):
        if (
            u.source_ff_id == ff
            and u.target_wb_name == wb
            and (u.package_type or "BOX") == pkg
            and u.is_newcomer == newcomer
        ):
            return i
    return None


async def _resolve_nomenclature(db: AsyncSession, project_id: int, barcodes: set[str]) -> dict[str, Nomenclature]:
    """Резолв баркодов → Nomenclature одним запросом. 400, если что-то не найдено."""
    if not barcodes:
        return {}
    result = await db.execute(
        select(Nomenclature).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(barcodes),
        )
    )
    nom_map = {n.barcode: n for n in result.scalars().all()}
    missing = [bc for bc in barcodes if bc not in nom_map]
    if missing:
        raise HTTPException(status_code=400, detail=f"Barcode not found: {missing[0]}")
    return nom_map


async def _create_one_request(
    db: AsyncSession,
    project_id: int,
    *,
    source_ff_id: int,
    target_wb_name: str,
    package_type: str,
    is_newcomer: bool,
    barcodes: dict[str, int],
    nom_map: dict[str, Nomenclature],
    distribution: AssemblyDraftDistribution,
    base_comment: str | None,
) -> AssemblyRequest:
    """Создать один AssemblyRequest (IN_PROGRESS) из набора баркод→qty."""
    number = await _next_number(db, project_id, "ASM", AssemblyRequest)
    if is_newcomer:
        pieces = [NEWCOMER_COMMENT_PREFIX]
        if base_comment:
            pieces.append(base_comment)
        comment: str | None = " | ".join(pieces)
    else:
        comment = base_comment

    eta: date | None = None
    if distribution.estimated_ready_date:
        try:
            eta = date.fromisoformat(distribution.estimated_ready_date)
        except ValueError:
            raise HTTPException(  # noqa: B904
                status_code=400,
                detail=f"Invalid estimated_ready_date: {distribution.estimated_ready_date}",
            )

    req = AssemblyRequest(
        project_id=project_id,
        warehouse_id=source_ff_id,
        number=number,
        status=AssemblyStatus.IN_PROGRESS,
        wb_fbo_supply_id=None,
        wb_warehouse_name_manual=target_wb_name,
        estimated_ready_date=eta,
        pallets_count=max(1, int(distribution.pallets_count or 1)),
        pallet_weight_kg=distribution.pallet_weight_kg or 0.0,
        comment=comment,
        package_type=package_type,
    )
    db.add(req)
    await db.flush()
    for bc, qty in barcodes.items():
        if qty <= 0:
            continue
        db.add(
            AssemblyRequestItem(
                project_id=project_id,
                assembly_request_id=req.id,
                nomenclature_id=nom_map[bc].id,
                barcode=bc,
                quantity=qty,
            )
        )
    await db.flush()
    return req


def _carve_unit_from_rows(
    rows: list[AssemblyDraftRow],
    source_ff_id: int,
    target_wb_name: str,
    norm_pkg: str,
    newcomer: bool,
    newcomer_set: set[int],
) -> tuple[dict[str, HandedUnitItem], list[AssemblyDraftRow]]:
    """Вырезать поток ff→wb из строк. Возвращает (позиции по баркоду, остаток строк).
    Σsrc и Σtgt падают на одну величину → строки остаются сбалансированными."""
    sid = str(source_ff_id)
    carved: dict[str, HandedUnitItem] = {}
    remaining: list[AssemblyDraftRow] = []
    for row in rows:
        is_new = row.nm_id in newcomer_set
        if (row.package_type or "BOX") != norm_pkg or is_new != newcomer:
            remaining.append(row)
            continue
        qty = _allocate_pairs(row.src, row.tgt).get((source_ff_id, target_wb_name), 0)
        if qty <= 0:
            remaining.append(row)
            continue
        if not row.barcode:
            raise HTTPException(status_code=400, detail=f"Row {row.nm_id}: barcode is required")
        item = carved.get(row.barcode)
        if item:
            item.qty += qty
        else:
            carved[row.barcode] = HandedUnitItem(
                nm_id=row.nm_id,
                barcode=row.barcode,
                vendor_code=row.vendor_code,
                qty=qty,
            )
        new_src = dict(row.src)
        new_tgt = dict(row.tgt)
        new_src[sid] = new_src.get(sid, 0) - qty
        if new_src[sid] <= 0:
            new_src.pop(sid, None)
        new_tgt[target_wb_name] = new_tgt.get(target_wb_name, 0) - qty
        if new_tgt[target_wb_name] <= 0:
            new_tgt.pop(target_wb_name, None)
        if new_src and new_tgt:
            remaining.append(row.model_copy(update={"src": new_src, "tgt": new_tgt}))
    return carved, remaining


async def hand_off_unit(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    source_ff_id: int,
    target_wb_name: str,
    package_type: str,
    newcomer: bool,
) -> AssemblyDraftRead:
    """«Передать на ФФ»: заморозить заявку-юнит со статусом handed. Если юнит уже
    заморожен (ручной черновик после правки) — просто меняем статус на handed;
    иначе вырезаем поток ff→wb из rows в новый снимок."""
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    norm_pkg = _norm_pkg(package_type)

    units = list(distribution.handed_units)
    idx = _find_handed_index(units, source_ff_id, target_wb_name, norm_pkg, newcomer)
    if idx is not None:
        units[idx].status = "handed"
        distribution.handed_units = units
    else:
        newcomer_set = await fetch_newcomer_nm_ids(db, project_id, {r.nm_id for r in distribution.rows})
        carved, remaining = _carve_unit_from_rows(
            distribution.rows, source_ff_id, target_wb_name, norm_pkg, newcomer, newcomer_set
        )
        if not carved:
            raise HTTPException(status_code=400, detail="В заявке нет позиций для передачи")
        distribution.rows = remaining
        distribution.handed_units = [
            *units,
            HandedUnit(
                source_ff_id=source_ff_id,
                target_wb_name=target_wb_name,
                package_type=norm_pkg,
                is_newcomer=newcomer,
                status="handed",
                items=list(carved.values()),
            ),
        ]
    draft.distribution = distribution.model_dump(mode="json")
    await db.commit()
    await db.refresh(draft)
    return await to_read_model(db, project_id, draft)


async def revert_unit(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    source_ff_id: int,
    target_wb_name: str,
    package_type: str,
    newcomer: bool,
) -> AssemblyDraftRead:
    """«Вернуть в черновик»: убрать handed-юнит и влить его позиции обратно в rows."""
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    norm_pkg = _norm_pkg(package_type)
    units = list(distribution.handed_units)
    idx = _find_handed_index(units, source_ff_id, target_wb_name, norm_pkg, newcomer)
    if idx is None:
        raise HTTPException(status_code=404, detail="Переданная заявка не найдена")

    unit = units.pop(idx)
    sid = str(source_ff_id)
    rows_by_key: dict[tuple[int, str], AssemblyDraftRow] = {
        (r.nm_id, r.package_type or "BOX"): r for r in distribution.rows
    }
    for it in unit.items:
        row = rows_by_key.get((it.nm_id, norm_pkg))
        if row is None:
            row = AssemblyDraftRow(
                nm_id=it.nm_id,
                barcode=it.barcode,
                vendor_code=it.vendor_code,
                src={},
                tgt={},
                package_type=norm_pkg,
            )
            distribution.rows.append(row)
            rows_by_key[(it.nm_id, norm_pkg)] = row
        row.src[sid] = row.src.get(sid, 0) + it.qty
        row.tgt[target_wb_name] = row.tgt.get(target_wb_name, 0) + it.qty

    distribution.handed_units = units
    draft.distribution = distribution.model_dump(mode="json")
    await db.commit()
    await db.refresh(draft)
    return await to_read_model(db, project_id, draft)


async def commit_unit(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    source_ff_id: int,
    target_wb_name: str,
    package_type: str,
    newcomer: bool,
) -> AssemblyDraftCommitResponse:
    """«В сборку»: создать AssemblyRequest из замороженного handed-юнита и убрать
    его из черновика. Если черновик опустел (нет rows и handed_units) — soft-delete."""
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    norm_pkg = _norm_pkg(package_type)
    units = list(distribution.handed_units)
    idx = _find_handed_index(units, source_ff_id, target_wb_name, norm_pkg, newcomer)
    if idx is None:
        raise HTTPException(status_code=404, detail="Переданная заявка не найдена")
    if units[idx].status != "handed":
        raise HTTPException(status_code=400, detail="Сначала передайте заявку на ФФ")

    unit = units[idx]
    barcodes: dict[str, int] = {}
    for it in unit.items:
        if it.qty > 0:
            barcodes[it.barcode] = barcodes.get(it.barcode, 0) + it.qty
    if not barcodes:
        raise HTTPException(status_code=400, detail="В заявке нет позиций для сборки")
    nom_map = await _resolve_nomenclature(db, project_id, set(barcodes))

    created_ids: list[int] = []
    try:
        req = await _create_one_request(
            db,
            project_id,
            source_ff_id=source_ff_id,
            target_wb_name=target_wb_name,
            package_type=norm_pkg,
            is_newcomer=newcomer,
            barcodes=barcodes,
            nom_map=nom_map,
            distribution=distribution,
            base_comment=draft.comment,
        )
        created_ids.append(req.id)
        del units[idx]
        distribution.handed_units = units
        if distribution.rows or distribution.handed_units:
            draft.distribution = distribution.model_dump(mode="json")
        else:
            draft.soft_delete()
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.exception("commit_unit failed for draft_id=%s", draft_id)
        raise HTTPException(status_code=400, detail=f"Failed to commit unit: {e}") from None

    return AssemblyDraftCommitResponse(created_request_ids=created_ids, draft_id=draft_id)


async def set_unit_items(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    source_ff_id: int,
    target_wb_name: str,
    package_type: str,
    newcomer: bool,
    items: list[HandedUnitItem],
) -> AssemblyDraftRead:
    """Заменить наполнение заявки-юнита (ручная правка черновика). Если юнит ещё
    авто (в rows) — вырезаем его текущий поток из rows и фиксируем как ручной
    черновик (status='draft') с НОВЫМ наполнением. Если уже заморожен —
    заменяем items. Переданный на ФФ (handed) править нельзя.
    Проверку остатка ФФ делает фронт; здесь — резолв баркодов и qty>0."""
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    norm_pkg = _norm_pkg(package_type)

    clean: list[HandedUnitItem] = []
    for it in items:
        qty = int(it.qty or 0)
        if qty <= 0:
            continue
        if not it.barcode:
            raise HTTPException(status_code=400, detail=f"Позиция {it.nm_id}: не указан баркод")
        clean.append(HandedUnitItem(nm_id=it.nm_id, barcode=it.barcode, vendor_code=it.vendor_code, qty=qty))
    if not clean:
        raise HTTPException(status_code=400, detail="Заявка не может быть пустой")
    await _resolve_nomenclature(db, project_id, {it.barcode for it in clean})

    units = list(distribution.handed_units)
    idx = _find_handed_index(units, source_ff_id, target_wb_name, norm_pkg, newcomer)
    if idx is not None:
        if units[idx].status != "draft":
            raise HTTPException(status_code=400, detail="Заявка передана на ФФ — правка запрещена")
        units[idx].items = clean
        distribution.handed_units = units
    else:
        newcomer_set = await fetch_newcomer_nm_ids(db, project_id, {r.nm_id for r in distribution.rows})
        _carved, remaining = _carve_unit_from_rows(
            distribution.rows, source_ff_id, target_wb_name, norm_pkg, newcomer, newcomer_set
        )
        distribution.rows = remaining
        distribution.handed_units = [
            *units,
            HandedUnit(
                source_ff_id=source_ff_id,
                target_wb_name=target_wb_name,
                package_type=norm_pkg,
                is_newcomer=newcomer,
                status="draft",
                items=clean,
            ),
        ]
    draft.distribution = distribution.model_dump(mode="json")
    await db.commit()
    await db.refresh(draft)
    return await to_read_model(db, project_id, draft)


async def delete_unit(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    source_ff_id: int,
    target_wb_name: str,
    package_type: str,
    newcomer: bool,
) -> AssemblyDraftRead:
    """Удалить заявку-юнит из черновика целиком (товар остаётся на ФФ, не
    отгружается). Заморожен (ручной черновик) → убираем снимок; авто → вырезаем
    поток из rows и отбрасываем. Переданный на ФФ удалять нельзя (сначала
    вернуть в черновик). Пустой черновик → soft-delete."""
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    norm_pkg = _norm_pkg(package_type)

    units = list(distribution.handed_units)
    idx = _find_handed_index(units, source_ff_id, target_wb_name, norm_pkg, newcomer)
    if idx is not None:
        if units[idx].status == "handed":
            raise HTTPException(status_code=400, detail="Заявка передана на ФФ — сначала верните в черновик")
        del units[idx]
        distribution.handed_units = units
    else:
        newcomer_set = await fetch_newcomer_nm_ids(db, project_id, {r.nm_id for r in distribution.rows})
        carved, remaining = _carve_unit_from_rows(
            distribution.rows, source_ff_id, target_wb_name, norm_pkg, newcomer, newcomer_set
        )
        if not carved:
            raise HTTPException(status_code=404, detail="Заявка не найдена")
        distribution.rows = remaining

    draft.distribution = distribution.model_dump(mode="json")
    if not (distribution.rows or distribution.handed_units):
        draft.soft_delete()
    await db.commit()
    await db.refresh(draft)
    return await to_read_model(db, project_id, draft)
