# ruff: noqa: RUF001, RUF002, RUF003
"""
Тесты клиента WB Marketplace API (FBS) — без сети, HTTP замокан `httpx.MockTransport`.

Покрыто: чанкование (1000 / 100), пагинация через `next`, ретраи 429/5xx,
ОТСУТСТВИЕ ретрая на 4xx (каждый 4XX стоит 10 запросов бакета), парсинг ответов,
изоляция circuit breaker'а и невытекание токена в текст ошибки.
"""

import json as _js
from datetime import datetime, timezone

import httpx
import pytest

from backend.integrations import wb_fbs_api
from backend.integrations.wb_fbs_api import (
    WB_FBS_PROD_BASE,
    WB_FBS_SANDBOX_BASE,
    WbFbsApiError,
    WbFbsClient,
    WbFbsClientError,
    WbFbsRateLimited,
)

TOKEN = "eyJ0-super-secret-marketplace-token"
PROJECT_ID = 424242


@pytest.fixture(autouse=True)
def _no_sleep_no_breaker(monkeypatch):
    """Ретраи без реальных пауз + чистый брейкер + разрешённая запись.

    `WB_FBS_MODE` здесь ЯВНО ставится в `prod`: дефолт проекта — `safe`, и в нём
    write-методы падают `WbFbsWriteBlocked` до HTTP. Этот файл проверяет
    транспорт (чанки, ретраи, парсинг), а гейт режима — tests/test_wb_fbs_mode.py.
    """
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "prod")
    monkeypatch.setattr(wb_fbs_api, "_sleep", fake_sleep)
    wb_fbs_api._fbs_circuits.reset(PROJECT_ID)
    yield slept
    wb_fbs_api._fbs_circuits.reset(PROJECT_ID)


def _json(request: httpx.Request) -> dict:
    """Тело запроса, отправленное клиентом."""
    return _js.loads(request.content.decode())


def _client(handler, project_id: int = PROJECT_ID) -> WbFbsClient:
    return WbFbsClient(
        api_key=TOKEN,
        project_id=project_id,
        transport=httpx.MockTransport(handler),
    )


class _Recorder:
    """Собирает запросы и отдаёт заранее заготовленные ответы."""

    def __init__(self, responses):
        self.requests: list[httpx.Request] = []
        self._responses = responses

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if callable(self._responses):
            return self._responses(request)
        idx = min(len(self.requests) - 1, len(self._responses) - 1)
        return self._responses[idx]

    @property
    def count(self) -> int:
        return len(self.requests)


# ─── База URL ────────────────────────────────────────────────────────────────


def test_base_url_defaults_to_prod():
    assert WbFbsClient(TOKEN, PROJECT_ID).base_url == WB_FBS_PROD_BASE


def test_base_url_sandbox_switch(monkeypatch):
    monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "sandbox")
    assert WbFbsClient(TOKEN, PROJECT_ID).base_url == WB_FBS_SANDBOX_BASE


def test_base_url_sandbox_legacy_flag(monkeypatch):
    """LEGACY: WB_FBS_SANDBOX=true работает, пока WB_FBS_MODE не задан."""
    monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "")
    monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_SANDBOX", True)
    assert WbFbsClient(TOKEN, PROJECT_ID).base_url == WB_FBS_SANDBOX_BASE


def test_base_url_explicit_wins(monkeypatch):
    monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "sandbox")
    assert WbFbsClient(TOKEN, PROJECT_ID, base_url="https://stub.local/").base_url == "https://stub.local"


# ─── Заголовок авторизации ───────────────────────────────────────────────────


async def test_auth_header_without_bearer_prefix():
    rec = _Recorder([httpx.Response(200, json=[])])
    await _client(rec).list_warehouses()
    assert rec.requests[0].headers["Authorization"] == TOKEN


# ─── Чанкование ──────────────────────────────────────────────────────────────


async def test_put_stocks_chunks_by_1000():
    rec = _Recorder([httpx.Response(204)])
    items = [(1000 + i, i) for i in range(2500)]
    await _client(rec).put_stocks(77, items)

    assert rec.count == 3
    assert [len(_json(r)["stocks"]) for r in rec.requests] == [1000, 1000, 500]
    first = _json(rec.requests[0])["stocks"][0]
    assert first == {"chrtId": 1000, "amount": 0}
    assert rec.requests[0].method == "PUT"
    assert rec.requests[0].url.path == "/api/v3/stocks/77"


