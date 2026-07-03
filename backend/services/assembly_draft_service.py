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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
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
    CommitSupply,
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

# Advisory-lock namespace для get_or_create_current_draft: сериализует
# конкурентные POST /assembly/drafts/current одного project_id, иначе синглтон
# не синглтон (два пустых черновика) либо двойной merge задваивает количества.
# 0x41534D = 'ASM'. Lock снимается на commit/rollback транзакции.
_CURRENT_DRAFT_LOCK_NS = 0x41534D


def _dedupe_rows(rows: list[AssemblyDraftRow]) -> list[AssemblyDraftRow]:
    """Схлопнуть дубли строк по (nm_id, package_type, barcode), оставляя ПЕРВУЮ.

    Старая генерация черновика (до перехода на Set уникальных nm_id) могла
    обработать один SKU дважды → дублирующиеся строки. Дубль спурьёзный: первая
    строка несёт полную аллокацию, повторная — остаток истощённого FF-пула.
    Суммировать нельзя — commit_draft складывает qty по баркоду в корзину
    (ФФ, WB, упаковка) и задвоил бы отгрузку. Поэтому keep-first.

    ⚠ Ключ включает `barcode`: одна WB-карточка (nm_id) может иметь НЕСКОЛЬКО
    баркодов (размерные варианты); карточка без `article_wb` уходит в nm_id=0.
    Без barcode в ключе разные баркоды одного nm_id схлопнулись бы в один →
    commit отгрузил бы чужой физический товар (потеря/мисаттрибуция). Истинный
    спурьёзный дубль (тот же баркод) по-прежнему схлопывается keep-first.
    """
    seen: set[tuple[int, str, str, bool]] = set()
    out: list[AssemblyDraftRow] = []
    for r in rows:
        # as_is в ключе: частичная паллета «Оставить так» и обычная строка того же
        # SKU — РАЗНЫЕ строки (keep-first без as_is молча съел бы одну из них).
        key = (r.nm_id, r.package_type or "BOX", r.barcode or "", bool(r.as_is))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _merge_rows(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Слить два списка строк распределения по ключу (nm_id, package_type, barcode).

    Строки одного ключа суммируются поэлементно: `src` (по ff-id-строке) и `tgt`
    (по wb-имени). `vendor_code` берётся от ПЕРВОЙ увиденной непустой строки.
    Порядок `existing` сохраняется, новые ключи `incoming` дописываются в конце.
    Возвращает новый список dict-строк, по одной на уникальный ключ.

    ⚠ Ключ включает `barcode` (см. `_dedupe_rows`): одна WB-карточка (nm_id)
    может нести несколько баркодов (размерные варианты), а карточка без
    `article_wb` — nm_id=0. Без barcode в ключе разные физические товары одного
    nm_id (или все nm_id=0) схлопнулись бы в одну строку → commit отгрузил бы
    чужой товар / молча потерял количество. С barcode в ключе одинаковые баркоды
    по-прежнему суммируются (истинный merge), разные — остаются раздельными.

    Чистый помощник без БД — общий код для `merge_drafts` (слияние черновиков) и
    `add_rows_to_draft` (дозалив строк в существующий черновик). Складывает (не
    keep-first, в отличие от `_dedupe_rows`): наивный append того же ключа без
    суммирования молча терял бы количество.
    """

    def _key(r: dict) -> tuple[int, str, str, bool]:
        # as_is в ключе: частичная «Оставить так» не суммируется с обычной строкой
        # того же SKU (иначе флаг расползся бы на целые паллеты или потерялся).
        return (
            int(r.get("nm_id") or 0),
            str(r.get("package_type") or "BOX"),
            str(r.get("barcode") or ""),
            bool(r.get("as_is")),
        )

    by_key: dict[tuple[int, str, str, bool], dict] = {}
    order: list[tuple[int, str, str, bool]] = []
    for src_list in (existing, incoming):
        for row in src_list:
            key = _key(row)
            cur = by_key.get(key)
            if cur is None:
                # Глубокая копия src/tgt — не делим mutable-словари с входом.
                by_key[key] = {
                    "nm_id": key[0],
                    "barcode": key[2],
                    "vendor_code": row.get("vendor_code") or "",
                    "src": {str(k): int(v or 0) for k, v in (row.get("src") or {}).items()},
                    "tgt": {str(k): int(v or 0) for k, v in (row.get("tgt") or {}).items()},
                    "package_type": key[1],
                    "as_is": key[3],
                }
                order.append(key)
                continue
            for wh, qty in (row.get("src") or {}).items():
                cur["src"][str(wh)] = cur["src"].get(str(wh), 0) + int(qty or 0)
            for wb, qty in (row.get("tgt") or {}).items():
                cur["tgt"][str(wb)] = cur["tgt"].get(str(wb), 0) + int(qty or 0)
            if not cur["vendor_code"] and row.get("vendor_code"):
                cur["vendor_code"] = row["vendor_code"]
    return [by_key[k] for k in order]


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
    """Извлечь все nm_id из draft.distribution: rows И prebook (toJSON-friendly).

    Prebook обязателен: SKU, лежащий ТОЛЬКО в предброни, — тоже часть черновика.
    Без него новинка из предброни выпадала из newcomer_nm_ids → фронт терял
    бейдж 🆕 и newcomer-логику (гистерезис: «одинаковые» товары вели себя по-разному
    в зависимости от того, в rows они или в prebook).
    """
    dist = draft.distribution if isinstance(draft.distribution, dict) else {}
    out: set[int] = set()
    for part in ("rows", "prebook"):
        for r in dist.get(part) or []:
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


async def _get_draft_for_update(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
) -> AssemblyDraft | None:
    """`get_draft` с блокировкой строки (`FOR UPDATE`) — для commit-путей.

    Сериализует конкурентные/повторные коммиты одного черновика: второй вызов
    ждёт первый, затем перечитывает уже soft-deleted строку → None (404), не
    задваивая заявки. (`expire_on_commit=False` + отдельные сессии не дают
    кросс-инвалидизации, поэтому защита нужна на уровне БД.)
    """
    result = await db.execute(
        select(AssemblyDraft)
        .where(
            AssemblyDraft.id == draft_id,
            AssemblyDraft.project_id == project_id,
            AssemblyDraft.is_deleted == False,  # noqa: E712
        )
        .with_for_update()
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


async def remove_rows_by_nm(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    nm_ids: list[int],
) -> AssemblyDraft:
    """Убрать из черновика все строки и handed-юниты указанных SKU (удаление неликвида
    из прогноза). handed-юниты с несколькими SKU очищаются от этих nm_id; пустые
    юниты выбрасываются. Остальные поля черновика не трогаются. 404 если не найден."""
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    nm_set = set(nm_ids)
    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    distribution.rows = [r for r in distribution.rows if r.nm_id not in nm_set]
    kept_units = []
    for unit in distribution.handed_units:
        unit.items = [it for it in unit.items if it.nm_id not in nm_set]
        if unit.items:
            kept_units.append(unit)
    distribution.handed_units = kept_units

    draft.distribution = distribution.model_dump(mode="json")
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

    # Rows merged element-wise by (nm_id, package_type) via the shared helper.
    # Start from survivor's rows; fold each other draft in, preserving order.
    merged_rows: list[dict] = [r.model_dump() for r in merged.rows]
    # Предбронь сливается так же, как rows (по nm_id×pkg), чтобы не терять её при мерже
    # дублей текущего черновика.
    merged_prebook: list[dict] = [r.model_dump() for r in merged.prebook]

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

        # Merge rows: sum (nm_id, pkg) element-wise, append new keys.
        merged_rows = _merge_rows(merged_rows, [r.model_dump() for r in other_dist.rows])
        merged_prebook = _merge_rows(merged_prebook, [r.model_dump() for r in other_dist.prebook])

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

    merged.rows = [AssemblyDraftRow.model_validate(r) for r in merged_rows]
    merged.prebook = [AssemblyDraftRow.model_validate(r) for r in merged_prebook]
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


async def get_or_create_current_draft(
    db: AsyncSession,
    project_id: int,
) -> AssemblyDraft:
    """Вернуть единственный «текущий» черновик проекта (синглтон).

    Политика «всегда ровно один активный черновик» для единой страницы «Сборка»:
    - нет черновиков → создать пустой;
    - один → вернуть его (не плодим);
    - несколько → объединить все в один (merge_drafts) и вернуть survivor.

    Так «Создать заявку из потребности», редактор и автосейв всегда работают над
    одним и тем же черновиком, а ранее накопленные параллельные черновики (прод)
    лениво консолидируются при первом входе.

    Конкурентные вызовы (StrictMode double-mount, две вкладки) сериализуются
    advisory-lock'ом по project_id: lock держится до commit'а внутри
    create_draft/merge_drafts, поэтому следующий ждущий запрос перечитывает
    list_drafts уже после фиксации и видит ровно один черновик. Без этого гонка
    создавала два пустых черновика или задваивала merge.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :pid)"),
        {"ns": _CURRENT_DRAFT_LOCK_NS, "pid": project_id},
    )
    drafts = await list_drafts(db, project_id)
    if not drafts:
        return await create_draft(
            db,
            project_id,
            AssemblyDraftCreate(distribution=AssemblyDraftDistribution()),
        )
    if len(drafts) == 1:
        return drafts[0]
    return await merge_drafts(db, project_id, [d.id for d in drafts])


