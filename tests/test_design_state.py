# ruff: noqa: RUF002, RUF003
"""Ф1 «Дизайн карточек»: state.py — переходы, гварды, побочные эффекты, гонки.

AC-1: все разрешённые переходы словаря проходят validate_transition; по одному
запрещённому на каждый статус — ValueError; терминалы без исходящих.
AC-2: гварды переходов — каждая ошибка с заданным текстом спека.
AC-6: цикл IN_PROGRESS→ON_HOLD→IN_PROGRESS восстанавливает работу,
held_from_status очищен.
Плюс: гонка двух параллельных переходов (FOR UPDATE) — проходит ровно один.
"""

import asyncio

import pytest
from sqlalchemy import select

from backend.models.design import (
    DESIGN_TASK_TRANSITIONS,
    DesignSubmission,
    DesignTaskEvent,
    DesignTaskStatus,
    DesignVerdict,
)
from backend.services.design import state as design_state
from tests.conftest_api import TestSessionLocal
from tests.design_helpers import actor, add_submission, make_task, make_team

S = DesignTaskStatus

# По одному запрещённому переходу на каждый статус (AC-1).
FORBIDDEN_SAMPLES = [
    (S.NEW, S.REVIEW),
    (S.ASSIGNED, S.ACCEPTED),
    (S.IN_PROGRESS, S.ACCEPTED),
    (S.REVIEW, S.NEW),
    (S.REVISION, S.ACCEPTED),
    (S.ON_HOLD, S.ACCEPTED),
    (S.ACCEPTED, S.NEW),
    (S.CANCELLED, S.NEW),
]


class TestValidateTransition:
    def test_all_allowed_transitions_pass(self):
        """AC-1: каждое ребро словаря валидно."""
        for src, targets in DESIGN_TASK_TRANSITIONS.items():
            for tgt in targets:
                design_state.validate_transition(src.value, tgt.value)  # не бросает

    @pytest.mark.parametrize("src,tgt", FORBIDDEN_SAMPLES)
    def test_forbidden_transition_raises(self, src, tgt):
        """AC-1: запрещённый переход — ValueError с понятным текстом."""
        with pytest.raises(ValueError, match="Недопустимый переход"):
            design_state.validate_transition(src.value, tgt.value)

    def test_terminals_have_no_outgoing(self):
        """AC-1: из терминалов нельзя никуда."""
        for term in (S.ACCEPTED, S.CANCELLED):
            for tgt in S:
                if tgt is term:
                    continue
                with pytest.raises(ValueError):
                    design_state.validate_transition(term.value, tgt.value)

    def test_on_hold_to_revision_allowed(self):
        """Carry-over Ф0: ребро ON_HOLD→REVISION есть в словаре и валидно."""
        design_state.validate_transition(S.ON_HOLD.value, S.REVISION.value)