async def test_put_stocks_empty_does_not_call_wb():
    rec = _Recorder([httpx.Response(204)])
    await _client(rec).put_stocks(77, [])
    assert rec.count == 0


async def test_get_stocks_chunks_and_merges():
    def handler(request: httpx.Request) -> httpx.Response:
        chrt_ids = _json(request)["chrtIds"]
        return httpx.Response(
            200,
            json={"stocks": [{"chrtId": c, "amount": c % 7} for c in chrt_ids]},
        )

    rec = _Recorder(handler)
    ids = list(range(1, 1201))
    result = await _client(rec).get_stocks(9, ids)

    assert rec.count == 2
    assert len(_json(rec.requests[0])["chrtIds"]) == 1000
    assert len(_json(rec.requests[1])["chrtIds"]) == 200
    assert len(result) == 1200
    assert result[3] == 3 and result[1200] == 1200 % 7


async def test_get_stocks_empty_returns_empty_dict():
    rec = _Recorder([httpx.Response(200, json={"stocks": []})])
    assert await _client(rec).get_stocks(9, []) == {}
    assert rec.count == 0


async def test_delete_stocks_chunks_by_1000():
    rec = _Recorder([httpx.Response(204)])
    await _client(rec).delete_stocks(5, list(range(1050)))
    assert rec.count == 2
    assert rec.requests[0].method == "DELETE"
    assert len(_json(rec.requests[0])["chrtIds"]) == 1000


async def test_orders_status_chunks_by_1000():
    def handler(request: httpx.Request) -> httpx.Response:
        ids = _json(request)["orders"]
        return httpx.Response(200, json={"orders": [{"id": i, "supplierStatus": "new"} for i in ids]})

    rec = _Recorder(handler)
    rows = await _client(rec).get_orders_status(list(range(2001)))
    assert rec.count == 3
    assert [len(_json(r)["orders"]) for r in rec.requests] == [1000, 1000, 1]
    assert len(rows) == 2001


async def test_stickers_chunk_by_100_and_pass_params():
    def handler(request: httpx.Request) -> httpx.Response:
        ids = _json(request)["orders"]
        return httpx.Response(200, json={"stickers": [{"orderId": i, "file": "Zm9v"} for i in ids]})

    rec = _Recorder(handler)
    stickers = await _client(rec).get_stickers(list(range(250)), sticker_type="svg", width=40, height=30)

    assert rec.count == 3
    assert [len(_json(r)["orders"]) for r in rec.requests] == [100, 100, 50]
    assert len(stickers) == 250
    params = rec.requests[0].url.params
    assert params["type"] == "svg" and params["width"] == "40" and params["height"] == "30"


async def test_add_orders_to_supply_chunks_by_100():
    rec = _Recorder([httpx.Response(204)])
    await _client(rec).add_orders_to_supply("WB-GI-123", list(range(150)))
    assert rec.count == 2
    assert [len(_json(r)["orders"]) for r in rec.requests] == [100, 50]
    assert rec.requests[0].url.path == "/api/marketplace/v3/supplies/WB-GI-123/orders"
    assert rec.requests[0].method == "PATCH"


# ─── Пагинация ───────────────────────────────────────────────────────────────


