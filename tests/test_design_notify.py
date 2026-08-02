# ruff: noqa: RUF001, RUF002, RUF003
"""Ф4 «Дизайн карточек»: личные TG-уведомления + утренняя сводка (design_notify).

AC-1: без TG-привязки — операция успешна, отправок 0.
AC-2: каждое из 4 событий (назначили / вернули / приняли / сдали версию) —
ровно одна отправка нужному получателю с ожидаемым текстом.
AC-3: сбой отправки — операция сервиса всё равно коммитится.
AC-4: повторный запуск джобы в тот же день — 0 повторных сообщений (антиспам).
AC-5: чат с design_notify_enabled=false сводку не получает; с true — получает.
AC-6: в джобе есть `except asyncio.CancelledError: raise` перед `except Exception`.
"""

import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update

import backend.scheduler.jobs.design_notify as dn
import backend.services.design.notify as design_notify
from backend.models.auth import Project, ProjectMember
from backend.models.design import DesignTask, DesignTaskStatus
from backend.models.telegram import TelegramBotUser, TelegramChatBinding
from backend.services import telegram_service
from backend.services.design import crud as design_crud, state as design_state
from backend.utils.time import utcnow
from tests.conftest_api import TestSessionLocal
from tests.design_helpers import actor, add_submission, make_task, make_team

S = DesignTaskStatus


# ─── Хелперы / фикстуры ──────────────────────────────────────────────────────


class _SendRecorder:
    """Мок send_analytics_message: копит (chat_id, text); fail=True — кидает."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[int, str]] = []
        self.fail = fail

    async def __call__(self, chat_id: int, text: str, **kwargs) -> bool:
        if self.fail:
            raise RuntimeError("tg down")
        self.calls.append((chat_id, text))
        return True

    def to_chat(self, chat_id: int) -> list[str]:
        return [t for c, t in self.calls if c == chat_id]


class _FakeRedis:
    """SET NX EX как в redis-py: NX по существующему ключу → nil."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


