# ruff: noqa: RUF002, RUF003
"""Стейт-машина задач дизайна: переходы + матрица «кто двигает» + гварды.

Словарь DESIGN_TASK_TRANSITIONS (модель, Р1) — единственный источник правды;
здесь только применение. change_status работает под SELECT ... FOR UPDATE:
перечитать → validate_transition → право на переход → гварды → побочные
эффекты → DesignTaskEvent → commit → notify-хук (Ф4: личные TG-уведомления,
best-effort — сбой отправки логируется и операцию не роняет, §6.8).

Побочный эффект ACCEPTED — закрытие текущей PENDING-версии — живёт ТОЛЬКО
здесь (см. docstring apply_transition_locked): принятие достижимо и через
set_verdict, и прямой сменой статуса из деталки, а закрывать версию обязаны
оба пути. files.set_verdict(ACCEPTED) лишь делегирует сюда — без дублирования.
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.design import (
    DESIGN_TASK_TRANSITIONS,
    DesignSubmission,
    DesignSubmissionFile,
    DesignTask,
    DesignTaskEvent,
    DesignTaskStatus,
    DesignVerdict,
)
from backend.services.design.common import actor_name, get_task_row, is_lead
from backend.services.design.notify import notify_status_changed
from backend.utils.time import utcnow

logger = logging.getLogger("dds.design")

_S = DesignTaskStatus

# Ключ db.info для накопленных notify-событий (диспатч — _commit_and_notify).
_NOTIFY_KEY = "design_notify_events"


def validate_transition(current: str, target: str) -> None:
    """Проверка ребра по словарю. ValueError с понятным текстом (зеркало payment_request)."""
    allowed = DESIGN_TASK_TRANSITIONS.get(_S(current), set())
    if _S(target) not in allowed:
        raise ValueError(
            f"Недопустимый переход {current} → {target}. Разрешено: "
            f"{', '.join(sorted(s.value for s in allowed)) or 'нет'}"
        )


def can_user_transition(task: Any, target: str, user_id: int, member_role: str) -> bool:
    """Матрица «кто двигает» (спек F1). Ребро вне словаря — False для всех.

    lead (owner/admin) может любое ребро словаря; editor различается как
    автор (author_user_id) или исполнитель (assignee_user_id).
    """
    cur = _S(str(task.status))
    tgt = _S(target)
    if tgt not in DESIGN_TASK_TRANSITIONS.get(cur, set()):
        return False
    if is_lead(member_role):
        return True

    is_author = bool(task.author_user_id == user_id)
    is_assignee = bool(task.assignee_user_id == user_id)

    if tgt is _S.CANCELLED:  # *→CANCELLED: author, lead
        return is_author
    if tgt is _S.ON_HOLD:  # *→ON_HOLD: assignee (свою), lead
        return is_assignee
    if cur is _S.ON_HOLD:  # ON_HOLD→(NEW|ASSIGNED|IN_PROGRESS|REVISION): lead, assignee
        return is_assignee

    pair_rules: dict[tuple[_S, _S], bool] = {
        (_S.NEW, _S.ASSIGNED): False,  # только lead
        (_S.ASSIGNED, _S.NEW): False,  # только lead
        (_S.ASSIGNED, _S.IN_PROGRESS): is_assignee,
        (_S.IN_PROGRESS, _S.REVIEW): is_assignee,
        (_S.REVIEW, _S.REVISION): is_author,
        (_S.REVIEW, _S.ACCEPTED): is_author,
        (_S.REVISION, _S.REVIEW): is_assignee,
    }
    return pair_rules.get((cur, tgt), False)


async def _has_pending_submission(
    db: AsyncSession, project_id: int, task_id: int, *, with_files: bool = False
) -> bool:
    """with_files=True — PENDING-версия обязана иметь хотя бы один файл: короткие
    транзакции create_submission допускают окно «PENDING без файлов» (заливка
    сорвалась), и гвард →REVIEW не должен пропускать такую версию — текст
    «Сдайте версию с файлами» остаётся честным."""
    stmt = select(DesignSubmission.id).where(
        DesignSubmission.project_id == project_id,
        DesignSubmission.task_id == task_id,
        DesignSubmission.verdict == DesignVerdict.PENDING.value,
    )
    if with_files:
        stmt = stmt.join(
            DesignSubmissionFile,
            DesignSubmissionFile.submission_id == DesignSubmission.id,
        )
    res = await db.execute(stmt.limit(1))
    return res.scalar_one_or_none() is not None


async def apply_transition_locked(
    db: AsyncSession,
    task: DesignTask,
    target: str | DesignTaskStatus,
    user: Any,
    member_role: str,
    comment: str | None = None,
    submission_id: int | None = None,
) -> None:
    """Применить переход к УЖЕ заблокированной (FOR UPDATE) задаче. Без commit.

    Порядок: словарь → право → гварды → побочные эффекты → событие в журнал +
    накопление notify-события (диспатч — _commit_and_notify после commit).
    Вызывается из change_status, board.move_task, crud.assign, files.set_verdict —
    единая точка правил, чтобы drag&drop и деталка не расходились.

    submission_id — версия, к которой применяется вердикт-эффект (REVISION →
    REJECTED, ACCEPTED → закрытие): files.set_verdict прокидывает переданную
    версию; фолбэк «последняя PENDING» — только для прямого change_status.
    """
    target_s = target.value if isinstance(target, DesignTaskStatus) else str(target)
    current = str(task.status)
    validate_transition(current, target_s)
    if not can_user_transition(task, target_s, user.id, member_role):
        raise PermissionError(f"Недостаточно прав для перехода {current} → {target_s}")

    cur = _S(current)
    tgt = _S(target_s)
    now = utcnow()

    # ─── Гварды (тексты — спек F1) ────────────────────────────────────────────
    if tgt in (_S.ASSIGNED, _S.IN_PROGRESS) and task.assignee_user_id is None:
        raise ValueError("Назначьте исполнителя")
    if cur is _S.IN_PROGRESS and tgt is _S.REVIEW and not await _has_pending_submission(
        db, task.project_id, task.id, with_files=True
    ):
        raise ValueError("Сдайте версию с файлами")
    if cur is _S.REVISION and tgt is _S.REVIEW and not await _has_pending_submission(
        db, task.project_id, task.id, with_files=True
    ):
        raise ValueError("Приложите исправленную версию")
    if cur is _S.REVIEW and tgt is _S.REVISION and not (comment and comment.strip()):
        raise ValueError("Напишите, что исправить")
    if tgt is _S.ACCEPTED and not await _has_pending_submission(db, task.project_id, task.id):
        raise ValueError("Нет версии на проверке")
    if tgt is _S.CANCELLED and not (comment and comment.strip()):
        raise ValueError("Укажите причину отмены")

    # ─── Побочные эффекты ─────────────────────────────────────────────────────
    if tgt is _S.NEW:
        task.assignee_user_id = None  # NEW = без исполнителя (в т.ч. возврат из ON_HOLD)
    if tgt is _S.IN_PROGRESS and task.started_at is None:
        task.started_at = now  # только первый вход
    if tgt is _S.ON_HOLD:
        task.held_from_status = current  # UI предложит вернуть сюда (Р1)
    elif cur is _S.ON_HOLD:
        task.held_from_status = None
    if tgt is _S.REVISION:
        # Любой путь в REVISION отклоняет текущую PENDING-версию (verdict_comment =
        # комментарий перехода) — гвард REVISION→REVIEW требует НОВОЙ версии.
        await _reject_pending_submission(db, task, user, now, comment, submission_id)
    if tgt is _S.ACCEPTED:
        task.accepted_at = now
        await _close_pending_submission(db, task, user, now, comment, submission_id)

    task.status = target_s
    db.add(
        DesignTaskEvent(
            project_id=task.project_id,
            task_id=task.id,
            old_status=current,
            new_status=target_s,
            changed_at=now,
            changed_by=actor_name(user),
            comment=comment,
        )
    )
    # Накопить notify-событие: диспатч единый — _commit_and_notify после commit (§6.8).
    db.info.setdefault(_NOTIFY_KEY, []).append((task, current, target_s, comment, user))


async def _pending_submission_row(
    db: AsyncSession, task: DesignTask, submission_id: int | None
) -> DesignSubmission | None:
    """Целевая PENDING-версия: указанная submission_id или последняя PENDING."""
    stmt = select(DesignSubmission).where(
        DesignSubmission.project_id == task.project_id,
        DesignSubmission.task_id == task.id,
        DesignSubmission.verdict == DesignVerdict.PENDING.value,
    )
    if submission_id is not None:
        stmt = stmt.where(DesignSubmission.id == submission_id)
    res = await db.execute(stmt.order_by(DesignSubmission.version_no.desc()).limit(1))
    return res.scalar_one_or_none()


async def _close_pending_submission(
    db: AsyncSession,
    task: DesignTask,
    user: Any,
    now: Any,
    comment: str | None = None,
    submission_id: int | None = None,
) -> None:
    """ACCEPTED закрывает PENDING-версию (переданную или последнюю) — единственное место.

    comment перехода = комментарий приёмки: сохраняется в verdict_comment версии
    (зеркало REJECTED-ветки) — иначе текст, который приёмщик написал в
    `POST /verdict {verdict_comment}`, терялся бы навсегда. Пустой/None не
    затирает уже записанное значение.
    """
    sub = await _pending_submission_row(db, task, submission_id)
    if sub is not None:  # гвард «Нет версии на проверке» уже гарантировал наличие
        sub.verdict = DesignVerdict.ACCEPTED.value
        if comment and comment.strip():
            sub.verdict_comment = comment
        sub.verdict_by_user_id = user.id
        sub.verdict_at = now


async def _reject_pending_submission(
    db: AsyncSession,
    task: DesignTask,
    user: Any,
    now: Any,
    comment: str | None,
    submission_id: int | None = None,
) -> None:
    """Вход в REVISION отклоняет PENDING-версию (переданную или последнюю) —
    единственное место REJECTED-эффекта; files.set_verdict лишь делегирует сюда."""
    sub = await _pending_submission_row(db, task, submission_id)
    if sub is not None:
        sub.verdict = DesignVerdict.REJECTED.value
        sub.verdict_comment = comment
        sub.verdict_by_user_id = user.id
        sub.verdict_at = now


async def _commit_and_notify(db: AsyncSession) -> None:
    """Commit + единый диспатч накопленных notify-событий всех путей переходов
    (change_status / move_task / set_verdict / assign). Best-effort (§6.8):
    сбой уведомления логируется, операцию не роняет."""
    events = db.info.pop(_NOTIFY_KEY, [])
    await db.commit()
    for task, old_status, new_status, comment, user in events:
        try:
            await notify_status_changed(task, old_status, new_status, comment, actor=user)
        except Exception as e:  # best-effort; в сервисе нет CancelledError-джоб
            logger.warning("design notify_status_changed failed: %s", e)


async def change_status(
    db: AsyncSession,
    project_id: int,
    task_id: int,
    target: str | DesignTaskStatus,
    user: Any,
    member_role: str,
    comment: str | None = None,
) -> DesignTask:
    """Смена статуса под FOR UPDATE. Два параллельных перехода не проходят оба:
    второй перечитывает уже изменённый статус и бьётся о словарь (ValueError)."""
    task = await get_task_row(db, project_id, task_id, for_update=True)
    await apply_transition_locked(db, task, target, user, member_role, comment)
    await _commit_and_notify(db)
    await db.refresh(task)
    return task
