"""
Тесты внутридневных снимков рекламы (интрадей-график «место принятия решения»).

- snapshot_ad_intraday: пишет накопительные счётчики активных кампаний из уже синканной
  официальной статистики WB (таблица wb_ad_campaign_daily за сегодня, ноль обращений к WB);
  гейт частоты (ads_snapshot_interval_min); skip если daily пуст; дедуп «unchanged».
- get_intraday_metrics: дельта соседних снимков = метрики за интервал; totals = последний
  накопительный счётчик.
- settings: get/set ads_snapshot_interval_min (валидация кратности тику).
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
import pytz
from sqlalchemy import func, select

from backend.models.integrations import WbAdCampaign, WbAdCampaignDaily, WbAdCampaignSnapshot
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


async def _seed_campaign(db, project_id, status=9):
    db.add(WbAdCampaign(project_id=project_id, campaign_id=CID, name="Intraday", status=status))
    await db.commit()


async def _seed_daily(db, project_id, views, clicks, spend, campaign_id=CID, day=None):
    """Строка официальной статистики за день — источник накопительного «сегодня»."""
    db.add(WbAdCampaignDaily(
        project_id=project_id, campaign_id=campaign_id, date=day or _today_msk(),
        views=views, clicks=clicks, spend=Decimal(str(spend)),
    ))
    await db.commit()


# ─── snapshot_ad_intraday ─────────────────────────────────────────────────────


async def test_snapshot_writes_rows(db_session, project, monkeypatch):
    await _seed_campaign(db_session, project.id)
    await _seed_daily(db_session, project.id, views=500, clicks=31, spend=277.0)
    res = await snapshot_ad_intraday(db_session, project.id)
    assert res["snapshots"] == 1
    snap = (await db_session.execute(
        select(WbAdCampaignSnapshot).where(WbAdCampaignSnapshot.project_id == project.id)
    )).scalar_one()
    assert snap.views_cum == 500 and snap.clicks_cum == 31 and float(snap.spend_cum) == 277.0
    assert snap.stat_date == _today_msk()


async def test_snapshot_no_active_campaigns(db_session, project, monkeypatch):
    await _seed_campaign(db_session, project.id, status=11)  # приостановлена
    await _seed_daily(db_session, project.id, views=1, clicks=0, spend=0.0)
    res = await snapshot_ad_intraday(db_session, project.id)
    assert res == {"snapshots": 0, "skipped": "no_active_campaigns"}


async def test_snapshot_no_rows_from_wb(db_session, project, monkeypatch):
    """Кампания активна, но синк ещё не наполнил сегодняшний день — не поломка."""
    await _seed_campaign(db_session, project.id)  # daily НЕ засеян
    res = await snapshot_ad_intraday(db_session, project.id)
    assert res == {"snapshots": 0, "skipped": "no_rows_from_wb"}


async def test_snapshot_unchanged_skips_duplicate(db_session, project, monkeypatch):
    """Счётчик не изменился с прошлого снимка → дубль не пишем (иначе график в нулях)."""
    await _seed_campaign(db_session, project.id)
    await _seed_daily(db_session, project.id, views=500, clicks=31, spend=277.0)
    # первый снимок 40 мин назад с теми же счётчиками (гейт интервала — дефолт 10 мин — пройдёт)
    db_session.add(WbAdCampaignSnapshot(
        project_id=project.id, campaign_id=CID, stat_date=_today_msk(),
        captured_at=utcnow() - timedelta(minutes=40), views_cum=500, clicks_cum=31, spend_cum=Decimal("277.0"),
    ))
    await db_session.commit()
    res = await snapshot_ad_intraday(db_session, project.id)
    assert res == {"snapshots": 0, "skipped": "unchanged"}
    cnt = (await db_session.execute(
        select(func.count()).select_from(WbAdCampaignSnapshot)
        .where(WbAdCampaignSnapshot.project_id == project.id)
    )).scalar()
    assert cnt == 1  # дубль не добавился


async def test_snapshot_writes_when_counter_grew(db_session, project, monkeypatch):
    """Счётчик вырос → новый снимок пишется."""
    await _seed_campaign(db_session, project.id)
    await _seed_daily(db_session, project.id, views=600, clicks=35, spend=300.0)
    db_session.add(WbAdCampaignSnapshot(
        project_id=project.id, campaign_id=CID, stat_date=_today_msk(),
        captured_at=utcnow() - timedelta(minutes=40), views_cum=500, clicks_cum=31, spend_cum=Decimal("277.0"),
    ))
    await db_session.commit()
    res = await snapshot_ad_intraday(db_session, project.id)
    assert res["snapshots"] == 1


async def test_snapshot_interval_gate_skips_when_recent(db_session, project, monkeypatch):
    await _seed_campaign(db_session, project.id)
    await _seed_daily(db_session, project.id, views=99, clicks=9, spend=9.0)
    await set_ads_snapshot_interval_min(db_session, project.id, 30)
    # свежий снимок 5 минут назад — при интервале 30 тик должен пропуститься
    db_session.add(WbAdCampaignSnapshot(
        project_id=project.id, campaign_id=CID, stat_date=_today_msk(),
        captured_at=utcnow() - timedelta(minutes=5), views_cum=10, clicks_cum=1, spend_cum=5,
    ))
    await db_session.commit()
    res = await snapshot_ad_intraday(db_session, project.id)
    assert res == {"snapshots": 0, "skipped": "interval"}
    # новый снимок НЕ добавился
    cnt = (await db_session.execute(
        select(func.count()).select_from(WbAdCampaignSnapshot)
        .where(WbAdCampaignSnapshot.project_id == project.id)
    )).scalar()
    assert cnt == 1


async def test_snapshot_interval_gate_passes_when_stale(db_session, project, monkeypatch):
    await _seed_campaign(db_session, project.id)
    await _seed_daily(db_session, project.id, views=99, clicks=9, spend=9.0)
    await set_ads_snapshot_interval_min(db_session, project.id, 30)
    db_session.add(WbAdCampaignSnapshot(
        project_id=project.id, campaign_id=CID, stat_date=_today_msk(),
        captured_at=utcnow() - timedelta(minutes=40), views_cum=10, clicks_cum=1, spend_cum=5,
    ))
    await db_session.commit()
    res = await snapshot_ad_intraday(db_session, project.id)
    assert res["snapshots"] == 1


# ─── get_intraday_metrics ─────────────────────────────────────────────────────


# captured_at — naive UTC; МСК = UTC+3. Час МСК H → UTC H-3.
def _yesterday_at_msk(hour_msk):
    y = _today_msk() - timedelta(days=1)
    return datetime.combine(y, time(hour=hour_msk - 3))


async def test_intraday_grid_buckets_deltas(db_session, project):
    """Дельты снимков ложатся в РЕГУЛЯРНУЮ сетку интервалов; прошлый день = все сутки.

    Шаг по умолчанию 30 мин → 48 бакетов. Снимки в 10:00 / 12:00 / 14:00 МСК.
    Первый (10:00) — БАЗА отсчёта, в бар не идёт. Приросты со второго: 12:00 → бакет 24
    (350-100=250), 14:00 → бакет 28 (500-350=150). Бакет 20 (10:00) остаётся нулевым.
    """
    await _seed_campaign(db_session, project.id)
    y = _today_msk() - timedelta(days=1)
    for hour, (v, c, s) in [(10, (100, 5, 50)), (12, (350, 20, 180)), (14, (500, 31, 277))]:
        db_session.add(WbAdCampaignSnapshot(
            project_id=project.id, campaign_id=CID, stat_date=y,
            captured_at=_yesterday_at_msk(hour), views_cum=v, clicks_cum=c, spend_cum=s,
        ))
    await db_session.commit()
    res = await get_intraday_metrics(db_session, project.id, CID, date=y.isoformat())

    assert len(res["points"]) == 48  # весь прошлый день регулярной сеткой по 30 мин
    pts = res["points"]
    assert (pts[20]["views"], pts[20]["clicks"]) == (0, 0)  # первый снимок = база, не объём
    assert (pts[24]["views"], pts[24]["clicks"], pts[24]["spend"]) == (250, 15, 130.0)
    assert (pts[28]["views"], pts[28]["clicks"], pts[28]["spend"]) == (150, 11, 97.0)
    assert pts[24]["time"] == "12:30" and pts[23]["time"] == "12:00"  # диапазон 12:00–12:30
    assert pts[22]["views"] == 0  # интервал без снимка = ноль
    # totals = последний накопительный WB (весь день), не сумма приростов
    assert res["totals"] == {"views": 500, "clicks": 31, "spend": 277.0}


async def test_intraday_clamps_counter_decrease(db_session, project):
    """WB может скорректировать счётчик вниз — дельта зажимается в ноль, не уходит в минус."""
    await _seed_campaign(db_session, project.id)
    y = _today_msk() - timedelta(days=1)
    for hour, (v, c, s) in [(10, (100, 5, 50)), (12, (80, 4, 40))]:  # второй снимок МЕНЬШЕ
        db_session.add(WbAdCampaignSnapshot(
            project_id=project.id, campaign_id=CID, stat_date=y,
            captured_at=_yesterday_at_msk(hour), views_cum=v, clicks_cum=c, spend_cum=s,
        ))
    await db_session.commit()
    res = await get_intraday_metrics(db_session, project.id, CID, date=y.isoformat())
    assert (res["points"][24]["views"], res["points"][24]["clicks"]) == (0, 0)  # бакет 12:00 (шаг 30)


async def test_intraday_unknown_campaign(db_session, project):
    res = await get_intraday_metrics(db_session, project.id, 424242)
    assert res == {"error": "campaign_not_found"}


# ─── settings: ads_snapshot_interval_min ──────────────────────────────────────


async def test_interval_default_is_30(db_session, project):
    assert await get_ads_snapshot_interval_min(db_session, project.id) == 30


async def test_interval_set_get(db_session, project):
    await set_ads_snapshot_interval_min(db_session, project.id, 60)
    assert await get_ads_snapshot_interval_min(db_session, project.id) == 60


async def test_interval_rejects_invalid(db_session, project):
    # 7 не кратно, 10/20 убраны (WB не обновляет чаще ~30 мин) — всё отклоняется
    for bad in (7, 10, 20):
        with pytest.raises(ValueError):
            await set_ads_snapshot_interval_min(db_session, project.id, bad)


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
        2: {"snapshots": 0, "skipped": "unchanged"},
        3: {"snapshots": 0, "skipped": "interval"},
        4: {"snapshots": 0, "skipped": "no_active_campaigns"},
    })
    with caplog.at_level("INFO", logger="dds.scheduler"):
        await snapshot_ad_intraday_all_projects()

    summary = [r.message for r in caplog.records if "done" in r.message]
    assert len(summary) == 1, "ожидается ровно одна сводная строка на тик"
    line = summary[0]
    assert "3 snapshots" in line
    assert "ok=1" in line and "unchanged=1" in line and "interval=1" in line
    assert "no_active_campaigns=1" in line


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
