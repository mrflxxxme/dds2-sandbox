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
    assert data["token_type"] == "bearer"  # noqa: S105
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


# ─── Project membership: soft-delete edge cases ───────────────────────────────


async def _register_user(client):
    """Register a fresh user; return (auth_headers, user_id)."""
    uid = uuid.uuid4().hex[:8]
    username = f"member_{uid}"
    await client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "password": "memberpass123",
            "email": f"{username}@test.com",
        },
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "memberpass123"},
    )
    data = resp.json()
    assert "access_token" in data, f"Login failed ({resp.status_code}): {data}"
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    return headers, me.json()["id"]


async def _create_project(client, owner_headers, name="Members Test"):
    """Owner creates a project; return its JSON dict."""
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=owner_headers)
    assert resp.status_code == 200, f"Create project failed: {resp.text}"
    return resp.json()


async def _make_invite_token(client, owner_headers, slug, role="editor", pages=None):
    """Owner generates an invite link; return its token."""
    params = {"role": role}
    if pages is not None:
        import json as _json

        params["pages"] = _json.dumps(pages)
    resp = await client.get(
        f"/api/v1/projects/{slug}/invite-link",
        params=params,
        headers=owner_headers,
    )
    assert resp.status_code == 200, f"invite-link failed: {resp.text}"
    return resp.json()["invite_token"]


@pytest.mark.asyncio
async def test_removed_member_can_be_reinvited(client, auth_headers, db_session):
    """Bug #1: a soft-deleted member must be re-invitable.

    remove_member soft-deletes the row (it keeps occupying uq_project_member).
    accept_invite on a fresh invite must restore that row, apply the new
    role/pages, mark the invite accepted, and re-grant access — not 500 on the
    unique constraint nor return a misleading "already a member".
    """
    from sqlalchemy import text

    owner_headers = auth_headers
    project = await _create_project(client, owner_headers)
    slug = project["slug"]

    invitee_headers, invitee_id = await _register_user(client)

    # 1. invitee joins as editor
    token1 = await _make_invite_token(client, owner_headers, slug, role="editor")
    resp = await client.post(f"/api/v1/projects/invite/accept/{token1}", headers=invitee_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["project_slug"] == slug

    # 2. owner removes the invitee → soft-delete
    resp = await client.delete(f"/api/v1/projects/{slug}/members/{invitee_id}", headers=owner_headers)
    assert resp.status_code == 200, resp.text

    # access is gone while soft-deleted
    resp = await client.get(f"/api/v1/projects/{slug}", headers=invitee_headers)
    assert resp.status_code == 403

    # 3. owner re-invites (different role + pages), invitee accepts again
    token2 = await _make_invite_token(client, owner_headers, slug, role="viewer", pages=["reports"])
    resp = await client.post(f"/api/v1/projects/invite/accept/{token2}", headers=invitee_headers)
    assert resp.status_code == 200, f"Re-accept should not 500: {resp.text}"
    body = resp.json()
    assert body["project_slug"] == slug
    assert body["role"] == "viewer"

    # member row restored (single active row), role/pages from the 2nd invite
    rows = (
        await db_session.execute(
            text(
                "SELECT is_deleted, role, pages FROM project_members "
                "WHERE project_id = :pid AND user_id = :uid ORDER BY id"
            ),
            {"pid": project["id"], "uid": invitee_id},
        )
    ).all()
    assert len(rows) == 1, f"expected exactly one row (restored, not duplicated): {rows}"
    is_deleted, role, pages = rows[0]
    assert is_deleted is False
    assert role == "viewer"
    assert pages == '["reports"]'

    # invite is now accepted
    status = (
        await db_session.execute(
            text("SELECT status FROM project_invites WHERE invite_token = :t"),
            {"t": token2},
        )
    ).scalar_one()
    assert status == "accepted"

    # access restored
    resp = await client.get(f"/api/v1/projects/{slug}", headers=invitee_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_accept_invite_when_already_active_is_idempotent(client, auth_headers, db_session):
    """Bug #1 guard: accepting while already an ACTIVE member is idempotent.

    Must return the "already a member" message, not 500, and must not duplicate
    the membership row.
    """
    from sqlalchemy import text

    owner_headers = auth_headers
    project = await _create_project(client, owner_headers, name="Idempotent Test")
    slug = project["slug"]

    invitee_headers, invitee_id = await _register_user(client)

    token1 = await _make_invite_token(client, owner_headers, slug, role="editor")
    resp = await client.post(f"/api/v1/projects/invite/accept/{token1}", headers=invitee_headers)
    assert resp.status_code == 200, resp.text

    # accept a second, separate invite while already active
    token2 = await _make_invite_token(client, owner_headers, slug, role="editor")
    resp = await client.post(f"/api/v1/projects/invite/accept/{token2}", headers=invitee_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"message": "Вы уже участник этого проекта"}

    # exactly one active membership row, no duplication
    count = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM project_members "
                "WHERE project_id = :pid AND user_id = :uid AND is_deleted = false"
            ),
            {"pid": project["id"], "uid": invitee_id},
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_update_role_of_removed_member_returns_404(client, auth_headers):
    """Bug #2: updating the role of a soft-deleted member must 404."""
    owner_headers = auth_headers
    project = await _create_project(client, owner_headers, name="Role 404 Test")
    slug = project["slug"]

    invitee_headers, invitee_id = await _register_user(client)
    token = await _make_invite_token(client, owner_headers, slug, role="editor")
    resp = await client.post(f"/api/v1/projects/invite/accept/{token}", headers=invitee_headers)
    assert resp.status_code == 200, resp.text

    # remove (soft-delete) the member
    resp = await client.delete(f"/api/v1/projects/{slug}/members/{invitee_id}", headers=owner_headers)
    assert resp.status_code == 200, resp.text

    # updating role of the removed member must 404, not silently edit the dead row
    resp = await client.put(
        f"/api/v1/projects/{slug}/members/{invitee_id}/role",
        json={"role": "viewer", "pages": []},
        headers=owner_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_telegram_of_removed_member_returns_404(client, auth_headers):
    """Bug #2: updating telegram_username of a soft-deleted member must 404."""
    owner_headers = auth_headers
    project = await _create_project(client, owner_headers, name="TG 404 Test")
    slug = project["slug"]

    invitee_headers, invitee_id = await _register_user(client)
    token = await _make_invite_token(client, owner_headers, slug, role="editor")
    resp = await client.post(f"/api/v1/projects/invite/accept/{token}", headers=invitee_headers)
    assert resp.status_code == 200, resp.text

    # remove (soft-delete) the member
    resp = await client.delete(f"/api/v1/projects/{slug}/members/{invitee_id}", headers=owner_headers)
    assert resp.status_code == 200, resp.text

    # updating telegram of the removed member must 404
    resp = await client.put(
        f"/api/v1/projects/{slug}/members/{invitee_id}/telegram-username",
        json={"telegram_username": "newhandle"},
        headers=owner_headers,
    )
    assert resp.status_code == 404