async def add_rows_to_draft(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    rows: list[AssemblyDraftRow],
) -> AssemblyDraft:
    """Дозалить пачку строк в СУЩЕСТВУЮЩИЙ черновик, не теряя ручных правок.

    В отличие от `update_draft` (full-replace, затирает `handed_units`), здесь
    строки сливаются с `distribution['rows']` через `_merge_rows`: совпадающий
    (nm_id, package_type) суммируется поэлементно (наивный append того же nm_id
    в `_dedupe_rows` молча терял бы количество). `source_warehouse_ids` и
    `target_warehouse_names` объединяются с ключами входящих src/tgt.
    `handed_units`, `cold_start_shares`, `pallets_*` не трогаются.

    404, если черновик не найден / soft-deleted. Кэш не инвалидируется
    (черновики читаются живьём — как в `update_draft`).
    """
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})

    incoming = [r.model_dump() for r in rows]
    existing = [r.model_dump() for r in distribution.rows]
    merged_rows = _merge_rows(existing, incoming)
    distribution.rows = [AssemblyDraftRow.model_validate(r) for r in merged_rows]

    # Union source_warehouse_ids (из ключей src входящих строк, как int).
    src_ids: list[int] = list(distribution.source_warehouse_ids)
    src_ids_set: set[int] = set(src_ids)
    for r in incoming:
        for wid in (r.get("src") or {}):
            iwid = int(wid)
            if iwid not in src_ids_set:
                src_ids.append(iwid)
                src_ids_set.add(iwid)
    distribution.source_warehouse_ids = src_ids

    # Union target_warehouse_names (из ключей tgt входящих строк).
    tgt_names: list[str] = list(distribution.target_warehouse_names)
    tgt_names_set: set[str] = set(tgt_names)
    for r in incoming:
        for name in (r.get("tgt") or {}):
            sname = str(name)
            if sname not in tgt_names_set:
                tgt_names.append(sname)
                tgt_names_set.add(sname)
    distribution.target_warehouse_names = tgt_names

    # Persist JSONB как в update_draft (reassign — модель сериализуем целиком).
    draft.distribution = distribution.model_dump(mode="json")
    await db.commit()
    await db.refresh(draft)
    return draft


