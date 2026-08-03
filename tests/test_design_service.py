# ruff: noqa: RUF002, RUF003
"""Ф1 «Дизайн карточек»: crud / board / files / workload / stats.

AC-4: параллельные create_task и create_submission не дублируют number/version_no.
AC-5: move_task с исчерпанным зазором перенумеровывает колонку, сохраняя порядок.
AC-7: изоляция проектов в list_tasks и get_board.
Carry-over Ф0: next_number считает max ВКЛЮЧАЯ soft-deleted (append-only);
assign проверяет членство в проекте; move_task валидирует опорную задачу;
update_task — PATCH-семантика с отказом явного null для NOT NULL полей.
"""

import asyncio
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.models.cost import Nomenclature
from backend.models.design import (
    DesignSubmission,
    DesignTask,
    DesignTaskEvent,
    DesignTaskStatus,
    DesignVerdict,
)
from backend.schemas.design import DesignTaskCreate, DesignTaskUpdate
from backend.services.design import board as design_board
from backend.services.design import crud as design_crud
from backend.services.design import files as design_files
from backend.services.design import state as design_state
from backend.services.design import stats as design_stats
from backend.services.design import workload as design_workload
from backend.utils.time import utcnow
from tests.conftest_api import TestSessionLocal
from tests.design_helpers import actor, add_submission, make_task, make_team, make_user

S = DesignTaskStatus

PNG = b"\x89PNG\r\n\x1a\n" + b"fakepngdata"


def _payload(**kw) -> DesignTaskCreate:
    base = dict(
        title="Инфографика плед",
        description="Полный комплект, тёплые тона, 6 слайдов.",
        sheet_url="https://docs.google.com/spreadsheets/d/tz-42",
    )
    base.update(kw)
    return DesignTaskCreate(**base)


# ─── Fake MinIO ───────────────────────────────────────────────────────────────


class _FakeMinioClient:
    def __init__(self, store: dict):
        self.store = store

    async def put_object(self, bucket, name, data, length, content_type=None):
        self.store[name] = data.read()


@pytest.fixture
def fake_minio(monkeypatch):
    """Подменяет MinIO in-memory хранилищем на уровне модуля files."""
    store: dict[str, bytes] = {}
    client = _FakeMinioClient(store)

    async def _get_minio():
        return client

    async def _download_file(path: str):
        return store.get(path)

    monkeypatch.setattr(design_files, "get_minio", _get_minio)
    monkeypatch.setattr(design_files, "download_file", _download_file)
    return store


# ─── next_number / create_task ───────────────────────────────────────────────


class TestNumbering:
    async def test_sequential_numbers(self, db_session, project):
        team = await make_team(db_session, project)
        t1 = await design_crud.create_task(db_session, project.id, _payload(), actor(team.author))
        t2 = await design_crud.create_task(db_session, project.id, _payload(), actor(team.author))
        assert t1.number == "DES-1"
        assert t2.number == "DES-2"

    async def test_number_not_reused_after_soft_delete(self, db_session, project):
        """Carry-over Ф0: max включает soft-deleted — номер не переиспользуется."""
        team = await make_team(db_session, project)
        t1 = await design_crud.create_task(db_session, project.id, _payload(), actor(team.author))
        assert t1.number == "DES-1"
        t1.soft_delete()
        await db_session.commit()

        t2 = await design_crud.create_task(db_session, project.id, _payload(), actor(team.author))
        assert t2.number == "DES-2"

    async def test_parallel_create_no_duplicate_numbers(self, db_session, project):
        """AC-4: два параллельных create_task — разные номера."""
        team = await make_team(db_session, project)
        author = actor(team.author)

        async def create() -> str:
            async with TestSessionLocal() as session:
                task = await design_crud.create_task(session, project.id, _payload(), author)
                return task.number

        numbers = await asyncio.gather(create(), create())
        assert sorted(numbers) == ["DES-1", "DES-2"]

    async def test_create_task_defaults_and_event(self, db_session, project):
        team = await make_team(db_session, project)
        task = await design_crud.create_task(
            db_session, project.id, _payload(is_urgent=True, nm_id=123456), actor(team.author)
        )
        assert task.status == S.NEW.value
        assert task.author_user_id == team.author.id
        assert task.assignee_user_id is None
        assert task.sort_order == 1000
        assert task.is_urgent is True
        assert task.nm_id == 123456

        second = await design_crud.create_task(db_session, project.id, _payload(), actor(team.author))
        assert second.sort_order == 2000

        res = await db_session.execute(
            select(DesignTaskEvent)
            .where(
                DesignTaskEvent.project_id == project.id,
                DesignTaskEvent.task_id == task.id,
            )
            .limit(5)
        )
        events = res.scalars().all()
        assert len(events) == 1
        assert (events[0].old_status, events[0].new_status) == (None, S.NEW.value)


# ─── update_task ─────────────────────────────────────────────────────────────


