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
