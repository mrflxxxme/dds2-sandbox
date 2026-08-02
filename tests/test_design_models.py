# ruff: noqa: RUF002, RUF003
"""Ф0 «Дизайн карточек»: словарь переходов (AC-3) и partial-unique номера (AC-4).

AC-3: golden-snapshot словаря; у ACCEPTED/CANCELLED пустые исходящие; каждый
нетерминальный статус достижим из NEW обходом графа; DESIGN_BOARD_STATUSES
ровно 6 колонок PRD §4.
AC-4: повторное создание DES-1 в том же проекте после soft_delete() первой
задачи не нарушает uq_design_tasks_project_number (WHERE is_deleted = false).
Плюс DB-гарантия изоляции: составной FK (task_id, project_id) режет
cross-tenant вставку ребёнка с чужим project_id.
"""

from collections import deque

import pytest
from sqlalchemy.exc import IntegrityError

from backend.models import (
    DESIGN_ACTIVE_STATUSES,
    DESIGN_BOARD_STATUSES,
    DESIGN_TASK_TRANSITIONS,
    DesignMaterial,
    DesignTask,
    DesignTaskStatus,
)

TERMINAL_STATUSES = {DesignTaskStatus.ACCEPTED, DesignTaskStatus.CANCELLED}


# ═══════════════════════════════════════════════════════════════════════════════
# AC-3 — словарь переходов
# ═══════════════════════════════════════════════════════════════════════════════