class TestChangeStatusGuards:
    """AC-2: тексты гвардов — точно по спеку F1."""

    async def test_new_to_assigned_requires_assignee(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(ValueError, match="Назначьте исполнителя"):
            await design_state.change_status(
                db_session, project.id, task.id, S.ASSIGNED, actor(team.lead), "admin"
            )

    async def test_in_progress_to_review_requires_pending_version(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
        )
        with pytest.raises(ValueError, match="Сдайте версию с файлами"):
            await design_state.change_status(
                db_session, project.id, task.id, S.REVIEW, actor(team.designer), "editor"
            )

    async def test_in_progress_to_review_rejects_pending_without_files(self, db_session, project):
        """Гвард →REVIEW требует PENDING-версию С файлами: окно «PENDING без
        файлов» (заливка сорвалась) переход не открывает."""
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
        )
        await add_submission(db_session, task, team.designer.id)  # без файлов
        with pytest.raises(ValueError, match="Сдайте версию с файлами"):
            await design_state.change_status(
                db_session, project.id, task.id, S.REVIEW, actor(team.designer), "editor"
            )

        with_files = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
        )
        await add_submission(db_session, with_files, team.designer.id, with_file=True)
        updated = await design_state.change_status(
            db_session, project.id, with_files.id, S.REVIEW, actor(team.designer), "editor"
        )
        assert updated.status == S.REVIEW.value

    async def test_review_to_revision_requires_comment(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        await add_submission(db_session, task, team.designer.id)
        with pytest.raises(ValueError, match="Напишите, что исправить"):
            await design_state.change_status(
                db_session, project.id, task.id, S.REVISION, actor(team.author), "editor"
            )

    async def test_review_to_accepted_requires_pending_version(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        with pytest.raises(ValueError, match="Нет версии на проверке"):
            await design_state.change_status(
                db_session, project.id, task.id, S.ACCEPTED, actor(team.author), "editor"
            )

    async def test_revision_to_review_requires_new_pending_version(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVISION, assignee_id=team.designer.id,
        )
        # Единственная версия уже отклонена — новой PENDING нет.
        await add_submission(db_session, task, team.designer.id, verdict=DesignVerdict.REJECTED)
        with pytest.raises(ValueError, match="Приложите исправленную версию"):
            await design_state.change_status(
                db_session, project.id, task.id, S.REVIEW, actor(team.designer), "editor"
            )

    async def test_cancel_requires_comment(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(ValueError, match="Укажите причину отмены"):
            await design_state.change_status(
                db_session, project.id, task.id, S.CANCELLED, actor(team.author), "editor"
            )

    async def test_task_from_other_project_not_found(self, db_session, project, other_project):
        """Изоляция: change_status не видит задачу чужого проекта."""
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        with pytest.raises(ValueError, match="Задача не найдена"):
            await design_state.change_status(
                db_session, other_project.id, task.id, S.CANCELLED,
                actor(team.lead), "admin", comment="чужая",
            )


class TestChangeStatusSideEffects:
    async def test_assigned_to_new_clears_assignee(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.ASSIGNED, assignee_id=team.designer.id,
        )
        updated = await design_state.change_status(
            db_session, project.id, task.id, S.NEW, actor(team.lead), "admin"
        )
        assert updated.status == S.NEW.value
        assert updated.assignee_user_id is None

    async def test_first_in_progress_sets_started_at_once(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.ASSIGNED, assignee_id=team.designer.id,
        )
        updated = await design_state.change_status(
            db_session, project.id, task.id, S.IN_PROGRESS, actor(team.designer), "editor"
        )
        assert updated.started_at is not None
        first_started = updated.started_at

        # ON_HOLD и обратно — started_at не перезаписывается.
        await design_state.change_status(
            db_session, project.id, task.id, S.ON_HOLD, actor(team.designer), "editor"
        )
        updated = await design_state.change_status(
            db_session, project.id, task.id, S.IN_PROGRESS, actor(team.designer), "editor"
        )
        assert updated.started_at == first_started

    async def test_on_hold_cycle_restores_work(self, db_session, project):
        """AC-6: IN_PROGRESS→ON_HOLD пишет held_from_status; возврат чистит его."""
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.IN_PROGRESS, assignee_id=team.designer.id,
        )
        held = await design_state.change_status(
            db_session, project.id, task.id, S.ON_HOLD, actor(team.designer), "editor"
        )
        assert held.status == S.ON_HOLD.value
        assert held.held_from_status == S.IN_PROGRESS.value

        restored = await design_state.change_status(
            db_session, project.id, task.id, S.IN_PROGRESS, actor(team.designer), "editor"
        )
        assert restored.status == S.IN_PROGRESS.value
        assert restored.held_from_status is None
        assert restored.assignee_user_id == team.designer.id

    async def test_on_hold_to_revision_via_service(self, db_session, project):
        """Carry-over Ф0: возврат из ON_HOLD в REVISION работает end-to-end."""
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.ON_HOLD, assignee_id=team.designer.id,
            held_from_status=S.REVISION.value,
        )
        restored = await design_state.change_status(
            db_session, project.id, task.id, S.REVISION, actor(team.lead), "admin"
        )
        assert restored.status == S.REVISION.value
        assert restored.held_from_status is None

    async def test_accepted_sets_accepted_at_and_closes_pending_version(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        sub = await add_submission(db_session, task, team.designer.id)
        updated = await design_state.change_status(
            db_session, project.id, task.id, S.ACCEPTED, actor(team.author), "editor"
        )
        assert updated.status == S.ACCEPTED.value
        assert updated.accepted_at is not None

        res = await db_session.execute(
            select(DesignSubmission).where(
                DesignSubmission.id == sub.id,
                DesignSubmission.project_id == project.id,
            )
        )
        closed = res.scalar_one()
        assert closed.verdict == DesignVerdict.ACCEPTED.value
        assert closed.verdict_by_user_id == team.author.id
        assert closed.verdict_at is not None

    async def test_revision_entry_rejects_pending_and_requires_new_version(
        self, db_session, project
    ):
        """Fix-цикл п.6: вход в REVISION (любой путь) отклоняет текущую PENDING
        с verdict_comment=комментарий перехода; возврат в REVIEW требует НОВОЙ
        версии — гвард честный."""
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        sub = await add_submission(db_session, task, team.designer.id, with_file=True)

        # Прямой change_status(→REVISION) БЕЗ set_verdict.
        await design_state.change_status(
            db_session, project.id, task.id, S.REVISION,
            actor(team.author), "editor", comment="Заменить фон",
        )
        await db_session.refresh(sub)
        assert sub.verdict == DesignVerdict.REJECTED.value
        assert sub.verdict_comment == "Заменить фон"
        assert sub.verdict_by_user_id == team.author.id
        assert sub.verdict_at is not None

        # Старая версия отклонена — возврат без новой версии не проходит.
        with pytest.raises(ValueError, match="Приложите исправленную версию"):
            await design_state.change_status(
                db_session, project.id, task.id, S.REVIEW, actor(team.designer), "editor"
            )

        # Новая версия с файлами открывает путь.
        await add_submission(
            db_session, task, team.designer.id, version_no=2, with_file=True
        )
        updated = await design_state.change_status(
            db_session, project.id, task.id, S.REVIEW, actor(team.designer), "editor"
        )
        assert updated.status == S.REVIEW.value

    async def test_event_journal_appended(self, db_session, project):
        team = await make_team(db_session, project)
        task = await make_task(db_session, project.id, team.author.id)
        await design_state.change_status(
            db_session, project.id, task.id, S.CANCELLED,
            actor(team.author), "editor", comment="Дубликат заявки",
        )
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
        assert len(events) == 1
        ev = events[0]
        assert (ev.old_status, ev.new_status) == (S.NEW.value, S.CANCELLED.value)
        assert ev.comment == "Дубликат заявки"
        assert ev.changed_by == team.author.username


class TestChangeStatusRace:
    async def test_parallel_transitions_only_one_wins(self, db_session, project):
        """Гонка: REVIEW→ACCEPTED ∥ REVIEW→REVISION под FOR UPDATE — проходит ровно один."""
        team = await make_team(db_session, project)
        task = await make_task(
            db_session, project.id, team.author.id,
            status=S.REVIEW, assignee_id=team.designer.id,
        )
        await add_submission(db_session, task, team.designer.id)
        lead = actor(team.lead)

        async def attempt(target: DesignTaskStatus, comment: str | None) -> str:
            async with TestSessionLocal() as session:
                try:
                    await design_state.change_status(
                        session, project.id, task.id, target, lead, "admin", comment=comment
                    )
                    return "ok"
                except ValueError:
                    return "rejected"

        results = await asyncio.gather(
            attempt(S.ACCEPTED, None),
            attempt(S.REVISION, "поправить фон"),
        )
        assert sorted(results) == ["ok", "rejected"], results

        # refresh — иначе identity map сессии вернёт устаревший снапшот задачи.
        await db_session.refresh(task)
        assert task.status in (S.ACCEPTED.value, S.REVISION.value)
