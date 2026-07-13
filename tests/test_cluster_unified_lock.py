"""
Гард «единая ставка CPM»: у кампаний с bid_mode="unified" WB не даёт управлять
кластерами по отдельности (минус-фразы/ставки) → отвечает 400. Ловим режим ДО вызова
WB и возвращаем понятное сообщение, не дёргая кабинет. У "manual" — проходим к WB.

WB-вызовы (get/set-minus, set-bid) и резолвер ключа замоканы — реальной сети нет.
"""

import pytest

from backend.models import WbAdCampaign
from backend.services.funnel import cluster_analysis_service as cas

CID = 777100
NM = 111


async def _seed(db, project_id, bid_mode):
    db.add(WbAdCampaign(project_id=project_id, campaign_id=CID, name="x", status=9,
                        campaign_type="cpm", bid_mode=bid_mode, nm_ids=[NM, 222]))
    await db.commit()


def _patch_wb(monkeypatch, called):
    """Мокаем ключ + все WB-write/read так, чтобы факт вызова фиксировался в `called`."""
    async def fake_key(db, pid):
        return "KEY"

    async def fake_get_minus(api_key, pairs):
        called.add("get_minus")
        return {p: [] for p in pairs}

    async def fake_set_minus(api_key, advert_id, nm_id, norm_queries):
        called.add("set_minus")
        return True, None

    async def fake_set_bid(api_key, advert_id, nm_id, norm_query, bid_rub):
        called.add("set_bid")
        return True, None

    monkeypatch.setattr(cas, "_resolve_key", fake_key)
    monkeypatch.setattr(cas, "get_normquery_minus", fake_get_minus)
    monkeypatch.setattr(cas, "set_normquery_minus", fake_set_minus)
    monkeypatch.setattr(cas, "set_normquery_bid", fake_set_bid)


async def test_toggle_minus_unified_blocked_without_wb_call(db_session, project, monkeypatch):
    await _seed(db_session, project.id, bid_mode="unified")
    called: set[str] = set()
    _patch_wb(monkeypatch, called)
    res = await cas.toggle_cluster_minus(db_session, project.id, CID, NM, "диван раскладной", "add")
    assert res["ok"] is False
    assert "единая" in res["error"].lower()
    assert called == set()  # WB не дёргали — заведомо 400 не отправили


async def test_set_bid_unified_blocked_without_wb_call(db_session, project, monkeypatch):
    await _seed(db_session, project.id, bid_mode="unified")
    called: set[str] = set()
    _patch_wb(monkeypatch, called)
    res = await cas.set_cluster_bid(db_session, project.id, CID, NM, "диван раскладной", 50)
    assert res["ok"] is False
    assert "единая" in res["error"].lower()
    assert called == set()


async def test_toggle_minus_manual_reaches_wb(db_session, project, monkeypatch):
    await _seed(db_session, project.id, bid_mode="manual")
    called: set[str] = set()
    _patch_wb(monkeypatch, called)
    res = await cas.toggle_cluster_minus(db_session, project.id, CID, NM, "диван раскладной", "add")
    assert res["ok"] is True
    assert "set_minus" in called  # ручная ставка — доходим до кабинета


async def test_product_minus_unified_blocked(db_session, project, monkeypatch):
    """Артикульный минус делегирует в campaign-функцию → гард наследуется."""
    await _seed(db_session, project.id, bid_mode="unified")
    called: set[str] = set()
    _patch_wb(monkeypatch, called)
    res = await cas.toggle_product_cluster_minus(db_session, project.id, NM, "диван раскладной", "add")
    assert res["ok"] is False
    assert "единая" in res["error"].lower()
    assert called == set()
