"""
API tests — Planning endpoints (orders, payments, incomes, cashflow, customs).
"""

import pytest


# ─── Helper ──────────────────────────────────────────────────────────────────

async def _project_headers(client, auth_headers) -> dict:
    """Create a test project and return headers with X-Project-Id."""
    resp = await client.post(
        "/api/v1/projects/", json={"name": "Planning Test"},
        headers=auth_headers,
    )
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}


# ─── Orders ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_orders_list_empty(client, auth_headers):
    """Orders list should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/planning/orders", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_orders_crud(client, auth_headers):
    """Create, list, and delete an order."""
    headers = await _project_headers(client, auth_headers)

    # Create
    resp = await client.post("/api/v1/planning/orders", json={
        "order_no": 100,
        "ship_date": "2024-06-01",
        "order_amount": 50000.0,
        "logistics_cny": 1000.0,
        "customs_rub": 25000.0,
    }, headers=headers)
    assert resp.status_code == 200
    order = resp.json()

    # List
    resp = await client.get("/api/v1/planning/orders", headers=headers)
    assert resp.status_code == 200
    orders = resp.json()
    assert len(orders) >= 1

    # Delete
    resp = await client.delete(f"/api/v1/planning/orders/{order['id']}", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_orders_require_auth(client):
    """Orders endpoint requires authentication."""
    resp = await client.get("/api/v1/planning/orders")
    assert resp.status_code in (401, 403, 422)


# ─── Lead Times ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lead_times_list_empty(client, auth_headers):
    """Lead times list should return empty or default for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/planning/lead_times", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─── Planned Payments ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_payments_list_empty(client, auth_headers):
    """Payments list should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/planning/payments", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_payments_crud(client, auth_headers):
    """Create, list, and delete a planned payment."""
    headers = await _project_headers(client, auth_headers)

    # Create order first (payment needs order_no)
    await client.post("/api/v1/planning/orders", json={
        "order_no": 200,
        "ship_date": "2024-06-01",
    }, headers=headers)

    # Create payment
    resp = await client.post("/api/v1/planning/payments", json={
        "order_no": 200,
        "description": "Test Payment",
        "pay_date": "2024-06-15",
        "amount": 10000.0,
        "currency": "RUB",
        "amount_rub": 10000.0,
        "direction": "ЗАКАЗ",
    }, headers=headers)
    assert resp.status_code == 200
    payment = resp.json()

    # List
    resp = await client.get("/api/v1/planning/payments", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    # Delete
    resp = await client.delete(
        f"/api/v1/planning/payments/{payment['id']}",
        headers=headers,
    )
    assert resp.status_code == 200


# ─── Planned Incomes ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_incomes_list_empty(client, auth_headers):
    """Incomes list should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/planning/incomes", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_incomes_crud(client, auth_headers):
    """Create, list, and delete a planned income."""
    headers = await _project_headers(client, auth_headers)

    # Create
    resp = await client.post("/api/v1/planning/incomes", json={
        "source": "WB",
        "date": "2024-07-01",
        "amount_rub": 100000.0,
    }, headers=headers)
    assert resp.status_code == 200
    income = resp.json()

    # List
    resp = await client.get("/api/v1/planning/incomes", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1

    # Delete
    resp = await client.delete(
        f"/api/v1/planning/incomes/{income['id']}",
        headers=headers,
    )
    assert resp.status_code == 200


# ─── Cashflow Daily ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cashflow_daily(client, auth_headers):
    """Cashflow daily should return a valid series."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/planning/cashflow_daily?days=30&starting_balance=100000",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 31  # 30 days + today


# ─── Customs Topup ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_customs_topup_empty(client, auth_headers):
    """Customs topup list should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/planning/customs_topup", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─── WB Payouts ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wb_payouts_empty(client, auth_headers):
    """WB payouts list should return empty for a new project."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/planning/wb_payouts", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0