class TestUpdateTask:
    async def test_patch_semantics_only_set_fields(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        old_description = task.description

        updated = await design_crud.update_task(
            db_session, project.id, task.id,
            DesignTaskUpdate(title="Новое название"),
            actor(team.author), "editor",
        )
        assert updated.title == "Новое название"
        assert updated.description == old_description

    async def test_explicit_null_for_not_null_field_rejected(self, db_session, project):
        """Carry-over Ф0: явный null в sheet_url (NOT NULL) — ValueError."""
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(ValueError, match="sheet_url"):
            await design_crud.update_task(
                db_session, project.id, task.id,
                DesignTaskUpdate(sheet_url=None),
                actor(team.author), "editor",
            )

    async def test_non_lead_cannot_edit_in_review(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        with pytest.raises(PermissionError):
            await design_crud.update_task(
                db_session, project.id, task.id,
                DesignTaskUpdate(title="X" * 5),
                actor(team.author), "editor",
            )

    async def test_lead_cannot_edit_terminal(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id, status=S.ACCEPTED)
        with pytest.raises(PermissionError):
            await design_crud.update_task(
                db_session, project.id, task.id,
                DesignTaskUpdate(title="Правка терминала"),
                actor(team.lead), "admin",
            )

    async def test_complexity_lead_only(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(PermissionError):
            await design_crud.update_task(
                db_session, project.id, task.id,
                DesignTaskUpdate(complexity="L"),
                actor(team.author), "editor",
            )
        updated = await design_crud.update_task(
            db_session, project.id, task.id,
            DesignTaskUpdate(complexity="L", is_outsourced=True),
            actor(team.lead), "admin",
        )
        assert updated.complexity == "L"
        assert updated.is_outsourced is True


# ─── assign / mark_viewed ────────────────────────────────────────────────────


class TestAssign:
    async def test_lead_assigns_and_new_moves_to_assigned(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        updated = await design_crud.assign(
            db_session, project.id, task.id, team.designer.id, actor(team.lead), "admin"
        )
        assert updated.assignee_user_id == team.designer.id
        assert updated.status == S.ASSIGNED.value

        res = await db_session.execute(
            select(DesignTaskEvent)
            .where(
                DesignTaskEvent.project_id == project.id,
                DesignTaskEvent.task_id == task.id,
            )
            .order_by(DesignTaskEvent.id)
            .limit(5)
        )
        events = res.scalars().all()
        assert events[-1].new_status == S.ASSIGNED.value

    async def test_assign_requires_project_membership(self, db_session, project):
        """Carry-over Ф0: исполнитель обязан быть активным участником проекта."""
        team = await make_team(db_session, project)
        stranger = await make_user(db_session, "stranger")
        await db_session.commit()
        task = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(ValueError, match="Пользователь не состоит в проекте"):
            await design_crud.assign(
                db_session, project.id, task.id, stranger.id, actor(team.lead), "admin"
            )

    async def test_assign_rejects_soft_deleted_member(self, db_session, project):
        """Carry-over Ф0: soft-deleted членство = не участник."""
        from tests.design_helpers import make_member

        team = await make_team(db_session, project)
        ex_member = await make_user(db_session, "exmember")
        membership = await make_member(db_session, project.id, ex_member.id)
        membership.soft_delete()
        await db_session.commit()
        task = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(ValueError, match="Пользователь не состоит в проекте"):
            await design_crud.assign(
                db_session, project.id, task.id, ex_member.id, actor(team.lead), "admin"
            )

    async def test_non_lead_cannot_assign(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(PermissionError):
            await design_crud.assign(
                db_session, project.id, task.id, team.designer.id, actor(team.author), "editor"
            )

    async def test_unassign_returns_assigned_to_new(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.ASSIGNED, assignee_id=team.designer.id,
        )
        updated = await design_crud.assign(
            db_session, project.id, task.id, None, actor(team.lead), "admin"
        )
        assert updated.assignee_user_id is None
        assert updated.status == S.NEW.value

    async def test_unassign_blocked_outside_new_assigned_on_hold(self, db_session, project):
        """Fix-цикл п.7: снятие исполнителя из рабочих статусов запрещено."""
        team = await make_team(db_session, project)
        for status in (S.IN_PROGRESS, S.REVIEW, S.REVISION):
            task = await make_task(
                db_session, project.id, team.author.id,
                status=status, assignee_id=team.designer.id,
            )
            with pytest.raises(ValueError, match="Сначала верните задачу"):
                await design_crud.assign(
                    db_session, project.id, task.id, None, actor(team.lead), "admin"
                )

    async def test_unassign_allowed_in_on_hold(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.ON_HOLD, assignee_id=team.designer.id,
        )
        updated = await design_crud.assign(
            db_session, project.id, task.id, None, actor(team.lead), "admin"
        )
        assert updated.assignee_user_id is None
        assert updated.status == S.ON_HOLD.value


class TestDeleteTask:
    async def test_author_soft_deletes_with_event(self, db_session, project):
        """Fix-цикл п.10: удалённая не в списке/доске, событие «удалена» есть."""
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        await design_crud.delete_task(
            db_session, project.id, task.id, actor(team.author), "editor"
        )

        items = await design_crud.list_tasks(db_session, project.id)
        assert task.id not in {i.id for i in items}
        board = await design_board.get_board(db_session, project.id)
        assert task.id not in {i.id for col in board.columns.values() for i in col}

        res = await db_session.execute(
            select(DesignTaskEvent)
            .where(
                DesignTaskEvent.project_id == project.id,
                DesignTaskEvent.task_id == task.id,
            )
            .order_by(DesignTaskEvent.id)
            .limit(10)
        )
        events = res.scalars().all()
        assert events[-1].comment == "Задача удалена"

    async def test_lead_deletes_any_status(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        await design_crud.delete_task(db_session, project.id, task.id, actor(team.lead), "admin")
        items = await design_crud.list_tasks(db_session, project.id)
        assert task.id not in {i.id for i in items}

    async def test_foreign_editor_cannot_delete(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(PermissionError):
            await design_crud.delete_task(
                db_session, project.id, task.id, actor(team.designer), "editor"
            )


class TestMarkViewed:
    async def test_lead_marks_viewed_idempotent(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        first = await design_crud.mark_viewed(
            db_session, project.id, task.id, actor(team.lead), "admin"
        )
        assert first.viewed_by_lead_at is not None
        ts = first.viewed_by_lead_at

        second = await design_crud.mark_viewed(
            db_session, project.id, task.id, actor(team.lead), "admin"
        )
        assert second.viewed_by_lead_at == ts  # идемпотентно

    async def test_non_lead_cannot_mark_viewed(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(PermissionError):
            await design_crud.mark_viewed(
                db_session, project.id, task.id, actor(team.author), "editor"
            )


# ─── list_tasks / get_task ───────────────────────────────────────────────────


class TestListTasks:
    async def test_filters_and_sort(self, db_session, project):
        team = await make_team(db_session, project)
        today = utcnow().date()
        t_urgent = await make_task(
            db_session, project.id, team.author.id,
            due_date=today + timedelta(days=1), is_urgent=True,
        )
        t_plain = await make_task(
            db_session, project.id, team.author.id,
            due_date=today + timedelta(days=1),
        )
        t_nodue = await make_task(db_session, project.id, team.author.id)
        t_cancelled = await make_task(
            db_session, project.id, team.author.id, status=S.CANCELLED
        )

        items = await design_crud.list_tasks(db_session, project.id)
        ids = [i.id for i in items]
        # Сортировка: due ASC NULLS LAST, urgent DESC, id DESC.
        assert ids.index(t_urgent.id) < ids.index(t_plain.id)
        assert ids.index(t_plain.id) < ids.index(t_nodue.id)
        assert t_cancelled.id in ids  # без фильтра статуса виден и CANCELLED

        only_cancelled = await design_crud.list_tasks(
            db_session, project.id, status=[S.CANCELLED.value]
        )
        assert [i.id for i in only_cancelled] == [t_cancelled.id]

        urgent_only = await design_crud.list_tasks(db_session, project.id, is_urgent=True)
        assert [i.id for i in urgent_only] == [t_urgent.id]

    async def test_search_by_number_and_title(self, db_session, project):
        team = await make_team(db_session, project)
        task = await design_crud.create_task(
            db_session, project.id, _payload(title="Ковёр вискоза серый"), actor(team.author)
        )
        by_number = await design_crud.list_tasks(db_session, project.id, q=task.number)
        assert [i.id for i in by_number] == [task.id]

        by_title = await design_crud.list_tasks(db_session, project.id, q="вискоза")
        assert [i.id for i in by_title] == [task.id]

        none = await design_crud.list_tasks(db_session, project.id, q="such-no-match-%_")
        assert none == []

    async def test_overdue_filter(self, db_session, project):
        team = await make_team(db_session, project)
        overdue = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
            due_date=utcnow().date() - timedelta(days=2),
        )
        await make_task(
            db_session, project.id, team.author.id,
            due_date=utcnow().date() + timedelta(days=2),
        )
        # Принятая с прошедшим сроком — НЕ просрочена (не активна).
        await make_task(
            db_session, project.id, team.author.id,
            status=S.ACCEPTED, due_date=utcnow().date() - timedelta(days=2),
        )
        items = await design_crud.list_tasks(db_session, project.id, overdue=True)
        assert [i.id for i in items] == [overdue.id]
        assert items[0].is_overdue is True

    async def test_project_isolation(self, db_session, project, other_project):
        """AC-7: задачи проекта A не видны из проекта B."""
        team = await make_team(db_session, project)
        other_team = await make_team(db_session, other_project)
        mine = await make_task(db_session, project.id, team.author.id)
        foreign = await make_task(db_session, other_project.id, other_team.author.id)

        items = await design_crud.list_tasks(db_session, project.id)
        ids = {i.id for i in items}
        assert mine.id in ids
        assert foreign.id not in ids

    async def test_soft_deleted_hidden(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        task.soft_delete()
        await db_session.commit()
        items = await design_crud.list_tasks(db_session, project.id)
        assert task.id not in {i.id for i in items}


class TestGetTask:
    async def test_detail_with_children_and_permissions(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        await add_submission(db_session, task, team.designer.id)

        detail = await design_crud.get_task(
            db_session, project.id, task.id, actor(team.author), "editor"
        )
        assert detail.id == task.id
        assert detail.number == task.number
        assert len(detail.submissions) == 1
        assert detail.permissions.can_verdict is True
        assert detail.permissions.can_assign is False
        assert detail.assignee_name  # имя обогащено

    async def test_isolation(self, db_session, project, other_project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(ValueError, match="Задача не найдена"):
            await design_crud.get_task(
                db_session, other_project.id, task.id, actor(team.lead), "admin"
            )


# ─── board ───────────────────────────────────────────────────────────────────


class TestBoard:
    async def test_board_six_columns_and_counts(self, db_session, project, other_project):
        """AC-7 (board) + структура ответа: 6 колонок, ON_HOLD/CANCELLED в counts."""
        team = await make_team(db_session, project)
        other_team = await make_team(db_session, other_project)
        a = await make_task(db_session, project.id, team.author.id, sort_order=2000)
        b = await make_task(db_session, project.id, team.author.id, sort_order=1000)
        await make_task(db_session, project.id, team.author.id, status=S.ON_HOLD)
        foreign = await make_task(db_session, other_project.id, other_team.author.id)

        board = await design_board.get_board(db_session, project.id)
        assert set(board.columns.keys()) == {s.value for s in S if s not in (S.ON_HOLD, S.CANCELLED)}
        new_ids = [i.id for i in board.columns[S.NEW.value]]
        assert new_ids == [b.id, a.id]  # порядок — sort_order
        assert foreign.id not in {i.id for col in board.columns.values() for i in col}
        assert board.counts[S.ON_HOLD.value] == 1
        assert board.counts.get(S.CANCELLED.value, 0) == 0

    async def test_accepted_column_ordered_by_accepted_at_desc(self, db_session, project):
        """Fix-цикл п.3: колонка ACCEPTED — accepted_at DESC (свежепринятые сверху),
        живые колонки — по sort_order."""
        team = await make_team(db_session, project)
        now = utcnow()
        older = await make_task(
            db_session, project.id, team.author.id,
            status=S.ACCEPTED, accepted_at=now - timedelta(days=2), sort_order=1000,
        )
        newer = await make_task(
            db_session, project.id, team.author.id,
            status=S.ACCEPTED, accepted_at=now, sort_order=2000,
        )
        board = await design_board.get_board(db_session, project.id)
        accepted_ids = [i.id for i in board.columns[S.ACCEPTED.value]]
        assert accepted_ids == [newer.id, older.id]

    async def test_move_midpoint(self, db_session, project):
        team = await make_team(db_session, project)
        a = await make_task(db_session, project.id, team.author.id, sort_order=1000)
        await make_task(db_session, project.id, team.author.id, sort_order=2000)
        c = await make_task(db_session, project.id, team.author.id, sort_order=3000)

        moved = await design_board.move_task(
            db_session, project.id, c.id, S.NEW.value, a.id, actor(team.lead), "admin"
        )
        assert moved.sort_order == 1500

    async def test_reorder_same_column_lead_only(self, db_session, project):
        team = await make_team(db_session, project)
        a = await make_task(db_session, project.id, team.author.id, sort_order=1000)
        c = await make_task(db_session, project.id, team.author.id, sort_order=2000)
        with pytest.raises(PermissionError):
            await design_board.move_task(
                db_session, project.id, c.id, S.NEW.value, a.id, actor(team.author), "editor"
            )

    async def test_cross_column_move_applies_state_machine(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.ASSIGNED, assignee_id=team.designer.id, sort_order=1000,
        )
        moved = await design_board.move_task(
            db_session, project.id, task.id, S.IN_PROGRESS.value, None,
            actor(team.designer), "editor",
        )
        assert moved.status == S.IN_PROGRESS.value
        assert moved.started_at is not None

        # Запрещённый переход через drag&drop бьётся о словарь.
        fresh = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(ValueError, match="Недопустимый переход"):
            await design_board.move_task(
                db_session, project.id, fresh.id, S.ACCEPTED.value, None,
                actor(team.lead), "admin",
            )

    async def test_after_task_validation(self, db_session, project, other_project):
        """Carry-over Ф0: опорная задача — тот же проект, живая, тот же столбец."""
        team = await make_team(db_session, project)
        other_team = await make_team(db_session, other_project)
        task = await make_task(db_session, project.id, team.author.id, sort_order=1000)

        foreign_anchor = await make_task(db_session, other_project.id, other_team.author.id)
        with pytest.raises(ValueError, match="Опорная задача не найдена"):
            await design_board.move_task(
                db_session, project.id, task.id, S.NEW.value, foreign_anchor.id,
                actor(team.lead), "admin",
            )

        wrong_column = await make_task(
            db_session, project.id, team.author.id,
            status=S.ASSIGNED, assignee_id=team.designer.id,
        )
        with pytest.raises(ValueError, match="Опорная задача не найдена"):
            await design_board.move_task(
                db_session, project.id, task.id, S.NEW.value, wrong_column.id,
                actor(team.lead), "admin",
            )

        dead_anchor = await make_task(db_session, project.id, team.author.id)
        dead_anchor.soft_delete()
        await db_session.commit()
        with pytest.raises(ValueError, match="Опорная задача не найдена"):
            await design_board.move_task(
                db_session, project.id, task.id, S.NEW.value, dead_anchor.id,
                actor(team.lead), "admin",
            )

    async def test_gap_exhaustion_renumbers_keeping_order(self, db_session, project):
        """AC-5: 3 вставки в зазор шириной 2 — перенумерация, порядок сохранён."""
        team = await make_team(db_session, project)
        a = await make_task(db_session, project.id, team.author.id, sort_order=1000)
        b = await make_task(db_session, project.id, team.author.id, sort_order=1002)
        movers = [
            await make_task(db_session, project.id, team.author.id, sort_order=10_000 + i)
            for i in range(3)
        ]
        lead = actor(team.lead)
        for m in movers:
            await design_board.move_task(
                db_session, project.id, m.id, S.NEW.value, a.id, lead, "admin"
            )

        res = await db_session.execute(
            select(DesignTask)
            .where(
                DesignTask.project_id == project.id,
                DesignTask.status == S.NEW.value,
                DesignTask.is_deleted == False,  # noqa: E712
            )
            .order_by(DesignTask.sort_order)
            .limit(50)
        )
        ordered = res.scalars().all()
        # Каждый следующий mover вставал сразу после A: A, m3, m2, m1, B.
        assert [t.id for t in ordered] == [a.id, movers[2].id, movers[1].id, movers[0].id, b.id]
        sort_orders = [t.sort_order for t in ordered]
        assert sort_orders == sorted(sort_orders)
        assert len(set(sort_orders)) == len(sort_orders)


# ─── files ───────────────────────────────────────────────────────────────────


class TestMaterials:
    async def test_upload_material_file(self, db_session, project, fake_minio):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        material = await design_files.upload_material_file(
            db_session,
            project_id=project.id,
            task_id=task.id,
            file_data=PNG,
            filename="cover.png",
            user=actor(team.author),
        )
        assert material.kind == "FILE"
        assert material.minio_path.startswith(f"design/{project.id}/{task.id}/materials/")
        assert fake_minio[material.minio_path] == PNG

    async def test_upload_rejects_executable_and_bad_mime(self, db_session, project, fake_minio):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(HTTPException) as exc:
            await design_files.upload_material_file(
                db_session,
                project_id=project.id,
                task_id=task.id,
                file_data=b"MZfake",
                filename="virus.exe",
                user=actor(team.author),
            )
        assert exc.value.status_code == 400

        with pytest.raises(HTTPException) as exc2:
            await design_files.upload_material_file(
                db_session,
                project_id=project.id,
                task_id=task.id,
                file_data=b"plain text",
                filename="notes.txt",
                mime_type="text/plain",
                user=actor(team.author),
            )
        assert exc2.value.status_code == 400

    async def test_upload_size_limit_20mb(self, db_session, project, fake_minio):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        big = PNG + b"0" * (20 * 1024 * 1024)
        with pytest.raises(HTTPException) as exc:
            await design_files.upload_material_file(
                db_session,
                project_id=project.id,
                task_id=task.id,
                file_data=big,
                filename="huge.png",
                user=actor(team.author),
            )
        # Fix-цикл Ф2 п.7: единый источник 413 — сервис тоже поднимает 413.
        assert exc.value.status_code == 413

    async def test_upload_503_when_minio_down(self, db_session, project, monkeypatch):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)

        async def _none():
            return None

        monkeypatch.setattr(design_files, "get_minio", _none)
        with pytest.raises(HTTPException) as exc:
            await design_files.upload_material_file(
                db_session,
                project_id=project.id,
                task_id=task.id,
                file_data=PNG,
                filename="cover.png",
                user=actor(team.author),
            )
        assert exc.value.status_code == 503

    async def test_add_link_and_nm_material(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        link = await design_files.add_material_link(
            db_session,
            project_id=project.id,
            task_id=task.id,
            url="https://example.com/ref",
            caption="референс",
            user=actor(team.author),
        )
        assert link.kind == "LINK"
        nm = await design_files.add_material_nm(
            db_session,
            project_id=project.id,
            task_id=task.id,
            ref_nm_id=987654,
            caption=None,
            user=actor(team.author),
        )
        assert nm.kind == "NM"

    async def test_download_material_isolation(self, db_session, project, other_project, fake_minio):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        material = await design_files.upload_material_file(
            db_session,
            project_id=project.id,
            task_id=task.id,
            file_data=PNG,
            filename="cover.png",
            user=actor(team.author),
        )
        data, filename, _ = await design_files.download_material(
            db_session, project_id=project.id, task_id=task.id, material_id=material.id
        )
        assert data == PNG
        assert filename == "cover.png"

        with pytest.raises(HTTPException) as exc:
            await design_files.download_material(
                db_session,
                project_id=other_project.id,
                task_id=task.id,
                material_id=material.id,
            )
        assert exc.value.status_code == 404

    async def test_upload_material_foreign_project_task(
        self, db_session, project, other_project, fake_minio
    ):
        """Изоляция: task_id чужого проекта → «Задача не найдена»."""
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(ValueError, match="Задача не найдена"):
            await design_files.upload_material_file(
                db_session,
                project_id=other_project.id,
                task_id=task.id,
                file_data=PNG,
                filename="cover.png",
                user=actor(team.author),
            )

    async def test_download_material_of_deleted_task_hidden(
        self, db_session, project, fake_minio
    ):
        """Fix-цикл п.14: вложения удалённой задачи недоступны."""
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        material = await design_files.upload_material_file(
            db_session,
            project_id=project.id,
            task_id=task.id,
            file_data=PNG,
            filename="cover.png",
            user=actor(team.author),
        )
        task.soft_delete()
        await db_session.commit()
        with pytest.raises(HTTPException) as exc:
            await design_files.download_material(
                db_session, project_id=project.id, task_id=task.id, material_id=material.id
            )
        assert exc.value.status_code == 404


class TestSubmissions:
    async def test_create_submission_versions_and_files(self, db_session, project, fake_minio):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
        )
        sub = await design_files.create_submission(
            db_session,
            project_id=project.id,
            task_id=task.id,
            files=[(PNG, "v1.png", "image/png")],
            comment="первая версия",
            user=actor(team.designer),
            member_role="editor",
        )
        assert sub.version_no == 1
        assert sub.verdict == DesignVerdict.PENDING.value
        assert len(sub.files) == 1
        assert sub.files[0].minio_path.startswith(f"design/{project.id}/{task.id}/v1/")

    async def test_create_submission_requires_files(self, db_session, project, fake_minio):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
        )
        with pytest.raises(ValueError, match="файл"):
            await design_files.create_submission(
                db_session,
                project_id=project.id,
                task_id=task.id,
                files=[],
                comment=None,
                user=actor(team.designer),
                member_role="editor",
            )

    async def test_only_assignee_or_lead_submit(self, db_session, project, fake_minio):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
        )
        with pytest.raises(PermissionError):
            await design_files.create_submission(
                db_session,
                project_id=project.id,
                task_id=task.id,
                files=[(PNG, "v1.png", "image/png")],
                comment=None,
                user=actor(team.author),
                member_role="editor",
            )

    async def test_parallel_submissions_no_duplicate_version(self, db_session, project, fake_minio):
        """AC-4 + fix-цикл п.2/Ф2 п.3: параллельный create_submission не дублирует
        version_no. С переиспользованием пустой PENDING (Ф2 п.3) второй submit
        либо отбит («уже есть несданная» — файлы первого уже видны), либо
        переиспользует ТУ ЖЕ строку версии (файлы ещё не долиты) — в обоих
        исходах строка DesignSubmission ровно одна, version_no = 1."""
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
        )
        designer = actor(team.designer)

        async def submit() -> tuple[str, int | None]:
            async with TestSessionLocal() as session:
                try:
                    sub = await design_files.create_submission(
                        session,
                        project_id=project.id,
                        task_id=task.id,
                        files=[(PNG, "v.png", "image/png")],
                        comment=None,
                        user=designer,
                        member_role="editor",
                    )
                    return ("ok", sub.version_no)
                except ValueError:
                    return ("rejected", None)

        results = await asyncio.gather(submit(), submit())
        oks = [r for r in results if r[0] == "ok"]
        assert oks, results  # хотя бы один прошёл
        assert {r[1] for r in oks} == {1}  # version_no не дублируется

        res = await db_session.execute(
            select(DesignSubmission).where(
                DesignSubmission.project_id == project.id,
                DesignSubmission.task_id == task.id,
            )
        )
        rows = res.scalars().all()
        assert len(rows) == 1  # строка версии ровно одна
        assert rows[0].version_no == 1

    async def test_second_pending_forbidden(self, db_session, project, fake_minio):
        """Fix-цикл п.2: вторая PENDING-версия невозможна."""
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
        )
        await design_files.create_submission(
            db_session,
            project_id=project.id,
            task_id=task.id,
            files=[(PNG, "v1.png", "image/png")],
            comment=None,
            user=actor(team.designer),
            member_role="editor",
        )
        with pytest.raises(ValueError, match="несданная версия 1 — дождитесь вердикта"):
            await design_files.create_submission(
                db_session,
                project_id=project.id,
                task_id=task.id,
                files=[(PNG, "v2.png", "image/png")],
                comment=None,
                user=actor(team.designer),
                member_role="editor",
            )

    async def test_upload_failure_keeps_version_row(
        self, db_session, project, fake_minio, monkeypatch
    ):
        """Fix-цикл Ф2 п.2: MinIO-down при заливке — 503 сквозной (НЕ глотается
        в 400/ValueError, канон ретраев фронта); строка версии остаётся без файлов."""
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
        )

        async def _minio_down():
            return None

        monkeypatch.setattr(design_files, "get_minio", _minio_down)
        with pytest.raises(HTTPException) as exc:
            await design_files.create_submission(
                db_session,
                project_id=project.id,
                task_id=task.id,
                files=[(PNG, "v1.png", "image/png")],
                comment=None,
                user=actor(team.designer),
                member_role="editor",
            )
        assert exc.value.status_code == 503

        res = await db_session.execute(
            select(DesignSubmission).where(
                DesignSubmission.project_id == project.id,
                DesignSubmission.task_id == task.id,
            )
        )
        sub = res.scalar_one()
        assert sub.verdict == DesignVerdict.PENDING.value
        # Гвард →REVIEW версию без файлов не пропускает.
        with pytest.raises(ValueError, match="Сдайте версию с файлами"):
            await design_state.change_status(
                db_session, project.id, task.id, S.REVIEW, actor(team.designer), "editor"
            )

    async def test_retry_reuses_empty_pending_version(
        self, db_session, project, fake_minio, monkeypatch
    ):
        """Fix-цикл Ф2 п.3: пустая PENDING (след упавшей заливки) не клинит задачу —
        повторная сдача переиспользует её тем же version_no, файлы дозаливаются."""
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
        )

        async def _minio_down():
            return None

        fake_get_minio = design_files.get_minio  # подмена fake_minio — вернуть её после «падения»
        monkeypatch.setattr(design_files, "get_minio", _minio_down)
        with pytest.raises(HTTPException):
            await design_files.create_submission(
                db_session,
                project_id=project.id,
                task_id=task.id,
                files=[(PNG, "v1.png", "image/png")],
                comment="первая попытка",
                user=actor(team.designer),
                member_role="editor",
            )

        # MinIO «ожил» (возврат к fake_minio) — повторная сдача успешна той же версией.
        monkeypatch.setattr(design_files, "get_minio", fake_get_minio)
        sub = await design_files.create_submission(
            db_session,
            project_id=project.id,
            task_id=task.id,
            files=[(PNG, "v1_retry.png", "image/png")],
            comment="повторная сдача",
            user=actor(team.designer),
            member_role="editor",
        )
        assert sub.version_no == 1
        assert sub.comment == "повторная сдача"
        assert len(sub.files) == 1

        res = await db_session.execute(
            select(DesignSubmission).where(
                DesignSubmission.project_id == project.id,
                DesignSubmission.task_id == task.id,
            )
        )
        rows = res.scalars().all()
        assert len(rows) == 1  # версия одна, дублей нет

    async def test_verdict_targets_passed_version(self, db_session, project, fake_minio):
        """Fix-цикл п.2: вердикт бьёт точно в указанную версию, не в «последнюю»."""
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        v1 = await add_submission(
            db_session, task, team.designer.id, verdict=DesignVerdict.REJECTED
        )
        v2 = await add_submission(db_session, task, team.designer.id, version_no=2)

        updated = await design_files.set_verdict(
            db_session,
            project_id=project.id,
            task_id=task.id,
            submission_id=v2.id,
            verdict=DesignVerdict.ACCEPTED.value,
            verdict_comment=None,
            user=actor(team.author),
            member_role="editor",
        )
        assert updated.id == v2.id
        assert updated.verdict == DesignVerdict.ACCEPTED.value
        await db_session.refresh(v1)
        assert v1.verdict == DesignVerdict.REJECTED.value  # не тронута

    async def test_set_verdict_rejected_writes_comment_and_moves_to_revision(
        self, db_session, project, fake_minio
    ):
        """Carry-over Ф0 (п.7): REJECTED пишет verdict_comment и в событие."""
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        sub = await add_submission(db_session, task, team.designer.id)

        with pytest.raises(ValueError, match="Напишите, что исправить"):
            await design_files.set_verdict(
                db_session,
                project_id=project.id,
                task_id=task.id,
                submission_id=sub.id,
                verdict=DesignVerdict.REJECTED.value,
                verdict_comment=None,
                user=actor(team.author),
                member_role="editor",
            )

        updated = await design_files.set_verdict(
            db_session,
            project_id=project.id,
            task_id=task.id,
            submission_id=sub.id,
            verdict=DesignVerdict.REJECTED.value,
            verdict_comment="Заменить фон на белый",
            user=actor(team.author),
            member_role="editor",
        )
        assert updated.verdict == DesignVerdict.REJECTED.value
        assert updated.verdict_comment == "Заменить фон на белый"

        res = await db_session.execute(
            select(DesignTask).where(
                DesignTask.id == task.id, DesignTask.project_id == project.id
            )
        )
        assert res.scalar_one().status == S.REVISION.value

        ev_res = await db_session.execute(
            select(DesignTaskEvent)
            .where(
                DesignTaskEvent.project_id == project.id,
                DesignTaskEvent.task_id == task.id,
            )
            .order_by(DesignTaskEvent.id)
            .limit(10)
        )
        last = ev_res.scalars().all()[-1]
        assert last.new_status == S.REVISION.value
        assert last.comment == "Заменить фон на белый"

    async def test_set_verdict_accepted_closes_version_and_task(
        self, db_session, project, fake_minio
    ):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        sub = await add_submission(db_session, task, team.designer.id)
        updated = await design_files.set_verdict(
            db_session,
            project_id=project.id,
            task_id=task.id,
            submission_id=sub.id,
            verdict=DesignVerdict.ACCEPTED.value,
            verdict_comment=None,
            user=actor(team.author),
            member_role="editor",
        )
        assert updated.verdict == DesignVerdict.ACCEPTED.value
        res = await db_session.execute(
            select(DesignTask).where(
                DesignTask.id == task.id, DesignTask.project_id == project.id
            )
        )
        task_row = res.scalar_one()
        assert task_row.status == S.ACCEPTED.value
        assert task_row.accepted_at is not None

    async def test_set_verdict_accepted_saves_comment(self, db_session, project, fake_minio):
        """Аудит Ф7 п.2: комментарий приёмки НЕ теряется — ложится в
        verdict_comment принятой версии (зеркало REJECTED-ветки)."""
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        sub = await add_submission(db_session, task, team.designer.id)
        updated = await design_files.set_verdict(
            db_session,
            project_id=project.id,
            task_id=task.id,
            submission_id=sub.id,
            verdict=DesignVerdict.ACCEPTED.value,
            verdict_comment="Принято, отличная работа",
            user=actor(team.author),
            member_role="editor",
        )
        assert updated.verdict == DesignVerdict.ACCEPTED.value
        assert updated.verdict_comment == "Принято, отличная работа"

        # Событие журнала тоже несёт комментарий приёмки.
        ev_res = await db_session.execute(
            select(DesignTaskEvent)
            .where(
                DesignTaskEvent.project_id == project.id,
                DesignTaskEvent.task_id == task.id,
            )
            .order_by(DesignTaskEvent.id)
            .limit(10)
        )
        last = ev_res.scalars().all()[-1]
        assert last.new_status == S.ACCEPTED.value
        assert last.comment == "Принято, отличная работа"

    async def test_set_verdict_only_on_pending(self, db_session, project, fake_minio):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        sub = await add_submission(
            db_session, task, team.designer.id, verdict=DesignVerdict.REJECTED
        )
        await add_submission(db_session, task, team.designer.id, version_no=2)
        with pytest.raises(ValueError, match="PENDING"):
            await design_files.set_verdict(
                db_session,
                project_id=project.id,
                task_id=task.id,
                submission_id=sub.id,
                verdict=DesignVerdict.ACCEPTED.value,
                verdict_comment=None,
                user=actor(team.author),
                member_role="editor",
            )


class TestDownloadSubmissionFile:
    async def test_isolation_and_deleted_task(
        self, db_session, project, other_project, fake_minio
    ):
        """Изоляция: чужой проект → «Файл не найден»; удалённая задача — тоже."""
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
        )
        sub = await design_files.create_submission(
            db_session,
            project_id=project.id,
            task_id=task.id,
            files=[(PNG, "v1.png", "image/png")],
            comment=None,
            user=actor(team.designer),
            member_role="editor",
        )
        file_id = sub.files[0].id

        data, filename, _ = await design_files.download_submission_file(
            db_session, project_id=project.id, task_id=task.id, file_id=file_id
        )
        assert data == PNG
        assert filename == "v1.png"

        with pytest.raises(HTTPException, match="Файл не найден") as exc:
            await design_files.download_submission_file(
                db_session, project_id=other_project.id, task_id=task.id, file_id=file_id
            )
        assert exc.value.status_code == 404

        # Fix-цикл п.14: файл версии удалённой задачи недоступен.
        task.soft_delete()
        await db_session.commit()
        with pytest.raises(HTTPException) as exc2:
            await design_files.download_submission_file(
                db_session, project_id=project.id, task_id=task.id, file_id=file_id
            )
        assert exc2.value.status_code == 404


class TestNotifyDispatch:
    async def test_notify_called_on_move_and_verdict(
        self, db_session, project, fake_minio, monkeypatch
    ):
        """Fix-цикл п.8: notify-хук зовётся на ВСЕХ путях переходов —
        и при move_task (dnd), и при set_verdict."""
        calls: list[tuple[str, str]] = []

        async def _spy(task, old_status, new_status, comment=None, actor=None):
            calls.append((old_status, new_status))

        monkeypatch.setattr(design_state, "notify_status_changed", _spy)

        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.ASSIGNED, assignee_id=team.designer.id, sort_order=1000,
        )
        await design_board.move_task(
            db_session, project.id, task.id, S.IN_PROGRESS.value, None,
            actor(team.designer), "editor",
        )
        assert (S.ASSIGNED.value, S.IN_PROGRESS.value) in calls

        task2 = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        sub = await add_submission(db_session, task2, team.designer.id, with_file=True)
        await design_files.set_verdict(
            db_session,
            project_id=project.id,
            task_id=task2.id,
            submission_id=sub.id,
            verdict=DesignVerdict.ACCEPTED.value,
            verdict_comment=None,
            user=actor(team.author),
            member_role="editor",
        )
        assert (S.REVIEW.value, S.ACCEPTED.value) in calls


class TestComments:
    async def test_add_comment(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        comment = await design_crud.add_comment(
            db_session, project.id, task.id, "Взял в работу завтра", actor(team.designer)
        )
        assert comment.body == "Взял в работу завтра"
        assert comment.author_user_id == team.designer.id


# ─── product_suggest ─────────────────────────────────────────────────────────


class TestProductSuggest:
    async def test_suggest_matches_and_isolation(self, db_session, project, other_project):
        db_session.add_all(
            [
                Nomenclature(
                    project_id=project.id,
                    barcode="2000000000011",
                    brand="CozyHome",
                    subject="Ковры",
                    article_seller="COZY-RUG-80",
                    article_wb=111222333,
                ),
                Nomenclature(
                    project_id=project.id,
                    barcode="2000000000012",
                    brand="CozyHome",
                    subject="Пледы",
                    article_seller="COZY-PLED-1",
                    article_wb=444555666,
                ),
                Nomenclature(
                    project_id=other_project.id,
                    barcode="2000000000013",
                    brand="CozyHome",
                    subject="Ковры",
                    article_seller="COZY-RUG-99",
                    article_wb=777888999,
                ),
            ]
        )
        await db_session.commit()

        rugs = await design_crud.product_suggest(db_session, project.id, "COZY-RUG")
        assert [s.nm_id for s in rugs] == [111222333]

        by_brand = await design_crud.product_suggest(db_session, project.id, "cozyhome")
        assert {s.nm_id for s in by_brand} == {111222333, 444555666}

        by_nm = await design_crud.product_suggest(db_session, project.id, "111222")
        assert [s.nm_id for s in by_nm] == [111222333]

    async def test_suggest_escapes_like_wildcards(self, db_session, project):
        db_session.add(
            Nomenclature(
                project_id=project.id,
                barcode="2000000000014",
                brand="B",
                subject="S",
                article_seller="PLAIN-1",
                article_wb=123123123,
            )
        )
        await db_session.commit()
        # «%» не должен матчить всё подряд.
        result = await design_crud.product_suggest(db_session, project.id, "%")
        assert result == []


# ─── workload / stats ────────────────────────────────────────────────────────


class TestWorkload:
    async def test_workload_aggregates(self, db_session, project):
        team = await make_team(db_session, project)
        today = utcnow().date()
        await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
            due_date=today - timedelta(days=1),
        )
        await make_task(
            db_session, project.id, team.author.id,
            status=S.REVISION, assignee_id=team.designer.id,
            due_date=today + timedelta(days=3),
        )
        await make_task(
            db_session, project.id, team.author.id,
            status=S.ASSIGNED, assignee_id=team.designer2.id,
        )
        # ON_HOLD и терминалы не считаются.
        await make_task(
            db_session, project.id, team.author.id,
            status=S.ON_HOLD, assignee_id=team.designer.id,
        )
        await make_task(
            db_session, project.id, team.author.id,
            status=S.ACCEPTED, assignee_id=team.designer.id,
        )

        rows = await design_workload.get_workload(db_session, project.id)
        by_user = {r.user_id: r for r in rows}
        d1 = by_user[team.designer.id]
        assert d1.active_tasks == 2
        assert d1.overdue == 1
        assert d1.in_revision == 1
        assert d1.nearest_due == today - timedelta(days=1)
        assert by_user[team.designer2.id].active_tasks == 1

    async def test_workload_isolation(self, db_session, project, other_project):
        team = await make_team(db_session, project)
        other_team = await make_team(db_session, other_project)
        await make_task(
            db_session, other_project.id, other_team.author.id,
            status=S.IN_PROGRESS, assignee_id=other_team.designer.id,
        )
        rows = await design_workload.get_workload(db_session, project.id)
        assert rows == []


class TestStats:
    async def test_stats_smoke(self, db_session, project):
        team = await make_team(db_session, project)
        today = utcnow().date()
        now = utcnow()
        # Принята в срок, с версией, с nm_id.
        on_time = await make_task(
            db_session, project.id, team.author.id,
            status=S.ACCEPTED, assignee_id=team.designer.id,
            due_date=today + timedelta(days=1), accepted_at=now, nm_id=111,
        )
        await add_submission(db_session, on_time, team.designer.id)
        # Принята с просрочкой, аутсорс.
        await make_task(
            db_session, project.id, team.author.id,
            status=S.ACCEPTED, assignee_id=team.designer.id,
            due_date=today - timedelta(days=3), accepted_at=now, is_outsourced=True,
        )
        # Висит без исполнителя больше 2 дней.
        stale = await make_task(db_session, project.id, team.author.id)
        stale.created_at = now - timedelta(days=3)
        await db_session.commit()

        stats = await design_stats.get_stats(db_session, project.id, None, None)
        assert stats.on_time_share == pytest.approx(0.5)
        assert stats.avg_versions_to_accept == pytest.approx(0.5)  # 1 версия на 2 принятых
        assert stats.median_cycle_days is not None
        assert stats.unassigned_over_2d == 1
        assert stats.outsourced_share == pytest.approx(0.5)
        assert stats.tracked_share is not None

    async def test_stats_isolation(self, db_session, project, other_project):
        """Изоляция: данные второго проекта на метрики первого не влияют."""
        other_team = await make_team(db_session, other_project)
        now = utcnow()
        foreign = await make_task(
            db_session, other_project.id, other_team.author.id,
            status=S.ACCEPTED, assignee_id=other_team.designer.id,
            due_date=now.date() + timedelta(days=1), accepted_at=now, nm_id=999,
        )
        await add_submission(db_session, foreign, other_team.designer.id)
        stale_foreign = await make_task(
            db_session, other_project.id, other_team.author.id
        )
        stale_foreign.created_at = now - timedelta(days=5)
        await db_session.commit()

        stats = await design_stats.get_stats(db_session, project.id, None, None)
        assert stats.on_time_share is None
        assert stats.avg_versions_to_accept is None
        assert stats.median_cycle_days is None
        assert stats.unassigned_over_2d == 0
        assert stats.outsourced_share is None
        assert stats.tracked_share is None
