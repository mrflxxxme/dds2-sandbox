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


def _dedupe_rows(rows: list[AssemblyDraftRow]) -> list[AssemblyDraftRow]:
    """Схлопнуть дубли строк по (nm_id, package_type), оставляя ПЕРВУЮ.

    Старая генерация черновика (до перехода на Set уникальных nm_id) могла
    обработать один SKU дважды → дублирующиеся строки. Дубль спурьёзный: первая
    строка несёт полную аллокацию, повторная — остаток истощённого FF-пула.
    Суммировать нельзя — commit_draft складывает qty по баркоду в корзину
    (ФФ, WB, упаковка) и задвоил бы отгрузку. Поэтому keep-first.
    """
    seen: set[tuple[int, str]] = set()
    out: list[AssemblyDraftRow] = []
    for r in rows:
        key = (r.nm_id, r.package_type or "BOX")
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


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
    payload.distribution.rows = _dedupe_rows(payload.distribution.rows)
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
        payload.distribution.rows = _dedupe_rows(payload.distribution.rows)
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


async def merge_drafts(
    db: AsyncSession,
    project_id: int,
    draft_ids: list[int],
) -> AssemblyDraft:
    """Объединить N черновиков в один.

    Survivor: черновик с наибольшим числом строк (tie-break — наименьший id).
    Строки с совпадающим (nm_id, package_type) суммируются поэлементно (src, tgt).
    handed_units переносятся в survivor: юниты с совпадающим ключом
    (source_ff_id, target_wb_name, package_type) сливаются — позиции
    суммируются по баркоду, статус 'handed' побеждает 'draft'.
    source_warehouse_ids и target_warehouse_names объединяются (union).
    cold_start_shares сбрасывается (суммировать доли бессмысленно).
    Остальные черновики → soft_delete. Атомарно.

    404 если хоть один id не найден или принадлежит другому проекту.
    """
    # 1. Fetch all drafts scoped to project
    result = await db.execute(
        select(AssemblyDraft).where(
            AssemblyDraft.project_id == project_id,
            AssemblyDraft.id.in_(draft_ids),
            AssemblyDraft.is_deleted == False,  # noqa: E712
        )
    )
    drafts = list(result.scalars().all())

    found_ids = {d.id for d in drafts}
    missing = [i for i in draft_ids if i not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"Черновики не найдены: {missing}")

    # 2. Choose survivor: most rows; tie-break — lowest id
    def _row_count(d: AssemblyDraft) -> tuple[int, int]:
        rows = (d.distribution or {}).get("rows") or [] if isinstance(d.distribution, dict) else []
        return (len(rows), -d.id)

    survivor = max(drafts, key=_row_count)
    others = [d for d in drafts if d.id != survivor.id]

    # 3. Merge all distributions into survivor
    merged = AssemblyDraftDistribution.model_validate(survivor.distribution or {})

    # Rows keyed by (nm_id, package_type). Preserve survivor's insertion order.
    rows_by_key: dict[tuple[int, str], AssemblyDraftRow] = {(r.nm_id, r.package_type or "BOX"): r for r in merged.rows}

    # Handed units keyed by (ff, wb, pkg). Preserve survivor's order.
    def _handed_key(u: HandedUnit) -> tuple[int, str, str]:
        return (u.source_ff_id, u.target_wb_name, u.package_type or "BOX")

    handed_by_key: dict[tuple[int, str, str], HandedUnit] = {_handed_key(u): u for u in merged.handed_units}

    src_ids: list[int] = list(merged.source_warehouse_ids)
    src_ids_set: set[int] = set(src_ids)
    tgt_names: list[str] = list(merged.target_warehouse_names)
    tgt_names_set: set[str] = set(tgt_names)

    for other in others:
        other_dist = AssemblyDraftDistribution.model_validate(other.distribution or {})

        # Union source_warehouse_ids: survivor order first, new entries appended
        for wid in other_dist.source_warehouse_ids:
            if wid not in src_ids_set:
                src_ids.append(wid)
                src_ids_set.add(wid)

        # Union target_warehouse_names: same
        for name in other_dist.target_warehouse_names:
            if name not in tgt_names_set:
                tgt_names.append(name)
                tgt_names_set.add(name)

        # Merge rows
        for row in other_dist.rows:
            key = (row.nm_id, row.package_type or "BOX")
            existing = rows_by_key.get(key)
            if existing is None:
                # New SKU+pkg: deep-copy and append (avoids sharing mutable dicts)
                rows_by_key[key] = row.model_copy(deep=True)
            else:
                # Same (nm_id, pkg): sum src/tgt element-wise
                for wh, qty in row.src.items():
                    existing.src[wh] = existing.src.get(wh, 0) + qty
                for wb, qty in row.tgt.items():
                    existing.tgt[wb] = existing.tgt.get(wb, 0) + qty
                # Fill blank barcode / vendor_code if survivor's row was empty
                if not existing.barcode and row.barcode:
                    existing.barcode = row.barcode
                if not existing.vendor_code and row.vendor_code:
                    existing.vendor_code = row.vendor_code

        # Merge handed_units: carry into survivor (merge would otherwise drop them).
        # Same key → sum items by barcode; 'handed' beats 'draft' (part is at FF).
        for unit in other_dist.handed_units:
            hkey = _handed_key(unit)
            existing_unit = handed_by_key.get(hkey)
            if existing_unit is None:
                handed_by_key[hkey] = unit.model_copy(deep=True)
            else:
                items_by_bc = {it.barcode: it for it in existing_unit.items}
                for it in unit.items:
                    dup = items_by_bc.get(it.barcode)
                    if dup is not None:
                        dup.qty += it.qty
                    else:
                        new_it = it.model_copy(deep=True)
                        existing_unit.items.append(new_it)
                        items_by_bc[it.barcode] = new_it
                if unit.status == "handed":
                    existing_unit.status = "handed"

    merged.rows = list(rows_by_key.values())
    merged.handed_units = list(handed_by_key.values())
    merged.source_warehouse_ids = src_ids
    merged.target_warehouse_names = tgt_names
    # Summing cold-start shares is meaningless; user re-runs auto-balance if needed
    merged.cold_start_shares = None

    # 4. Persist atomically: write survivor, soft-delete others
    try:
        survivor.distribution = merged.model_dump(mode="json")
        for other in others:
            other.soft_delete()
        await db.commit()
        await db.refresh(survivor)
    except Exception as e:
        await db.rollback()
        logger.exception("merge_drafts failed for ids=%s", draft_ids)
        raise HTTPException(status_code=500, detail=f"Ошибка объединения черновиков: {e}") from None

    return survivor


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
) -> AssemblyDraftCommitResponse:
    """
    Validate the distribution, then create one AssemblyRequest per
    (source_ff_warehouse, target_wb_name, package_type) pair with non-zero qty.
    On any failure rolls back.

    Партиальный коммит по упаковке: `package_type` (BOX/MONOPALLET/...) — короб/моно
    раздельно; всё не выбранное остаётся в черновике для последующих сборок. Без
    фильтра коммитит весь черновик и soft-delete'ит его. Новинки и обычные товары
    на один склад идут ОДНОЙ заявкой (заявка с новинкой получает префикс 🆕 в
    комментарии).
    """
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    try:
        distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid distribution: {e}") from None

    # Схлопываем дубли (nm_id, package_type): старые черновики с задвоенными
    # строками иначе задвоят отгрузку (qty по баркоду суммируется в корзину).
    distribution.rows = _dedupe_rows(distribution.rows)

    if not distribution.rows:
        raise HTTPException(status_code=400, detail="Distribution has no rows")

    # Партиальный коммит по упаковке (короб/моно). newcomer-set считаем по ВСЕМ
    # строкам — он нужен лишь для пометки 🆕 в комментарии заявки (новинки и
    # обычные на один склад едут одной заявкой, не разделяются).
    norm_pkg = (package_type or "").strip().upper() or None

    newcomer_nm_ids = await fetch_newcomer_nm_ids(db, project_id, {row.nm_id for row in distribution.rows})

    def _pkg_selected(rpkg: str) -> bool:
        # Срез упаковки = вкладка UI, а не точный тип:
        # «Короб» (norm_pkg=BOX) включает BOX И SUPERSAFE (всё, кроме моно);
        # «Моно» (MONOPALLET) — только моно. Иначе SUPERSAFE-строки молча
        # оставались бы в черновике после «Короб»-коммита (он не закрывался).
        if not norm_pkg:
            return True
        if norm_pkg == "MONOPALLET":
            return rpkg == "MONOPALLET"
        if norm_pkg == "BOX":
            return rpkg != "MONOPALLET"
        return rpkg == norm_pkg

    def _is_selected(r: AssemblyDraftRow) -> bool:
        return _pkg_selected(r.package_type or "BOX")

    commit_rows = [r for r in distribution.rows if _is_selected(r)]
    leftover_rows = [r for r in distribution.rows if not _is_selected(r)]
    if norm_pkg and not commit_rows:
        raise HTTPException(
            status_code=400,
            detail="Нет строк выбранной упаковки для сборки",
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

    # 2. Group items per (source_ff_id, target_wb_name, package_type) tuple.
    # One AssemblyRequest = one transport unit = one package_type; новинки и обычные
    # на один склад в ОДНОЙ заявке. pair_has_newcomer помечает группы с новинкой —
    # такая заявка получит префикс 🆕 в комментарии.
    # pair_items[(src_id, wb_name, pkg)] -> {barcode: total_qty}
    pair_items: dict[tuple[int, str, str], dict[str, int]] = {}
    pair_has_newcomer: dict[tuple[int, str, str], bool] = {}
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
            key = (src_id, wb_name, pkg)
            bucket = pair_items.setdefault(key, {})
            bucket[row.barcode] = bucket.get(row.barcode, 0) + qty
            if is_new:
                pair_has_newcomer[key] = True

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
        for (source_ff_id, target_wb_name, package_type), barcodes in pair_items.items():
            number = await _next_number(db, project_id, "ASM", AssemblyRequest)
            if pair_has_newcomer.get((source_ff_id, target_wb_name, package_type)):
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
                source_draft_id=draft_id,
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
# «Заявка-юнит» = (source_ff × target_wb × package_type). Новинки и обычные товары
# на один склад живут в ОДНОМ юните. «Передать на ФФ» вырезает юнит из rows в
# замороженный handed_units (правки распределения его больше не трогают). «В
# сборку» создаёт из снимка AssemblyRequest.


def _norm_pkg(package_type: str | None) -> PackageTypeStr:
    p = (package_type or "BOX").strip().upper() or "BOX"
    if p not in ("BOX", "MONOPALLET", "SUPERSAFE"):
        raise HTTPException(status_code=400, detail=f"Invalid package_type: {package_type!r}")
    return cast(PackageTypeStr, p)


def _find_handed_index(units: list[HandedUnit], ff: int, wb: str, pkg: str) -> int | None:
    for i, u in enumerate(units):
        if u.source_ff_id == ff and u.target_wb_name == wb and (u.package_type or "BOX") == pkg:
            return i
    return None


def _normalize_handed_units(units: list[HandedUnit]) -> list[HandedUnit]:
    """Схлопнуть снимки одного ключа (ff, wb, pkg) в один: позиции суммируются по
    баркоду, статус 'handed' побеждает 'draft'. Нужно для старых черновиков, где
    новинки и обычные были разнесены в РАЗНЫЕ снимки одного склада (до объединения
    юнитов). Для новых черновиков — no-op (ключи и так уникальны)."""
    by_key: dict[tuple[int, str, str], HandedUnit] = {}
    order: list[tuple[int, str, str]] = []
    for u in units:
        key = (u.source_ff_id, u.target_wb_name, u.package_type or "BOX")
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = u.model_copy(deep=True)
            order.append(key)
            continue
        items_by_bc = {it.barcode: it for it in existing.items}
        for it in u.items:
            dup = items_by_bc.get(it.barcode)
            if dup is not None:
                dup.qty += it.qty
            else:
                new_it = it.model_copy(deep=True)
                existing.items.append(new_it)
                items_by_bc[it.barcode] = new_it
        if u.status == "handed":
            existing.status = "handed"
    return [by_key[k] for k in order]


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
    has_newcomer: bool,
    barcodes: dict[str, int],
    nom_map: dict[str, Nomenclature],
    distribution: AssemblyDraftDistribution,
    base_comment: str | None,
    source_draft_id: int,
) -> AssemblyRequest:
    """Создать один AssemblyRequest (IN_PROGRESS) из набора баркод→qty.
    has_newcomer=True (в наборе есть товар-новинка) → префикс 🆕 в комментарии."""
    number = await _next_number(db, project_id, "ASM", AssemblyRequest)
    if has_newcomer:
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
        source_draft_id=source_draft_id,
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
) -> tuple[dict[str, HandedUnitItem], list[AssemblyDraftRow]]:
    """Вырезать поток ff→wb из строк (любой товар: новинки и обычные вместе).
    Возвращает (позиции по баркоду, остаток строк). Σsrc и Σtgt падают на одну
    величину → строки остаются сбалансированными."""
    sid = str(source_ff_id)
    carved: dict[str, HandedUnitItem] = {}
    remaining: list[AssemblyDraftRow] = []
    for row in rows:
        if (row.package_type or "BOX") != norm_pkg:
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


