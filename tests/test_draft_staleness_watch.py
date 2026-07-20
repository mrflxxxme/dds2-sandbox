# ruff: noqa: RUF001, RUF002, RUF003
"""Сторож черновиков сборки (scheduler job draft_staleness_watch).

Юнит-тесты на выборку протухших черновиков/остатков (реальная тест-БД, фикстуры
project/other_project из conftest) и на анти-спам гейт (fake Redis).
"""

from datetime import timedelta

import pytest

import backend.scheduler.jobs.draft_staleness_watch as dsw
from backend.models.assembly import AssemblyDraft, AssemblyDraftEvent
from backend.scheduler.jobs.draft_staleness_watch import (
    STALE_THRESHOLD_SECONDS,
    build_draft_stale_message,
    build_wb_stocks_stale_message,
    get_stale_drafts,
    get_wb_stocks_age_seconds,
)
from backend.utils.time import utcnow

NOW = utcnow()


async def _mk_draft(db, project_id: int, *, age_hours: float, name: str = "Черновик", deleted: bool = False) -> AssemblyDraft:
    draft = AssemblyDraft(
        project_id=project_id,
        name=name,
        distribution={},
        created_at=NOW - timedelta(hours=age_hours + 1),
        updated_at=NOW - timedelta(hours=age_hours),
        is_deleted=deleted,
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def _mk_event(db, project_id: int, draft_id: int, event_type: str, *, age_hours: float) -> None:
    db.add(
        AssemblyDraftEvent(
            project_id=project_id,
            draft_id=draft_id,
            event_type=event_type,
            created_at=NOW - timedelta(hours=age_hours),
        )
    )
    await db.commit()


# ─── Выборка протухших черновиков ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_draft_without_events(db_session, project):
    draft = await _mk_draft(db_session, project.id, age_hours=7, name="Старый")
    stale = await get_stale_drafts(db_session, project.id, now=NOW)
    assert [d["draft_id"] for d in stale] == [draft.id]
    assert stale[0]["name"] == "Старый"
    assert stale[0]["age_hours"] == 7


@pytest.mark.asyncio
async def test_fresh_draft_not_stale(db_session, project):
    await _mk_draft(db_session, project.id, age_hours=1)
    assert await get_stale_drafts(db_session, project.id, now=NOW) == []


@pytest.mark.asyncio
async def test_fresh_sync_event_unstales_draft(db_session, project):
    """Старый updated_at, но свежее событие синка → черновиком занимались."""
    draft = await _mk_draft(db_session, project.id, age_hours=10)
    await _mk_event(db_session, project.id, draft.id, "AUTO_SYNC", age_hours=1)
    assert await get_stale_drafts(db_session, project.id, now=NOW) == []


@pytest.mark.asyncio
async def test_old_sync_event_keeps_draft_stale(db_session, project):
    """Событие есть, но само старше порога → черновик протух, возраст — от события."""
    draft = await _mk_draft(db_session, project.id, age_hours=20)
    await _mk_event(db_session, project.id, draft.id, "ACCEPTANCE_SYNC", age_hours=8)
    stale = await get_stale_drafts(db_session, project.id, now=NOW)
    assert [d["draft_id"] for d in stale] == [draft.id]
    assert stale[0]["age_hours"] == 8  # от последнего события, не от updated_at


@pytest.mark.asyncio
async def test_non_sync_event_type_ignored(db_session, project):
    """COMMIT_REQUEST не входит в SYNC_EVENT_TYPES — свежесть по нему не считается."""
    draft = await _mk_draft(db_session, project.id, age_hours=10)
    await _mk_event(db_session, project.id, draft.id, "COMMIT_REQUEST", age_hours=1)
    stale = await get_stale_drafts(db_session, project.id, now=NOW)
    assert [d["draft_id"] for d in stale] == [draft.id]
    assert stale[0]["age_hours"] == 10  # fallback на updated_at


@pytest.mark.asyncio
async def test_soft_deleted_draft_excluded(db_session, project):
    await _mk_draft(db_session, project.id, age_hours=10, deleted=True)
    assert await get_stale_drafts(db_session, project.id, now=NOW) == []


@pytest.mark.asyncio
async def test_project_isolation(db_session, project, other_project):
    """Iron rule: протухший черновик чужого проекта в выборку не попадает."""
    await _mk_draft(db_session, other_project.id, age_hours=10)
    assert await get_stale_drafts(db_session, project.id, now=NOW) == []


# ─── Возраст остатков WB ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wb_stocks_age_none_without_rows(db_session, project):
    assert await get_wb_stocks_age_seconds(db_session, project.id, now=NOW) is None


@pytest.mark.asyncio
async def test_wb_stocks_age_from_max_updated_at(db_session, project):
    from sqlalchemy import text

    for nm_id, hours in ((1, 9), (2, 7)):  # max(updated_at) = самый свежий (7ч)
        await db_session.execute(
            text(
                "INSERT INTO wb_warehouse_stocks "
                "(project_id, nm_id, warehouse_name, quantity, quantity_full, "
                " in_way_to_client, in_way_from_client, updated_at) "
                "VALUES (:p, :nm, 'Коледино', 1, 1, 0, 0, :ts)"
            ),
            {"p": project.id, "nm": nm_id, "ts": NOW - timedelta(hours=hours)},
        )
    await db_session.commit()

    age = await get_wb_stocks_age_seconds(db_session, project.id, now=NOW)
    assert age is not None
    assert abs(age - 7 * 3600) < 5
    assert age >= STALE_THRESHOLD_SECONDS


# ─── Анти-спам гейт ──────────────────────────────────────────────────────────


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None  # как redis-py: NX по существующему ключу → nil
        self.store[key] = value
        return True


@pytest.mark.asyncio
async def test_antispam_first_send_then_gate(monkeypatch):
    fake = _FakeRedis()

    async def _fake_get_redis():
        return fake

    monkeypatch.setattr(dsw, "get_redis", _fake_get_redis)

    assert await dsw._antispam_acquire("draft_stale_alert:1:10") is True
    assert await dsw._antispam_acquire("draft_stale_alert:1:10") is False  # в течение TTL — молчим
    assert await dsw._antispam_acquire("draft_stale_alert:1:11") is True  # другой черновик — свой ключ
    assert await dsw._antispam_acquire("wb_stocks_stale_alert:1") is True


@pytest.mark.asyncio
async def test_antispam_open_when_redis_down(monkeypatch):
    """Redis недоступен → best-effort шлём (частоту капит интервал джоба)."""

    async def _no_redis():
        return None

    monkeypatch.setattr(dsw, "get_redis", _no_redis)
    assert await dsw._antispam_acquire("draft_stale_alert:1:10") is True


# ─── Тексты алертов ──────────────────────────────────────────────────────────


def test_draft_stale_message_text():
    msg = build_draft_stale_message("Матрица июль", 7)
    assert "Черновик" in msg and "Матрица июль" in msg
    assert "7 ч" in msg
    assert "синк подтянет свежую потребность" in msg


def test_draft_stale_message_html_escaped():
    msg = build_draft_stale_message("A<b>&", 6)
    assert "A&lt;b&gt;&amp;" in msg
    assert "A<b>&" not in msg


def test_wb_stocks_stale_message_text():
    msg = build_wb_stocks_stale_message(9)
    assert "Остатки WB не обновлялись 9 ч" in msg
    assert "по старым данным" in msg