async def test_get_orders_follows_next_cursor():
    pages = {
        0: {"next": 55, "orders": [{"id": 1}, {"id": 2}]},
        55: {"next": 99, "orders": [{"id": 3}]},
        99: {"next": 0, "orders": [{"id": 4}]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = int(request.url.params["next"])
        return httpx.Response(200, json=pages[cursor])

    rec = _Recorder(handler)
    orders = await _client(rec).get_orders(
        date_from=datetime(2026, 7, 1, tzinfo=timezone.utc),
        date_to=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    assert [o["id"] for o in orders] == [1, 2, 3, 4]
    assert rec.count == 3
    first = rec.requests[0].url.params
    assert first["limit"] == "1000"
    assert first["dateFrom"] == str(int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp()))


async def test_get_orders_respects_max_pages():
    rec = _Recorder(lambda r: httpx.Response(200, json={"next": int(r.url.params["next"]) + 1, "orders": [{"id": 1}]}))
    orders = await _client(rec).get_orders(max_pages=3)
    assert rec.count == 3 and len(orders) == 3


async def test_get_orders_omits_absent_dates():
    rec = _Recorder([httpx.Response(200, json={"next": 0, "orders": []})])
    await _client(rec).get_orders()
    params = rec.requests[0].url.params
    assert "dateFrom" not in params and "dateTo" not in params


async def test_list_supplies_paginates():
    pages = {0: {"next": 7, "supplies": [{"id": "WB-GI-1"}]}, 7: {"next": 0, "supplies": [{"id": "WB-GI-2"}]}}
    rec = _Recorder(lambda r: httpx.Response(200, json=pages[int(r.url.params["next"])]))
    supplies = await _client(rec).list_supplies()
    assert [s["id"] for s in supplies] == ["WB-GI-1", "WB-GI-2"]


# ─── Парсинг ответов ─────────────────────────────────────────────────────────


async def test_create_warehouse_returns_id():
    rec = _Recorder([httpx.Response(201, json={"id": 5001})])
    assert await _client(rec).create_warehouse("Основной", 507) == 5001
    assert _json(rec.requests[0]) == {"name": "Основной", "officeId": 507}


async def test_create_supply_returns_string_id():
    rec = _Recorder([httpx.Response(201, json={"id": "WB-GI-987654"})])
    assert await _client(rec).create_supply("Поставка 24.07") == "WB-GI-987654"


async def test_update_warehouse_omits_office_when_none():
    rec = _Recorder([httpx.Response(204)])
    await _client(rec).update_warehouse(12, "Новое имя")
    assert _json(rec.requests[0]) == {"name": "Новое имя"}
    assert rec.requests[0].url.path == "/api/v3/warehouses/12"


async def test_get_new_orders_unwraps_orders_key():
    rec = _Recorder([httpx.Response(200, json={"orders": [{"id": 1, "chrtId": 99}]})])
    orders = await _client(rec).get_new_orders()
    assert orders == [{"id": 1, "chrtId": 99}]


async def test_orders_status_accepts_bare_list():
    """WB отдаёт то `{"orders": [...]}`, то голый массив — принимаем оба варианта."""
    rec = _Recorder([httpx.Response(200, json=[{"id": 1, "supplierStatus": "confirm"}])])
    rows = await _client(rec).get_orders_status([1])
    assert rows[0]["supplierStatus"] == "confirm"


async def test_supply_order_ids_parses_dicts_and_ints():
    rec = _Recorder([httpx.Response(200, json={"orders": [{"id": 11}, {"id": 22}]})])
    assert await _client(rec).get_supply_order_ids("WB-GI-1") == [11, 22]

    rec2 = _Recorder([httpx.Response(200, json=[33, 44])])
    assert await _client(rec2).get_supply_order_ids("WB-GI-1") == [33, 44]


async def test_deliver_supply_accepts_204_without_body():
    rec = _Recorder([httpx.Response(204)])
    await _client(rec).deliver_supply("WB-GI-1")
    assert rec.requests[0].url.path == "/api/v3/supplies/WB-GI-1/deliver"


async def test_ping_true_and_false():
    assert await _client(_Recorder([httpx.Response(200, json={"Status": "OK"})])).ping() is True
    assert await _client(_Recorder([httpx.Response(401, json={"title": "unauthorized"})])).ping() is False


# ─── Ретраи и коды ошибок ────────────────────────────────────────────────────


async def test_400_is_not_retried():
    """4xx стоит 10 запросов бакета — ретраить нельзя ни при каких условиях."""
    rec = _Recorder([httpx.Response(400, json={"code": "IncorrectParameter", "message": "bad chrtId"})])
    with pytest.raises(WbFbsClientError) as exc:
        await _client(rec).put_stocks(1, [(1, 1)])
    assert rec.count == 1
    assert exc.value.status == 400
    assert exc.value.code == "IncorrectParameter"
    assert "bad chrtId" in exc.value.message
    assert isinstance(exc.value, WbFbsApiError)


@pytest.mark.parametrize("status", [401, 403, 404, 406, 409])
async def test_other_4xx_are_not_retried(status):
    rec = _Recorder([httpx.Response(status, json={"code": "StoreIsProcessing", "message": "склад в обработке"})])
    with pytest.raises(WbFbsClientError):
        await _client(rec).delete_warehouse(1)
    assert rec.count == 1


async def test_429_retries_by_ratelimit_retry_header(_no_sleep_no_breaker):
    responses = [
        httpx.Response(429, headers={"X-Ratelimit-Retry": "12"}, json={"code": "TooManyRequests"}),
        httpx.Response(200, json={"stocks": [{"chrtId": 1, "amount": 5}]}),
    ]
    rec = _Recorder(responses)
    result = await _client(rec).get_stocks(1, [1])
    assert rec.count == 2
    assert result == {1: 5}
    assert _no_sleep_no_breaker == [12.0]


async def test_429_gives_up_after_two_retries():
    rec = _Recorder([httpx.Response(429, headers={"X-Ratelimit-Retry": "3"})])
    with pytest.raises(WbFbsRateLimited) as exc:
        await _client(rec).get_new_orders()
    assert rec.count == 3  # 1 попытка + 2 повтора
    assert exc.value.retry_after == 3.0
    assert exc.value.status == 429


async def test_429_without_header_uses_default_pause(_no_sleep_no_breaker):
    rec = _Recorder([httpx.Response(429), httpx.Response(200, json={"orders": []})])
    await _client(rec).get_new_orders()
    assert _no_sleep_no_breaker == [wb_fbs_api.RETRY_AFTER_DEFAULT]


async def test_429_pause_is_capped():
    rec = _Recorder([httpx.Response(429, headers={"X-Ratelimit-Retry": "99999"})])
    with pytest.raises(WbFbsRateLimited) as exc:
        await _client(rec).get_new_orders()
    assert exc.value.retry_after == wb_fbs_api.RETRY_AFTER_MAX


async def test_5xx_retries_then_succeeds(_no_sleep_no_breaker):
    rec = _Recorder([httpx.Response(503), httpx.Response(502), httpx.Response(200, json=[{"id": 1}])])
    warehouses = await _client(rec).list_warehouses()
    assert rec.count == 3
    assert warehouses == [{"id": 1}]
    assert _no_sleep_no_breaker == [1.0, 2.0]  # экспоненциальный backoff


async def test_5xx_gives_up_after_two_retries():
    rec = _Recorder([httpx.Response(500, text="upstream down")])
    with pytest.raises(WbFbsApiError) as exc:
        await _client(rec).list_offices()
    assert rec.count == 3
    assert exc.value.status == 500
    assert not isinstance(exc.value, WbFbsClientError)


async def test_network_error_retried_then_raised():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(WbFbsApiError) as exc:
        await _client(handler).list_warehouses()
    assert calls["n"] == 3
    assert exc.value.status == 0


# ─── Секреты и брейкер ───────────────────────────────────────────────────────


async def test_token_never_leaks_into_error_message():
    """Даже если WB эхом вернёт токен в теле — наружу он не уходит."""
    rec = _Recorder([httpx.Response(400, json={"code": "Bad", "message": f"invalid header {TOKEN}"})])
    with pytest.raises(WbFbsApiError) as exc:
        await _client(rec).cancel_order(1)
    assert TOKEN not in str(exc.value)
    assert TOKEN not in exc.value.message
    assert "***" in exc.value.message


async def test_token_never_leaks_from_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"handshake failed for {TOKEN}", request=request)

    with pytest.raises(WbFbsApiError) as exc:
        await _client(handler).list_offices()
    assert TOKEN not in str(exc.value)
    assert TOKEN not in exc.value.message


def test_circuit_registry_is_isolated_from_legacy_wb_api():
    """Общий с `wb_api` брейкер уронил бы синки остатков/воронки при FBS-поллинге."""
    from backend.integrations import wb_api

    assert wb_fbs_api._fbs_circuits is not wb_api._wb_circuits
    assert wb_fbs_api._fbs_circuits.get(PROJECT_ID).name.startswith("wb_fbs-")


async def test_4xx_does_not_trip_the_circuit_breaker():
    """Доменные 4xx — не отказ сервиса: после 6 подряд клиент обязан работать."""
    rec = _Recorder([httpx.Response(409, json={"code": "SupplyNotClosed"})])
    client = _client(rec)
    for _ in range(6):
        with pytest.raises(WbFbsClientError):
            await client.deliver_supply("WB-GI-1")

    ok = _Recorder([httpx.Response(200, json=[])])
    assert await _client(ok).list_warehouses() == []


async def test_5xx_trips_the_circuit_breaker():
    """А настоящие отказы — трипают: 5 неудачных вызовов и брейкер открыт."""
    rec = _Recorder([httpx.Response(500, text="boom")])
    client = _client(rec)
    for _ in range(5):
        with pytest.raises(WbFbsApiError):
            await client.list_offices()
    before = rec.count

    with pytest.raises(WbFbsApiError) as exc:
        await client.list_offices()
    assert exc.value.code == "CircuitOpen"
    assert rec.count == before  # запрос до сети даже не дошёл
