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
from decimal import Decimal
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
    # Self-heal display: убрать устаревшие ручные снимки, задваивающие ФФ-сток с rows
    # (rows — источник истины). Не персистится — чинится при следующей записи.
    _reconcile_handed_with_rows(read.distribution)
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


async def get_drafts_reserved(
    db: AsyncSession,
    project_id: int,
    exclude_draft_id: int | None = None,
) -> dict[str, dict[str, int]]:
    """Резерв стока черновиками: barcode → {ff_warehouse_id(str) → qty}.

    Суммирует по ВСЕМ не-удалённым черновикам проекта (кроме `exclude_draft_id`)
    ТРИ источника distribution JSONB:
    - rows[].src (warehouse_id-строка → qty),
    - prebook[].src (та же структура),
    - handed_units[].items (ФФ юнита = source_ff_id, qty по items[].barcode).

    Фронт вычитает резерв из доступного ФФ-стока, чтобы параллельные черновики
    (в т.ч. категорийные) не планировали один товар дважды. Читаем сырой JSONB
    (не Pydantic): в проде встречается explicit null вместо [] / {} — модельная
    валидация items его не переживает, поэтому везде `.get(k) or []` / `or {}`.
    Пустые баркоды и qty ≤ 0 пропускаются.
    """
    query = (
        select(AssemblyDraft)
        .where(
            AssemblyDraft.project_id == project_id,
            AssemblyDraft.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
        )
        # Детерминированное усечение (свежие первыми) — как list_drafts; без ORDER BY
        # кап .limit() резал бы произвольные черновики и терял их резерв.
        .order_by(AssemblyDraft.updated_at.desc())
        .limit(500)
    )
    if exclude_draft_id is not None:
        query = query.where(AssemblyDraft.id != exclude_draft_id)
    result = await db.execute(query)
    drafts = list(result.scalars().all())

    reserved: dict[str, dict[str, int]] = {}

    def _add(barcode: str, ff_key: str, qty: object) -> None:
        if isinstance(qty, bool) or not isinstance(qty, (int, float, str)):
            return  # null/мусор в JSONB — пропускаем
        try:
            q = int(qty)
        except (TypeError, ValueError):
            return
        if not barcode or not ff_key or q <= 0:
            return
        bucket = reserved.setdefault(barcode, {})
        bucket[ff_key] = bucket.get(ff_key, 0) + q

    for d in drafts:
        dist = d.distribution if isinstance(d.distribution, dict) else {}
        dist = dist or {}
        # rows[].src и prebook[].src — одинаковая строчная структура.
        for row_list_key in ("rows", "prebook"):
            for row in dist.get(row_list_key) or []:
                if not isinstance(row, dict):
                    continue
                barcode = str(row.get("barcode") or "")
                for wid, qty in (row.get("src") or {}).items():
                    _add(barcode, str(wid), qty)
        # handed_units[].items — ФФ задаёт юнит (source_ff_id).
        for unit in dist.get("handed_units") or []:
            if not isinstance(unit, dict):
                continue
            ff = unit.get("source_ff_id")
            if ff is None:
                continue
            ff_key = str(ff)
            for item in unit.get("items") or []:
                if not isinstance(item, dict):
                    continue
                _add(str(item.get("barcode") or ""), ff_key, item.get("qty"))

    return reserved


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
    changed_by: str | None = None,
) -> AssemblyDraft:
    """Update mutable fields of a draft. Raises 404 if missing/deleted.

    Если `payload.event` задан (дозабор из предброни / запись из матрицы) — снапшотит
    СТАРЫЙ distribution в историю (`AssemblyDraftEvent`) для отката. Обычный autosave
    события не передаёт → не логируется.

    Входящий distribution проходит server-side ДЕЛЬТА-гейт `_subtract_in_transit`:
    транзит активных заявок вычитается только из ПРИРОСТА плана относительно уже
    сохранённого в БД черновика (baseline). Уже сохранённый план чистый (гейтован
    при записи) — безусловный вычет на каждом PUT повторно урезал бы его при
    каждом автосейве матрицы (PUT на каждый клик), и черновик «таял»."""
    import copy

    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    # CAS-гвард фоновых писателей: full-replace от stale-снимка молча затирал
    # свежие изменения (прод 2026-07-17: фоновая консолидация второй вкладки
    # воскресила очищенный черновик через 9с — 11 177 шт вернулись без события).
    # Клиент на 409 перечитывает черновик вместо записи.
    if payload.base_updated_at is not None and draft.updated_at != payload.base_updated_at:
        raise HTTPException(
            status_code=409,
            detail="DRAFT_VERSION_CONFLICT: черновик изменился в другой вкладке/окне — данные перечитаны",
        )

    before_distribution = copy.deepcopy(draft.distribution) if payload.event is not None else None

    if payload.name is not None:
        draft.name = payload.name
    if payload.distribution is not None:
        payload.distribution.rows = _dedupe_rows(payload.distribution.rows)
        _reconcile_handed_with_rows(payload.distribution)
        # «Один мир», server-side ДЕЛЬТА-гейт: план не может дублировать уже
        # едущее в активных заявках (вкл. PRE_DISTRIBUTED — резерв машины).
        # Клиентский reconcile при загрузке страницы НЕ защищает от stale-вкладки:
        # открытая до создания заявок вкладка автосейвом PUT'ила старые строки
        # поверх очищенных (прод-кейс «швабры апл» 2026-07-10: 7 PUT за 20с
        # вернули дубль 54 шт после клиентской очистки). Гейт на записи закрывает
        # гонку для ЛЮБОГО источника PUT.
        # Baseline = Σ tgt ТЕКУЩЕГО draft.distribution (в этот момент ещё старый):
        # транзит вычитается только из прироста относительно него, иначе каждый
        # автосейв матрицы повторно урезал бы уже чистый план (см. docstring
        # _subtract_in_transit). Сбой выборки не валит автосейв (best-effort).
        try:
            baseline = _plan_tgt_sums(AssemblyDraftDistribution.model_validate(draft.distribution or {}))
            await _subtract_in_transit(db, project_id, payload.distribution, baseline)
        except Exception:
            logger.warning("draft %s: in-transit гейт пропущен (ошибка выборки)", draft_id, exc_info=True)
        draft.distribution = payload.distribution.model_dump()
    if payload.comment is not None:
        draft.comment = payload.comment

    await db.commit()
    await db.refresh(draft)

    if payload.event is not None:
        from backend.services.assembly.draft_history import log_draft_event

        # Best-effort: черновик уже сохранён (commit выше) — сбой лога не валит PUT.
        try:
            await log_draft_event(
                db,
                project_id,
                draft_id,
                payload.event.event_type,
                summary=payload.event.summary,
                before_distribution=before_distribution,
                draft_updated_at=draft.updated_at,
                changed_by=changed_by,
            )
            await db.refresh(draft)
        except Exception:
            logger.warning("Failed to log draft event for draft_id=%s", draft_id, exc_info=True)
    return draft


