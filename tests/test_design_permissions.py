# ruff: noqa: RUF002, RUF003
"""Ф1 «Дизайн карточек»: матрица «кто двигает» (AC-3) + compute_permissions.

AC-3: editor-автор не может ASSIGNED→IN_PROGRESS чужой задачи; editor-исполнитель
не может REVIEW→ACCEPTED; lead может всё по словарю. По кейсу на строку матрицы.
"""

import pytest

from backend.models.design import DESIGN_TASK_TRANSITIONS, DesignTaskStatus
from backend.services.design import state as design_state
from backend.services.design.permissions import compute_permissions
from tests.design_helpers import actor, make_task, make_team

S = DesignTaskStatus

AUTHOR_ID = 101
ASSIGNEE_ID = 202
OTHER_ID = 303


def _task(status: S, *, assignee_id: int | None = ASSIGNEE_ID, nm_id: int | None = None):
    """Лёгкая задача-объект для чистых функций (без БД)."""

    class _T:
        pass

    t = _T()
    t.status = status.value
    t.author_user_id = AUTHOR_ID
    t.assignee_user_id = assignee_id
    t.nm_id = nm_id
    return t


def can(task, target: S, user_id: int, member_role: str) -> bool:
    return design_state.can_user_transition(task, target.value, user_id, member_role)


class TestTransitionMatrix:
    """По кейсу на каждую строку матрицы «кто двигает» (спек F1)."""

    def test_lead_can_every_transition_in_dict(self):
        """Lead (owner/admin) может каждое ребро словаря."""
        for src, targets in DESIGN_TASK_TRANSITIONS.items():
            for tgt in targets:
                task = _task(src)
                for role in ("owner", "admin"):
                    assert can(task, tgt, OTHER_ID, role), f"lead({role}): {src.value}→{tgt.value}"

    def test_new_to_assigned_lead_only(self):
        task = _task(S.NEW)
        assert not can(task, S.ASSIGNED, AUTHOR_ID, "editor")
        assert not can(task, S.ASSIGNED, ASSIGNEE_ID, "editor")

    def test_assigned_to_new_lead_only(self):
        task = _task(S.ASSIGNED)
        assert not can(task, S.NEW, AUTHOR_ID, "editor")
        assert not can(task, S.NEW, ASSIGNEE_ID, "editor")

    def test_assigned_to_in_progress_assignee_not_author(self):
        """AC-3: editor-автор не может взять в работу чужую задачу; исполнитель может."""
        task = _task(S.ASSIGNED)
        assert can(task, S.IN_PROGRESS, ASSIGNEE_ID, "editor")
        assert not can(task, S.IN_PROGRESS, AUTHOR_ID, "editor")
        assert not can(task, S.IN_PROGRESS, OTHER_ID, "editor")

    def test_in_progress_to_review_assignee(self):
        task = _task(S.IN_PROGRESS)
        assert can(task, S.REVIEW, ASSIGNEE_ID, "editor")
        assert not can(task, S.REVIEW, AUTHOR_ID, "editor")

    def test_review_to_revision_author_not_assignee(self):
        task = _task(S.REVIEW)
        assert can(task, S.REVISION, AUTHOR_ID, "editor")
        assert not can(task, S.REVISION, ASSIGNEE_ID, "editor")

    def test_review_to_accepted_author_not_assignee(self):
        """AC-3: editor-исполнитель не может принять сам себя."""
        task = _task(S.REVIEW)
        assert can(task, S.ACCEPTED, AUTHOR_ID, "editor")
        assert not can(task, S.ACCEPTED, ASSIGNEE_ID, "editor")

    def test_revision_to_review_assignee_not_author(self):
        task = _task(S.REVISION)
        assert can(task, S.REVIEW, ASSIGNEE_ID, "editor")
        assert not can(task, S.REVIEW, AUTHOR_ID, "editor")

    def test_to_on_hold_assignee_own_only(self):
        """*→ON_HOLD: assignee — свою; чужой editor — нет; автор — нет."""
        task = _task(S.IN_PROGRESS)
        assert can(task, S.ON_HOLD, ASSIGNEE_ID, "editor")
        assert not can(task, S.ON_HOLD, AUTHOR_ID, "editor")
        assert not can(task, S.ON_HOLD, OTHER_ID, "editor")

    def test_from_on_hold_assignee_or_lead(self):
        task = _task(S.ON_HOLD)
        assert can(task, S.IN_PROGRESS, ASSIGNEE_ID, "editor")
        assert not can(task, S.IN_PROGRESS, AUTHOR_ID, "editor")

    def test_to_cancelled_author_not_assignee(self):
        task = _task(S.IN_PROGRESS)
        assert can(task, S.CANCELLED, AUTHOR_ID, "editor")
        assert not can(task, S.CANCELLED, ASSIGNEE_ID, "editor")

    def test_forbidden_edge_false_for_everyone(self):
        """Ребро вне словаря — False даже для lead (словарь первичен)."""
        task = _task(S.NEW)
        assert not can(task, S.ACCEPTED, OTHER_ID, "owner")