@pytest.fixture(autouse=True)
def _test_sessions(monkeypatch):
    """notify/джоба открывают свои сессии — направляем в тестовый engine."""
    monkeypatch.setattr(design_notify, "AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(dn, "AsyncSessionLocal", TestSessionLocal)


@pytest.fixture
def sent(monkeypatch) -> _SendRecorder:
    rec = _SendRecorder()
    monkeypatch.setattr(telegram_service, "send_analytics_message", rec)
    return rec


@pytest.fixture
def fake_redis(monkeypatch) -> _FakeRedis:
    fake = _FakeRedis()

    async def _get():
        return fake

    monkeypatch.setattr(dn, "get_redis", _get)
    return fake


def _uniq_tg_id() -> int:
    return int(uuid.uuid4().int % 10**12) + 10**12


def _uniq_chat_id() -> int:
    return -(int(uuid.uuid4().int % 10**12) + 10**6)


async def _link_tg(db, user_id: int) -> int:
    """Привязать TG-аккаунт к пользователю; вернуть telegram_id (= личный чат)."""
    tg_id = _uniq_tg_id()
    db.add(TelegramBotUser(telegram_id=tg_id, user_id=user_id))
    await db.commit()
    return tg_id


async def _bind_chat(db, project, *, enabled: bool) -> int:
    """Чат-привязка проекта с флагом design_notify_enabled; вернуть chat_id."""
    binding = await _make_binding(db, project, enabled=enabled)
    return binding.chat_id


async def _make_binding(db, project, *, enabled: bool = False) -> TelegramChatBinding:
    binding = TelegramChatBinding(
        chat_id=_uniq_chat_id(),
        project_id=project.id,
        design_notify_enabled=enabled,
        created_by_id=project.owner_id,
    )
    db.add(binding)
    await db.commit()
    await db.refresh(binding)
    return binding


async def _soft_delete_member(db, project_id: int, user_id: int) -> None:
    """Смоделировать удаление пользователя из проекта (soft-delete членства)."""
    await db.execute(
        update(ProjectMember)
        .where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
        .values(is_deleted=True)
    )
    await db.commit()


# ─── AC-1: нет привязки — молча скип, операция успешна ───────────────────────


async def test_assign_without_tg_binding_is_silent_success(db_session, project, sent):
    team = await make_team(db_session, project)
    task = await make_task(db_session, project.id, team.author.id)

    updated = await design_crud.assign(
        db_session, project.id, task.id, team.designer.id, actor(team.lead), "admin"
    )

    assert updated.assignee_user_id == team.designer.id
    assert updated.status == S.ASSIGNED.value  # операция успешна
    assert sent.calls == []  # отправок нет


# ─── AC-2: 4 события — ровно одна отправка с ожидаемым текстом ───────────────


async def test_assigned_sends_one_message_to_assignee(db_session, project, sent):
    team = await make_team(db_session, project)
    tg = await _link_tg(db_session, team.designer.id)
    task = await make_task(
        db_session, project.id, team.author.id, due_date=date(2026, 9, 1)
    )

    await design_crud.assign(
        db_session, project.id, task.id, team.designer.id, actor(team.lead), "admin"
    )

    assert len(sent.calls) == 1
    chat_id, text = sent.calls[0]
    assert chat_id == tg
    assert task.number in text
    assert "Инфографика ковёр 80x150" in text
    assert "срок 01.09.2026" in text
    assert f"/p/{project.slug}/design-tasks/{task.id}" in text


async def test_returned_sends_reason_to_assignee(db_session, project, sent):
    team = await make_team(db_session, project)
    tg = await _link_tg(db_session, team.designer.id)
    task = await make_task(
        db_session, project.id, team.author.id,
        status=S.REVIEW, assignee_id=team.designer.id,
    )
    await add_submission(db_session, task, team.designer.id)

    await design_state.change_status(
        db_session, project.id, task.id, S.REVISION, actor(team.author), "editor",
        comment="Поправь фон",
    )

    assert len(sent.calls) == 1
    chat_id, text = sent.calls[0]
    assert chat_id == tg
    assert task.number in text
    assert "вернули" in text
    assert "Поправь фон" in text


async def test_accepted_sends_one_message_to_assignee(db_session, project, sent):
    team = await make_team(db_session, project)
    tg = await _link_tg(db_session, team.designer.id)
    task = await make_task(
        db_session, project.id, team.author.id,
        status=S.REVIEW, assignee_id=team.designer.id,
    )
    await add_submission(db_session, task, team.designer.id)

    await design_state.change_status(
        db_session, project.id, task.id, S.ACCEPTED, actor(team.author), "editor"
    )

    assert len(sent.calls) == 1
    chat_id, text = sent.calls[0]
    assert chat_id == tg
    assert task.number in text
    assert "принята" in text


async def test_submitted_version_sends_one_message_to_author(db_session, project, sent):
    team = await make_team(db_session, project)
    author_tg = await _link_tg(db_session, team.author.id)
    await _link_tg(db_session, team.designer.id)  # исполнитель тоже привязан — но письмо только автору
    task = await make_task(
        db_session, project.id, team.author.id,
        status=S.IN_PROGRESS, assignee_id=team.designer.id,
    )
    await add_submission(db_session, task, team.designer.id, with_file=True)

    await design_state.change_status(
        db_session, project.id, task.id, S.REVIEW, actor(team.designer), "editor"
    )

    assert len(sent.calls) == 1
    chat_id, text = sent.calls[0]
    assert chat_id == author_tg
    assert task.number in text
    assert "ждёт проверки" in text
    assert "версия 1" in text


# ─── AC-3: сбой отправки не роняет операцию ──────────────────────────────────


async def test_send_failure_does_not_break_commit(db_session, project, monkeypatch):
    monkeypatch.setattr(telegram_service, "send_analytics_message", _SendRecorder(fail=True))
    team = await make_team(db_session, project)
    await _link_tg(db_session, team.designer.id)
    task = await make_task(
        db_session, project.id, team.author.id,
        status=S.REVIEW, assignee_id=team.designer.id,
    )
    await add_submission(db_session, task, team.designer.id)

    await design_state.change_status(
        db_session, project.id, task.id, S.ACCEPTED, actor(team.author), "editor"
    )  # не бросает

    fresh = (
        await db_session.execute(
            select(DesignTask.status).where(
                DesignTask.id == task.id, DesignTask.project_id == project.id
            )
        )
    ).scalar_one()
    assert fresh == S.ACCEPTED.value  # операция закоммичена несмотря на сбой TG


# ─── AC-4: повторный запуск джобы в тот же день — no-op ──────────────────────


async def test_digest_repeat_run_same_day_is_noop(db_session, project, sent, fake_redis):
    chat_id = await _bind_chat(db_session, project, enabled=True)

    await dn.send_design_digests()
    assert len(sent.to_chat(chat_id)) == 1  # первая — ушла

    await dn.send_design_digests()
    assert len(sent.to_chat(chat_id)) == 1  # повтор в день — 0 новых (антиспам-ключ)
    today = datetime.now(dn.MSK).date().isoformat()
    # ключ ветки сводки — с суффиксом :digest; ветки «срок завтра» не было → :due нет
    assert f"design_digest:{project.id}:{today}:digest" in fake_redis.store
    assert f"design_digest:{project.id}:{today}:due" not in fake_redis.store


async def test_antispam_keys_are_per_branch(db_session, project, sent, fake_redis):
    """LOW-1: ключи раздельные — захваченный :digest не гасит ветку «срок завтра»."""
    team = await make_team(db_session, project)
    tg = await _link_tg(db_session, team.designer.id)
    chat_id = await _bind_chat(db_session, project, enabled=True)
    tomorrow_msk = datetime.now(dn.MSK).date() + timedelta(days=1)
    await make_task(
        db_session, project.id, team.author.id,
        status=S.IN_PROGRESS, assignee_id=team.designer.id,
        due_date=tomorrow_msk,
    )

    today = datetime.now(dn.MSK).date().isoformat()
    # как будто сводка сегодня уже уходила (или её ветка упала после захвата)
    fake_redis.store[f"design_digest:{project.id}:{today}:digest"] = "1"

    await dn.send_design_digests()

    assert sent.to_chat(chat_id) == []  # сводка погашена своим ключом
    assert len(sent.to_chat(tg)) == 1  # личное «срок завтра» ушло независимо
    assert f"design_digest:{project.id}:{today}:due" in fake_redis.store


# ─── AC-5: флаг чата гейтит сводку; содержание сводки ────────────────────────


async def test_digest_respects_chat_flag_and_counts(
    db_session, project, other_project, sent, fake_redis
):
    team = await make_team(db_session, project)
    chat_on = await _bind_chat(db_session, project, enabled=True)
    chat_off = await _bind_chat(db_session, project, enabled=False)
    chat_other_off = await _bind_chat(db_session, other_project, enabled=False)

    today_msk = datetime.now(dn.MSK).date()
    await make_task(
        db_session, project.id, team.author.id,
        status=S.IN_PROGRESS, assignee_id=team.designer.id,
    )
    await make_task(
        db_session, project.id, team.author.id,
        status=S.REVIEW, assignee_id=team.designer.id,
    )
    await make_task(  # просрочена: срок вчера, активный статус
        db_session, project.id, team.author.id,
        status=S.ASSIGNED, assignee_id=team.designer.id,
        due_date=today_msk - timedelta(days=1),
    )
    await make_task(  # принята вчера
        db_session, project.id, team.author.id,
        status=S.ACCEPTED, assignee_id=team.designer.id,
        accepted_at=utcnow() - timedelta(days=1),
    )

    await dn.send_design_digests()

    on_msgs = sent.to_chat(chat_on)
    assert len(on_msgs) == 1  # включённый чат — ровно одна сводка
    text = on_msgs[0]
    assert "В работе: <b>1</b>" in text
    assert "На проверке: <b>1</b>" in text
    assert "Просрочено: <b>1</b>" in text
    assert "Принято вчера: <b>1</b>" in text
    assert f"/p/{project.slug}/design-tasks" in text

    assert sent.to_chat(chat_off) == []  # флаг false — сводки нет
    assert sent.to_chat(chat_other_off) == []


async def test_due_tomorrow_personal_reminder(db_session, project, sent, fake_redis):
    team = await make_team(db_session, project)
    tg = await _link_tg(db_session, team.designer.id)
    tomorrow_msk = datetime.now(dn.MSK).date() + timedelta(days=1)
    task = await make_task(
        db_session, project.id, team.author.id,
        status=S.IN_PROGRESS, assignee_id=team.designer.id,
        due_date=tomorrow_msk,
    )

    await dn.send_design_digests()

    personal = sent.to_chat(tg)
    assert len(personal) == 1
    assert task.number in personal[0]
    assert "срок завтра" in personal[0]
    assert f"/p/{project.slug}/design-tasks/{task.id}" in personal[0]


async def test_two_tg_bindings_resolve_to_freshest(db_session, project, sent, fake_redis):
    """`telegram_bot_users.user_id` НЕ уникален: две привязки одного пользователя
    → и джоба, и личные уведомления шлют в СВЕЖУЮ (максимальный id), а не в
    случайную (детерминированный ORDER BY + limit)."""
    team = await make_team(db_session, project)
    old_tg = await _link_tg(db_session, team.designer.id)
    new_tg = await _link_tg(db_session, team.designer.id)  # id больше → свежее
    assert old_tg != new_tg

    tomorrow_msk = datetime.now(dn.MSK).date() + timedelta(days=1)
    task = await make_task(db_session, project.id, team.author.id, due_date=tomorrow_msk)

    # 1. Личное уведомление (services/design/notify._resolve_chat_id).
    await design_crud.assign(
        db_session, project.id, task.id, team.designer.id, actor(team.lead), "admin"
    )
    assert sent.to_chat(new_tg) and sent.to_chat(old_tg) == []

    # 2. Батч-резолв джобы «срок завтра» (_resolve_telegram_ids).
    sent.calls.clear()
    await dn.send_design_digests()
    assert sent.to_chat(new_tg) and sent.to_chat(old_tg) == []


async def test_resolve_telegram_ids_is_deterministic(db_session, project):
    """Прямой контракт батч-резолва: одна запись на пользователя, свежайшая."""
    team = await make_team(db_session, project)
    first = await _link_tg(db_session, team.designer.id)
    last = await _link_tg(db_session, team.designer.id)

    resolved = await dn._resolve_telegram_ids(db_session, project.id, {team.designer.id})

    assert resolved == {team.designer.id: last}
    assert first not in resolved.values()


# ─── AC-6: CancelledError пробрасывается перед except Exception ──────────────


def test_job_source_reraises_cancelled_error():
    src = Path(dn.__file__).read_text(encoding="utf-8")
    i_cancel = src.index("except asyncio.CancelledError:")
    # следующая значимая строка — raise
    next_line = src[i_cancel:].split("\n")[1].strip()
    assert next_line == "raise"
    # и только ПОСЛЕ него в цикле джобы идёт except Exception
    assert src.index("except Exception", i_cancel) > i_cancel


# ─── Iron rule 2: soft-deleted проект — сводка не уходит ─────────────────────


async def test_digest_skips_soft_deleted_project(db_session, other_project, sent, fake_redis):
    chat_id = await _bind_chat(db_session, other_project, enabled=True)
    await db_session.execute(
        update(Project).where(Project.id == other_project.id).values(is_deleted=True)
    )
    await db_session.commit()

    await dn.send_design_digests()

    assert sent.to_chat(chat_id) == []  # сводка по soft-deleted проекту не уходит
    # проект вообще не входит в обход — антиспам-ключей по нему нет
    assert not any(k.startswith(f"design_digest:{other_project.id}:") for k in fake_redis.store)


# ─── MEDIUM-1: удалённый из проекта пользователь — без уведомлений ───────────


async def test_removed_member_gets_no_personal_notify(db_session, project, sent):
    """Исполнителя удалили из проекта ПОСЛЕ назначения — событие по задаче
    операцию не ломает, но личное уведомление ему уже не уходит."""
    team = await make_team(db_session, project)
    await _link_tg(db_session, team.designer.id)
    task = await make_task(
        db_session, project.id, team.author.id,
        status=S.REVIEW, assignee_id=team.designer.id,
    )
    await add_submission(db_session, task, team.designer.id)
    await _soft_delete_member(db_session, project.id, team.designer.id)

    await design_state.change_status(
        db_session, project.id, task.id, S.ACCEPTED, actor(team.author), "editor"
    )

    fresh = (
        await db_session.execute(
            select(DesignTask.status).where(
                DesignTask.id == task.id, DesignTask.project_id == project.id
            )
        )
    ).scalar_one()
    assert fresh == S.ACCEPTED.value  # операция успешна
    assert sent.calls == []  # но уведомление удалённому члену не уходит


async def test_removed_member_gets_no_due_tomorrow_reminder(db_session, project, sent, fake_redis):
    team = await make_team(db_session, project)
    tg = await _link_tg(db_session, team.designer.id)
    tomorrow_msk = datetime.now(dn.MSK).date() + timedelta(days=1)
    await make_task(
        db_session, project.id, team.author.id,
        status=S.IN_PROGRESS, assignee_id=team.designer.id,
        due_date=tomorrow_msk,
    )
    await _soft_delete_member(db_session, project.id, team.designer.id)

    await dn.send_design_digests()

    assert sent.to_chat(tg) == []  # membership soft-deleted → напоминания нет


# ─── MEDIUM-2: позитивная изоляция проектов + экранирование HTML ─────────────


async def test_digest_isolation_two_projects_each_own_counts(
    db_session, project, other_project, sent, fake_redis
):
    team_a = await make_team(db_session, project)
    team_b = await make_team(db_session, other_project)
    chat_a = await _bind_chat(db_session, project, enabled=True)
    chat_b = await _bind_chat(db_session, other_project, enabled=True)

    await make_task(
        db_session, project.id, team_a.author.id,
        status=S.IN_PROGRESS, assignee_id=team_a.designer.id,
    )
    await make_task(
        db_session, other_project.id, team_b.author.id,
        status=S.IN_PROGRESS, assignee_id=team_b.designer.id,
    )
    await make_task(
        db_session, other_project.id, team_b.author.id,
        status=S.REVIEW, assignee_id=team_b.designer.id,
    )

    await dn.send_design_digests()

    msgs_a = sent.to_chat(chat_a)
    msgs_b = sent.to_chat(chat_b)
    assert len(msgs_a) == 1 and len(msgs_b) == 1  # каждый чат — ровно одна сводка
    assert "В работе: <b>1</b>" in msgs_a[0]
    assert "На проверке: <b>0</b>" in msgs_a[0]
    assert f"/p/{project.slug}/design-tasks" in msgs_a[0]
    assert "В работе: <b>1</b>" in msgs_b[0]
    assert "На проверке: <b>1</b>" in msgs_b[0]
    assert f"/p/{other_project.slug}/design-tasks" in msgs_b[0]


async def test_due_tomorrow_title_html_escaped(db_session, project, sent, fake_redis):
    team = await make_team(db_session, project)
    tg = await _link_tg(db_session, team.designer.id)
    tomorrow_msk = datetime.now(dn.MSK).date() + timedelta(days=1)
    await make_task(
        db_session, project.id, team.author.id,
        status=S.IN_PROGRESS, assignee_id=team.designer.id,
        due_date=tomorrow_msk,
        title="<script>alert(1)</script>",
    )

    await dn.send_design_digests()

    personal = sent.to_chat(tg)
    assert len(personal) == 1
    assert "&lt;script&gt;" in personal[0]
    assert "<script>" not in personal[0]


# ─── HIGH-2: пара toggle_design_notify / list_design_notify_chats ────────────


class TestToggleDesignNotify:
    async def test_toggle_on_off(self, db_session, project):
        b = await _make_binding(db_session, project)
        assert b.design_notify_enabled is False

        assert await telegram_service.toggle_design_notify(db_session, b.id, project.id, True) is True
        await db_session.refresh(b)
        assert b.design_notify_enabled is True

        assert await telegram_service.toggle_design_notify(db_session, b.id, project.id, False) is True
        await db_session.refresh(b)
        assert b.design_notify_enabled is False

    async def test_project_isolation(self, db_session, project, other_project):
        b = await _make_binding(db_session, project)
        # тоггл под чужим project_id — no-op (not found)
        assert await telegram_service.toggle_design_notify(db_session, b.id, other_project.id, True) is False
        await db_session.refresh(b)
        assert b.design_notify_enabled is False

    async def test_missing_binding(self, db_session, project):
        assert await telegram_service.toggle_design_notify(db_session, 99_999_999, project.id, True) is False


class TestListDesignNotifyChats:
    async def test_only_opted_in_and_project_scoped(self, db_session, project, other_project):
        b_on = await _make_binding(db_session, project, enabled=True)
        await _make_binding(db_session, project, enabled=False)
        await _make_binding(db_session, other_project, enabled=True)

        chats = await telegram_service.list_design_notify_chats(db_session, project.id)
        assert {c.chat_id for c in chats} == {b_on.chat_id}
