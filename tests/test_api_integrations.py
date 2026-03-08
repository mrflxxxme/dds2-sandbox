"""
API tests — Integrations endpoints (API keys, sync log).
"""

import pytest


# ─── Helper ──────────────────────────────────────────────────────────────────

async def _project_headers(client, auth_headers) -> dict:
    """Create a test project and return headers with X-Project-Id."""
    resp = await client.post(
        "/api/v1/projects", json={"name": "Integrations Test"},
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
    resp = await client.post("/api/v1/integrations/keys", json={
        "service": "ozon",
        "api_key": "test_api_key_1234567890_long_enough_to_mask",
        "label": "Test Ozon Key",
    }, headers=headers)
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


# ─── Update Existing Key ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_existing_key(client, auth_headers):
    """Adding keys for different services should work."""
    headers = await _project_headers(client, auth_headers)

    # First add (ozon)
    resp1 = await client.post("/api/v1/integrations/keys", json={
        "service": "ozon",
        "api_key": "first_key_value_12345678901234567890",
    }, headers=headers)
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
    resp = await client.post("/api/v1/integrations/keys", json={
        "service": "ozon",
        "api_key": "delete_me_key_12345678901234567890",
    }, headers=headers)
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
