"""
Тесты внутридневных снимков рекламы (интрадей-график «место принятия решения»).

- snapshot_ad_intraday: пишет накопительные счётчики активных кампаний из кабинетной
  сессии; гейт частоты (ads_snapshot_interval_min); skip без сессии; EXPIRED при 401.
- get_intraday_metrics: дельта соседних снимков = метрики за интервал; totals = последний
  накопительный счётчик.
- settings: get/set ads_snapshot_interval_min (валидация кратности тику).

Кабинетный клиент замокан (живой WB в тесты не ходит).
"""

from datetime import timedelta

import pytest
import pytz
from sqlalchemy import func, select

from backend.models.integrations import WbAdCampaign, WbAdCampaignSnapshot
from backend.services.funnel.ad_campaigns_service import snapshot_ad_intraday
from backend.services.funnel.ads_manager import MSK, get_intraday_metrics
from backend.services.settings_service import (
    get_ads_snapshot_interval_min,
    set_ads_snapshot_interval_min,
)
from backend.utils.time import utcnow

CID = 990101


def _today_msk():
    return pytz.UTC.localize(utcnow()).astimezone(MSK).date()


class _FakeClient:
    """Замена WbPortalClient: отдаёт заранее заданные строки или бросает исключение."""

    def __init__(self, rows=None, raise_exc=None):
        self.rows = rows or []
        self.raise_exc = raise_exc
        self.closed = False

    async def fetch_campaigns_stats(self, ids, d_from, d_to):
        if self.raise_exc:
            raise self.raise_exc
        return self.rows

    async def aclose(self):
        self.closed = True


def _patch_getter(monkeypatch, client_or_exc):
    async def _getter(db, pid):
        if isinstance(client_or_exc, Exception):
            raise client_or_exc
        return client_or_exc

    monkeypatch.setattr(
        "backend.services.integrations_service.get_wb_portal_client", _getter
    )


async def _seed_campaign(db, project_id, status=9):
    db.add(WbAdCampaign(project_id=project_id, campaign_id=CID, name="Intraday", status=status))
    await db.commit()


# ─── snapshot_ad_intraday ─────────────────────────────────────────────────────


async def test_snapshot_writes_rows(db_session, project, monkeypatch):
    await _seed_campaign(db_session, project.id)
    _patch_getter(monkeypatch, _FakeClient(rows=[
        {"campaign_id": CID, "views": 500, "clicks": 31, "spend": 277.0},
    ]))
    res = await snapshot_ad_intraday(db_session, project.id)
    assert res["snapshots"] == 1
    snap = (await db_session.execute(
        select(WbAdCampaignSnapshot).where(WbAdCampaignSnapshot.project_id == project.id)
    )).scalar_one()
    assert snap.views_cum == 500 and snap.clicks_cum == 31 and float(snap.spend_cum) == 277.0
    assert snap.stat_date == _today_msk()


async def test_snapshot_no_session_skips(db_session, project, monkeypatch):
    await _seed_campaign(db_session, project.id)
    _patch_getter(monkeypatch, ValueError("нет сессии"))
    res = await snapshot_ad_intraday(db_session, project.id)
    # detail несёт текст ValueError: «нет сессии» и «не расшифровался ключ» неразличимы
    # по коду причины, и без текста обе тонут в сводке тика одинаковым no_session.
    assert res == {"snapshots": 0, "skipped": "no_session", "detail": "нет сессии"}


async def test_snapshot_no_active_campaigns(db_session, project, monkeypatch):
    await _seed_campaign(db_session, project.id, status=11)  # приостановлена
    client = _FakeClient(rows=[{"campaign_id": CID, "views": 1, "clicks": 0, "spend": 0.0}])
    _patch_getter(monkeypatch, client)
    res = await snapshot_ad_intraday(db_session, project.id)
    assert res == {"snapshots": 0, "skipped": "no_active_campaigns"}
    assert client.closed is True  # клиент закрыт даже на пустой выборке


