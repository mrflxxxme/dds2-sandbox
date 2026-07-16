# ruff: noqa: RUF001, RUF002, RUF003
"""Батч normquery/stats: чанкинг по 100, демукс по advertId, сумма кластеров."""

from backend.services.funnel import wb_advertising_api as api


class _FakeResp:
    status_code = 200
    headers: dict = {}

    def __init__(self, items):
        self._items = items

    def json(self):
        return {"items": self._items}

    @property
    def text(self):
        return ""


class _FakeClient:
    """Эхо-клиент: на каждый (advertId,nmId) отдаёт 2 кластера за один день."""

    chunk_sizes: list[int] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.chunk_sizes.append(len(json["items"]))
        items = [
            {
                "advertId": it["advertId"], "nmId": it["nmId"],
                "dailyStats": [
                    {"date": "2026-07-14", "stat": {"views": 1, "clicks": 0, "spend": 1.5, "atbs": 0, "orders": 0, "shks": 0}},
                    {"date": "2026-07-14", "stat": {"views": 2, "clicks": 0, "spend": 2.5, "atbs": 0, "orders": 0, "shks": 0}},
                ],
            }
            for it in json["items"]
        ]
        return _FakeResp(items)


async def test_fetch_search_daily_batch_chunks_and_demuxes(monkeypatch):
    _FakeClient.chunk_sizes = []
    monkeypatch.setattr(api.httpx, "AsyncClient", _FakeClient)

    # 150 уникальных пар (advertId,nmId) → 2 чанка: 100 + 50
    pairs = [(1000 + i, 900000 + i) for i in range(150)]
    agg = await api.fetch_search_daily_batch("key", pairs, "2026-07-13", "2026-07-16")

    assert _FakeClient.chunk_sizes == [100, 50]          # чанкинг по _NORMQUERY_ITEM_CAP
    assert len(agg) == 150                                # каждая пара демукс-нута отдельно
    # ключ — (advert_id, nm_id, date); 2 кластера за день сложились
    assert agg[(1000, 900000, "2026-07-14")] == {
        "views": 3, "clicks": 0, "spend": 4.0, "atbs": 0, "orders": 0, "shks": 0,
    }
    # разные кампании не слиплись
    assert (1149, 900149, "2026-07-14") in agg


async def test_fetch_search_daily_batch_empty():
    assert await api.fetch_search_daily_batch("key", [], "2026-07-13", "2026-07-16") == {}
