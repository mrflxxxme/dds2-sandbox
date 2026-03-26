"""
API tests — Transactions endpoints (search, unassigned, INBOX).
"""

import pytest

# ─── Helper ──────────────────────────────────────────────────────────────────


async def _project_headers(client, auth_headers) -> dict:
    """Create a test project and return headers with X-Project-Id."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Txn Test"},
        headers=auth_headers,
    )
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}


# ─── Search ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_empty(client, auth_headers):
    """Search with no data should return empty list."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.post("/api/v1/transactions/search", json={}, headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_search_with_filters(client, auth_headers):
    """Search with date filters should work."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/transactions/search",
        json={
            "date_from": "2024-01-01",
            "date_to": "2024-12-31",
            "currency": "RUB",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_search_requires_auth(client):
    """Search endpoint requires authentication."""
    resp = await client.post("/api/v1/transactions/search", json={})
    assert resp.status_code in (401, 403, 422)


# ─── Unassigned (INBOX) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unassigned_empty(client, auth_headers):
    """Unassigned with no data should return empty list."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/transactions/unassigned", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_unassigned_with_limit(client, auth_headers):
    """Unassigned supports limit parameter."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/transactions/unassigned?limit=10",
        headers=headers,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─── Unassigned Grouped ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unassigned_grouped_empty(client, auth_headers):
    """Grouped unassigned with no data should return empty list."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/transactions/unassigned_grouped",
        headers=headers,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0


# ─── Assign Category ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assign_nonexistent_txn(client, auth_headers):
    """Assigning category to nonexistent transaction should fail."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/transactions/assign_category",
        json={
            "txn_id": "NONEXISTENT_TXN_ID",
            "cat_lvl1": "Расходы",
            "cat_lvl2": "Зарплата",
            "scope": "txn",
        },
        headers=headers,
    )
    assert resp.status_code == 404


# ─── Bulk Assign ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assign_bulk_validation(client, auth_headers):
    """Bulk assign requires cp_key and cat_lvl1."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/transactions/assign_category_bulk",
        json={
            "cp_key": "",
            "cat_lvl1": "",
        },
        headers=headers,
    )
    assert resp.status_code == 400


# ─── Assign by IDs ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_assign_by_ids_validation(client, auth_headers):
    """Assign by IDs requires txn_ids and cat_lvl1."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/transactions/assign_category_by_ids",
        json={
            "txn_ids": [],
            "cat_lvl1": "",
        },
        headers=headers,
    )
    assert resp.status_code == 400
