"""
Tests for backend/services/funnel/wb_advertising_api._parse_advert_item

Проверяет разбор реального ответа WB /api/advert/v2/adverts:
- payment_type ('cpm'/'cpc') → campaign_type
- bid_type ('unified'/'manual') → bid_mode
Образцы — реальные объекты из кабинета (ковры), затёрты не были.
"""

from backend.services.funnel.wb_advertising_api import _parse_advert_item

# Реальный CPC с ручной ставкой
SAMPLE_CPC_MANUAL = {
    "bid_type": "manual",
    "currency": "RUB",
    "id": 35085588,
    "nm_settings": [{"nm_id": 439648636, "subject": {"id": 7161, "name": "ковры"}}],
    "settings": {"name": "120х170_трава_ 439648636_клики", "payment_type": "cpc"},
    "status": 11,
}

# Реальный CPM с единой ставкой
SAMPLE_CPM_UNIFIED = {
    "bid_type": "unified",
    "currency": "RUB",
    "id": 33636585,
    "nm_settings": [{"nm_id": 399583912, "subject": {"id": 7161, "name": "ковры"}}],
    "settings": {"name": "399583912 120х160 бежевый активна ", "payment_type": "cpm"},
    "status": 11,
}


class TestParseAdvertItem:
    def test_cpc_manual(self):
        r = _parse_advert_item(SAMPLE_CPC_MANUAL)
        assert r is not None
        assert r["advertId"] == 35085588
        assert r["type"] == "cpc"
        assert r["bid_mode"] == "manual"
        assert r["nm_ids"] == [439648636]
        assert r["status"] == 11

    def test_cpm_unified(self):
        r = _parse_advert_item(SAMPLE_CPM_UNIFIED)
        assert r is not None
        assert r["type"] == "cpm"
        assert r["bid_mode"] == "unified"
        assert r["nm_ids"] == [399583912]

    def test_missing_bid_type_gives_none_mode(self):
        """Старая кампания без bid_type: campaign_type сохраняется, bid_mode = None."""
        r = _parse_advert_item({"id": 1, "settings": {"payment_type": "cpm", "name": "x"}})
        assert r["type"] == "cpm"
        assert r["bid_mode"] is None

    def test_non_dict_returns_none(self):
        assert _parse_advert_item("not a dict") is None
        assert _parse_advert_item(None) is None

    def test_nm_ids_fallback_to_params(self):
        """Если nm_settings пуст — берём из params[].nms[]."""
        r = _parse_advert_item({"id": 2, "params": [{"nms": [111, 222]}], "settings": {"payment_type": "cpc"}})
        assert r["nm_ids"] == [111, 222]
        assert r["bid_mode"] is None


# ─── set_normquery_minus: контракт тела ───────────────────────────────────────


async def test_set_minus_body_is_toplevel_not_items(monkeypatch):
    """set-minus принимает {advert_id, nm_id, norm_queries} на ВЕРХНЕМ уровне.

    Регресс: обёртка {"items": [...]} (как у get-minus) давала 400
    «invalid advert id» — минусация не работала вовсе (поймано на живом
    токене 2026-07-14; контракт сверен с форумом dev.wildberries.ru).
    """
    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_minus

    captured = {}

    async def fake_post(self, url, json=None, headers=None):
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    ok, err = await set_normquery_minus("key", 36019399, 893149026, ["фраза"])

    assert ok is True and err is None
    assert captured["json"] == {"advert_id": 36019399, "nm_id": 893149026, "norm_queries": ["фраза"]}
    assert "items" not in captured["json"]


async def test_set_minus_unknown_phrase_readable_error(monkeypatch):
    """400 «is not valid for nm» → человеческое сообщение, не «WB вернул 400»."""
    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_minus

    async def fake_post(self, url, json=None, headers=None):
        return httpx.Response(
            400,
            text='{"detail":"norm_query \'x\' is not valid for nm 1","title":"failed to set norm query minus"}',
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    ok, err = await set_normquery_minus("key", 1, 2, ["x"])
    assert ok is False
    assert "кластеры" in err
