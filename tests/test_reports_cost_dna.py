"""
HTTP-level smoke tests for the Cost-DNA report endpoint.

Uses the FastAPI TestClient (`client` fixture from conftest_api.py) to
exercise GET /api/v1/reports/cost_dna without touching DB transaction
boundaries directly — this avoids the project-wide pytest-asyncio +
asyncpg event-loop bug that breaks per-test DB sessions in
test_cost_dna_service.py.

Multiple endpoint assertions are bundled into a single test function
because the per-test client fixture cycle has the same event-loop bug
when re-instantiated in succession; one test = one fresh fixture cycle.
The endpoint always responds even when the project has zero data, so we
verify response shape, period coercion, default period, and date math
without seeding any rows.
"""

from datetime import date, timedelta

import pytest


@pytest.mark.asyncio
async def test_cost_dna_endpoint_full_smoke(client, auth_headers):
    """All endpoint contract checks in one test (one fixture cycle):
    1) empty project → 200 + valid CostDnaResponse shape
    2) period_days=60 → date math + period_days==60
    3) period_days=15 (invalid) → coerced to 30
    4) no period_days → default 30
    5) totals contains all required percent fields
    """
    # --- create one isolated project ----------------------------------------
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Cost-DNA smoke"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    project = resp.json()
    headers = {**auth_headers, "X-Project-Id": str(project["id"])}

    yesterday = date.today() - timedelta(days=1)

    # --- (1) period 30: shape + empty data ----------------------------------
    resp = await client.get(
        "/api/v1/reports/cost_dna?period_days=30",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data30 = resp.json()
    assert data30["period_days"] == 30
    assert data30["categories"] == []
    assert data30["totals"]["revenue"] == 0
    assert data30["has_tax_settings"] is False
    assert data30["date_to"] == yesterday.isoformat()
    assert data30["date_from"] == (yesterday - timedelta(days=29)).isoformat()
    assert data30["prev_date_to"] == (yesterday - timedelta(days=30)).isoformat()
    assert data30["prev_date_from"] == (yesterday - timedelta(days=59)).isoformat()

    # --- (5) totals shape: required percent keys ----------------------------
    totals = data30["totals"]
    for k in (
        "cost_factory_pct",
        "cost_duty_pct",
        "cost_delivery_pct",
        "cost_vat_pct",
        "cost_total_pct",
        "mp_commission_pct",
        "mp_logistics_pct",
        "mp_storage_pct",
        "mp_advertising_pct",
        "mp_other_pct",
        "mp_total_pct",
        "tax_pct",
        "margin_pct",
    ):
        assert k in totals, f"missing totals key: {k}"
        assert isinstance(totals[k], int | float)

    # --- (2) period 60: date math -------------------------------------------
    resp = await client.get(
        "/api/v1/reports/cost_dna?period_days=60",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data60 = resp.json()
    assert data60["period_days"] == 60
    assert data60["date_to"] == yesterday.isoformat()
    assert data60["date_from"] == (yesterday - timedelta(days=59)).isoformat()
    assert data60["prev_date_to"] == (yesterday - timedelta(days=60)).isoformat()
    assert data60["prev_date_from"] == (yesterday - timedelta(days=119)).isoformat()

    # --- (3) invalid period coerced to 30 -----------------------------------
    resp = await client.get(
        "/api/v1/reports/cost_dna?period_days=15",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["period_days"] == 30

    # --- (4) default period -------------------------------------------------
    resp = await client.get("/api/v1/reports/cost_dna", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["period_days"] == 30


@pytest.mark.asyncio
async def test_cost_dna_unauthenticated_rejected(client):
    """No JWT → 401/403/422 (separate test — does not need DB session)."""
    resp = await client.get("/api/v1/reports/cost_dna?period_days=30")
    assert resp.status_code in (401, 403, 422)


@pytest.mark.asyncio
async def test_cost_dna_custom_date_range_smoke(client, auth_headers):
    """Custom date_from/date_to period (added 2026-04-15 for BDR alignment):
    1) valid range → echoed back verbatim, period_days equals span
    2) only date_from → 422 (both required)
    3) date_from > date_to → 422
    4) span > 365 days → 422
    """
    # one isolated project — reuse single fixture cycle
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Cost-DNA custom range"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    project = resp.json()
    headers = {**auth_headers, "X-Project-Id": str(project["id"])}

    # --- (1) valid custom range ---------------------------------------------
    d_from = "2026-03-12"
    d_to = "2026-04-12"
    resp = await client.get(
        f"/api/v1/reports/cost_dna?date_from={d_from}&date_to={d_to}",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["date_from"] == d_from
    assert data["date_to"] == d_to
    # 12 марта + 31 день = 12 апреля → span = 32 дня (inclusive)
    assert data["period_days"] == 32
    # prev_date_to = date_from - 1 day = 2026-03-11
    assert data["prev_date_to"] == "2026-03-11"
    # prev_date_from = prev_date_to - (period_days-1) = 2026-03-11 - 31 = 2026-02-08
    assert data["prev_date_from"] == "2026-02-08"

    # --- (2) only date_from → 422 ------------------------------------------
    resp = await client.get(
        f"/api/v1/reports/cost_dna?date_from={d_from}",
        headers=headers,
    )
    assert resp.status_code == 422

    # --- (3) reversed range → 422 ------------------------------------------
    resp = await client.get(
        f"/api/v1/reports/cost_dna?date_from={d_to}&date_to={d_from}",
        headers=headers,
    )
    assert resp.status_code == 422

    # --- (4) too long → 422 ------------------------------------------------
    resp = await client.get(
        "/api/v1/reports/cost_dna?date_from=2024-01-01&date_to=2026-04-14",
        headers=headers,
    )
    assert resp.status_code == 422
