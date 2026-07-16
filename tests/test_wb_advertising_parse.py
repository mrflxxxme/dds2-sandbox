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

    def test_default_bid_search_priority(self):
        """default_bid = макс. search-ставка по nm (копейки→₽), поиск приоритетнее рекомендаций."""
        r = _parse_advert_item({
            "id": 3, "settings": {"payment_type": "cpm"}, "bid_type": "manual",
            "nm_settings": [
                {"nm_id": 10, "bids_kopecks": {"search": 45000, "recommendations": 17000}},
                {"nm_id": 11, "bids_kopecks": {"search": 55000, "recommendations": 20000}},
            ],
        })
        assert r["default_bid"] == 550.0  # max(450, 550) поиска

    def test_default_bid_recommendations_fallback(self):
        """Если поиска нет — берём рекомендации."""
        r = _parse_advert_item({
            "id": 4, "settings": {"payment_type": "cpm"},
            "nm_settings": [{"nm_id": 10, "bids_kopecks": {"recommendations": 13000}}],
        })
        assert r["default_bid"] == 130.0

    def test_default_bid_none_when_absent(self):
        """Нет bids_kopecks → default_bid None (не затираем зеркало при синке)."""
        assert _parse_advert_item(SAMPLE_CPC_MANUAL)["default_bid"] is None


class TestExtractMinBid:
    def test_parses_min_from_wb_error(self):
        from backend.services.funnel.wb_advertising_api import _extract_min_bid
        assert _extract_min_bid('{"detail":"bid value must be no less than 555.00"}') == 555.0

    def test_none_for_unrelated_error(self):
        from backend.services.funnel.wb_advertising_api import _extract_min_bid
        assert _extract_min_bid('{"detail":"campaign not found"}') is None
        assert _extract_min_bid("") is None


async def test_set_bid_auto_bumps_to_min(monkeypatch):
    """Ставка ниже пола WB → бэкенд СРАЗУ повторяет с минимумом и применяет его.

    1-й PATCH (450) → 400 «no less than 555», 2-й PATCH (555) → 200.
    Результат: ok, bid=555, adjusted=True (пользователь ввёл любую — получил минимум).
    """
    import httpx

    from backend.services.funnel.wb_advertising_api import set_campaign_bid

    calls: list[int] = []

    async def fake_patch(self, url, json=None, headers=None):
        kop = json["bids"][0]["nm_bids"][0]["bid_kopecks"]
        calls.append(kop)
        if kop < 55500:
            return httpx.Response(400, text='{"detail":"bid value must be no less than 555.00"}', request=httpx.Request("PATCH", url))
        return httpx.Response(200, request=httpx.Request("PATCH", url))

    monkeypatch.setattr(httpx.AsyncClient, "patch", fake_patch)
    res = await set_campaign_bid("key", 123, [10], 450.0, "search")
    assert res["ok"] is True
    assert res["bid"] == 555.0
    assert res["adjusted"] is True
    assert calls == [45000, 55500]  # ввели 450 → повтор с минимумом 555


async def test_set_bid_min_never_settles(monkeypatch):
    """WB упорно отбивает даже минимум (3 попытки) → ok=False, min_bid отдан."""
    import httpx

    from backend.services.funnel.wb_advertising_api import set_campaign_bid

    async def fake_patch(self, url, json=None, headers=None):
        return httpx.Response(400, text='{"detail":"bid value must be no less than 555.00"}', request=httpx.Request("PATCH", url))

    monkeypatch.setattr(httpx.AsyncClient, "patch", fake_patch)
    res = await set_campaign_bid("key", 123, [10], 450.0, "search")
    assert res["ok"] is False
    assert res["min_bid"] == 555.0


async def test_normquery_bid_503_retries_then_ok(monkeypatch):
    """WB отдаёт 503 (транзиентный сбой), затем 200 success → ставка применяется после повтора."""
    import asyncio

    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_bid

    calls = {"n": 0}

    async def fake_post(self, url, json=None, headers=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="<html>503 Service Unavailable</html>", request=httpx.Request("POST", url))
        return httpx.Response(200, json={"success": [{"advert_id": 1}], "failed": []}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    async def _no_sleep(*_a, **_k):
        return None
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)  # без реальной задержки
    ok, err, applied = await set_normquery_bid("key", 1, 2, "фраза", 555.0)
    assert ok is True and err is None and applied == 555.0
    assert calls["n"] == 2  # первый 503, второй успех