async def remove_rows_by_nm(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    nm_ids: list[int],
) -> AssemblyDraft:
    """Убрать из черновика все строки, ПРЕДБРОНЬ и draft-снимки указанных SKU
    (удаление неликвида из прогноза / чистка SKU из ручной раскладки). Ручные
    снимки (status='draft') очищаются от этих nm_id, пустые выбрасываются;
    физически переданные (status='handed') НЕ трогаются — это факт выдачи товара
    оператору ФФ (сначала «Вернуть в черновик»). Остальные поля не трогаются.
    404 если не найден."""
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    import copy

    nm_set = set(nm_ids)
    before_distribution = copy.deepcopy(draft.distribution)
    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    distribution.rows = [r for r in distribution.rows if r.nm_id not in nm_set]
    distribution.prebook = [r for r in distribution.prebook if r.nm_id not in nm_set]
    # Бейджи «из предброни» (ключ nmId::wb) удаляемых SKU — иначе stale-метка
    # всплывёт, если SKU вернётся в черновик пересчётом.
    prefixes = tuple(f"{nm}::" for nm in nm_set)
    distribution.prebook_origin = [k for k in distribution.prebook_origin if not k.startswith(prefixes)]
    kept_units = []
    for unit in distribution.handed_units:
        # handed-юнит — физический факт передачи товара оператору ФФ (та же природа,
        # что гвард delete_draft): чистка неликвида его НЕ трогает — иначе черновик
        # молча «забывал» бы переданное, а /drafts/reserved отпускал бы его резерв.
        if (unit.status or "handed") == "handed":
            kept_units.append(unit)
            continue
        unit.items = [it for it in unit.items if it.nm_id not in nm_set]
        if unit.items:
            kept_units.append(unit)
    distribution.handed_units = kept_units

    draft.distribution = distribution.model_dump(mode="json")
    await db.commit()
    await db.refresh(draft)

    # История: ✕ из матрицы / чистка неликвида — значимое изменение, снапшотим
    # старый distribution для отката. Best-effort — сбой лога не валит удаление.
    try:
        from backend.services.assembly.draft_history import log_draft_event

        await log_draft_event(
            db,
            project_id,
            draft_id,
            "REMOVE_ROWS",
            summary=f"Убраны SKU из черновика: {len(nm_set)} шт (nm {', '.join(str(n) for n in sorted(nm_set)[:5])}{'…' if len(nm_set) > 5 else ''})",
            before_distribution=before_distribution,
            draft_updated_at=draft.updated_at,
        )
        await db.refresh(draft)
    except Exception:
        logger.warning("Failed to log REMOVE_ROWS event for draft_id=%s", draft_id, exc_info=True)
    return draft


def _has_live_handed_units(draft: AssemblyDraft) -> bool:
    """Есть ли в черновике юниты, реально переданные на ФФ (status=handed, непустые items).

    Такой черновик — единственная запись о физически переданном товаре (плюс его
    резерв в /drafts/reserved): молча удалить его нельзя. Читаем сырой JSONB
    (не Pydantic) — в проде встречается explicit null вместо []/{}.
    """
    dist = draft.distribution if isinstance(draft.distribution, dict) else {}
    for unit in (dist or {}).get("handed_units") or []:
        if not isinstance(unit, dict):
            continue
        if (unit.get("status") or "handed") == "handed" and (unit.get("items") or []):
            return True
    return False


async def delete_draft(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
) -> None:
    """Soft-delete a draft. Raises 404 if missing/already deleted.

    Гвард (паритет с delete_unit/set_unit_items/move_unit): handed-юнит — физический
    факт передачи товара оператору ФФ, и черновик — единственная запись об этом
    (плюс его резерв в /drafts/reserved). Удаление с живым handed-юнитом молча
    забывало бы переданный товар → другие черновики запланировали бы его повторно.
    """
    draft = await get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    if _has_live_handed_units(draft):
        raise HTTPException(
            status_code=400,
            detail="В черновике есть заявки, переданные на ФФ — сначала верните их в черновик",
        )
    draft.soft_delete()
    await db.commit()