class TestDesignTaskTransitions:
    def test_transitions_golden_snapshot(self):
        """Golden-snapshot: любое изменение словаря — осознанная правка теста (Р1)."""
        assert DESIGN_TASK_TRANSITIONS == {
            DesignTaskStatus.NEW: {
                DesignTaskStatus.ASSIGNED,
                DesignTaskStatus.ON_HOLD,
                DesignTaskStatus.CANCELLED,
            },
            DesignTaskStatus.ASSIGNED: {
                DesignTaskStatus.IN_PROGRESS,
                DesignTaskStatus.NEW,  # снять исполнителя
                DesignTaskStatus.ON_HOLD,
                DesignTaskStatus.CANCELLED,
            },
            DesignTaskStatus.IN_PROGRESS: {
                DesignTaskStatus.REVIEW,
                DesignTaskStatus.ON_HOLD,
                DesignTaskStatus.CANCELLED,
            },
            DesignTaskStatus.REVIEW: {
                DesignTaskStatus.ACCEPTED,
                DesignTaskStatus.REVISION,
                DesignTaskStatus.CANCELLED,
            },
            DesignTaskStatus.REVISION: {
                DesignTaskStatus.REVIEW,
                DesignTaskStatus.ON_HOLD,
                DesignTaskStatus.CANCELLED,
            },
            DesignTaskStatus.ON_HOLD: {
                DesignTaskStatus.NEW,
                DesignTaskStatus.ASSIGNED,
                DesignTaskStatus.IN_PROGRESS,
                DesignTaskStatus.REVISION,  # симметрия ⇄ (REVISION→ON_HOLD уже есть)
                DesignTaskStatus.CANCELLED,
            },
            DesignTaskStatus.ACCEPTED: set(),
            DesignTaskStatus.CANCELLED: set(),
        }

    def test_cancel_reachable_from_every_nonterminal(self):
        """CANCELLED — в целях каждого нетерминального статуса (отмена всегда доступна)."""
        for status in set(DesignTaskStatus) - TERMINAL_STATUSES:
            assert DesignTaskStatus.CANCELLED in DESIGN_TASK_TRANSITIONS[status], (
                f"Из {status.value} нет перехода в CANCELLED"
            )

    def test_terminal_statuses_have_no_outgoing(self):
        """ACCEPTED и CANCELLED — терминалы: пустые множества исходящих."""
        for status in TERMINAL_STATUSES:
            assert DESIGN_TASK_TRANSITIONS[status] == set(), (
                f"{status.value} терминален, но имеет исходящие переходы: "
                f"{DESIGN_TASK_TRANSITIONS[status]}"
            )

    def test_dict_covers_every_status(self):
        """Каждый статус — ключ словаря (полнота статусной машины)."""
        assert set(DESIGN_TASK_TRANSITIONS.keys()) == set(DesignTaskStatus)

    def test_targets_are_valid_and_not_self(self):
        """Все цели переходов — валидные статусы, самопереходов нет."""
        for src, targets in DESIGN_TASK_TRANSITIONS.items():
            for tgt in targets:
                assert isinstance(tgt, DesignTaskStatus)
            assert src not in targets, f"Самопереход {src.value} → {src.value}"

    def test_every_nonterminal_reachable_from_new(self):
        """Обход графа: каждый нетерминальный статус достижим из NEW."""
        reachable: set[DesignTaskStatus] = {DesignTaskStatus.NEW}
        queue = deque([DesignTaskStatus.NEW])
        while queue:
            current = queue.popleft()
            for nxt in DESIGN_TASK_TRANSITIONS[current]:
                if nxt not in reachable:
                    reachable.add(nxt)
                    queue.append(nxt)

        non_terminal = set(DesignTaskStatus) - TERMINAL_STATUSES
        unreachable = non_terminal - reachable
        assert not unreachable, f"Недостижимы из NEW: {sorted(s.value for s in unreachable)}"
        # Терминалы тоже должны быть достижимы (иначе задачу нельзя ни принять, ни отменить).
        assert TERMINAL_STATUSES <= reachable

    def test_board_statuses_exactly_six_columns(self):
        """Доска PRD §4 — ровно 6 колонок; ON_HOLD и CANCELLED вне доски."""
        assert len(DESIGN_BOARD_STATUSES) == 6
        assert len(set(DESIGN_BOARD_STATUSES)) == 6, "Дубли в DESIGN_BOARD_STATUSES"
        assert DESIGN_BOARD_STATUSES == [
            DesignTaskStatus.NEW,
            DesignTaskStatus.ASSIGNED,
            DesignTaskStatus.IN_PROGRESS,
            DesignTaskStatus.REVIEW,
            DesignTaskStatus.REVISION,
            DesignTaskStatus.ACCEPTED,
        ]
        assert DesignTaskStatus.ON_HOLD not in DESIGN_BOARD_STATUSES
        assert DesignTaskStatus.CANCELLED not in DESIGN_BOARD_STATUSES

    def test_active_statuses(self):
        """DESIGN_ACTIVE_STATUSES (workload/просрочка) — 5 рабочих, без ON_HOLD и терминалов."""
        assert DESIGN_ACTIVE_STATUSES == {
            DesignTaskStatus.NEW,
            DesignTaskStatus.ASSIGNED,
            DesignTaskStatus.IN_PROGRESS,
            DesignTaskStatus.REVIEW,
            DesignTaskStatus.REVISION,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# AC-4 — partial-unique uq_design_tasks_project_number (WHERE is_deleted = false)
# ═══════════════════════════════════════════════════════════════════════════════


def _make_task(project_id: int, author_id: int, number: str = "DES-1") -> DesignTask:
    return DesignTask(
        project_id=project_id,
        number=number,
        title="Инфографика ковёр 80x150",
        description="Главное фото + карусель, акцент на состав и износостойкость.",
        sheet_url="https://docs.google.com/spreadsheets/d/test-tz",
        author_user_id=author_id,
    )


class TestDesignTaskNumberPartialUnique:
    async def test_recreate_number_after_soft_delete(self, db_session, project):
        """soft_delete() освобождает номер: повторный DES-1 в том же проекте проходит."""
        first = _make_task(project.id, project.owner_id)
        db_session.add(first)
        await db_session.commit()

        first.soft_delete()
        await db_session.commit()
        assert first.is_deleted is True

        second = _make_task(project.id, project.owner_id)
        db_session.add(second)
        await db_session.commit()  # не должно упасть IntegrityError

        assert second.id != first.id
        assert second.number == first.number

    async def test_duplicate_active_number_rejected(self, db_session, project):
        """Живой дубль номера в том же проекте бьётся об уникальный индекс."""
        db_session.add(_make_task(project.id, project.owner_id, number="DES-2"))
        await db_session.commit()

        db_session.add(_make_task(project.id, project.owner_id, number="DES-2"))
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    async def test_same_number_in_other_project_allowed(self, db_session, project, other_project):
        """Нумерация DES-N — внутри проекта: одинаковый номер в двух проектах живёт."""
        db_session.add(_make_task(project.id, project.owner_id, number="DES-3"))
        db_session.add(_make_task(other_project.id, other_project.owner_id, number="DES-3"))
        await db_session.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Cross-tenant DB-гарантия — составной FK (task_id, project_id)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDesignChildTenantIsolation:
    async def test_material_with_foreign_project_id_rejected(
        self, db_session, project, other_project
    ):
        """Ребёнок с task_id задачи ЧУЖОГО проекта бьётся об составной FK (IntegrityError)."""
        task = _make_task(project.id, project.owner_id, number="DES-10")
        db_session.add(task)
        await db_session.commit()

        db_session.add(
            DesignMaterial(
                project_id=other_project.id,  # ≠ project_id задачи
                task_id=task.id,
                kind="LINK",
                url="https://example.com/ref",
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.commit()
        await db_session.rollback()

    async def test_material_with_matching_project_id_ok(self, db_session, project):
        """Контроль: тот же project_id, что у задачи — вставка проходит."""
        task = _make_task(project.id, project.owner_id, number="DES-11")
        db_session.add(task)
        await db_session.commit()

        db_session.add(
            DesignMaterial(
                project_id=project.id,
                task_id=task.id,
                kind="LINK",
                url="https://example.com/ref",
            )
        )
        await db_session.commit()
