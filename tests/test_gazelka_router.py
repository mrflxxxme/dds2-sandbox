# ruff: noqa: RUF001, RUF002, RUF003
"""HTTP-контракт роутера Газельки — то, что не ловится тестами сервиса.

Оба дефекта, ради которых написан файл, жили ровно на границе router↔service и
были невидимы изнутри сервиса:

  * `match_order` сменил сигнатуру (4-й аргумент стал схемой), а роутер
    продолжал слать `payload.assembly_id` — сервис падал `AttributeError` на
    `int`, мимо `except GazelkaServiceError`, то есть голым 500 на каждом
    «Сопоставить», включая давно живой сборочный путь;
  * `GazelkaLinkKind` не был импортирован в модуль роутера. Под
    `from __future__ import annotations` импорт модуля проходит молча, а
    аннотация остаётся неразрешённым ForwardRef: запрос БЕЗ параметра берёт
    дефолт и работает, а первый же запрос СО значением `?kind=` падает
    PydanticUserError → 500. Фронт всегда шлёт `kind`.

Отсюда правило: контракт роутера проверяем прогоном запроса, а не импортом.
"""

import pytest


async def _project_headers(client, auth_headers) -> dict:
    resp = await client.post(
        "/api/v1/projects", json={"name": "Gazelka router"}, headers=auth_headers
    )
    return {**auth_headers, "X-Project-Id": str(resp.json()["id"])}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["assembly", "transfer"])
async def test_match_candidates_accepts_explicit_kind(client, auth_headers, kind):
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(f"/api/v1/gazelka/match-candidates?kind={kind}", headers=headers)
    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_match_candidates_rejects_unknown_kind(client, auth_headers):
    """Литерал обязан ВАЛИДИРОВАТЬСЯ, а не молча проезжать — иначе неразрешённый
    ForwardRef снова пройдёт незамеченным."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/gazelka/match-candidates?kind=nope", headers=headers)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"assembly_id": 1}, {"transfer_id": 1}])
async def test_match_order_reaches_service_for_both_kinds(client, auth_headers, body):
    """Интеграция в проекте не настроена, поэтому корректный ответ — 400 от
    сервиса. Важно именно ОТСУТСТВИЕ 500: он означал бы, что тело так и не
    доехало до сервиса схемой."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.post("/api/v1/gazelka/order/12345/match", json=body, headers=headers)
    assert resp.status_code == 400, resp.text
    assert "интеграция не настроена" in resp.text


@pytest.mark.asyncio
async def test_match_order_requires_exactly_one_link(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    for body in ({}, {"assembly_id": 1, "transfer_id": 2}):
        resp = await client.post("/api/v1/gazelka/order/12345/match", json=body, headers=headers)
        assert resp.status_code == 422, resp.text
