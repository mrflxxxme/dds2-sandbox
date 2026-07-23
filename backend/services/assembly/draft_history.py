"""История изменений черновика сборки + откат (вкладка «🕘 История»).

Логирует значимые изменения черновика (`AssemblyDraftEvent`) и умеет их откатывать:
  • PREBOOK_TOPUP / MATRIX_WRITE — снапшот-возврат `before_distribution`, но ТОЛЬКО
    если черновик не менялся после (сверка `draft.updated_at == draft_updated_at_after`);
  • COMMIT_REQUEST — удаление созданных заявок (гвард: НЕ на ФФ и НЕ WB-поставка) +
    возврат `committed_rows` в черновик (восстановление, если черновик был soft-delete).

Циклы импортов: этот модуль импортит helpers из `assembly_draft_service` на уровне
модуля; `assembly_draft_service` зовёт `log_draft_event`/`build_committed_rows` ЛОКАЛЬНО
(внутри функций) — модульного цикла нет.
"""

import logging
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import (
    AssemblyDraft,
    AssemblyDraftEvent,
    AssemblyRequest,
    AssemblyStatus,
    DraftEventType,
)
from backend.schemas.assembly_draft import (
    DraftEventRead,
    DraftEventRevertResponse,
    DraftHistoryResponse,
)
from backend.services.assembly_draft_service import _merge_rows, to_read_model
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

# События со снапшот-возвратом (откат зависит от версии черновика). Строковые типы
# (MATRIX_EDIT/AUTO_SYNC — маркеры PUT из DraftEventLog; REMOVE_ROWS — ✕ SKU) пишутся
# тем же путём с before_distribution+draft_updated_at_after; вне этого списка они были
# «неизвестным типом события» — неоткатываемы И, оставаясь новейшим незакрытым
# событием, навсегда блокировали LIFO-откат всей истории под ними.
_SNAPSHOT_EVENTS: tuple[DraftEventType | str, ...] = (
    DraftEventType.PREBOOK_TOPUP,
    DraftEventType.MATRIX_WRITE,
    "MATRIX_EDIT",
    "AUTO_SYNC",
    "ACCEPTANCE_SYNC",
    "REMOVE_ROWS",
    # Аудит гейта ёмкости ФФ (update_draft): before = план ДО среза. Откат вернёт
    # несрезанный план — следующий PUT прогонит его через гейт заново (не дыра).
    "CAPACITY_TRIM",
)
_HISTORY_LIMIT = 200


def _row_key(r: dict) -> tuple[int, str, str, bool]:
    return (
        int(r.get("nm_id") or 0),
        str(r.get("package_type") or "BOX"),
        str(r.get("barcode") or ""),
        bool(r.get("as_is")),
    )


def build_committed_rows(before_rows: list[dict], leftover_rows: list[dict]) -> list[dict]:
    """Строки, реально вынесенные в заявки = before − leftover (поэлементно по ключу).

    Учитывает частичный carve (режим source_ff разрезает строку): остаток строки
    (`src`/`tgt` минус leftover) — то, что уехало в заявку. Держит вендор-код исходной
    строки (для корректного возврата в черновик). Чистая."""
    left_by_key: dict[tuple, dict] = {}
    for r in leftover_rows:
        left_by_key[_row_key(r)] = r
    out: list[dict] = []
    for r in before_rows:
        left = left_by_key.get(_row_key(r))
        src = {str(k): int(v or 0) for k, v in (r.get("src") or {}).items()}
        tgt = {str(k): int(v or 0) for k, v in (r.get("tgt") or {}).items()}
        if left:
            for k, v in (left.get("src") or {}).items():
                src[str(k)] = src.get(str(k), 0) - int(v or 0)
            for k, v in (left.get("tgt") or {}).items():
                tgt[str(k)] = tgt.get(str(k), 0) - int(v or 0)
        src = {k: v for k, v in src.items() if v > 0}
        tgt = {k: v for k, v in tgt.items() if v > 0}
        if not src or not tgt:
            continue
        out.append({
            "nm_id": r.get("nm_id"),
            "barcode": r.get("barcode"),
            "vendor_code": r.get("vendor_code") or "",
            "src": src,
            "tgt": tgt,
            "package_type": r.get("package_type") or "BOX",
            "as_is": bool(r.get("as_is")),
        })
    return out


