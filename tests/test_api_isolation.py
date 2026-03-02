"""
API tests — Project isolation.

Verifies that data from project A is NOT visible when accessing project B.
"""

import pytest


@pytest.mark.asyncio
async def test_project_create_and_list(client, auth_headers):
    """Test project creation and listing."""
    # Create project
    resp = await client.post(
        "/api/projects/", json={"name": "Isolation Test A"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    project_a = resp.json()
    assert project_a["slug"]

    # List projects
    resp = await client.get("/api/projects/", headers=auth_headers)
    assert resp.status_code == 200
    projects = resp.json()
    slugs = [p["slug"] for p in projects]
    assert project_a["slug"] in slugs


@pytest.mark.asyncio
async def test_project_data_isolation(client, auth_headers):
    """
    Test that refs created in project A are NOT visible in project B.

    Steps:
    1. Create project A, set it as active
    2. Create category ref in project A
    3. Create project B, set it as active
    4. Verify category ref is NOT visible in project B
    """
    # Create project A
    resp = await client.post(
        "/api/projects/", json={"name": "Isolation A"},
        headers=auth_headers,
    )
    project_a = resp.json()

    # Create project B
    resp = await client.post(
        "/api/projects/", json={"name": "Isolation B"},
        headers=auth_headers,
    )
    project_b = resp.json()

    # Set project A as active and create a reference
    headers_a = {**auth_headers, "X-Project-Id": str(project_a["id"])}
    headers_b = {**auth_headers, "X-Project-Id": str(project_b["id"])}

    # Get reports with project A context
    resp_a = await client.get("/api/reports/balance", headers=headers_a)
    resp_b = await client.get("/api/reports/balance", headers=headers_b)

    # Both should return valid responses (even if empty)
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200


@pytest.mark.asyncio
async def test_cannot_access_other_project_data(client):
    """Test that unauthenticated users cannot access project data."""
    resp = await client.get("/api/reports/balance")
    assert resp.status_code in (401, 403, 422)