def _freeze_unit_in_place(
    distribution: AssemblyDraftDistribution,
    source_ff_id: int,
    target_wb_name: str,
    norm_pkg: PackageTypeStr,
) -> int | None:
    """Заморозить юнит: вырезать поток ff→wb из rows (новинки + обычные вместе)
    и слить с уже существующим снимком этого склада, если он есть (старые
    черновики с раздельными снимками). Мутирует distribution; статус юнита
    становится handed. Возвращает индекс юнита в handed_units или None, если
    позиций нет ни в rows, ни в снимке."""
    distribution.handed_units = _normalize_handed_units(distribution.handed_units)
    carved, remaining = _carve_unit_from_rows(distribution.rows, source_ff_id, target_wb_name, norm_pkg)
    distribution.rows = remaining

    units = list(distribution.handed_units)
    idx = _find_handed_index(units, source_ff_id, target_wb_name, norm_pkg)
    if idx is not None:
        if carved:
            by_bc = {it.barcode: it for it in units[idx].items}
            for bc, it in carved.items():
                dup = by_bc.get(bc)
                if dup is not None:
                    dup.qty += it.qty
                else:
                    units[idx].items.append(it)
                    by_bc[bc] = it
        units[idx].status = "handed"
    else:
        if not carved:
            return None
        units.append(
            HandedUnit(
                source_ff_id=source_ff_id,
                target_wb_name=target_wb_name,
                package_type=norm_pkg,
                status="handed",
                items=list(carved.values()),
            ),
        )
        idx = len(units) - 1
    distribution.handed_units = units
    return idx