async def test_snapshot_session_expired_marks_expired(db_session, project, monkeypatch):
    from backend.integrations.wb_portal_client import WbSessionExpired

    await _seed_campaign(db_session, project.id)
    _patch_getter(monkeypatch, _FakeClient(raise_exc=WbSessionExpired("401")))
    marked = {}

    async def _mark(db, pid):
        marked["pid"] = pid

    monkeypatch.setattr("backend.services.integrations_service.mark_wb_portal_expired", _mark)
    res = await snapshot_ad_intraday(db_session, project.id)
    assert res["error"] == "session_expired"
    assert marked.get("pid") == project.id


async def test_snapshot_interval_gate_skips_when_recent(db_session, project, monkeypatch):
    await _seed_campaign(db_session, project.id)
    await set_ads_snapshot_interval_min(db_session, project.id, 30)
    # свежий снимок 5 минут назад — при интервале 30 тик должен пропуститься
    db_session.add(WbAdCampaignSnapshot(
        project_id=project.id, campaign_id=CID, stat_date=_today_msk(),
        captured_at=utcnow() - timedelta(minutes=5), views_cum=10, clicks_cum=1, spend_cum=5,
    ))
    await db_session.commit()
    client = _FakeClient(rows=[{"campaign_id": CID, "views": 99, "clicks": 9, "spend": 9.0}])
    _patch_getter(monkeypatch, client)
    res = await snapshot_ad_intraday(db_session, project.id)
    assert res == {"snapshots": 0, "skipped": "interval"}
    assert client.closed is True
    # новый снимок НЕ добавился
    cnt = (await db_session.execute(
        select(func.count()).select_from(WbAdCampaignSnapshot)
        .where(WbAdCampaignSnapshot.project_id == project.id)
    )).scalar()
    assert cnt == 1


async def test_snapshot_interval_gate_passes_when_stale(db_session, project, monkeypatch):
    await _seed_campaign(db_session, project.id)
    await set_ads_snapshot_interval_min(db_session, project.id, 30)
    db_session.add(WbAdCampaignSnapshot(
        project_id=project.id, campaign_id=CID, stat_date=_today_msk(),
        captured_at=utcnow() - timedelta(minutes=40), views_cum=10, clicks_cum=1, spend_cum=5,
    ))
    await db_session.commit()
    _patch_getter(monkeypatch, _FakeClient(rows=[{"campaign_id": CID, "views": 99, "clicks": 9, "spend": 9.0}]))
    res = await snapshot_ad_intraday(db_session, project.id)
    assert res["snapshots"] == 1


# ─── get_intraday_metrics ─────────────────────────────────────────────────────


async def test_intraday_deltas_and_totals(db_session, project):
    await _seed_campaign(db_session, project.id)
    today = _today_msk()
    base = utcnow().replace(hour=6, minute=0, second=0, microsecond=0)
    for i, (v, c, s) in enumerate([(100, 5, 50), (350, 20, 180), (500, 31, 277)]):
        db_session.add(WbAdCampaignSnapshot(
            project_id=project.id, campaign_id=CID, stat_date=today,
            captured_at=base + timedelta(hours=2 * i), views_cum=v, clicks_cum=c, spend_cum=s,
        ))
    await db_session.commit()
    res = await get_intraday_metrics(db_session, project.id, CID)
    assert res["snapshots"] == 3
    assert [(p["views"], p["clicks"], p["spend"]) for p in res["points"]] == [
        (100, 5, 50.0), (250, 15, 130.0), (150, 11, 97.0),
    ]
    assert res["totals"] == {"views": 500, "clicks": 31, "spend": 277.0}


async def test_intraday_clamps_counter_decrease(db_session, project):
    """WB может скорректировать счётчик вниз — дельта зажимается в ноль, не уходит в минус."""
    await _seed_campaign(db_session, project.id)
    today = _today_msk()
    base = utcnow().replace(hour=6, minute=0, second=0, microsecond=0)
    for i, (v, c, s) in enumerate([(100, 5, 50), (80, 4, 40)]):  # второй снимок МЕНЬШЕ
        db_session.add(WbAdCampaignSnapshot(
            project_id=project.id, campaign_id=CID, stat_date=today,
            captured_at=base + timedelta(hours=i), views_cum=v, clicks_cum=c, spend_cum=s,
        ))
    await db_session.commit()
    res = await get_intraday_metrics(db_session, project.id, CID)
    p = res["points"][1]
    assert (p["views"], p["clicks"], p["spend"]) == (0, 0, 0.0)