async def clear_draft(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    changed_by: str | None = None,
) -> tuple[AssemblyDraft, list[str], list[str]]:
    """«Очистить черновик»: сброс наполнения + удаление категорийных черновиков.

    Обнуляет rows/prebook/prebook_origin/manual_nms/источники/цели/cold_start_shares,
    сохраняя handed_units (переданное на ФФ — физический факт) и category_scope.
    Для ОСНОВНОГО (бесскоупного) черновика дополнительно soft-delete'ит категорийные
    черновики проекта: они создаются с того же экрана «Сборка» и раньше переживали
    очистку вместе со своим наполнением и резервом (прод-баг 2026-07-21). Категорийные
    с живыми handed-юнитами не удаляются (гвард как в delete_draft) — возвращаются
    в kept. Очистка КАТЕГОРИЙНОГО черновика чистит только его самого.

    Одна транзакция; в историю пишется MATRIX_EDIT с before-снапшотом ОСНОВНОГО
    черновика (откат события вернёт его наполнение, но НЕ воскресит удалённые
    категорийные — их можно восстановить только руками через is_deleted).

    Returns (черновик, имена удалённых категорийных, имена оставленных).
    """
    import copy

    draft = await _get_draft_for_update(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    before_distribution = copy.deepcopy(draft.distribution)
    dist = AssemblyDraftDistribution.model_validate(draft.distribution or {})
    dist.rows = []
    dist.prebook = []
    dist.prebook_origin = []
    dist.manual_nms = []
    dist.source_warehouse_ids = []
    dist.target_warehouse_names = []
    dist.cold_start_shares = None
    draft.distribution = dist.model_dump()

    deleted_scoped: list[str] = []
    kept_scoped: list[str] = []
    if not _category_scope_set(draft):
        # FOR UPDATE и на категорийных: решение «удалять или нет» принимается по
        # handed-юнитам, а конкурентный hand_off/commit_unit (сами под FOR UPDATE)
        # мог бы добавить юнит между чтением снимка и soft_delete — TOCTOU-обход
        # гварда физически переданного товара (ревью MEDIUM).
        others = await db.execute(
            select(AssemblyDraft)
            .where(
                AssemblyDraft.project_id == project_id,
                AssemblyDraft.id != draft.id,
                AssemblyDraft.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
            )
            .order_by(AssemblyDraft.updated_at.desc())
            .limit(500)
            .with_for_update()
        )
        for d in others.scalars().all():
            if not _category_scope_set(d):
                continue
            if _has_live_handed_units(d):
                kept_scoped.append(d.name)
            else:
                d.soft_delete()
                deleted_scoped.append(d.name)

    await db.commit()
    await db.refresh(draft)

    from backend.services.assembly.draft_history import log_draft_event

    summary = "Черновик очищен (строки + предбронь)"
    if deleted_scoped:
        summary += f"; удалены категорийные: {', '.join(deleted_scoped)}"
    if kept_scoped:
        summary += f"; оставлены (переданы на ФФ): {', '.join(kept_scoped)}"
    # Best-effort: черновик уже сохранён (commit выше) — сбой лога не валит очистку.
    try:
        await log_draft_event(
            db,
            project_id,
            draft_id,
            "MATRIX_EDIT",
            summary=summary,
            before_distribution=before_distribution,
            draft_updated_at=draft.updated_at,
            changed_by=changed_by,
        )
        await db.refresh(draft)
    except Exception:
        logger.warning("Failed to log clear event for draft_id=%s", draft_id, exc_info=True)

    return draft, deleted_scoped, kept_scoped


def _category_scope_set(d: AssemblyDraft) -> frozenset[str]:
    """Категорийный скоуп черновика как множество (None и [] эквивалентны)."""
    dist = d.distribution if isinstance(d.distribution, dict) else {}
    return frozenset(str(c) for c in ((dist or {}).get("category_scope") or []))


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
    ValueError (роутер мапит в 400), если у объединяемых черновиков разный
    категорийный скоуп: слияние скоупленного с общим/чужим размыло бы правило
    «этот черновик — только эти категории».
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

    # Guard: категорийный скоуп сравнивается как множество (None ≡ []).
    if len({_category_scope_set(d) for d in drafts}) > 1:
        raise ValueError("Нельзя объединять черновики с разным категорийным скоупом")

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
    # ✋-флаги и бейджи «из предброни» не-survivor'ов обязаны пережить слияние:
    # manual_nms защищает ручной план SKU от авто-синка (иначе ленивая консолидация
    # POST /drafts/current молча отдаёт ручные решения пересчёту по потребности),
    # prebook_origin — провенанс паллет для бейджа. Union с сохранением порядка.
    manual_nms: list[int] = list(merged.manual_nms)
    manual_nms_set: set[int] = set(manual_nms)
    origin_keys: list[str] = list(merged.prebook_origin)
    origin_keys_set: set[str] = set(origin_keys)

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

        # Union manual_nms / prebook_origin: ручные решения и провенанс не теряются.
        for nm in other_dist.manual_nms:
            if nm not in manual_nms_set:
                manual_nms.append(nm)
                manual_nms_set.add(nm)
        for origin_key in other_dist.prebook_origin:
            if origin_key not in origin_keys_set:
                origin_keys.append(origin_key)
                origin_keys_set.add(origin_key)

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
    merged.manual_nms = manual_nms
    merged.prebook_origin = origin_keys
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

    Категорийные черновики (`distribution.category_scope` непуст) — параллельные
    рабочие пространства, в синглтоне НЕ участвуют: без фильтра консолидация либо
    молча сливала бы категорийный черновик в общий (потеря скоупа), либо падала
    на guard'e merge_drafts («разный категорийный скоуп»).
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :pid)"),
        {"ns": _CURRENT_DRAFT_LOCK_NS, "pid": project_id},
    )
    drafts = [d for d in await list_drafts(db, project_id) if not _category_scope_set(d)]
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
    # Дозабор мог заново заложить ФФ-сток, зарезервированный ручным снимком → сверить.
    _reconcile_handed_with_rows(distribution)

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


def _dec(x: object) -> Decimal:
    """Безопасный коэрс в Decimal (вес паллеты бывает float 0.0 или Decimal из БД)."""
    return Decimal(str(x or 0))


