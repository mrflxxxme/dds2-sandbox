"""
API tests — Cost endpoints (nomenclature, duty rules, cost orders).
"""

import pytest


# ─── Helper ──────────────────────────────────────────────────────────────────

async def _project_headers(client, auth_headers) -> dict:
    """Create a test project and return headers with X-Project-Id."""
    resp = await client.post(
        "/api/v1/projects/", json={"name": "Cost Test"},
        headers=auth_headers,
    )
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}


# ─── Nomenclature ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_nomenclature_empty(client, auth_headers):
    """Nomenclature list should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/cost/nomenclature", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_nomenclature_requires_auth(client):
    """Nomenclature endpoint requires authentication."""
    resp = await client.get("/api/v1/cost/nomenclature")
    assert resp.status_code in (401, 403, 422)


# ─── Duty Rules ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duty_rules_empty(client, auth_headers):
    """Duty rules list should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/cost/duty_rules", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_duty_rules_crud(client, auth_headers):
    """Create, list, and delete a duty rule."""
    headers = await _project_headers(client, auth_headers)

    # Create
    resp = await client.post("/api/v1/cost/duty_rules", json={
        "category": "Ковры",
        "duty_rate": 10.0,
        "basis": "CIF",
    }, headers=headers)
    assert resp.status_code == 200
    rule = resp.json()

    # List
    resp = await client.get("/api/v1/cost/duty_rules", headers=headers)
    assert resp.status_code == 200
    rules = resp.json()
    assert len(rules) >= 1

    # Delete
    resp = await client.delete(
        f"/api/v1/cost/duty_rules/{rule['id']}",
        headers=headers,
    )
    assert resp.status_code == 200


# ─── Cost Orders ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cost_orders_empty(client, auth_headers):
    """Cost orders list should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/cost/orders", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_cost_orders_create(client, auth_headers):
    """Create a cost order."""
    headers = await _project_headers(client, auth_headers)

    resp = await client.post("/api/v1/cost/orders", json={
        "order_no": "COST-001",
        "ship_date": "2024-06-01",
        "supplier": "China Supplier Co",
    }, headers=headers)
    assert resp.status_code == 200
    order = resp.json()
    assert order.get("order_no") == "COST-001" or order.get("ok")


@pytest.mark.asyncio
async def test_cost_order_items_empty(client, auth_headers):
    """Cost order items should return empty for nonexistent order."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/cost/orders/NONEXISTENT/items",
        headers=headers,
    )
    # Should return 200 with empty list or 404
    assert resp.status_code in (200, 404)