async def test_intraday_unknown_campaign(db_session, project):
    res = await get_intraday_metrics(db_session, project.id, 424242)
    assert res == {"error": "campaign_not_found"}


# ─── settings: ads_snapshot_interval_min ──────────────────────────────────────


async def test_interval_default_is_10(db_session, project):
    assert await get_ads_snapshot_interval_min(db_session, project.id) == 10


async def test_interval_set_get(db_session, project):
    await set_ads_snapshot_interval_min(db_session, project.id, 20)
    assert await get_ads_snapshot_interval_min(db_session, project.id) == 20


async def test_interval_rejects_invalid(db_session, project):
    with pytest.raises(ValueError):
        await set_ads_snapshot_interval_min(db_session, project.id, 7)


# ─── snapshot_ad_intraday_all_projects: видимость нулевых исходов ─────────────


class _FakeSession:
    """Заглушка AsyncSessionLocal() — джобе сессия нужна лишь чтобы передать её в сервис."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch_job(monkeypatch, project_ids, results):
    """results: dict[pid] → dict-результат сервиса или Exception."""
    from backend.scheduler.jobs import funnel as job_mod

    async def _ids():
        return project_ids

    async def _snapshot(db, pid):
        res = results[pid]
        if isinstance(res, Exception):
            raise res
        return res

    monkeypatch.setattr(job_mod, "get_sync_project_ids", _ids)
    monkeypatch.setattr(job_mod, "AsyncSessionLocal", _FakeSession)
    monkeypatch.setattr(
        "backend.services.funnel.ad_campaigns_service.snapshot_ad_intraday", _snapshot
    )


async def test_job_summary_reports_every_zero_reason(monkeypatch, caplog):
    """Сводка тика перечисляет ПРИЧИНЫ нулей — иначе молчащая джоба неотличима от рабочей."""
    from backend.scheduler.jobs.funnel import snapshot_ad_intraday_all_projects

    _patch_job(monkeypatch, [1, 2, 3, 4], {
        1: {"snapshots": 3},
        2: {"snapshots": 0, "skipped": "no_session", "detail": "Сессия WB-кабинета не задана."},
        3: {"snapshots": 0, "skipped": "interval"},
        4: {"snapshots": 0, "skipped": "no_active_campaigns"},
    })
    with caplog.at_level("INFO", logger="dds.scheduler"):
        await snapshot_ad_intraday_all_projects()

    summary = [r.message for r in caplog.records if "done" in r.message]
    assert len(summary) == 1, "ожидается ровно одна сводная строка на тик"
    line = summary[0]
    assert "3 snapshots" in line
    assert "ok=1" in line and "no_session=1" in line and "interval=1" in line
    assert "no_active_campaigns=1" in line
    # причина «нет сессии» неоднозначна (нет ключа vs не расшифровался) → текст в сводке
    assert "Сессия WB-кабинета не задана." in line


async def test_job_summary_survives_project_failure(monkeypatch, caplog):
    """Падение одного проекта не роняет тик и попадает в сводку отдельной причиной."""
    from backend.scheduler.jobs.funnel import snapshot_ad_intraday_all_projects

    _patch_job(monkeypatch, [1, 2], {
        1: RuntimeError("боом"),
        2: {"snapshots": 2},
    })
    with caplog.at_level("INFO", logger="dds.scheduler"):
        await snapshot_ad_intraday_all_projects()

    line = next(r.message for r in caplog.records if "done" in r.message)
    assert "failed=1" in line and "ok=1" in line


async def test_job_reports_zero_rows_from_wb(monkeypatch, caplog):
    """Кампании активны, а WB не отдал строк — это отдельный сигнал, не «нет кампаний»."""
    from backend.scheduler.jobs.funnel import snapshot_ad_intraday_all_projects

    _patch_job(monkeypatch, [1], {1: {"snapshots": 0}})
    with caplog.at_level("INFO", logger="dds.scheduler"):
        await snapshot_ad_intraday_all_projects()

    line = next(r.message for r in caplog.records if "done" in r.message)
    assert "no_rows_from_wb=1" in line