async def _fold_barcodes_into_request(
    db: AsyncSession,
    project_id: int,
    req: AssemblyRequest,
    barcodes: dict[str, int],
    nom_map: dict[str, Nomenclature],
    *,
    add_pallets: int,
    add_pallet_weight: object,
) -> None:
    """Долить набор barcode→qty в уже существующую сборку (повторный commit того же
    черновика на то же направление). Позиции суммируются по (nomenclature_id, barcode);
    паллеты складываются, `pallet_weight_kg` (вес одной паллеты) пересчитывается как
    взвешенное среднее. Так «дозабор из предброни» не плодит вторую заявку."""
    existing_items = (
        (
            await db.execute(
                select(AssemblyRequestItem).where(
                    AssemblyRequestItem.project_id == project_id,
                    AssemblyRequestItem.assembly_request_id == req.id,
                )
            )
        )
        .scalars()
        .all()
    )
    by_key: dict[tuple[int, str], AssemblyRequestItem] = {(it.nomenclature_id, it.barcode): it for it in existing_items}
    for bc, qty in barcodes.items():
        if qty <= 0:
            continue
        nom = nom_map[bc]
        key = (nom.id, bc)
        cur = by_key.get(key)
        if cur is not None:
            cur.quantity += qty
        else:
            new_it = AssemblyRequestItem(
                project_id=project_id,
                assembly_request_id=req.id,
                nomenclature_id=nom.id,
                barcode=bc,
                quantity=qty,
            )
            db.add(new_it)
            by_key[key] = new_it

    old_pallets = max(0, int(req.pallets_count or 0))
    add_p = max(0, int(add_pallets or 0))
    total_pallets = old_pallets + add_p
    old_w = _dec(req.pallet_weight_kg)
    add_w = _dec(add_pallet_weight)
    # Взвешенное среднее — ТОЛЬКО из известных (>0) весов. Черновики со страницы
    # распределения несут pallet_weight_kg=0: усреднение с нулём разбавляло бы уже
    # проставленный вес заявки (2×150кг + 1×0 → «100 кг/паллета»), занижая вес в
    # экспорте/аналитике; разбавленный >0 вес вдобавок минует авто-пересчёт
    # mark_ready (он срабатывает лишь при весе 0). Нулевой добавляемый вес не
    # трогает старый; нулевой старый — заменяется новым.
    if total_pallets > 0:
        if old_w > 0 and add_w > 0:
            total_w = _dec(old_pallets) * old_w + _dec(add_p) * add_w
            req.pallet_weight_kg = (total_w / Decimal(total_pallets)).quantize(Decimal("0.01"))
        elif old_w <= 0 < add_w:
            req.pallet_weight_kg = add_w.quantize(Decimal("0.01"))
    req.pallets_count = max(1, total_pallets)
    await db.flush()


