"""
API tests — Auth endpoints.
"""

import uuid

import pytest


@pytest.mark.asyncio
async def test_register_and_login(client):
    """Test user registration and login flow."""
    uid = uuid.uuid4().hex[:8]
    username = f"authtest_{uid}"
    # Register
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "password": "securepass123",
            "email": f"auth_{uid}@test.com",
        },
    )
    assert resp.status_code == 200, f"Register failed: {resp.text}"
    data = resp.json()
    assert "access_token" in data

    # Login
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "username": username,
            "password": "securepass123",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert "access_token" in data


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    """Test login with wrong password returns 401."""
    uid = uuid.uuid4().hex[:8]
    username = f"authtest_wp_{uid}"
    # Register first to ensure user exists
    await client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "password": "correctpass123",
            "email": f"wrongpw_{uid}@test.com",
        },
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={
            "username": username,
            "password": "wrong_password",
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_profile(client, auth_headers):
    """Test profile retrieval with auth token."""
    resp = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "username" in data
    assert "id" in data


@pytest.mark.asyncio
async def test_update_profile(client, auth_headers):
    """Test profile update."""
    resp = await client.put(
        "/api/v1/auth/me",
        json={
            "first_name": "Test",
            "last_name": "User",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200

    # Verify
    resp = await client.get("/api/v1/auth/me", headers=auth_headers)
    data = resp.json()
    assert data["first_name"] == "Test"
    assert data["last_name"] == "User"


@pytest.mark.asyncio
async def test_unauthenticated_profile(client):
    """Test that profile endpoint requires auth."""
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code in (401, 403)
