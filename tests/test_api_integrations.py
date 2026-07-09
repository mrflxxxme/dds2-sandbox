"""
API tests — Integrations endpoints (API keys, sync log).
"""

import pytest

# ─── Helper ──────────────────────────────────────────────────────────────────


async def _project_headers(client, auth_headers) -> dict:
    """Create a test project and return headers with X-Project-Id."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Integrations Test"},
        headers=auth_headers,
    )
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}


# ─── List Keys ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_keys_empty(client, auth_headers):
    """Keys list should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/integrations/keys", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_keys_require_auth(client):
    """Keys endpoint requires authentication."""
    resp = await client.get("/api/v1/integrations/keys")
    assert resp.status_code in (401, 403, 422)


# ─── Add and List Key ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_and_list_key(client, auth_headers):
    """Add an API key (ozon — no external validation) and verify it appears in the list."""
    headers = await _project_headers(client, auth_headers)

    # Add key (use "ozon" to avoid WB external API validation in tests)
    resp = await client.post(
        "/api/v1/integrations/keys",
        json={
            "service": "ozon",
            "api_key": "test_api_key_1234567890_long_enough_to_mask",
            "label": "Test Ozon Key",
        },
        headers=headers,
    )
    assert resp.status_code == 200, f"Add key failed: {resp.text}"
    data = resp.json()
    assert data["service"] == "ozon"

    # List keys
    resp = await client.get("/api/v1/integrations/keys", headers=headers)
    assert resp.status_code == 200
    keys = resp.json()
    assert len(keys) >= 1

    # Verify key is masked
    found = [k for k in keys if k["service"] == "ozon"]
    assert len(found) >= 1
    assert "***" in found[0]["key_preview"]


# ─── Advert-scope Key ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_wb_advert_key(client, auth_headers, monkeypatch):
    """A wb_advert key is validated via the advert (Продвижение) endpoint, not sales."""

    async def _ok(_api_key: str) -> str:
        return "ok"

    # Patch where add_key imports it (function-local import resolves the module attr).
    monkeypatch.setattr(
        "backend.services.funnel.wb_advertising_api.check_advert_scope", _ok
    )

    headers = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/integrations/keys",
        json={
            "service": "wb_advert",
            "api_key": "advert_token_1234567890_long_enough",
            "label": "Реклама",
        },
        headers=headers,
    )
    assert resp.status_code == 200, f"Add advert key failed: {resp.text}"
    assert resp.json()["service"] == "wb_advert"


@pytest.mark.asyncio
async def test_add_wb_advert_key_invalid_token(client, auth_headers, monkeypatch):
    """An advert token without «Продвижение» access (401/403 → no_scope) is rejected with 400."""

    async def _no_scope(_api_key: str) -> str:
        return "no_scope"

    monkeypatch.setattr(
        "backend.services.funnel.wb_advertising_api.check_advert_scope", _no_scope
    )

    headers = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/integrations/keys",
        json={"service": "wb_advert", "api_key": "no_advert_scope_token_123456"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "Продвижение" in resp.text


@pytest.mark.asyncio
async def test_add_wb_advert_key_transient_saves(client, auth_headers, monkeypatch):
    """A rate-limited/transient probe ("unknown") must NOT block a legit key — it saves."""

    async def _unknown(_api_key: str) -> str:
        return "unknown"

    monkeypatch.setattr(
        "backend.services.funnel.wb_advertising_api.check_advert_scope", _unknown
    )

    headers = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/integrations/keys",
        json={"service": "wb_advert", "api_key": "rate_limited_probe_token_123456"},
        headers=headers,
    )
    assert resp.status_code == 200, f"Transient probe should save, got: {resp.text}"
    assert resp.json()["service"] == "wb_advert"


@pytest.mark.asyncio
async def test_add_key_rejects_unknown_service(client, auth_headers):
    """Unsupported service is rejected at the API boundary (422 from Literal)."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/integrations/keys",
        json={"service": "telegram", "api_key": "whatever_key_1234567890"},
        headers=headers,
    )
    assert resp.status_code == 422


# ─── Update Existing Key ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_existing_key(client, auth_headers):
    """Adding keys for different services should work."""
    headers = await _project_headers(client, auth_headers)

    # First add (ozon)
    resp1 = await client.post(
        "/api/v1/integrations/keys",
        json={
            "service": "ozon",
            "api_key": "first_key_value_12345678901234567890",
        },
        headers=headers,
    )
    assert resp1.status_code == 200, f"First add failed: {resp1.text}"

    # List and verify it was added
    resp = await client.get("/api/v1/integrations/keys", headers=headers)
    assert resp.status_code == 200
    keys = resp.json()
    assert len(keys) >= 1


# ─── Delete Key ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_key(client, auth_headers):
    """Add and then delete an API key."""
    headers = await _project_headers(client, auth_headers)

    # Add
    resp = await client.post(
        "/api/v1/integrations/keys",
        json={
            "service": "ozon",
            "api_key": "delete_me_key_12345678901234567890",
        },
        headers=headers,
    )
    assert resp.status_code == 200, f"Add key failed: {resp.text}"
    key_id = resp.json()["id"]

    # Delete
    resp = await client.delete(
        f"/api/v1/integrations/keys/{key_id}",
        headers=headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_delete_nonexistent_key(client, auth_headers):
    """Deleting a nonexistent key should return 404."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.delete(
        "/api/v1/integrations/keys/999999",
        headers=headers,
    )
    assert resp.status_code == 404


# ─── Sync Log ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_log_empty(client, auth_headers):
    """Sync log should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/integrations/sync_log", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_sync_log_with_filter(client, auth_headers):
    """Sync log supports service filter."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/integrations/sync_log?service=wb&limit=5",
        headers=headers,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
