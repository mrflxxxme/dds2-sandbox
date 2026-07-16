"""
Tests for set_cluster_bid guard (backend/services/funnel/cluster_analysis_service.py).

bid==0 — валидный «сброс к ставке кампании» (WB принимает bid_kopecks=0), поэтому
доходит до WB; отбиваем только отрицательные ставки как bad_bid. Регресс на баг, когда
кнопка «Сбросить ставку» (шлёт 0) молча не работала из-за гарда `bid_rub <= 0`.
"""

from unittest.mock import AsyncMock, patch

from backend.models import WbAdCampaign
from backend.services.funnel.cluster_analysis_service import set_cluster_bid

CID = 991200
NM = 555


async def _seed(db, project_id, campaign_type="cpm", bid_mode=None):
    db.add(WbAdCampaign(
        project_id=project_id, campaign_id=CID, name="c",
        campaign_type=campaign_type, bid_mode=bid_mode, status=9, nm_ids=[NM],
    ))
    await db.commit()


async def test_reset_bid_zero_goes_to_wb(db_session, project):
    await _seed(db_session, project.id)
    sent = AsyncMock(return_value=(True, None, 0.0))  # (ok, err, applied_bid)
    with patch("backend.services.funnel.cluster_analysis_service._resolve_key", AsyncMock(return_value="key")), \
         patch("backend.services.funnel.cluster_analysis_service.set_normquery_bid", sent):
        res = await set_cluster_bid(db_session, project.id, CID, NM, "юбка", 0)
    assert res["ok"] is True
    sent.assert_awaited_once()
    assert sent.await_args.args[4] == 0  # bid_rub=0 (сброс) реально ушёл в WB


async def test_negative_bid_rejected_before_wb(db_session, project):
    await _seed(db_session, project.id)
    sent = AsyncMock()
    with patch("backend.services.funnel.cluster_analysis_service.set_normquery_bid", sent):
        res = await set_cluster_bid(db_session, project.id, CID, NM, "юбка", -5)
    assert res == {"ok": False, "error": "bad_bid"}
    sent.assert_not_awaited()  # до WB не дошли


# ─── Гейт по типу кампании: пофразовую ставку WB применяет не везде ───

async def test_unified_cpm_blocked_before_wb(db_session, project):
    """Единая ставка (unified): одна ставка правит всеми фразами — WB override не применит, гейтим."""
    await _seed(db_session, project.id, bid_mode="unified")
    sent = AsyncMock()
    with patch("backend.services.funnel.cluster_analysis_service.set_normquery_bid", sent):
        res = await set_cluster_bid(db_session, project.id, CID, NM, "юбка", 200)
    assert res["ok"] is False
    assert res["unsupported"] is True
    sent.assert_not_awaited()  # до WB не дошли — гейт


async def test_cpc_blocked_before_wb(db_session, project):
    """CPC: пофразовых CPM-ставок нет — гейтим до записи."""
    await _seed(db_session, project.id, campaign_type="cpc")
    sent = AsyncMock()
    with patch("backend.services.funnel.cluster_analysis_service.set_normquery_bid", sent):
        res = await set_cluster_bid(db_session, project.id, CID, NM, "юбка", 200)
    assert res["ok"] is False
    assert res["unsupported"] is True
    sent.assert_not_awaited()


# ─── Проверка после записи: перечитываем ставку из WB (get-bids) ───

async def test_manual_cpm_verifies_applied(db_session, project):
    """Ручной CPM: пишем в WB и подтверждаем перечитыванием — verified=True."""
    await _seed(db_session, project.id, bid_mode="manual")
    sent = AsyncMock(return_value=(True, None, 100.0))
    back = AsyncMock(return_value={(CID, NM): {"юбка": 100.0}})
    with patch("backend.services.funnel.cluster_analysis_service._resolve_key", AsyncMock(return_value="key")), \
         patch("backend.services.funnel.cluster_analysis_service.set_normquery_bid", sent), \
         patch("backend.services.funnel.cluster_analysis_service.get_normquery_bids", back):
        res = await set_cluster_bid(db_session, project.id, CID, NM, "юбка", 100)
    assert res["ok"] is True
    assert res["verified"] is True
    back.assert_awaited_once()


async def test_manual_cpm_verify_false_when_not_stored(db_session, project):
    """WB ответил успехом, но перечитывание не нашло ставку по фразе → verified=False."""
    await _seed(db_session, project.id, bid_mode="manual")
    sent = AsyncMock(return_value=(True, None, 100.0))
    back = AsyncMock(return_value={})  # WB не сохранил пофразовую ставку
    with patch("backend.services.funnel.cluster_analysis_service._resolve_key", AsyncMock(return_value="key")), \
         patch("backend.services.funnel.cluster_analysis_service.set_normquery_bid", sent), \
         patch("backend.services.funnel.cluster_analysis_service.get_normquery_bids", back):
        res = await set_cluster_bid(db_session, project.id, CID, NM, "юбка", 100)
    assert res["ok"] is True
    assert res["verified"] is False


async def test_bulk_skips_verify(db_session, project):
    """verify=False (массовое действие): get-bids не дёргаем — правду перечитают кластерами."""
    await _seed(db_session, project.id, bid_mode="manual")
    sent = AsyncMock(return_value=(True, None, 100.0))
    back = AsyncMock()
    with patch("backend.services.funnel.cluster_analysis_service._resolve_key", AsyncMock(return_value="key")), \
         patch("backend.services.funnel.cluster_analysis_service.set_normquery_bid", sent), \
         patch("backend.services.funnel.cluster_analysis_service.get_normquery_bids", back):
        res = await set_cluster_bid(db_session, project.id, CID, NM, "юбка", 100, verify=False)
    assert res["ok"] is True
    assert res["verified"] is None
    back.assert_not_awaited()