async def log_draft_event(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    event_type: DraftEventType | str,
    *,
    summary: str | None = None,
    before_distribution: dict | None = None,
    committed_rows: list[dict] | None = None,
    created_request_ids: list[int] | None = None,
    draft_updated_at: datetime | None = None,
    changed_by: str | None = None,
) -> AssemblyDraftEvent:
    """Записать событие истории черновика (self-committing)."""
    ev = AssemblyDraftEvent(
        project_id=project_id,
        draft_id=draft_id,
        event_type=str(event_type),
        summary=summary,
        created_by=(changed_by or None),
        before_distribution=before_distribution,
        committed_rows=committed_rows,
        created_request_ids=created_request_ids,
        draft_updated_at_after=draft_updated_at,
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    return ev


async def _load_draft_any(db: AsyncSession, project_id: int, draft_id: int) -> AssemblyDraft | None:
    """Черновик БЕЗ фильтра is_deleted — историю показываем и по soft-deleted черновику."""
    return (
        await db.execute(
            select(AssemblyDraft).where(
                AssemblyDraft.id == draft_id,
                AssemblyDraft.project_id == project_id,
            )
        )
    ).scalar_one_or_none()


async def _revert_guard(
    db: AsyncSession,
    project_id: int,
    ev: AssemblyDraftEvent,
    draft: AssemblyDraft | None,
    newest_open_id: int | None,
) -> tuple[bool, str | None]:
    """Можно ли откатить событие сейчас + причина запрета (None если можно).

    `newest_open_id` — id самого нового НЕ откаченного события черновика."""
    if ev.reverted_at is not None:
        return False, "уже откачено"

    # LIFO: откатывать можно только САМОЕ НОВОЕ незавершённое событие. Откат «через
    # голову» более позднего (частичного) коммита рушит целостность количеств —
    # `draft.is_deleted` перестаёт однозначно указывать, ЧЕЙ коммит закрыл черновик,
    # и restore/merge даёт потерю+задвоение. Сначала откатывается новейшее.
    if newest_open_id is not None and ev.id < newest_open_id:
        return False, "сначала откатите более новое действие в истории"

    if ev.event_type in _SNAPSHOT_EVENTS:
        if draft is None or draft.is_deleted:
            return False, "черновик удалён — откат этого действия недоступен"
        if ev.draft_updated_at_after is None or draft.updated_at != ev.draft_updated_at_after:
            return False, "черновик изменился после этого действия — откат недоступен"
        return True, None

    if ev.event_type == DraftEventType.COMMIT_REQUEST:
        ids = ev.created_request_ids or []
        if not ids:
            return False, "нет связанных заявок"
        reqs = (
            await db.execute(
                select(AssemblyRequest)
                .where(
                    AssemblyRequest.id.in_(ids),
                    AssemblyRequest.project_id == project_id,
                    AssemblyRequest.is_deleted == False,  # noqa: E712
                )
                .limit(len(ids))
            )
        ).scalars().all()
        if not reqs:
            return False, "заявки уже удалены"
        # ЧАСТИЧНО удалённый набор (заявку объединили merge'ем с чужой сборкой →
        # loser soft-deleted, количества живут в survivor): откат вернул бы в черновик
        # ПОЛНЫЙ committed_rows, включая долю слитой заявки — товар существовал бы
        # дважды (в survivor и в rows). Разбивки committed_rows по заявкам нет,
        # частичный возврат невозможен → блокируем целиком.
        if len(reqs) < len(set(ids)):
            return False, "часть заявок уже удалена (объединена с другой сборкой?) — откат недоступен"
        # FOLD-защита: повторный commit того же черновика ДОЛИВАЕТ в уже созданную
        # заявку (см. _fold_barcodes_into_request) — та же заявка фигурирует в
        # НЕСКОЛЬКИХ COMMIT-событиях. Откат любого из них удалил бы заявку целиком,
        # вместе с количествами других (неоткаченных) коммитов → блокируем, пока
        # у заявки есть другое живое COMMIT-событие.
        ids_set = set(ids)
        sibling_rows = (
            await db.execute(
                select(AssemblyDraftEvent.created_request_ids)
                .where(
                    AssemblyDraftEvent.project_id == project_id,
                    AssemblyDraftEvent.draft_id == ev.draft_id,
                    AssemblyDraftEvent.id != ev.id,
                    AssemblyDraftEvent.event_type == str(DraftEventType.COMMIT_REQUEST),
                    AssemblyDraftEvent.reverted_at.is_(None),
                )
                .limit(_HISTORY_LIMIT)
            )
        ).scalars().all()
        for sibling_ids in sibling_rows:
            if ids_set & set(sibling_ids or []):
                return False, "заявка дополнялась другим коммитом (долив) — откат недоступен"
        # Локальные импорты — избегаем тяжёлых модульных зависимостей и циклов.
        from backend.services.assembly.status import _NON_DELETABLE_STATUSES
        from backend.services.fulfillment_service import get_ff_link_for_assembly

        for r in reqs:
            if AssemblyStatus(r.status) in _NON_DELETABLE_STATUSES:
                return False, f"заявка {r.number} уже отгружена на WB — откат недоступен"
            link = await get_ff_link_for_assembly(db, project_id, r.id)
            if link is not None:
                return False, f"заявка {r.number} уже отправлена на ФФ — откат недоступен"
        return True, None

    return False, "неизвестный тип события"


async def get_draft_history(db: AsyncSession, project_id: int, draft_id: int) -> DraftHistoryResponse:
    """События истории черновика, новейшие первыми, с вычисленным can_revert."""
    draft = await _load_draft_any(db, project_id, draft_id)
    events = (
        await db.execute(
            select(AssemblyDraftEvent)
            .where(
                AssemblyDraftEvent.project_id == project_id,
                AssemblyDraftEvent.draft_id == draft_id,
            )
            .order_by(AssemblyDraftEvent.id.desc())
            .limit(_HISTORY_LIMIT)
        )
    ).scalars().all()
    # Самое новое НЕ откаченное событие — только его можно откатить (LIFO).
    newest_open_id = max((e.id for e in events if e.reverted_at is None), default=None)
    out: list[DraftEventRead] = []
    for ev in events:
        can, reason = await _revert_guard(db, project_id, ev, draft, newest_open_id)
        out.append(
            DraftEventRead(
                id=ev.id,
                event_type=ev.event_type,
                summary=ev.summary,
                created_at=ev.created_at,
                created_by=ev.created_by,
                reverted_at=ev.reverted_at,
                reverted_by=ev.reverted_by,
                created_request_ids=ev.created_request_ids,
                can_revert=can,
                revert_blocked_reason=reason,
            )
        )
    return DraftHistoryResponse(events=out)


async def revert_draft_event(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    event_id: int,
    changed_by: str | None = None,
) -> DraftEventRevertResponse:
    """Откатить событие истории (гвард → действие). 409 если откат недоступен."""
    ev = (
        await db.execute(
            select(AssemblyDraftEvent).where(
                AssemblyDraftEvent.id == event_id,
                AssemblyDraftEvent.project_id == project_id,
                AssemblyDraftEvent.draft_id == draft_id,
            )
        )
    ).scalar_one_or_none()
    if ev is None:
        raise HTTPException(status_code=404, detail="Событие истории не найдено")

    draft = await _load_draft_any(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Черновик не найден")

    newest_open_id = (
        await db.execute(
            select(func.max(AssemblyDraftEvent.id)).where(
                AssemblyDraftEvent.project_id == project_id,
                AssemblyDraftEvent.draft_id == draft_id,
                AssemblyDraftEvent.reverted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    can, reason = await _revert_guard(db, project_id, ev, draft, newest_open_id)
    if not can:
        raise HTTPException(status_code=409, detail=f"Откат недоступен: {reason}")

    deleted_ids: list[int] = []

    if ev.event_type in _SNAPSHOT_EVENTS:
        # Снапшот-возврат: черновик не менялся после (проверено гвардом).
        draft.distribution = ev.before_distribution or {}
    elif ev.event_type == DraftEventType.COMMIT_REQUEST:
        from backend.services.assembly.status import cancel_request, delete_request

        for rid in ev.created_request_ids or []:
            req = (
                await db.execute(
                    select(AssemblyRequest).where(
                        AssemblyRequest.id == rid,
                        AssemblyRequest.project_id == project_id,
                        AssemblyRequest.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalar_one_or_none()
            if req is None:
                continue  # уже удалена — пропускаем
            if AssemblyStatus(req.status) != AssemblyStatus.CANCELLED:
                await cancel_request(db, project_id, rid)
            await delete_request(db, project_id, rid)
            deleted_ids.append(rid)
        # Возврат строк в черновик. ВАЖНО про три случая:
        #  • ПОЛНЫЙ коммит — черновик soft-delete'нут ЭТИМ коммитом, `distribution.rows`
        #    НЕ очищались (остались исходные строки) → достаточно restore(), merge НЕ
        #    нужен (иначе x2). Признак: draft.updated_at == токену события — после
        #    коммита черновик никто не трогал (все операции фильтруют is_deleted).
        #  • Черновик пережил коммит (rows вырезаны в leftover), но удалён ПОЗЖЕ другой
        #    операцией (delete_draft при живой предброни) → restore() поднимает
        #    состояние ПОСЛЕ коммита, committed_rows нужно вернуть merge'ем — голый
        #    restore() молча терял вынесенные строки.
        #  • ЧАСТИЧНЫЙ коммит, черновик жив → merge committed_rows поверх leftover.
        was_deleted = draft.is_deleted
        deleted_by_this_commit = was_deleted and (
            ev.draft_updated_at_after is None  # legacy-события без токена — старое поведение
            or draft.updated_at == ev.draft_updated_at_after
        )
        if was_deleted:
            draft.restore()
        if not deleted_by_this_commit:
            restore_rows = ev.committed_rows or []
            if restore_rows:
                dist = dict(draft.distribution or {})
                dist["rows"] = _merge_rows(list(dist.get("rows") or []), restore_rows)
                draft.distribution = dist

    ev.reverted_at = utcnow()
    ev.reverted_by = changed_by or None

    # ЦЕПНОЙ undo: сам откат меняет draft.updated_at (onupdate), что ломало бы
    # version-гейт СЛЕДУЮЩЕГО (более старого) снапшот-события — вопреки подсказке
    # «сначала откатите более новое действие». Если новейшее ОСТАВШЕЕСЯ незакрытое
    # событие — снапшотное, возвращаем draft.updated_at к его токену: состояние
    # черновика после отката соответствует именно этой точке истории (снапшот-откат —
    # полная замена distribution, испортить количества он не может; несохранённые в
    # событиях промежуточные автосейвы цепной undo осознанно отбрасывает).
    # Явное присваивание updated_at подавляет onupdate. select ниже автофлашит ev
    # (reverted_at уже проставлен) — само событие из выборки выпадает.
    prev_open = (
        await db.execute(
            select(AssemblyDraftEvent)
            .where(
                AssemblyDraftEvent.project_id == project_id,
                AssemblyDraftEvent.draft_id == draft_id,
                AssemblyDraftEvent.reverted_at.is_(None),
            )
            .order_by(AssemblyDraftEvent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if (
        prev_open is not None
        and prev_open.event_type in _SNAPSHOT_EVENTS
        and prev_open.draft_updated_at_after is not None
        and not draft.is_deleted
    ):
        draft.updated_at = prev_open.draft_updated_at_after

    await db.commit()
    await db.refresh(draft)

    # to_read_model (не model_validate) — чтобы вернулись newcomer_nm_ids (бейдж 🆕).
    draft_read = await to_read_model(db, project_id, draft) if not draft.is_deleted else None
    return DraftEventRevertResponse(
        reverted_event_id=ev.id,
        event_type=ev.event_type,
        restored_draft=True,
        deleted_request_ids=deleted_ids,
        draft=draft_read,
    )
