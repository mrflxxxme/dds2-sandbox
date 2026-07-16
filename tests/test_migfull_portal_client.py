# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты чистых хелперов migfull-портального клиента (без сети; HTTP — MockTransport)."""

import html as _html
import json

import httpx
import pytest

from backend.integrations.migfull_portal_client import (
    _GUID_RE,
    MigfullPortalAuthError,
    MigfullPortalClient,
    MigfullPortalError,
    _find_snapshot,
    _meta_csrf,
    _normalize_host,
    _snapshot_data,
)


# ─── Host allowlist (SSRF-guard) ──────────────────────────────────────────────


def test_normalize_host_allows_migfull_subdomain():
    assert _normalize_host("plusvb.migfull.app") == "https://plusvb.migfull.app"
    assert _normalize_host("https://plusvb.migfull.app/") == "https://plusvb.migfull.app"
    assert _normalize_host("migfull.app") == "https://migfull.app"
    assert _normalize_host("") == "https://plusvb.migfull.app"  # дефолт


@pytest.mark.parametrize(
    "bad",
    [
        "http://plusvb.migfull.app",  # не https
        "https://evil.com",  # чужой домен
        "https://migfull.app.evil.com",  # суффикс-обман
        "https://plusvb.migfull.app:8080",  # порт
        "https://user:pw@plusvb.migfull.app",  # userinfo
        "https://plusvb.migfull.app/app",  # путь
    ],
)
def test_normalize_host_rejects(bad):
    with pytest.raises(MigfullPortalError):
        _normalize_host(bad)


# ─── CSRF / snapshot парсинг ──────────────────────────────────────────────────


def test_meta_csrf():
    page = '<meta name="csrf-token" content="tok123" />'
    assert _meta_csrf(page) == "tok123"
    assert _meta_csrf("<head></head>") is None


def _snapshot_attr(snapshot_json: str) -> str:
    """Завернуть JSON в HTML-атрибут wire:snapshot (как на странице)."""
    return f'<div wire:snapshot="{_html.escape(snapshot_json, quote=True)}" wire:id="x"></div>'


def test_find_snapshot_matches_component_by_name_suffix():
    login = '{"data":[],"memo":{"id":"a","name":"Filament\\\\Auth\\\\Pages\\\\Login"}}'
    other = '{"data":[],"memo":{"id":"b","name":"Filament\\\\Livewire\\\\Notifications"}}'
    page = _snapshot_attr(other) + _snapshot_attr(login)
    raw = _find_snapshot(page, "Login")
    assert raw is not None
    assert '"name":"Filament\\\\Auth\\\\Pages\\\\Login"' in raw
    assert _find_snapshot(page, "DoesNotExist") is None


def test_snapshot_data_unwraps_create_form_array():
    snap = '{"data":{"data":[{"number":"40337302","filter_delivery_type":"direct"},{"s":"arr"}]},"memo":{"name":"CreateShipment"}}'
    data = _snapshot_data(snap)
    assert data["number"] == "40337302"
    assert data["filter_delivery_type"] == "direct"


# ─── GUID из redirect ─────────────────────────────────────────────────────────


def test_guid_regex_extracts_from_redirect():
    m = _GUID_RE.search("https://plusvb.migfull.app/app/shipments/4dfc6d57-acda-432c-be18-de8f4236a323")
    assert m and m.group(1) == "4dfc6d57-acda-432c-be18-de8f4236a323"


# ─── PII-редакция ─────────────────────────────────────────────────────────────


def test_redact_strips_login_and_email():
    client = MigfullPortalClient(login="ohotnikova1010@gmail.com", password="secret")
    out = client._redact("вход ohotnikova1010@gmail.com отклонён, contact other@x.ru")
    assert "ohotnikova1010@gmail.com" not in out
    assert "[login]" in out
    assert "other@x.ru" not in out  # любой email тоже вырезан


def test_host_used_for_base_url():
    client = MigfullPortalClient(login="a@b.ru", password="p", host="plusvb.migfull.app")
    assert client.host == "https://plusvb.migfull.app"


# ─── Переиспользование cookie-сессии ──────────────────────────────────────────


async def test_export_restore_session_roundtrip():
    async with MigfullPortalClient(login="a@b.ru", password="p") as c1:
        c1._http.cookies.set("migfull_session", "abc", domain="plusvb.migfull.app")
        state = c1.export_session()
    assert state["cookies"]["migfull_session"] == "abc"
    async with MigfullPortalClient(login="a@b.ru", password="p") as c2:
        assert c2.restore_session(state) is True
        assert dict(c2._http.cookies)["migfull_session"] == "abc"
        assert c2.restore_session(None) is False
        assert c2.restore_session({"cookies": {}}) is False


def _mock_transport_client(client: MigfullPortalClient, handler) -> None:
    """Подменить внутренний httpx-клиент на MockTransport (тесты без сети)."""
    client._client = httpx.AsyncClient(base_url=client.host, transport=httpx.MockTransport(handler))


async def test_create_and_upload_raise_auth_error_on_login_redirect():
    # Протухшая/отсутствующая сессия: портал редиректит на логин ДО любых мутаций —
    # клиент должен поднять различимую MigfullPortalAuthError (вызвавший перелогинится).
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/app/login"})

    client = MigfullPortalClient(login="a@b.ru", password="p")
    async with client:
        _mock_transport_client(client, handler)
        with pytest.raises(MigfullPortalAuthError):
            await client.create_shipment({})
        with pytest.raises(MigfullPortalAuthError):
            await client.upload_opis("4dfc6d57-acda-432c-be18-de8f4236a323", "f.xlsx", b"x", "application/xlsx")


async def test_authenticate_no_redirect_no_errors_hints_rate_limit():
    # Filament-троттлинг логина (~5/мин): ни redirect, ни errors — только уведомление.
    login_snap = json.dumps({"data": [], "memo": {"id": "a", "name": "Filament\\Auth\\Pages\\Login", "errors": []}})
    page = '<meta name="csrf-token" content="tok" />' + _snapshot_attr(login_snap)

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/app/login":
            return httpx.Response(200, text=page)
        return httpx.Response(
            200,
            json={"components": [{"snapshot": json.dumps({"memo": {"errors": {}}}), "effects": {}}]},
        )

    client = MigfullPortalClient(login="a@b.ru", password="p")
    async with client:
        _mock_transport_client(client, handler)
        with pytest.raises(MigfullPortalError) as exc:
            await client.authenticate()
    assert exc.value.status_code == 429
    assert "частоту входа" in str(exc.value)
