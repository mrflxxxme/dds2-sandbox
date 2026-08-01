"""
Родное автопополнение ВБ (backend/services/funnel/ads_autorefill.py).

Правило живёт в кабинете ВБ и тратит реальные деньги, поэтому проверяем:
маппинг обоих направлений, валидацию до похода в кабинет и то, что протухшая
сессия не выглядит как «автопополнение выключено».
"""

from unittest.mock import AsyncMock

import pytest

from backend.services.funnel import ads_autorefill as ar

PROJECT_ID = 1
CAMPAIGN_ID = 37227684

# Живой ответ кабинета (снят 2026-08-01)
WB_SETTINGS = {
    "is_enable": True,
    "bet_min": 100,
    "bet_min_cents": 10000,
    "bet_sum": 5000,
    "bet_sum_cents": 500000,
    "source": {"unified_account": True},
    "is_daily_limit": True,
    "limit": 1,
    "status": "working",
    "history": [
        {"id": "286368544", "date": "2026-07-31T14:07:34.286520Z", "source": "net", "sum": 5000, "sum_cents": 500000},
    ],
}


def test_to_ui_maps_wb_settings():
    ui = ar.to_ui(WB_SETTINGS)
    assert ui["enabled"] is True
    assert ui["threshold"] == 100.0 and ui["amount"] == 5000.0
    assert ui["daily_limit"] is True and ui["limit"] == 1
    assert ui["unified_account"] is True and ui["status"] == "working"
    assert ui["history"][0] == {"id": "286368544", "date": "2026-07-31T14:07:34.286520Z", "source": "net", "sum": 5000.0}


def test_to_ui_survives_empty_and_broken_payload():
    ui = ar.to_ui({})
    assert ui["enabled"] is False and ui["history"] == []
    # мусор в истории пропускаем, а не роняем окно
    assert ar.to_ui({"history": [None, "x", {"id": 1, "sum": "700"}]})["history"] == [
        {"id": "1", "date": None, "source": None, "sum": 700.0}
    ]


def test_to_wb_sends_only_ruble_fields():
    """*_cents считает сервер ВБ — клиент кабинета их не шлёт, и мы тоже."""
    body = ar.to_wb({"enabled": True, "threshold": 100, "amount": 5000, "daily_limit": True, "limit": 1})
    assert body == {
        "bet_min": 100, "bet_sum": 5000, "is_daily_limit": True, "limit": 1,
        "is_enable": True, "source": {"unified_account": True},
    }


def test_validate_guards_wb_minimum():
    assert ar.validate({"enabled": True, "amount": 500}) and "1000" in ar.validate({"enabled": True, "amount": 500})
    assert ar.validate({"enabled": True, "amount": 1000, "threshold": 100}) is None
    # выключение не требует корректных сумм — иначе автопополнение не отключить
    assert ar.validate({"enabled": False, "amount": 0}) is None


@pytest.mark.asyncio
async def test_get_returns_settings_from_cabinet(monkeypatch):
    client = AsyncMock()
    client.fetch_autorefill.return_value = WB_SETTINGS

    async def fake_client(db, pid):
        return client

    monkeypatch.setattr("backend.services.integrations_service.get_wb_portal_client", fake_client)

    res = await ar.get_autorefill(AsyncMock(), PROJECT_ID, CAMPAIGN_ID)
    assert res["session"] == "ACTIVE" and res["settings"]["amount"] == 5000.0
    client.fetch_autorefill.assert_awaited_once_with(CAMPAIGN_ID)
    client.aclose.assert_awaited()


@pytest.mark.asyncio
async def test_no_session_is_not_disabled_autorefill(monkeypatch):
    """Нет доступа к кабинету → settings=None, а НЕ «выключено»."""
    async def fake_client(db, pid):
        raise ValueError("Сессия WB-кабинета не задана")

    monkeypatch.setattr("backend.services.integrations_service.get_wb_portal_client", fake_client)

    res = await ar.get_autorefill(AsyncMock(), PROJECT_ID, CAMPAIGN_ID)
    assert res == {"session": "NONE", "settings": None}


@pytest.mark.asyncio
async def test_expired_session_marks_key_and_reports(monkeypatch):
    from backend.integrations.wb_portal_client import WbSessionExpired

    client = AsyncMock()
    client.fetch_autorefill.side_effect = WbSessionExpired("401")
    marked: list[int] = []

    async def fake_client(db, pid):
        return client

    async def fake_mark(db, pid):
        marked.append(pid)

    monkeypatch.setattr("backend.services.integrations_service.get_wb_portal_client", fake_client)
    monkeypatch.setattr("backend.services.integrations_service.mark_wb_portal_expired", fake_mark)

    res = await ar.get_autorefill(AsyncMock(), PROJECT_ID, CAMPAIGN_ID)
    assert res["session"] == "EXPIRED" and res["settings"] is None
    assert marked == [PROJECT_ID]


@pytest.mark.asyncio
async def test_save_sends_wb_body_and_returns_confirmed(monkeypatch):
    client = AsyncMock()
    client.save_autorefill.return_value = {**WB_SETTINGS, "bet_sum": 7000}

    async def fake_client(db, pid):
        return client

    monkeypatch.setattr("backend.services.integrations_service.get_wb_portal_client", fake_client)

    res = await ar.save_autorefill(AsyncMock(), PROJECT_ID, CAMPAIGN_ID, {
        "enabled": True, "threshold": 200, "amount": 7000, "daily_limit": False, "limit": 1, "unified_account": True,
    })
    assert res["ok"] is True and res["settings"]["amount"] == 7000.0
    _, body = client.save_autorefill.await_args.args
    assert body == {
        "bet_min": 200, "bet_sum": 7000, "is_daily_limit": False, "limit": 1,
        "is_enable": True, "source": {"unified_account": True},
    }


@pytest.mark.asyncio
async def test_save_below_minimum_never_touches_cabinet(monkeypatch):
    client = AsyncMock()

    async def fake_client(db, pid):
        return client

    monkeypatch.setattr("backend.services.integrations_service.get_wb_portal_client", fake_client)

    res = await ar.save_autorefill(AsyncMock(), PROJECT_ID, CAMPAIGN_ID, {"enabled": True, "amount": 300})
    assert res["ok"] is False and "1000" in res["error"]
    client.save_autorefill.assert_not_awaited()
