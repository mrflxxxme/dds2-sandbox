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


async def _seed(db, project_id):
    db.add(WbAdCampaign(
        project_id=project_id, campaign_id=CID, name="c",
        campaign_type="cpm", status=9, nm_ids=[NM],
    ))
    await db.commit()


async def test_reset_bid_zero_goes_to_wb(db_session, project):
    await _seed(db_session, project.id)
    sent = AsyncMock(return_value=(True, None))
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
