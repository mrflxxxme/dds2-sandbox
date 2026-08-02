# ruff: noqa: RUF002, RUF003
"""Матрица прав PRD §7 → булевы флаги. Права считает ТОЛЬКО бэк (инвариант §6.9).

Роутер (Ф2) и фронт (Ф3) опираются на эти флаги, не дублируя логику.
Схема DesignTaskPermissions (Ф0) содержит подмножество флагов — расширение
схемы до полного набора: эскалация lead'у на Ф2 (schemas/** — tripwire §8).
"""

from typing import Any

from backend.models.design import DESIGN_TASK_TRANSITIONS, DesignTaskStatus
from backend.services.design.common import is_lead
from backend.services.design.state import can_user_transition

_S = DesignTaskStatus
_TERMINAL = (_S.ACCEPTED, _S.CANCELLED)


def compute_permissions(
    task: Any,
    user: Any = None,
    member_role: str = "viewer",
    *,
    actor_id: int | None = None,
) -> dict[str, bool]:
    """Флаги действий для пользователя. `user` — объект с .id (или actor_id явно).

    can_edit: автор — везде, кроме REVIEW и терминалов (правило update_task);
    lead — везде, кроме терминалов. can_comment: все с page-доступом (сам
    page-гейт — require_role в роутере, Ф2).
    """
    uid = actor_id if actor_id is not None else int(user.id)
    lead = is_lead(member_role)
    status = _S(str(task.status))
    terminal = status in _TERMINAL
    is_author = task.author_user_id == uid
    is_assignee = task.assignee_user_id == uid

    can_change_status = any(
        can_user_transition(task, t.value, uid, member_role)
        for t in DESIGN_TASK_TRANSITIONS.get(status, set())
    )

    return {
        "can_edit": (lead and not terminal)
        or (is_author and not terminal and status is not _S.REVIEW),
        "can_assign": lead and not terminal,
        "can_take": (is_assignee or lead) and status is _S.ASSIGNED,
        "can_submit": (lead or is_assignee) and status in (_S.IN_PROGRESS, _S.REVISION),
        "can_verdict": (lead or is_author) and status is _S.REVIEW,
        "can_hold": can_user_transition(task, _S.ON_HOLD.value, uid, member_role),
        "can_cancel": (lead or is_author) and not terminal,
        "can_delete": lead or is_author,
        "can_set_complexity": lead,
        "can_set_outsource": lead,
        "can_reorder": lead,
        "can_comment": True,
        "can_create_ab_test": (lead or is_author)
        and status is _S.ACCEPTED
        and task.nm_id is not None,
        "can_change_status": can_change_status,
        "can_move": lead or can_change_status,
    }
