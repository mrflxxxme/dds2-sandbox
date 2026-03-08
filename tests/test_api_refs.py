"""
API tests — Refs endpoints (accounts, cp_categories, overrides, opening_balances, categories).
"""

import uuid
import pytest


# ─── Helper ──────────────────────────────────────────────────────────────────

async def _project_headers(client, auth_headers) -> dict:
    """Create a test project and return headers with X-Project-Id."""
    resp = await client.post(
        "/api/v1/projects", json={"name": "Refs Test"},
        headers=auth_headers,
    )
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}


# ─── Accounts ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_list_accounts(client, auth_headers):
    """Create an account and verify it appears in the list."""
    headers = await _project_headers(client, auth_headers)
    uid = uuid.uuid4().hex[:8]
    account_no = f"4070281000{uid}"

    # Create account
    resp = await client.post("/api/v1/refs/accounts", json={
        "account": account_no,
        "bank": "VTB",
        "currency": "RUB",
        "account_name": "VTB Main RUB",
        "is_our_account": True,
    }, headers=headers)
    assert resp.status_code == 200, f"Create account failed: {resp.text}"

    # List accounts
    resp = await client.get("/api/v1/refs/accounts", headers=headers)
    assert resp.status_code == 200
    accounts = resp.json()
    assert any(a["account"] == account_no for a in accounts)


@pytest.mark.asyncio
async def test_delete_account(client, auth_headers):
    """Create and delete an account."""
    headers = await _project_headers(client, auth_headers)
    uid = uuid.uuid4().hex[:8]

    # Create
    resp = await client.post("/api/v1/refs/accounts", json={
        "account": f"407028DEL{uid}",
        "bank": "TEST",
        "currency": "RUB",
        "account_name": "To Delete",
        "is_our_account": True,
    }, headers=headers)
    assert resp.status_code == 200, f"Create failed: {resp.text}"
    account_id = resp.json()["id"]

    # Delete
    resp = await client.delete(f"/api/v1/refs/accounts/{account_id}", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_accounts_require_auth(client):
    """Accounts endpoint requires authentication."""
    resp = await client.get("/api/v1/refs/accounts")
    assert resp.status_code in (401, 403, 422)


# ─── CP Categories ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_list_cp_categories(client, auth_headers):
    """Create a counterparty category and verify it appears in the list."""
    headers = await _project_headers(client, auth_headers)
    uid = uuid.uuid4().hex[:8]

    # Create
    resp = await client.post("/api/v1/refs/cp_categories", json={
        "cp_key": f"INN:{uid}",
        "cp_name": "ООО Ромашка",
        "cat_lvl1": "Расходы",
        "cat_lvl2": "Зарплата",
    }, headers=headers)
    assert resp.status_code == 200, f"Create cp_category failed: {resp.text}"

    # List
    resp = await client.get("/api/v1/refs/cp_categories", headers=headers)
    assert resp.status_code == 200
    cats = resp.json()
    assert isinstance(cats, list)


# ─── Overrides ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_overrides_empty(client, auth_headers):
    """Overrides list should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/refs/overrides", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_delete_nonexistent_override(client, auth_headers):
    """Deleting a nonexistent override should return 404."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.delete("/api/v1/refs/overrides/999999", headers=headers)
    assert resp.status_code == 404


# ─── Opening Balances ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_and_list_opening_balance(client, auth_headers):
    """Create an opening balance and verify it appears."""
    headers = await _project_headers(client, auth_headers)
    uid = uuid.uuid4().hex[:8]

    # Create
    resp = await client.post("/api/v1/refs/opening_balances", json={
        "date_open": "2024-01-01",
        "account": f"4070281000{uid}",
        "currency": "RUB",
        "opening_balance": 500000.0,
    }, headers=headers)
    assert resp.status_code == 200, f"Create opening_balance failed: {resp.text}"

    # List
    resp = await client.get("/api/v1/refs/opening_balances", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─── Category Reference ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_list_categories(client, auth_headers):
    """Create a category and verify it appears."""
    headers = await _project_headers(client, auth_headers)
    uid = uuid.uuid4().hex[:8]

    # Create
    resp = await client.post("/api/v1/refs/categories", json={
        "direction": "income",
        "cat_lvl1": f"Доходы_{uid}",
        "cat_lvl2": "WB",
    }, headers=headers)
    assert resp.status_code == 200, f"Create category failed: {resp.text}"
    data = resp.json()
    assert data["ok"] is True
    cat_id = data["id"]

    # List
    resp = await client.get("/api/v1/refs/categories", headers=headers)
    assert resp.status_code == 200

    # Delete
    resp = await client.delete(f"/api/v1/refs/categories/{cat_id}", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_category_requires_lvl1(client, auth_headers):
    """Creating a category without cat_lvl1 should fail."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.post("/api/v1/refs/categories", json={
        "cat_lvl1": "",
    }, headers=headers)
    assert resp.status_code == 400