async def hand_off_unit(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    source_ff_id: int,
    target_wb_name: str,
    package_type: str,
) -> AssemblyDraftRead:
    """«Передать на ФФ»: заморозить заявку-юнит со статусом handed. Если
    позиций для передачи нет и снимка нет — 400."""
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    norm_pkg = _norm_pkg(package_type)

    idx = _freeze_unit_in_place(distribution, source_ff_id, target_wb_name, norm_pkg)
    if idx is None:
        raise HTTPException(status_code=400, detail="В заявке нет позиций для передачи")
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
) -> AssemblyDraftRead:
    """«Вернуть в черновик»: убрать handed-юнит и влить его позиции обратно в rows."""
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    norm_pkg = _norm_pkg(package_type)
    distribution.handed_units = _normalize_handed_units(distribution.handed_units)
    units = list(distribution.handed_units)
    idx = _find_handed_index(units, source_ff_id, target_wb_name, norm_pkg)
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
) -> AssemblyDraftCommitResponse:
    """«В сборку»: создать AssemblyRequest из юнита и убрать его из черновика.
    Юнит в rows замораживается неявно (отдельный шаг «Передать на ФФ» не нужен).
    Если черновик опустел (нет rows и handed_units) — soft-delete."""
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    norm_pkg = _norm_pkg(package_type)
    idx = _freeze_unit_in_place(distribution, source_ff_id, target_wb_name, norm_pkg)
    if idx is None:
        raise HTTPException(status_code=404, detail="Заявка не найдена: нет позиций для сборки")
    units = list(distribution.handed_units)

    unit = units[idx]
    barcodes: dict[str, int] = {}
    for it in unit.items:
        if it.qty > 0:
            barcodes[it.barcode] = barcodes.get(it.barcode, 0) + it.qty
    if not barcodes:
        raise HTTPException(status_code=400, detail="В заявке нет позиций для сборки")
    nom_map = await _resolve_nomenclature(db, project_id, set(barcodes))

    # Новинка определяется по товару (derived из nomenclature) → пометка 🆕 в заявке.
    newcomer_set = await fetch_newcomer_nm_ids(db, project_id, {it.nm_id for it in unit.items})
    has_newcomer = any(it.nm_id in newcomer_set for it in unit.items)

    created_ids: list[int] = []
    try:
        req = await _create_one_request(
            db,
            project_id,
            source_ff_id=source_ff_id,
            target_wb_name=target_wb_name,
            package_type=norm_pkg,
            has_newcomer=has_newcomer,
            barcodes=barcodes,
            nom_map=nom_map,
            distribution=distribution,
            base_comment=draft.comment,
            source_draft_id=draft_id,
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

    distribution.handed_units = _normalize_handed_units(distribution.handed_units)
    units = list(distribution.handed_units)
    idx = _find_handed_index(units, source_ff_id, target_wb_name, norm_pkg)
    if idx is not None:
        if units[idx].status != "draft":
            raise HTTPException(status_code=400, detail="Заявка передана на ФФ — правка запрещена")
        units[idx].items = clean
        distribution.handed_units = units
    else:
        _carved, remaining = _carve_unit_from_rows(distribution.rows, source_ff_id, target_wb_name, norm_pkg)
        distribution.rows = remaining
        distribution.handed_units = [
            *units,
            HandedUnit(
                source_ff_id=source_ff_id,
                target_wb_name=target_wb_name,
                package_type=norm_pkg,
                status="draft",
                items=clean,
            ),
        ]
    draft.distribution = distribution.model_dump(mode="json")
    await db.commit()
    await db.refresh(draft)
    return await to_read_model(db, project_id, draft)


async def move_unit(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    source_ff_id: int,
    target_wb_name: str,
    package_type: str,
    new_target_wb_name: str,
) -> AssemblyDraftRead:
    """«Сменить склад WB»: перенести заявку-юнит этого ФФ на другой WB-склад.
    Двигается только поток ff→wb (для этого ФФ), баланс матрицы сохраняется.
    Источник — авто (вырезаем поток из rows) или ручной черновик (берём снимок);
    переданный на ФФ (handed) переносить нельзя. На складе-получателе: если уже
    есть ручной черновик-юнит — позиции сливаются по баркоду, иначе поток
    возвращается в rows как (ff → new_wb)."""
    new_wb = (new_target_wb_name or "").strip()
    if not new_wb:
        raise HTTPException(status_code=400, detail="Не указан склад-получатель")
    if new_wb == target_wb_name:
        raise HTTPException(status_code=400, detail="Заявка уже на этом складе")

    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    norm_pkg = _norm_pkg(package_type)
    distribution.handed_units = _normalize_handed_units(distribution.handed_units)
    units = list(distribution.handed_units)

    # 1. Извлечь позиции исходного юнита: ручной снимок (draft) И авто-поток из rows
    # (смешанный случай legacy-черновика — переезжает ВСЁ, иначе rows-часть осталась
    # бы на старом складе при объединённой карточке на фронте).
    src_idx = _find_handed_index(units, source_ff_id, target_wb_name, norm_pkg)
    if src_idx is not None and units[src_idx].status != "draft":
        raise HTTPException(status_code=400, detail="Заявка передана на ФФ — сначала верните в черновик")
    moved_by_bc: dict[str, HandedUnitItem] = {}
    if src_idx is not None:
        for it in units.pop(src_idx).items:
            dup = moved_by_bc.get(it.barcode)
            if dup is not None:
                dup.qty += it.qty
            else:
                moved_by_bc[it.barcode] = it.model_copy(deep=True)
    carved, remaining = _carve_unit_from_rows(distribution.rows, source_ff_id, target_wb_name, norm_pkg)
    distribution.rows = remaining
    for bc, it in carved.items():
        dup = moved_by_bc.get(bc)
        if dup is not None:
            dup.qty += it.qty
        else:
            moved_by_bc[bc] = it
    if not moved_by_bc:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    moved = list(moved_by_bc.values())

    # 2. Влить в склад-получатель: merge в ручной черновик или вернуть в rows.
    dest_idx = _find_handed_index(units, source_ff_id, new_wb, norm_pkg)
    if dest_idx is not None:
        if units[dest_idx].status != "draft":
            raise HTTPException(
                status_code=400,
                detail="На складе-получателе заявка уже передана на ФФ — сначала верните её в черновик",
            )
        by_bc = {it.barcode: it for it in units[dest_idx].items}
        for it in moved:
            existing = by_bc.get(it.barcode)
            if existing:
                existing.qty += it.qty
            else:
                new_it = HandedUnitItem(nm_id=it.nm_id, barcode=it.barcode, vendor_code=it.vendor_code, qty=it.qty)
                units[dest_idx].items.append(new_it)
                by_bc[it.barcode] = new_it
        distribution.handed_units = units
    else:
        distribution.handed_units = units
        sid = str(source_ff_id)
        rows_by_key: dict[tuple[int, str], AssemblyDraftRow] = {
            (r.nm_id, r.package_type or "BOX"): r for r in distribution.rows
        }
        for it in moved:
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
            row.tgt[new_wb] = row.tgt.get(new_wb, 0) + it.qty

    if new_wb not in distribution.target_warehouse_names:
        distribution.target_warehouse_names = [*distribution.target_warehouse_names, new_wb]

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
) -> AssemblyDraftRead:
    """Удалить заявку-юнит из черновика целиком (товар остаётся на ФФ, не
    отгружается). Убираем ручной снимок И вырезаем авто-поток из rows (смешанный
    случай: часть в снимке, часть в rows). Переданный на ФФ удалять нельзя
    (сначала вернуть в черновик). Пустой черновик → soft-delete."""
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    norm_pkg = _norm_pkg(package_type)

    distribution.handed_units = _normalize_handed_units(distribution.handed_units)
    units = list(distribution.handed_units)
    idx = _find_handed_index(units, source_ff_id, target_wb_name, norm_pkg)
    if idx is not None and units[idx].status == "handed":
        raise HTTPException(status_code=400, detail="Заявка передана на ФФ — сначала верните в черновик")

    # Вырезать авто-поток из rows (отбросить) — и для смешанного случая тоже.
    carved, remaining = _carve_unit_from_rows(distribution.rows, source_ff_id, target_wb_name, norm_pkg)
    distribution.rows = remaining
    if idx is not None:
        del units[idx]
        distribution.handed_units = units
    elif not carved:
        raise HTTPException(status_code=404, detail="Заявка не найдена")

    draft.distribution = distribution.model_dump(mode="json")
    if not (distribution.rows or distribution.handed_units):
        draft.soft_delete()
    await db.commit()
    await db.refresh(draft)
    return await to_read_model(db, project_id, draft)