class TestChangeStatusPermissionEnforced:
    """Матрица применяется в change_status (PermissionError до гвардов)."""

    async def test_author_cannot_take_foreign_task(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.ASSIGNED, assignee_id=team.designer.id,
        )
        with pytest.raises(PermissionError):
            await design_state.change_status(
                db_session, project.id, task.id, S.IN_PROGRESS, actor(team.author), "editor"
            )

    async def test_assignee_cannot_accept_own_work(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        with pytest.raises(PermissionError):
            await design_state.change_status(
                db_session, project.id, task.id, S.ACCEPTED, actor(team.designer), "editor"
            )

    async def test_other_editor_cannot_hold_foreign_task(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
        )
        with pytest.raises(PermissionError):
            await design_state.change_status(
                db_session, project.id, task.id, S.ON_HOLD, actor(team.designer2), "editor"
            )


class TestComputePermissions:
    def test_lead_full_flags_on_active_task(self):
        task = _task(S.ASSIGNED)
        p = compute_permissions(task, actor_id=OTHER_ID, member_role="admin")
        assert p["can_edit"] and p["can_assign"] and p["can_reorder"]
        assert p["can_set_complexity"] and p["can_set_outsource"]
        assert p["can_hold"] and p["can_cancel"] and p["can_delete"]
        assert p["can_comment"]
        # Fix-цикл п.11: lead тоже может взять ASSIGNED в работу (матрица это
        # разрешает — флаг честный).
        assert p["can_take"]

    def test_viewer_cannot_comment(self):
        """Fix-цикл Ф2 п.5: can_comment = member_role != 'viewer' — зеркало
        editor-гейта POST /comments (viewer read-only)."""
        p = compute_permissions(_task(S.NEW), actor_id=OTHER_ID, member_role="viewer")
        assert not p["can_comment"]
        p_editor = compute_permissions(_task(S.NEW), actor_id=OTHER_ID, member_role="editor")
        assert p_editor["can_comment"]

    def test_can_take_lead_only_in_assigned(self):
        """can_take для lead — только в ASSIGNED; в остальных статусах False."""
        for status in (S.NEW, S.IN_PROGRESS, S.REVIEW, S.REVISION, S.ON_HOLD, S.ACCEPTED):
            p = compute_permissions(_task(status), actor_id=OTHER_ID, member_role="admin")
            assert not p["can_take"], status
        # Чужой editor (не исполнитель) в ASSIGNED — по-прежнему нет.
        p_editor = compute_permissions(_task(S.ASSIGNED), actor_id=OTHER_ID, member_role="editor")
        assert not p_editor["can_take"]

    def test_author_flags(self):
        task = _task(S.REVIEW)
        p = compute_permissions(task, actor_id=AUTHOR_ID, member_role="editor")
        assert p["can_verdict"]
        assert p["can_cancel"]
        assert not p["can_edit"]  # автор не правит в REVIEW
        assert not p["can_assign"] and not p["can_reorder"]
        assert not p["can_submit"]

    def test_author_can_edit_before_review(self):
        task = _task(S.NEW, assignee_id=None)
        p = compute_permissions(task, actor_id=AUTHOR_ID, member_role="editor")
        assert p["can_edit"]
        assert not p["can_verdict"]

    def test_assignee_flags(self):
        task = _task(S.ASSIGNED)
        p = compute_permissions(task, actor_id=ASSIGNEE_ID, member_role="editor")
        assert p["can_take"]
        assert not p["can_verdict"] and not p["can_assign"]

        task_ip = _task(S.IN_PROGRESS)
        p_ip = compute_permissions(task_ip, actor_id=ASSIGNEE_ID, member_role="editor")
        assert p_ip["can_submit"] and p_ip["can_hold"]
        assert not p_ip["can_take"]

    def test_terminal_locks_everything_even_for_lead(self):
        for status in (S.ACCEPTED, S.CANCELLED):
            task = _task(status)
            p = compute_permissions(task, actor_id=OTHER_ID, member_role="owner")
            assert not p["can_edit"]
            assert not p["can_assign"]
            assert not p["can_cancel"]
            assert not p["can_change_status"]

    def test_can_create_ab_test_only_accepted_with_nm(self):
        assert compute_permissions(
            _task(S.ACCEPTED, nm_id=12345), actor_id=AUTHOR_ID, member_role="editor"
        )["can_create_ab_test"]
        assert not compute_permissions(
            _task(S.ACCEPTED, nm_id=None), actor_id=AUTHOR_ID, member_role="editor"
        )["can_create_ab_test"]
        assert not compute_permissions(
            _task(S.REVIEW, nm_id=12345), actor_id=AUTHOR_ID, member_role="editor"
        )["can_create_ab_test"]
        # Чужой editor (не автор, не lead) — нет.
        assert not compute_permissions(
            _task(S.ACCEPTED, nm_id=12345), actor_id=OTHER_ID, member_role="editor"
        )["can_create_ab_test"]