async def commit_draft(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    package_type: str | None = None,
    pallet_counts: dict[str, int] | None = None,
    supplies: list[CommitSupply] | None = None,
    source_ff_id: int | None = None,
    changed_by: str | None = None,
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

    Партиальный коммит по ФФ-источнику: `source_ff_id` — создаём заявки ТОЛЬКО из
    порций, отгружаемых этим складом-ФФ; порции строк с других ФФ остаются в черновике
    (построчный карвинг pro-rata-пар). Идёт всегда через pro-rata (не через `supplies`).

    Идемпотентность: черновик берётся `FOR UPDATE`, поэтому два параллельных
    (или повторный после двойного клика/таймаута) коммита одного черновика
    сериализуются — первый создаёт заявки и soft-delete'ит/обрезает черновик,
    второй на разлоке перечитывает уже-удалённую строку и получает 404 (или
    «нет строк выбранной упаковки» при частичном коммите), не задваивая заявки.
    """
    draft = await _get_draft_for_update(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    # Снапшот вынесенных в заявки строк — для события истории COMMIT_REQUEST (откат).
    committed_rows_snapshot: list[dict] = []
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

    if supplies is not None and source_ff_id is None:
        # Явные отгрузки ФФ→склад (режим «только целые паллеты»): заявки строим ровно
        # из них, минуя pro-rata. Берём только отгрузки выбранного среза упаковки.
        # Партиальный коммит по ФФ (source_ff_id) идёт всегда через pro-rata-ветку
        # ниже — ей нужен построчный карвинг остатка в черновик (supplies его не дают).
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
        # Партиальный коммит по ФФ-источнику (source_ff_id): пары ЭТОГО ФФ коммитим,
        # порции строки с ДРУГИХ ФФ возвращаем в черновик отдельными строками. Остаток
        # сбалансирован: выбранный ФФ отгружает ровно свой src (allocatePairs хранит
        # маргиналы), поэтому Σsrc_ост == Σtgt_ост. source_ff_id=None → коммитим все пары.
        ff_leftover_rows: list[AssemblyDraftRow] = []
        for row in commit_rows:
            alloc = _allocate_pairs(row.src, row.tgt)
            pkg = row.package_type or "BOX"
            is_new = row.nm_id in newcomer_nm_ids
            rest_src: dict[str, int] = {}
            rest_tgt: dict[str, int] = {}
            for (src_id, wb_name), qty in alloc.items():
                if qty <= 0:
                    continue
                if source_ff_id is not None and src_id != source_ff_id:
                    rest_src[str(src_id)] = rest_src.get(str(src_id), 0) + qty
                    rest_tgt[wb_name] = rest_tgt.get(wb_name, 0) + qty
                    continue
                key = (src_id, wb_name, pkg)
                bucket = pair_items.setdefault(key, {})
                bucket[row.barcode] = bucket.get(row.barcode, 0) + qty
                if is_new:
                    pair_has_newcomer[key] = True
            if source_ff_id is not None and rest_tgt:
                ff_leftover_rows.append(
                    AssemblyDraftRow(
                        nm_id=row.nm_id,
                        barcode=row.barcode,
                        vendor_code=row.vendor_code,
                        src=rest_src,
                        tgt=rest_tgt,
                        package_type=row.package_type or "BOX",
                        as_is=row.as_is,
                    )
                )
        if source_ff_id is not None:
            # Непокрытые ФФ выбранного среза + строки другой упаковки — всё остаётся.
            leftover_rows = leftover_rows + ff_leftover_rows

    if not pair_items:
        if source_ff_id is not None:
            raise HTTPException(status_code=400, detail="Нет строк этого склада-ФФ для сборки")
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
    # (warehouse_id, номер, шт) созданных заявок — для уведомления ФФ-порталов.
    created_notify: list[tuple[int, str, int]] = []
    try:
        for (pair_src_id, target_wb_name, package_type), barcodes in pair_items.items():
            # Дедуп повторного commit ЭТОГО ЖЕ черновика: если на это направление
            # (склад · WB · упаковка) уже есть не-отгруженная сборка ИЗ ЭТОГО черновика —
            # доливаем позиции в неё, а не плодим дубль. Кейс: авто-раскладку закоммитили,
            # потом «дозабили из предброни» и закоммитили снова тем же черновиком (черновик
            # переживает commit, пока в нём осталась предбронь — см. ветку ниже). Скоуп
            # source_draft_id гарантирует, что намеренно раздельные сборки других
            # черновиков не затрагиваются.
            # Скоуп статусов — PENDING/IN_PROGRESS намеренно: если первую сборку уже
            # перевели в READY (собрана, готова к отгрузке), доливать в неё нельзя —
            # это тихо «расготовит» уже собранный груз. Такой (редкий) повторный
            # дозабор создаст отдельную сборку; её можно слить кнопкой «Объединить»
            # (предварительно вернув в «В сборке»).
            existing_sibling = (
                await db.execute(
                    select(AssemblyRequest)
                    .where(
                        AssemblyRequest.project_id == project_id,
                        AssemblyRequest.source_draft_id == draft_id,
                        AssemblyRequest.warehouse_id == pair_src_id,
                        AssemblyRequest.wb_warehouse_name_manual == target_wb_name,
                        AssemblyRequest.wb_fbo_supply_id.is_(None),
                        AssemblyRequest.package_type == package_type,
                        AssemblyRequest.status.in_((AssemblyStatus.PENDING, AssemblyStatus.IN_PROGRESS)),
                        AssemblyRequest.is_deleted == False,  # noqa: E712
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing_sibling is not None:
                await _fold_barcodes_into_request(
                    db,
                    project_id,
                    existing_sibling,
                    barcodes,
                    nom_map,
                    add_pallets=_pallets_for(pair_src_id, target_wb_name, package_type),
                    add_pallet_weight=pallet_weight,
                )
                if existing_sibling.id not in created_ids:
                    created_ids.append(existing_sibling.id)
                continue

            number = await _next_number(db, project_id, "ASM", AssemblyRequest)
            if pair_has_newcomer.get((pair_src_id, target_wb_name, package_type)):
                pieces = [NEWCOMER_COMMENT_PREFIX]
                if base_comment:
                    pieces.append(base_comment)
                req_comment: str | None = " | ".join(pieces)
            else:
                req_comment = base_comment

            assembly_req = AssemblyRequest(
                project_id=project_id,
                warehouse_id=pair_src_id,
                number=number,
                status=AssemblyStatus.IN_PROGRESS,
                wb_fbo_supply_id=None,  # not linked — manual WB warehouse
                wb_warehouse_name_manual=target_wb_name,
                estimated_ready_date=eta,
                pallets_count=_pallets_for(pair_src_id, target_wb_name, package_type),
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
            created_notify.append((pair_src_id, assembly_req.number, sum(q for q in barcodes.values() if q > 0)))

        # Снапшот того, что реально уехало в заявки = full − leftover (для отката коммита).
        from backend.services.assembly.draft_history import build_committed_rows

        committed_rows_snapshot = build_committed_rows(
            [r.model_dump(mode="json") for r in distribution.rows],
            [r.model_dump(mode="json") for r in leftover_rows],
        )

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
    # «Потребность по складам» вычитает активные заявки (in_assembly): без
    # инвалидации до 5 минут показывает свободным сток, уже уехавший в заявки
    # (паритет с create/status-путями assembly/crud и assembly/status).
    await invalidate_cache("reports:warehouse_need")
    await _notify_ff_portal_new_requests(db, project_id, created_notify)

    # Событие истории «создание заявки» — для вкладки «🕘 История» и отката.
    # Best-effort: заявки уже durable (коммит выше) — сбой лога НЕ должен валить ответ 500.
    if created_ids:
        from backend.models.assembly import DraftEventType
        from backend.services.assembly.draft_history import log_draft_event

        units = sum(int(q or 0) for r in committed_rows_snapshot for q in (r.get("tgt") or {}).values())
        try:
            await log_draft_event(
                db,
                project_id,
                draft_id,
                DraftEventType.COMMIT_REQUEST,
                summary=f"Создано заявок: {len(created_ids)} ({units} шт)",
                committed_rows=committed_rows_snapshot,
                created_request_ids=created_ids,
                draft_updated_at=draft.updated_at,
                changed_by=changed_by,
            )
        except Exception:
            logger.warning("Failed to log COMMIT_REQUEST draft event for draft_id=%s", draft_id, exc_info=True)

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


def _subtract_in_transit_rows(
    rows: list[AssemblyDraftRow],
    remaining: dict[int, dict[str, int]],
) -> tuple[list[AssemblyDraftRow], int, set[tuple[str, str]]]:
    """Вычесть «уже едет» per (nm, WB-склад) из строк; вернуть
    (строки, Σ вычтено, урезанные направления {(package_type, WB-склад)}).

    Greedy по порядку строк; remaining мутируется (расходуется) — вызывающий
    передаёт один и тот же dict для rows → prebook (нет двойного вычета).
    src ужимается с крупнейших источников до Σtgt (carve, Σsrc == Σtgt);
    строки с Σtgt=0 выпадают. Зеркало фронтового reconcileInTransit.ts.

    Урезанные направления нужны вызывающему: паллетная целость направления в
    rows после вычета потеряна — его остаток обязан уехать в предбронь
    (`_demote_directions_to_prebook`), иначе в rows остаётся частичная паллета.
    """
    kept: list[AssemblyDraftRow] = []
    subtracted = 0
    touched_dirs: set[tuple[str, str]] = set()
    for r in rows:
        per = remaining.get(r.nm_id)
        if not per:
            kept.append(r)
            continue
        row_sub = 0
        for wh, q in list(r.tgt.items()):
            avail = per.get(wh, 0)
            if avail <= 0 or (q or 0) <= 0:
                continue
            take = min(int(q), int(avail))
            r.tgt[wh] = int(q) - take
            per[wh] = int(avail) - take
            row_sub += take
            touched_dirs.add((r.package_type or "BOX", wh))
        if row_sub == 0:
            kept.append(r)
            continue
        subtracted += row_sub
        tgt_total = sum(r.tgt.values())
        if tgt_total <= 0:
            continue  # весь план строки уже едет — строка выпадает
        excess = sum((r.src or {}).values()) - tgt_total
        for ff in sorted(r.src or {}, key=lambda k: -r.src[k]):
            if excess <= 0:
                break
            cut = min(r.src[ff], excess)
            r.src[ff] -= cut
            excess -= cut
        r.tgt = {k: v for k, v in r.tgt.items() if v > 0}
        r.src = {k: v for k, v in (r.src or {}).items() if v > 0}
        kept.append(r)
    return kept, subtracted, touched_dirs


def _demote_directions_to_prebook(
    distribution: AssemblyDraftDistribution,
    touched_dirs: set[tuple[str, str]],
) -> int:
    """Увезти остатки урезанных гейтом направлений из rows в prebook; вернуть Σ штук.

    Канон: «в rows только ЦЕЛЫЕ паллеты, хвост < паллеты → предбронь». Фронт
    нормализует направление (ФФ→WB×упаковка) целыми паллетами ДО PUT, а гейт
    `_subtract_in_transit` вычитает транзит per (nm, склад) ПОСЛЕ — из смешанной
    паллеты выпадают чужие SKU, и остаток направления в rows перестаёт быть целым
    (прод-кейс draft 62, 2026-07-17: ЕКБ ехал «1 пал · ⚠ ~50%»). Геометрии коробов
    на бэке нет — консервативно демотируем ВЕСЬ остаток урезанного направления в
    предбронь: фронт-консолидация поднимет собравшиеся целые паллеты обратно.

    Исключения (правка их плана — осознанное решение юзера, не stale-дубль):
      • `as_is`-строки («Оставить так») — частичная паллета разрешена канонoм;
      • ✋ ручные SKU (`manual_nms`) — гейт их не режет, демоция их не трогает.

    Порция направления вырезается парами `_allocate_pairs` (Σsrc == Σtgt в обеих
    частях), в предбронь сливается `_merge_rows` (ключ nm×упаковка×barcode×as_is).
    """
    if not touched_dirs:
        return 0
    manual = set(distribution.manual_nms or [])
    kept_rows: list[AssemblyDraftRow] = []
    carved: list[dict] = []
    demoted_units = 0
    for r in distribution.rows or []:
        pkg = r.package_type or "BOX"
        hit = {wh for wh, q in (r.tgt or {}).items() if (pkg, wh) in touched_dirs and (q or 0) > 0}
        if not hit or r.as_is or r.nm_id in manual:
            kept_rows.append(r)
            continue
        move_src: dict[str, int] = {}
        move_tgt: dict[str, int] = {}
        keep_src: dict[str, int] = {}
        keep_tgt: dict[str, int] = {}
        for (sid, wb), q in _allocate_pairs(r.src or {}, r.tgt or {}).items():
            if q <= 0:
                continue
            src_part, tgt_part = (move_src, move_tgt) if wb in hit else (keep_src, keep_tgt)
            src_part[str(sid)] = src_part.get(str(sid), 0) + q
            tgt_part[wb] = tgt_part.get(wb, 0) + q
        if move_tgt:
            demoted_units += sum(move_tgt.values())
            carved.append(
                {
                    "nm_id": r.nm_id,
                    "barcode": r.barcode,
                    "vendor_code": r.vendor_code,
                    "src": move_src,
                    "tgt": move_tgt,
                    "package_type": pkg,
                }
            )
        if keep_tgt:
            kept_rows.append(
                AssemblyDraftRow(
                    nm_id=r.nm_id,
                    barcode=r.barcode,
                    vendor_code=r.vendor_code,
                    src=keep_src,
                    tgt=keep_tgt,
                    package_type=pkg,
                )
            )
    if not carved:
        return 0
    distribution.rows = kept_rows
    merged = _merge_rows([p.model_dump() for p in (distribution.prebook or [])], carved)
    distribution.prebook = [AssemblyDraftRow.model_validate(d) for d in merged]
    return demoted_units


def _plan_tgt_sums(distribution: AssemblyDraftDistribution) -> dict[int, dict[str, int]]:
    """Σ tgt плана per (nm_id, WB-склад) по rows И prebook вместе."""
    sums: dict[int, dict[str, int]] = {}
    for r in list(distribution.rows or []) + list(distribution.prebook or []):
        if not r.nm_id:
            continue
        per = sums.setdefault(r.nm_id, {})
        for wh, q in (r.tgt or {}).items():
            per[wh] = per.get(wh, 0) + int(q or 0)
    return sums


async def _subtract_in_transit(
    db: AsyncSession,
    project_id: int,
    distribution: AssemblyDraftDistribution,
    baseline: dict[int, dict[str, int]] | None = None,
) -> int:
    """Server-side «один мир», ДЕЛЬТА-гейт: урезать rows+prebook на уже едущее в
    активных заявках (fetch_in_transit_by_nm, вкл. PRE_DISTRIBUTED) — но только
    в пределах ПРИРОСТА плана относительно `baseline`. Возвращает Σ вычтено.

    `baseline` — Σ tgt per (nm_id, склад) УЖЕ сохранённого в БД черновика
    (rows+prebook, см. `_plan_tgt_sums`). Уже сохранённый план прошёл гейт при
    записи → он чистый; повторный безусловный вычет транзита из ВСЕГО входящего
    плана урезал бы его заново на каждом PUT (матрица-редактор автосейвит каждый
    клик) — черновик «таял». Поэтому лимит вычета per (nm, склад):

        effective = min(transit, max(0, incoming_sum − baseline_sum))

    Семантика: повторный автосейв неизменного плана → прирост 0 → вычет 0
    (идемпотентность); stale-вкладка вернула старый план поверх очищенного
    черновика → baseline меньше → прирост есть → вычет как раньше.
    `baseline=None` ≡ пустой черновик (весь входящий план — прирост).

    ✋ SKU из `distribution.manual_nms` гейт НЕ трогает: степпер матрицы правится
    при видимой колонке «В сборке» — прирост поверх транзита там осознанный
    добор, а не stale-дубль (прод-кейс 2026-07-15: +4 короба в Электросталь
    молча резались до нуля → «правка не сохраняется»).
    """
    from backend.services.cold_start_distribution_service import fetch_in_transit_by_nm

    manual = set(distribution.manual_nms or [])
    nm_ids = sorted(
        {r.nm_id for r in (distribution.rows or []) if r.nm_id and r.nm_id not in manual}
        | {r.nm_id for r in (distribution.prebook or []) if r.nm_id and r.nm_id not in manual}
    )
    if not nm_ids:
        return 0
    transit = await fetch_in_transit_by_nm(db, project_id, nm_ids)
    if not transit:
        return 0
    incoming = _plan_tgt_sums(distribution)
    remaining: dict[int, dict[str, int]] = {}
    for nm, per in transit.items():
        base_per = (baseline or {}).get(nm, {})
        inc_per = incoming.get(nm, {})
        eff: dict[str, int] = {}
        for wh, tq in per.items():
            growth = max(0, inc_per.get(wh, 0) - base_per.get(wh, 0))
            take = min(int(tq), growth)
            if take > 0:
                eff[wh] = take
        if eff:
            remaining[nm] = eff
    if not remaining:
        return 0
    distribution.rows, sub_rows, touched_dirs = _subtract_in_transit_rows(distribution.rows or [], remaining)
    distribution.prebook, sub_pb, _ = _subtract_in_transit_rows(distribution.prebook or [], remaining)
    # Урезанные в ROWS направления больше не гарантированно целые паллеты —
    # их остаток уезжает в предбронь (канон «rows = целые паллеты»). Вычеты в
    # prebook на целость rows не влияют (предбронь — уже хвосты).
    demoted = _demote_directions_to_prebook(distribution, touched_dirs)
    if demoted:
        logger.info(
            "in-transit гейт: %d шт остатков урезанных направлений демотированы в предбронь",
            demoted,
        )
    return sub_rows + sub_pb


def _reconcile_handed_with_rows(distribution: AssemblyDraftDistribution) -> bool:
    """Убрать двойной заклад ФФ-стока: если поток ФФ→склад→баркод уже распределён
    в `rows`, ручной снимок (`handed_units` со `status="draft"`) того же
    (ФФ, склад, баркод) — устаревший дубль. `rows` — источник истины: такая
    позиция вырезается из снимка, опустевший снимок удаляется целиком.

    Зачем: `set_unit_items` (ручная раскладка) вырезает поток ФФ→склад из `rows`
    в замороженный снимок; но последующее наполнение `rows` (дозабор
    `add_rows_to_draft` / автосейв `update_draft` / пере-распределение) не сверяется
    со снимками и может заново заложить ТОТ ЖЕ поток ФФ→склад. Тогда `rows` и снимок
    описывают ОДИН товар → страница склада (rows + handed) показывает фантомный
    перезаклад (товара физически меньше, чем сумма). Гвард держит два хранилища
    непересекающимися по (ФФ, склад, баркод), rows побеждает.

    Грань — именно (ФФ, склад, баркод), а не (ФФ, баркод): `_carve_unit_from_rows`
    уменьшает `src[ФФ]` на вырезанное, но баркод ЗАКОННО остаётся в rows, если тот
    же ФФ шлёт его на ДРУГИЕ склады — это не дубль. Дубль — только повторный тот же
    ФФ→склад→баркод.

    Реальные передачи на ФФ (`status="handed"`) НЕ трогаем — это физический факт
    выдачи оператору, а не устаревший авто-снимок. Мутирует distribution;
    возвращает True, если что-то изменилось."""
    rows_ff_wb_bc: set[tuple[int, str, str]] = set()
    for row in distribution.rows:
        bc = row.barcode
        if not bc:
            continue
        for (ff, wb), qty in _allocate_pairs(row.src, row.tgt).items():
            if qty > 0:
                rows_ff_wb_bc.add((ff, wb, bc))
    if not rows_ff_wb_bc:
        return False

    changed = False
    kept_units: list[HandedUnit] = []
    for unit in distribution.handed_units:
        if (unit.status or "handed") != "draft":
            kept_units.append(unit)
            continue
        kept_items = [
            it for it in unit.items
            if (unit.source_ff_id, unit.target_wb_name, it.barcode) not in rows_ff_wb_bc
        ]
        if len(kept_items) == len(unit.items):
            kept_units.append(unit)
            continue
        changed = True
        if kept_items:
            unit.items = kept_items
            kept_units.append(unit)
        # опустевший снимок целиком — не сохраняем (весь товар ушёл в rows)
    if changed:
        distribution.handed_units = kept_units
    return changed


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


async def _notify_ff_portal_new_requests(
    db: AsyncSession,
    project_id: int,
    created: list[tuple[int, str, int]],
) -> None:
    """Best-effort уведомление операторов ФФ-порталов (напр. Хамза) о созданных
    из черновика сборках — паритет с create_assembly_request (ручное создание и
    машина шлют, а коммит черновика создаёт AssemblyRequest напрямую и раньше
    молчал). `created` = (warehouse_id, номер, шт). Для складов без привязанного
    чата — no-op; сбой не валит ответ (CancelledError пробрасывается)."""
    if not created:
        return
    try:
        from backend.models.warehouse import Warehouse
        from backend.services import fulfillment_notify

        wh_ids = {w for w, _, _ in created}
        rows = (
            await db.execute(
                select(Warehouse.id, Warehouse.name).where(
                    Warehouse.project_id == project_id,
                    Warehouse.id.in_(wh_ids),
                    Warehouse.is_deleted == False,  # noqa: E712
                )
            )
        ).all()
        names: dict[int, str] = {wid: nm for wid, nm in rows}
        for wid, number, qty in created:
            await fulfillment_notify.notify_new_ff_assembly(
                db, project_id, wid, assembly_number=number, warehouse_name=names.get(wid), qty=qty
            )
    except Exception:
        logger.warning("new-ff-assembly notify (draft commit) failed", exc_info=True)


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
        # `or`, не `and`: у СБАЛАНСИРОВАННОЙ строки стороны пустеют одновременно,
        # а у несбалансированной («!», Σsrc≠Σtgt — PUT такие принимает, баланс
        # валидируется только на commit) опустевшая одна сторона молча уносила бы
        # ненулевой остаток другой (потеря плана без следа в истории).
        if new_src or new_tgt:
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
    # Ключ ВКЛЮЧАЕТ barcode (см. _dedupe_rows/_merge_rows): один nm_id несёт несколько
    # баркодов (размерные варианты) — влив по (nm_id, pkg) приписал бы qty чужому
    # баркоду, и commit отгрузил бы чужой физический товар. Строки «Оставить так»
    # (as_is) — отдельная сущность, возврат юнита в них не суммируется.
    rows_by_key: dict[tuple[int, str, str], AssemblyDraftRow] = {
        (r.nm_id, r.package_type or "BOX", r.barcode or ""): r for r in distribution.rows if not r.as_is
    }
    for it in unit.items:
        row = rows_by_key.get((it.nm_id, norm_pkg, it.barcode))
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
            rows_by_key[(it.nm_id, norm_pkg, it.barcode)] = row
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
        # Предбронь держит черновик живым (инвариант commit_draft): она резервирует
        # сток в /drafts/reserved — soft-delete молча терял бы её вместе с резервом.
        if distribution.rows or distribution.handed_units or distribution.prebook:
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
    # Паритет с commit_draft: заявка юнита попадает в in_assembly «Потребности».
    await invalidate_cache("reports:warehouse_need")
    await _notify_ff_portal_new_requests(
        db, project_id, [(source_ff_id, req.number, sum(q for q in barcodes.values() if q > 0))]
    )

    # Событие истории COMMIT_REQUEST (паритет с commit_draft): без него заявка из
    # юнита невидима во вкладке «🕘 История» и неоткатываема. committed_rows — позиции
    # юнита per-barcode (src=ФФ юнита, tgt=его WB-склад), чтобы откат вернул их в rows.
    # Best-effort: заявка уже durable (коммит выше) — сбой лога не валит ответ.
    from backend.models.assembly import DraftEventType
    from backend.services.assembly.draft_history import log_draft_event

    committed_rows = [
        {
            "nm_id": it.nm_id,
            "barcode": it.barcode,
            "vendor_code": it.vendor_code or "",
            "src": {str(source_ff_id): it.qty},
            "tgt": {target_wb_name: it.qty},
            "package_type": norm_pkg,
            "as_is": False,
        }
        for it in unit.items
        if it.qty > 0
    ]
    try:
        await log_draft_event(
            db,
            project_id,
            draft_id,
            DraftEventType.COMMIT_REQUEST,
            summary=f"Создана заявка {req.number} из юнита ({sum(barcodes.values())} шт)",
            committed_rows=committed_rows,
            created_request_ids=created_ids,
            draft_updated_at=draft.updated_at,
        )
    except Exception:
        logger.warning("Failed to log COMMIT_REQUEST draft event for draft_id=%s (commit_unit)", draft_id, exc_info=True)

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
        # Ключ с barcode — как в revert_unit: без него размерные варианты одного
        # nm_id сливались бы в одну строку (qty чужому баркоду). as_is — отдельно.
        rows_by_key: dict[tuple[int, str, str], AssemblyDraftRow] = {
            (r.nm_id, r.package_type or "BOX", r.barcode or ""): r for r in distribution.rows if not r.as_is
        }
        for it in moved:
            row = rows_by_key.get((it.nm_id, norm_pkg, it.barcode))
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
                rows_by_key[(it.nm_id, norm_pkg, it.barcode)] = row
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
    # Предбронь держит черновик живым (инвариант commit_draft) — см. commit_unit.
    if not (distribution.rows or distribution.handed_units or distribution.prebook):
        draft.soft_delete()
    await db.commit()
    await db.refresh(draft)
    return await to_read_model(db, project_id, draft)
