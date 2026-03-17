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


# ─── WB BDR ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wb_bdr_empty(client, auth_headers):
    """WB BDR with no finance data should return valid structure."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get(
        "/api/v1/reports/wb_bdr?date_from=2024-01-01&date_to=2024-01-31",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "articles" in data
    assert "summary" in data
    assert isinstance(data["articles"], list)


@pytest.mark.asyncio
async def test_wb_bdr_requires_params(client, auth_headers):
    """WB BDR requires date_from and date_to."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/reports/wb_bdr", headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_wb_bdr_requires_auth(client):
    """WB BDR requires authentication."""
    resp = await client.get(
        "/api/v1/reports/wb_bdr?date_from=2024-01-01&date_to=2024-01-31"
    )
    assert resp.status_code in (401, 403, 422)


# ─── OPIU ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_opiu_empty(client, auth_headers):
    """OPIU with no finance data should return valid structure."""
    import asyncio
    headers = await _project_headers(client, auth_headers)
    # OPIU uses async computation — may return {"computing": true} on first call
    for _ in range(5):
        resp = await client.get(
            "/api/v1/reports/opiu?date_from=2024-01-01&date_to=2024-03-31",
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        if "rows" in data:
            break
        await asyncio.sleep(1)
    assert "rows" in data
    assert "months" in data
    assert isinstance(data["rows"], list)


@pytest.mark.asyncio
async def test_opiu_requires_params(client, auth_headers):
    """OPIU requires date_from and date_to."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/reports/opiu", headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_opiu_requires_auth(client):
    """OPIU requires authentication."""
    resp = await client.get(
        "/api/v1/reports/opiu?date_from=2024-01-01&date_to=2024-03-31"
    )
    assert resp.status_code in (401, 403, 422)


# ─── Cache Prewarm ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_prewarm_project_no_crash():
    """prewarm_project should not crash even for non-existent project."""
    from backend.scheduler import prewarm_project
    # Should complete without raising (fail-safe design)
    await prewarm_project(999999)