# ─── Commit (draft -> N AssemblyRequests) ───────────────────────────────────


def _allocate_pairs(
    src: dict[str, int],
    tgt: dict[str, int],
) -> dict[tuple[int, str], int]:
    """
    Distribute one row's quantities across (source_warehouse_id, target_wb_name)
    pairs PRESERVING BOTH MARGINALS (transportation feasibility).

    Inputs:
      src = {warehouse_id_str: qty}  (sum == row total when balanced)
      tgt = {wb_warehouse_name: qty}  (sum == row total when balanced)

    Returns: {(source_id_int, target_name): qty}, each qty > 0, with
      Σ_target alloc[(sid, ·)] == src[sid]   for every source, and
      Σ_source alloc[(·, tname)] == tgt[tname]   for every target,
    when the row is balanced (Σsrc == Σtgt). Total shipped = min(Σsrc, Σtgt).

    Algorithm — greedy north-west-corner depletion over sources sorted by id and
    targets sorted by name: each cell takes min(remaining_src, remaining_tgt),
    draining one pool, then advances. This is a feasible integer transportation
    solution, so EACH source ships exactly its own stock.

    ⚠ The old joint pro-rata (sv*tv/Σ) preserved only the GRAND TOTAL, not the
    per-source/per-target marginals: e.g. src={1:1, 2:1}, tgt={A:1, B:1} rounded
    to {(1,A):1, (1,B):1} → source 1 shipped 2 while source 2 shipped 0. Commit
    then created an AssemblyRequest on warehouse 1 for stock it did not have and
    dropped warehouse 2 entirely. The mirror `allocatePairs` in
    frontend assemblyPreview.ts uses the identical algorithm so the preview
    matches what commit creates.
    """
    src_items: list[tuple[int, int]] = sorted(
        ((int(k), int(v)) for k, v in src.items() if int(v or 0) > 0),
        key=lambda x: x[0],
    )
    tgt_items: list[tuple[str, int]] = sorted(
        ((str(k), int(v)) for k, v in tgt.items() if int(v or 0) > 0),
        key=lambda x: x[0],
    )
    if not src_items or not tgt_items:
        return {}

    # Remaining quantities, parallel to *_items (mutated as pools drain).
    src_rem = [v for _, v in src_items]
    tgt_rem = [v for _, v in tgt_items]

    allocation: dict[tuple[int, str], int] = {}
    i = j = 0
    while i < len(src_items) and j < len(tgt_items):
        q = src_rem[i] if src_rem[i] < tgt_rem[j] else tgt_rem[j]
        if q > 0:
            pair = (src_items[i][0], tgt_items[j][0])
            allocation[pair] = allocation.get(pair, 0) + q
        src_rem[i] -= q
        tgt_rem[j] -= q
        if src_rem[i] == 0:
            i += 1
        if tgt_rem[j] == 0:
            j += 1

    return allocation


