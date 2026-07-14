"""
Tests for refresh_one_campaign (backend/services/funnel/ad_campaigns_service.py).

Точечный догруз ОДНОЙ кампании из WB (кнопка «Обновить»): деталь + бюджет + свежая
дневная стата за 2 дня пишутся в зеркало синхронно, с событиями на смену бюджета/статуса.
WB-вызовы замоканы — проверяем именно запись в зеркало и контракт ответа.
"""

import contextlib
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from backend.models import WbAdCampaign, WbAdCampaignEvent
from backend.models.integrations import WbAdCampaignDaily
from backend.services.funnel.ad_campaigns_service import refresh_one_campaign

CID = 990100


def _detail(status=9, name="New"):
    return {
        "advertId": CID, "name": name, "type": "cpm", "advert_type": 9,
        "create_time": None, "bid_mode": "unified", "status": status,
        "nm_ids": [111, 222],
    }


def _stats():
    day = {CID: {"views": 40, "clicks": 5, "sum": 20.0}}
    return {
        "_by_campaign": {
            "2026-07-12": {CID: {"views": 100, "clicks": 10, "sum": 50.0}},
            "2026-07-13": day,
        },
        "_by_nm_campaign": {},
    }


async def _run(db, project_id, *, detail, budgets, stats):
    with contextlib.ExitStack() as es:
        es.enter_context(patch("backend.services.funnel.ad_campaigns_service.get_wb_key", AsyncMock(return_value="key")))
        es.enter_context(patch("backend.services.funnel.wb_advertising_api.fetch_campaign_detail", AsyncMock(return_value=detail)))
        es.enter_context(patch("backend.services.funnel.ad_campaigns_service.fetch_campaign_budgets_batch", AsyncMock(return_value=budgets)))
        es.enter_context(patch("backend.services.funnel.wb_advertising_api.fetch_ad_stats", AsyncMock(return_value=stats)))
        es.enter_context(patch("backend.services.funnel.ad_nm_stats.upsert_ad_nm_daily", AsyncMock(return_value=0)))
        return await refresh_one_campaign(db, project_id, CID)


async def test_refresh_updates_mirror_and_daily(db_session, project):
    db_session.add(WbAdCampaign(
        project_id=project.id, campaign_id=CID, name="Old",
        campaign_type="cpm", status=11, budget=Decimal("100"), nm_ids=[111],
    ))
    await db_session.commit()

    res = await _run(db_session, project.id, detail=_detail(status=9, name="New"),
                     budgets={CID: Decimal("500")}, stats=_stats())
    assert res == {"ok": True, "error": None}

    row = (await db_session.execute(
        select(WbAdCampaign).where(
            WbAdCampaign.project_id == project.id, WbAdCampaign.campaign_id == CID)
    )).scalar_one()
    assert row.name == "New" and row.status == 9 and float(row.budget) == 500.0
    assert sorted(row.nm_ids) == [111, 222]  # nm_ids подтянулись из свежей детали

    daily = (await db_session.execute(
        select(WbAdCampaignDaily).where(
            WbAdCampaignDaily.project_id == project.id, WbAdCampaignDaily.campaign_id == CID)
    )).scalars().all()
    assert {str(d.date) for d in daily} == {"2026-07-12", "2026-07-13"}
    d13 = next(d for d in daily if str(d.date) == "2026-07-13")
    assert d13.views == 40 and d13.clicks == 5 and float(d13.spend) == 20.0

    events = (await db_session.execute(
        select(WbAdCampaignEvent).where(
            WbAdCampaignEvent.project_id == project.id, WbAdCampaignEvent.campaign_id == CID)
    )).scalars().all()
    types = {e.event_type for e in events}
    assert "status_change" in types and "budget_change" in types


async def test_refresh_preserves_budget_when_wb_omits_it(db_session, project):
    """WB не отдал бюджет (пустой ответ /budget) → сохраняем прежний, не обнуляем."""
    db_session.add(WbAdCampaign(
        project_id=project.id, campaign_id=CID, name="Old",
        campaign_type="cpm", status=9, budget=Decimal("777"), nm_ids=[111],
    ))
    await db_session.commit()

    res = await _run(db_session, project.id, detail=_detail(status=9),
                     budgets={}, stats=_stats())
    assert res["ok"] is True
    row = (await db_session.execute(
        select(WbAdCampaign).where(
            WbAdCampaign.project_id == project.id, WbAdCampaign.campaign_id == CID)
    )).scalar_one()
    assert float(row.budget) == 777.0  # прежний бюджет сохранён


async def test_refresh_no_api_key(db_session, project):
    with patch("backend.services.funnel.ad_campaigns_service.get_wb_key", AsyncMock(return_value=None)):
        res = await refresh_one_campaign(db_session, project.id, CID)
    assert res == {"ok": False, "error": "no_api_key"}


async def test_refresh_not_found_when_wb_has_no_campaign(db_session, project):
    with patch("backend.services.funnel.ad_campaigns_service.get_wb_key", AsyncMock(return_value="key")), \
         patch("backend.services.funnel.wb_advertising_api.fetch_campaign_detail", AsyncMock(return_value=None)):
        res = await refresh_one_campaign(db_session, project.id, CID)
    assert res == {"ok": False, "error": "not_found"}
