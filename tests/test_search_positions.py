"""
Тесты органических позиций для кластеризатора.

- find_position: разбор постраничной выдачи публичного поиска WB (found на 1-й/2-й стр.,
  не найден, 429→ретраи→None). HTTP замокан httpx.MockTransport (живой WB не дёргаем).
- get_positions_map: последняя + предыдущая позиция по каждой фразе из снимков.
"""

from datetime import timedelta

import httpx
import pytest

from backend.integrations import wb_search_client
from backend.integrations.wb_search_client import find_position
from backend.models.integrations import WbSearchPosition
from backend.services.funnel.search_positions import get_positions_map
from backend.utils.time import utcnow


def _page(ids: list[int], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"data": {"products": [{"id": i} for i in ids]}})


async def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_find_position_first_page():
    async with await _client(lambda req: _page([111, 222, 333, 444])) as c:
        pos, depth = await find_position(c, 333, "q", max_pages=3)
    assert pos == 3 and depth == 4


async def test_find_position_second_page():
    def handler(req):
        page = int(req.url.params.get("page"))
        return _page(list(range(1000, 1100))) if page == 1 else _page([500, 777])

    async with await _client(handler) as c:
        pos, depth = await find_position(c, 777, "q", max_pages=3)
    assert pos == 102  # 100 (стр.1) + 2-е место на стр.2


async def test_find_position_not_found_in_depth():
    # каждая страница полная (100) без нужного id → идём до max_pages, depth = 200
    async with await _client(lambda req: _page(list(range(1000, 1100)))) as c:
        pos, depth = await find_position(c, 999, "q", max_pages=2)
    assert pos is None and depth == 200


async def test_find_position_throttled_returns_none(monkeypatch):
    calls = {"n": 0}

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(wb_search_client.asyncio, "sleep", _no_sleep)  # не ждём бэкофф в тесте

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429, text="Too Many Requests")

    async with await _client(handler) as c:
        pos, depth = await find_position(c, 1, "q", max_pages=1)
    assert pos is None and depth == 0
    assert calls["n"] == wb_search_client._RETRIES  # 429 ретраится, не сдаётся с первого раза


async def test_positions_map_latest_and_prev(db_session, project):
    nm = 555001
    now = utcnow()
    db_session.add_all([
        WbSearchPosition(project_id=project.id, nm_id=nm, phrase="a", position=30, depth=300,
                         captured_at=now - timedelta(hours=2)),
        WbSearchPosition(project_id=project.id, nm_id=nm, phrase="a", position=12, depth=300,
                         captured_at=now),  # свежий → «Позиция»=12, «Была»=30
        WbSearchPosition(project_id=project.id, nm_id=nm, phrase="b", position=None, depth=500,
                         captured_at=now),  # не найден в топ-500
    ])
    await db_session.commit()
    m = await get_positions_map(db_session, project.id, nm)
    assert m["a"]["position"] == 12 and m["a"]["prev"] == 30
    assert m["b"]["position"] is None and m["b"]["depth"] == 500 and m["b"]["prev"] is None


async def test_positions_map_project_isolation(db_session, project, other_project):
    nm = 555002
    db_session.add(WbSearchPosition(project_id=project.id, nm_id=nm, phrase="x", position=5,
                                    depth=100, captured_at=utcnow()))
    await db_session.commit()
    assert await get_positions_map(db_session, other_project.id, nm) == {}


async def test_collect_one_writes_and_tracks_prev(db_session, project, monkeypatch):
    from backend.services.funnel import search_positions as sp

    async def fake7(client, nm, phrase, max_pages=5, dest=0):
        return (7, 100)

    monkeypatch.setattr(sp, "find_position", fake7)
    res = await sp.collect_one(db_session, project.id, 777001, "фраза")
    assert res["position"] == 7 and res["depth"] == 100 and res["prev"] is None and res["throttled"] is False

    async def fake3(client, nm, phrase, max_pages=5, dest=0):
        return (3, 100)

    monkeypatch.setattr(sp, "find_position", fake3)  # второй сбор → «Была»=7
    res2 = await sp.collect_one(db_session, project.id, 777001, "фраза")
    assert res2["position"] == 3 and res2["prev"] == 7


async def test_collect_one_throttled_flag(db_session, project, monkeypatch):
    from backend.services.funnel import search_positions as sp

    async def fake_throttled(client, nm, phrase, max_pages=5, dest=0):
        return (None, 0)  # depth 0 = страницу получить не удалось (429)

    monkeypatch.setattr(sp, "find_position", fake_throttled)
    res = await sp.collect_one(db_session, project.id, 777002, "q")
    assert res["position"] is None and res["throttled"] is True