async def commit_draft(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    package_type: str | None = None,
    pallet_counts: dict[str, int] | None = None,
    supplies: list[CommitSupply] | None = None,
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

    Идемпотентность: черновик берётся `FOR UPDATE`, поэтому два параллельных
    (или повторный после двойного клика/таймаута) коммита одного черновика
    сериализуются — первый создаёт заявки и soft-delete'ит/обрезает черновик,
    второй на разлоке перечитывает уже-удалённую строку и получает 404 (или
    «нет строк выбранной упаковки» при частичном коммите), не задваивая заявки.
    """
    draft = await _get_draft_for_update(db, project_id, draft_id)
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

    # Доступно по баркоду в выбранном срезе (Σ tgt) + баркод→nm — для валидации supplies.
    draft_bc_total: dict[str, int] = {}
    draft_bc_nm: dict[str, int] = {}
    for row in commit_rows:
        if not row.barcode:
            raise HTTPException(status_code=400, detail=f"Row {row.nm_id}: barcode is required")
        draft_bc_total[row.barcode] = draft_bc_total.get(row.barcode, 0) + sum(int(v or 0) for v in row.tgt.values())
        draft_bc_nm[row.barcode] = row.nm_id

    if supplies is not None:
        # Явные отгрузки ФФ→склад (режим «только целые паллеты»): заявки строим ровно
        # из них, минуя pro-rata. Берём только отгрузки выбранного среза упаковки.
        supplied_bc: dict[str, int] = {}
        for s in supplies:
            spkg = (s.package_type or "BOX").strip().upper() or "BOX"
            if not _pkg_selected(spkg):
                continue
            key = (int(s.source_ff_id), s.target_wb_name, spkg)
            bucket = pair_items.setdefault(key, {})
            for bc, raw_qty in s.items.items():
                qty = int(raw_qty or 0)
                if qty <= 0:
                    continue
                if bc not in draft_bc_total:
                    raise HTTPException(status_code=400, detail=f"Supply barcode not in draft: {bc}")
                bucket[bc] = bucket.get(bc, 0) + qty
                supplied_bc[bc] = supplied_bc.get(bc, 0) + qty
                if draft_bc_nm.get(bc) in newcomer_nm_ids:
                    pair_has_newcomer[key] = True
        # Защита от раздувания: по каждому баркоду нельзя отгрузить больше, чем в черновике.
        for bc, q in supplied_bc.items():
            if q > draft_bc_total.get(bc, 0):
                raise HTTPException(
                    status_code=400,
                    detail=f"Supply qty for {bc} exceeds draft ({q} > {draft_bc_total.get(bc, 0)})",
                )
        # Убираем пустые корзины (могли остаться от нулевых отгрузок).
        pair_items = {k: v for k, v in pair_items.items() if any(q > 0 for q in v.values())}
    else:
        for row in commit_rows:
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

    # Паллет на заявку (опц., из фронта): ключ "{ff_id}::{wb_name}::{pkg}". Иначе —
    # плоский pallets_count черновика (старое поведение). Считаем здесь, чтобы у
    # каждой заявки было своё число паллет (раньше всем ставился общий pallets_count=1).
    def _pallets_for(src_id: int, wb_name: str, pkg: str) -> int:
        if pallet_counts:
            override = pallet_counts.get(f"{src_id}::{wb_name}::{pkg}")
            if override is not None:
                return max(1, int(override))
        return pallets

    # 6. Create AssemblyRequest per pair (atomic — single transaction)
    from backend.services.assembly.status import _log_status_change

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
                pallets_count=_pallets_for(source_ff_id, target_wb_name, package_type),
                pallet_weight_kg=pallet_weight,
                comment=req_comment,
                package_type=package_type,
                source_draft_id=draft_id,
            )
            db.add(assembly_req)
            await db.flush()

            # Стартовая запись истории (None → IN_PROGRESS) — как в _create_one_request:
            # без неё аналитика потока не знает момент входа в IN_PROGRESS.
            await _log_status_change(
                db,
                project_id,
                assembly_req.id,
                None,
                AssemblyStatus.IN_PROGRESS,
                changed_by="system",
                comment="Создана из черновика",
            )

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
        # (передан на ФФ) юниты ИЛИ предбронь (коробы, ждущие паллету) — оставляем
        # черновик; иначе soft-delete. Предбронь НЕ коммитится (только rows), но и не
        # теряется при коммите (сохраняется в distribution).
        if leftover_rows or distribution.handed_units or distribution.prebook:
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

    await invalidate_cache("reports:assembly_flow")

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

    # Стартовая запись истории (None → IN_PROGRESS): без неё аналитика потока
    # сборки не знает момент входа в IN_PROGRESS и падает на fallback created_at.
    from backend.services.assembly.status import _log_status_change

    await _log_status_change(
        db,
        project_id,
        req.id,
        None,
        AssemblyStatus.IN_PROGRESS,
        changed_by="system",
        comment="Создана из черновика",
    )

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
    Если черновик опустел (нет rows и handed_units) — soft-delete.

    Черновик берётся `FOR UPDATE` — повторный/параллельный «В сборку» по тому же
    юниту сериализуется (без задвоения заявки)."""
    draft = await _get_draft_for_update(db, project_id, draft_id)
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

    await invalidate_cache("reports:assembly_flow")

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
