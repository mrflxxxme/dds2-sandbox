"""
API tests — Reports endpoints.
"""

import pytest


# ─── Helper: create project and get headers ──────────────────────────────────

async def _project_headers(client, auth_headers) -> dict:
    """Create a test project and return headers with X-Project-Id."""
    resp = await client.post(
        "/api/v1/projects", json={"name": "Reports Test"},
        headers=auth_headers,
    )
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}


# ─── Balance ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_balance_empty(client, auth_headers):
    """Balance with no data should return empty list."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/reports/balance", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_balance_requires_auth(client):
    """Balance endpoint requires authentication."""
    resp = await client.get("/api/v1/reports/balance")
    assert resp.status_code in (401, 403, 422)


# ─── DDS Month ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dds_month_empty(client, auth_headers):
    """DDS month with no data should return empty list."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/reports/dds_month?year=2024&month=1",
        headers=headers,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_dds_month_requires_params(client, auth_headers):
    """DDS month requires year and month params."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/reports/dds_month", headers=headers)
    assert resp.status_code == 422  # validation error


# ─── FX Control ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fx_control_empty(client, auth_headers):
    """FX control with no data should return empty list."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/reports/fx_control", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─── Customs Control ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_customs_control_empty(client, auth_headers):
    """Customs control with no data should return empty list."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/reports/customs_control", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─── Balance Daily ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_balance_daily_empty(client, auth_headers):
    """Balance daily with no data should return empty list."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/reports/balance_daily?account=TEST&currency=RUB",
        headers=headers,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_balance_daily_requires_params(client, auth_headers):
    """Balance daily requires account and currency."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/reports/balance_daily", headers=headers)
    assert resp.status_code == 422


# ─── Income Daily ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_income_daily_empty(client, auth_headers):
    """Income daily with no data should return empty list."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/reports/income_daily?year=2024&month=1",
        headers=headers,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─── Income by Category ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_income_by_category_empty(client, auth_headers):
    """Income by category with no data should return empty list."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/reports/income_by_category_daily?year=2024&month=1",
        headers=headers,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─── Dashboard Summary ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dashboard_summary_empty(client, auth_headers):
    """Dashboard summary with no data should return valid structure."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/reports/dashboard_summary", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "balance_rub" in data
    assert "month_income" in data
    assert "month_expense" in data
    assert "daily_cashflow" in data
    assert "expense_by_category" in data
    assert "inbox_count" in data
    assert isinstance(data["daily_cashflow"], list)
    assert isinstance(data["expense_by_category"], list)


@pytest.mark.asyncio
async def test_dashboard_summary_with_date_range(client, auth_headers):
    """Dashboard summary with explicit date range."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/reports/dashboard_summary?date_from=2024-01-01&date_to=2024-01-31",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["date_from"] == "2024-01-01"
    assert data["date_to"] == "2024-01-31"


@pytest.mark.asyncio
async def test_dashboard_summary_requires_auth(client):
    """Dashboard summary requires authentication."""
    resp = await client.get("/api/v1/reports/dashboard_summary")
    assert resp.status_code in (401, 403, 422)


@pytest.mark.asyncio
async def test_dashboard_daily_filtered_empty(client, auth_headers):
    """Dashboard daily filtered with no data should return empty list."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/reports/dashboard_daily_filtered?year=2024&month=1",
        headers=headers,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_dashboard_transactions_empty(client, auth_headers):
    """Dashboard transactions with no data should return empty paginated result."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/reports/dashboard_transactions?year=2024&month=1",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert isinstance(data["items"], list)
    assert data["total"] == 0

