"""
Тесты «паспорта» пофразовой ставки — колонка «Стоит N дн» в кластеризаторе
(backend/services/funnel/cluster_analysis_service.py).

Проверяем: запись при применении, правило таймера (та же ставка не сбрасывает applied_at,
другая — сбрасывает), удаление при сбросе к кампании, запись пачкой только по успешным
фразам и доклейку паспорта в _enrich только при совпадении сохранённой ставки с текущей.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from backend.models import WbAdCampaign, WbAdClusterBid
from backend.services.funnel.cluster_analysis_service import (
    _enrich,
    set_cluster_bid,
    set_cluster_bids_bulk,
)
from backend.utils.time import utcnow

CID = 991200
NM = 555

_SVC = "backend.services.funnel.cluster_analysis_service"


async def _seed(db, project_id, bid_mode="manual"):
    db.add(WbAdCampaign(
        project_id=project_id, campaign_id=CID, name="c",
        campaign_type="cpm", bid_mode=bid_mode, status=9, nm_ids=[NM],
    ))
    await db.commit()


async def _rows(db, project_id):
    return (await db.execute(
        select(WbAdClusterBid).where(WbAdClusterBid.project_id == project_id)
    )).scalars().all()


# ─── Запись при применении ───

async def test_records_passport_on_apply(db_session, project):
    await _seed(db_session, project.id)
    sent = AsyncMock(return_value=(True, None, 100.0))  # (ok, err, applied)
    with patch(f"{_SVC}._resolve_key", AsyncMock(return_value="key")), \
         patch(f"{_SVC}.set_normquery_bid", sent):
        res = await set_cluster_bid(
            db_session, project.id, CID, NM, "юбка", 100, verify=False,
            source="recommendation", basis_drr=12.6, basis_cpm=931, target_drr=8,
        )
    assert res["ok"] is True
    rows = await _rows(db_session, project.id)
    assert len(rows) == 1
    r = rows[0]
    assert r.norm_query == "юбка"
    assert float(r.applied_bid) == 100
    assert r.source == "recommendation"
    assert float(r.basis_drr) == 12.6
    assert float(r.basis_cpm) == 931
    assert r.applied_at is not None


# ─── Правило таймера ───

async def test_timer_not_reset_on_same_bid(db_session, project):
    """Та же ставка повторно — applied_at (старт «сбора данных») НЕ сбрасывается."""
    await _seed(db_session, project.id)
    sent = AsyncMock(return_value=(True, None, 100.0))
    with patch(f"{_SVC}._resolve_key", AsyncMock(return_value="key")), \
         patch(f"{_SVC}.set_normquery_bid", sent):
        await set_cluster_bid(db_session, project.id, CID, NM, "юбка", 100, verify=False)
        row = (await _rows(db_session, project.id))[0]
        old = utcnow() - timedelta(days=5)
        row.applied_at = old
        await db_session.commit()
        # повторно ровно та же ставка → таймер стоит на месте
        await set_cluster_bid(db_session, project.id, CID, NM, "юбка", 100, verify=False)
    await db_session.refresh(row)
    assert abs((row.applied_at - old).total_seconds()) < 1


async def test_timer_reset_on_changed_bid(db_session, project):
    """Другая ставка — applied_at переставляется на «сейчас»."""
    await _seed(db_session, project.id)
    with patch(f"{_SVC}._resolve_key", AsyncMock(return_value="key")), \
         patch(f"{_SVC}.set_normquery_bid", AsyncMock(return_value=(True, None, 100.0))):
        await set_cluster_bid(db_session, project.id, CID, NM, "юбка", 100, verify=False)
        row = (await _rows(db_session, project.id))[0]
        row.applied_at = utcnow() - timedelta(days=5)
        await db_session.commit()
    with patch(f"{_SVC}._resolve_key", AsyncMock(return_value="key")), \
         patch(f"{_SVC}.set_normquery_bid", AsyncMock(return_value=(True, None, 200.0))):
        await set_cluster_bid(db_session, project.id, CID, NM, "юбка", 200, verify=False)
    await db_session.refresh(row)
    assert float(row.applied_bid) == 200
    assert (utcnow() - row.applied_at).total_seconds() < 60  # таймер свежий


# ─── Сброс к ставке кампании удаляет паспорт ───

async def test_reset_deletes_passport(db_session, project):
    await _seed(db_session, project.id)
    with patch(f"{_SVC}._resolve_key", AsyncMock(return_value="key")), \
         patch(f"{_SVC}.set_normquery_bid", AsyncMock(return_value=(True, None, 100.0))):
        await set_cluster_bid(db_session, project.id, CID, NM, "юбка", 100, verify=False)
    assert len(await _rows(db_session, project.id)) == 1
    with patch(f"{_SVC}._resolve_key", AsyncMock(return_value="key")), \
         patch(f"{_SVC}.set_normquery_bid", AsyncMock(return_value=(True, None, 0.0))):
        await set_cluster_bid(db_session, project.id, CID, NM, "юбка", 0, verify=False)
    assert await _rows(db_session, project.id) == []


# ─── Пачка: паспорт только по успешно применённым фразам ───

async def test_bulk_records_only_successful(db_session, project):
    await _seed(db_session, project.id)
    batch = AsyncMock(return_value={
        "юбка": {"ok": True, "bid": 150.0, "error": None},
        "шорты": {"ok": False, "bid": None, "error": "norm_query disabled"},
    })
    with patch(f"{_SVC}._resolve_key", AsyncMock(return_value="key")), \
         patch(f"{_SVC}.set_normquery_bids_batch", batch):
        res = await set_cluster_bids_bulk(db_session, project.id, CID, [
            {"nm_id": NM, "norm_query": "юбка", "bid": 150, "source": "recommendation", "basis_drr": 5, "basis_cpm": 140},
            {"nm_id": NM, "norm_query": "шорты", "bid": 150, "source": "recommendation"},
        ])
    assert res["applied"] == 1
    rows = await _rows(db_session, project.id)
    assert {r.norm_query for r in rows} == {"юбка"}  # провалившаяся не записана
    assert float(rows[0].applied_bid) == 150
    assert rows[0].source == "recommendation"


# ─── Доклейка паспорта в _enrich (чистая логика) ───

def _state(bid: str, days_ago: float) -> WbAdClusterBid:
    return WbAdClusterBid(
        project_id=1, campaign_id=CID, nm_id=NM, norm_query="q",
        applied_bid=Decimal(bid), applied_at=utcnow() - timedelta(days=days_ago),
        source="recommendation", basis_drr=Decimal("12.6"), basis_cpm=Decimal("931"),
    )


def test_enrich_attaches_passport_when_bid_matches():
    r = _enrich({"norm_query": "q", "views": 500}, set(), {"q": 943.0},
                None, None, {"q": _state("943", 7.1)})
    assert r["bid_set_at"] is not None
    assert r["bid_days"] == 7
    assert r["bid_source"] == "recommendation"
    assert r["bid_basis_drr"] == 12.6
    assert r["bid_basis_cpm"] == 931.0


def test_enrich_no_passport_when_bid_mismatch():
    # сохранено 943, а текущая ставка в WB — 500 (сменили вне приложения) → паспорт неактуален
    r = _enrich({"norm_query": "q", "views": 500}, set(), {"q": 500.0},
                None, None, {"q": _state("943", 3)})
    assert r["bid_set_at"] is None
    assert r["bid_days"] is None
    assert r["bid_source"] is None


def test_enrich_no_passport_when_no_own_bid():
    # фраза без своей ставки (bid=None) — паспорт не доклеиваем
    r = _enrich({"norm_query": "q", "views": 500}, set(), {},
                None, None, {"q": _state("943", 3)})
    assert r["bid_set_at"] is None
