"""
Тесты смены ставки зоны кампании (set_campaign_zone_bid).

Проверяем выбор placement по типу ставки (единая→combined, ручная→зона, CPC→search),
валидацию (bid>0, статус 4/9/11) и проброс nm_ids. WB-вызов (set_campaign_bid) и резолвер
ключа замоканы — реальных денег/сети нет.
"""

import pytest

from backend.models import WbAdCampaign
from backend.services.funnel import ads_manager

CID = 555900


async def _seed(db, project_id, status=9, **kw):
    db.add(WbAdCampaign(project_id=project_id, campaign_id=CID, name="x", status=status,
                        nm_ids=[111, 222], **kw))
    await db.commit()


def _patch(monkeypatch, captured):
    async def fake_set(api_key, cid, nm_ids, bid, placement):
        captured.update(cid=cid, nm_ids=nm_ids, bid=bid, placement=placement)
        return {"ok": True, "error": None, "bid": bid}

    async def fake_key(db, pid):
        return "KEY"

    monkeypatch.setattr("backend.services.funnel.wb_advertising_api.set_campaign_bid", fake_set)
    monkeypatch.setattr(ads_manager, "_get_advert_api_key", fake_key)


async def test_unified_uses_combined(db_session, project, monkeypatch):
    await _seed(db_session, project.id, campaign_type="cpm", bid_mode="unified")
    cap = {}
    _patch(monkeypatch, cap)
    res = await ads_manager.set_campaign_zone_bid(db_session, project.id, CID, "search", 500)
    assert res["ok"] and cap["placement"] == "combined" and cap["nm_ids"] == [111, 222] and cap["bid"] == 500


async def test_manual_uses_zone(db_session, project, monkeypatch):
    await _seed(db_session, project.id, campaign_type="cpm", bid_mode="manual")
    cap = {}
    _patch(monkeypatch, cap)
    await ads_manager.set_campaign_zone_bid(db_session, project.id, CID, "recommendations", 300)
    assert cap["placement"] == "recommendations"


async def test_cpc_uses_search(db_session, project, monkeypatch):
    await _seed(db_session, project.id, campaign_type="cpc", bid_mode="manual")
    cap = {}
    _patch(monkeypatch, cap)
    await ads_manager.set_campaign_zone_bid(db_session, project.id, CID, "recommendations", 7)
    assert cap["placement"] == "search"  # CPC крутится только в поиске


async def test_rejects_zero_bid(db_session, project, monkeypatch):
    await _seed(db_session, project.id, campaign_type="cpm", bid_mode="unified")
    _patch(monkeypatch, {})
    res = await ads_manager.set_campaign_zone_bid(db_session, project.id, CID, "search", 0)
    assert res["ok"] is False


async def test_rejects_completed_status(db_session, project, monkeypatch):
    await _seed(db_session, project.id, campaign_type="cpm", bid_mode="unified", status=7)
    _patch(monkeypatch, {})
    res = await ads_manager.set_campaign_zone_bid(db_session, project.id, CID, "search", 500)
    assert res["ok"] is False and "актив" in res["error"].lower()