async def test_normquery_bid_503_exhausts_friendly(monkeypatch):
    """WB отдаёт 503 все попытки → человеческое сообщение, без сырого HTML/rpc-текста."""
    import asyncio

    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_bid

    async def fake_post(self, url, json=None, headers=None):
        return httpx.Response(503, text="s.conn.ValidateNmPresetNormQuery: rpc error ... text/html", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    async def _no_sleep(*_a, **_k):
        return None
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    ok, err, applied = await set_normquery_bid("key", 1, 2, "фраза", 555.0)
    assert ok is False and applied is None
    assert "недоступен" in err and "rpc" not in err and "html" not in err.lower()


async def test_normquery_bid_429_retries_and_succeeds(monkeypatch):
    """429 rate-limit (частая причина отказов при массовой правке) → ждём Retry-After и повторяем."""
    import asyncio

    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_bid

    calls = {"n": 0}
    waited: list[float] = []

    async def fake_post(self, url, json=None, headers=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, text="too many requests", request=httpx.Request("POST", url))
        return httpx.Response(200, json={"success": [{"advert_id": 1}], "failed": []}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    async def _no_sleep(s=0, *_a, **_k):
        waited.append(s)

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    ok, err, applied = await set_normquery_bid("key", 1, 2, "фраза", 700.0)
    assert ok is True and err is None and applied == 700.0
    assert calls["n"] == 2          # первый 429, второй успех
    assert waited and waited[0] == 1.0  # уважили Retry-After


async def test_normquery_bid_429_exhausts_friendly(monkeypatch):
    """429 все попытки → человеческое сообщение про частоту запросов, не сырой ответ WB."""
    import asyncio

    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_bid

    async def fake_post(self, url, json=None, headers=None):
        return httpx.Response(429, text="too many requests", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    ok, err, applied = await set_normquery_bid("key", 1, 2, "фраза", 700.0)
    assert ok is False and applied is None
    assert "429" in err and "частоту" in err


async def test_bids_batch_success_one_request(monkeypatch):
    """Пачка ставок уходит ОДНИМ POST (как Mkeeper): все фразы применены, bid = запрошенный."""
    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_bids_batch

    calls = {"n": 0}

    async def fake_post(self, url, json=None, headers=None):
        calls["n"] += 1
        qs = [b["norm_query"] for b in json["bids"]]
        return httpx.Response(200, json={"success": [{"norm_query": q} for q in qs], "failed": []}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    res = await set_normquery_bids_batch("key", 1, [(2, "юбка", 300), (2, "платье", 400)])
    assert calls["n"] == 1  # ОДИН запрос на обе фразы, не два
    assert res["юбка"]["ok"] and res["юбка"]["bid"] == 300.0
    assert res["платье"]["ok"] and res["платье"]["bid"] == 400.0


async def test_bids_batch_partial_and_min_retry(monkeypatch):
    """Пофразный разбор success/failed; отбитые «no less than X» добиваются добатчем с минимумом."""
    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_bids_batch

    calls = {"n": 0}

    async def fake_post(self, url, json=None, headers=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={
                "success": [{"norm_query": "a"}],
                "failed": [
                    {"norm_query": "b", "reason": "bid value must be no less than 500.00"},
                    {"norm_query": "c", "reason": "bid rejected"},
                ],
            }, request=httpx.Request("POST", url))
        qs = [b["norm_query"] for b in json["bids"]]  # добатч — только b с минимумом
        return httpx.Response(200, json={"success": [{"norm_query": q} for q in qs], "failed": []}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    res = await set_normquery_bids_batch("key", 1, [(2, "a", 300), (2, "b", 100), (2, "c", 200)])
    assert calls["n"] == 2  # основной батч + добатч по авто-минимуму
    assert res["a"]["ok"] and res["a"]["bid"] == 300.0
    assert res["b"]["ok"] and res["b"]["bid"] == 500.0  # добит минимумом WB
    assert not res["c"]["ok"] and "rejected" in (res["c"]["error"] or "")


async def test_bids_batch_reset_uses_delete(monkeypatch):
    """bid<=0 в пачке → сброс через DELETE-батч без значения ставки."""
    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_bids_batch

    seen = {}

    async def fake_request(self, method, url, json=None, headers=None):
        seen["method"] = method
        seen["has_bid"] = any("bid" in b for b in json["bids"])
        qs = [b["norm_query"] for b in json["bids"]]
        return httpx.Response(200, json={"success": [{"norm_query": q} for q in qs], "failed": []}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    res = await set_normquery_bids_batch("key", 1, [(2, "юбка", 0)])
    assert seen["method"] == "DELETE" and seen["has_bid"] is False
    assert res["юбка"]["ok"] and res["юбка"]["bid"] is None


async def test_bids_batch_drops_poison_phrase_and_retries(monkeypatch):
    """WB-батч всё-или-ничего: 200 с failed, где reason ВСЕХ строк называет одну «disabled» фразу →
    выкидываем её и повторяем остаток; валидные фразы применяются."""
    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_bids_batch

    calls = {"n": 0}

    async def fake_post(self, url, json=None, headers=None):
        calls["n"] += 1
        qs = [b["norm_query"] for b in json["bids"]]
        if "bad" in qs:  # WB кладёт ВЕСЬ батч в failed, reason у всех — про «bad»
            return httpx.Response(200, json={"success": [], "failed": [
                {"norm_query": q, "reason": "'bad' norm_query disabled for nm '1'"} for q in qs
            ]}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"success": [{"norm_query": q} for q in qs], "failed": []}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    res = await set_normquery_bids_batch("key", 1, [(2, "a", 300), (2, "bad", 300), (2, "c", 400)])
    assert res["a"]["ok"] and res["c"]["ok"]            # валидные применились
    assert not res["bad"]["ok"] and "disabled" in (res["bad"]["error"] or "")
    assert calls["n"] == 2                              # батч с bad (всё failed) + повтор без bad


async def test_bids_batch_429_all_failed_friendly(monkeypatch):
    """429 на весь батч → все фразы отбиты с человеческим сообщением про частоту."""
    import asyncio

    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_bids_batch

    async def fake_post(self, url, json=None, headers=None):
        return httpx.Response(429, text="too many requests", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    res = await set_normquery_bids_batch("key", 1, [(2, "a", 300), (2, "b", 400)])
    assert not res["a"]["ok"] and "429" in (res["a"]["error"] or "")
    assert not res["b"]["ok"]


async def test_set_bid_ok_returns_bid(monkeypatch):
    """200 с первой попытки → ok, применённая ставка ₽, adjusted=False."""
    import httpx

    from backend.services.funnel.wb_advertising_api import set_campaign_bid

    async def fake_patch(self, url, json=None, headers=None):
        return httpx.Response(200, request=httpx.Request("PATCH", url))

    monkeypatch.setattr(httpx.AsyncClient, "patch", fake_patch)
    res = await set_campaign_bid("key", 123, [10], 600.0, "combined")
    assert res["ok"] is True and res["bid"] == 600.0 and res["adjusted"] is False


# ─── set_normquery_bid: контракт пофразовой ставки (bid ₽, success/failed, DELETE-сброс) ──────

async def test_normquery_bid_success_sends_rubles(monkeypatch):
    """200 с {"success":[...],"failed":[]} → ok; тело шлёт поле `bid` в РУБЛЯХ, не bid_kopecks."""
    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_bid

    sent: dict = {}

    async def fake_post(self, url, json=None, headers=None):
        sent.update(json["bids"][0])
        return httpx.Response(200, json={"failed": [], "success": [{"norm_query": "юбка"}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    ok, err, applied = await set_normquery_bid("key", 1, 2, "юбка", 850)
    assert ok is True and err is None and applied == 850.0
    assert sent.get("bid") == 850 and "bid_kopecks" not in sent  # рубли в поле `bid`


async def test_normquery_bid_200_but_failed_is_not_ok(monkeypatch):
    """РЕГРЕСС: WB отдаёт 200, но фраза в failed → ok=False (раньше 200 молча считался успехом)."""
    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_bid

    async def fake_post(self, url, json=None, headers=None):
        return httpx.Response(200, json={"failed": [{"norm_query": "юбка", "reason": "bid rejected"}], "success": []}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    ok, err, applied = await set_normquery_bid("key", 1, 2, "юбка", 850)
    assert ok is False and applied is None and "bid rejected" in (err or "")


async def test_normquery_bid_bumps_to_min(monkeypatch):
    """failed reason «no less than 420.00» → повтор с 420 → success, applied=420."""
    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_bid

    calls: list[int] = []

    async def fake_post(self, url, json=None, headers=None):
        b = json["bids"][0]["bid"]
        calls.append(b)
        if b < 420:
            return httpx.Response(200, json={"failed": [{"reason": "bid value must be no less than 420.00"}], "success": []}, request=httpx.Request("POST", url))
        return httpx.Response(200, json={"failed": [], "success": [{"norm_query": "юбка"}]}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    ok, err, applied = await set_normquery_bid("key", 1, 2, "юбка", 100)
    assert ok is True and applied == 420.0 and calls == [100, 420]


async def test_normquery_bid_reset_uses_delete(monkeypatch):
    """bid_rub<=0 → сброс через HTTP DELETE без значения ставки (bid:0 WB не принимает)."""
    import httpx

    from backend.services.funnel.wb_advertising_api import set_normquery_bid

    seen: dict = {}

    async def fake_request(self, method, url, json=None, headers=None):
        seen["method"] = method
        seen["item"] = json["bids"][0]
        return httpx.Response(200, json={"failed": [], "success": [{"norm_query": "юбка"}]}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    ok, err, applied = await set_normquery_bid("key", 1, 2, "юбка", 0)
    assert ok is True and applied is None
    assert seen["method"] == "DELETE" and "bid" not in seen["item"]


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
