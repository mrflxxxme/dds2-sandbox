"""
Integration tests for /api/v1/counterparties endpoints.
Real FastAPI TestClient + PG. MinIO calls mocked.
"""

import uuid

import pytest

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _inn() -> str:
    return str(int(uuid.uuid4().hex[:9], 16) % 9000000000 + 1000000000)


async def _project_headers(client, auth_headers) -> tuple[dict, int]:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"test_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    project = resp.json()
    headers = {**auth_headers, "X-Project-Id": str(project["id"])}
    return headers, project["id"]


# ─── POST /counterparties ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_counterparty_201(client, auth_headers):
    """POST new counterparty returns 201 with id."""
    headers, _ = await _project_headers(client, auth_headers)
    inn = _inn()

    resp = await client.post(
        "/api/v1/counterparties",
        json={"inn": inn, "name": "ООО Тест КА", "primary_type": "CARRIER"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert "id" in data
    assert data["inn"] == inn


@pytest.mark.asyncio
async def test_create_counterparty_dup_inn_409(client, auth_headers):
    """POST with duplicate INN in same project returns 409."""
    headers, _ = await _project_headers(client, auth_headers)
    inn = _inn()

    await client.post(
        "/api/v1/counterparties",
        json={"inn": inn, "name": "КА Первый", "primary_type": "OTHER"},
        headers=headers,
    )
    resp = await client.post(
        "/api/v1/counterparties",
        json={"inn": inn, "name": "КА Второй", "primary_type": "OTHER"},
        headers=headers,
    )
    assert resp.status_code == 409
    err = resp.json()
    assert "inn_conflict" in str(err)


@pytest.mark.asyncio
async def test_create_counterparty_same_inn_different_project_201(client, auth_headers):
    """Same INN in different project returns 201 (multi-tenancy)."""
    headers1, _ = await _project_headers(client, auth_headers)
    headers2, _ = await _project_headers(client, auth_headers)
    inn = _inn()

    resp1 = await client.post(
        "/api/v1/counterparties",
        json={"inn": inn, "name": "КА Проект1", "primary_type": "OTHER"},
        headers=headers1,
    )
    assert resp1.status_code == 201

    resp2 = await client.post(
        "/api/v1/counterparties",
        json={"inn": inn, "name": "КА Проект2", "primary_type": "OTHER"},
        headers=headers2,
    )
    assert resp2.status_code == 201

    assert resp1.json()["id"] != resp2.json()["id"]


@pytest.mark.asyncio
async def test_create_counterparty_invalid_type_422(client, auth_headers):
    """POST with invalid primary_type returns 422."""
    headers, _ = await _project_headers(client, auth_headers)

    resp = await client.post(
        "/api/v1/counterparties",
        json={"inn": _inn(), "name": "КА", "primary_type": "INVALID_TYPE"},
        headers=headers,
    )
    assert resp.status_code == 422


# ─── GET /counterparties ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_counterparties_filter_type(client, auth_headers):
    """GET list filtered by type=FULFILLMENT returns only that type."""
    headers, _ = await _project_headers(client, auth_headers)

    await client.post(
        "/api/v1/counterparties",
        json={"inn": _inn(), "name": "ФФ КА", "primary_type": "FULFILLMENT"},
        headers=headers,
    )
    await client.post(
        "/api/v1/counterparties",
        json={"inn": _inn(), "name": "Поставщик КА", "primary_type": "SUPPLIER"},
        headers=headers,
    )

    resp = await client.get(
        "/api/v1/counterparties?type=FULFILLMENT",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["primary_type"] == "FULFILLMENT"


@pytest.mark.asyncio
async def test_list_counterparties_search(client, auth_headers):
    """GET list with q= returns matching counterparties."""
    headers, _ = await _project_headers(client, auth_headers)
    uid = uuid.uuid4().hex[:6]

    await client.post(
        "/api/v1/counterparties",
        json={"inn": _inn(), "name": f"Кузнецов {uid}", "primary_type": "CARRIER"},
        headers=headers,
    )

    resp = await client.get(
        f"/api/v1/counterparties?q={uid}",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert any(uid in item["name"] for item in data["items"])


@pytest.mark.asyncio
async def test_list_counterparties_ilike_injection_safe(client, auth_headers):
    """GET list with % in q does not crash (ILIKE injection safe)."""
    headers, _ = await _project_headers(client, auth_headers)

    resp = await client.get(
        "/api/v1/counterparties?q=%25injection",
        headers=headers,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_other_project_empty(client, auth_headers):
    """GET list with different project_id returns empty (tenant isolation)."""
    headers1, _ = await _project_headers(client, auth_headers)
    headers2, _ = await _project_headers(client, auth_headers)

    await client.post(
        "/api/v1/counterparties",
        json={"inn": _inn(), "name": "КА Проект1", "primary_type": "OTHER"},
        headers=headers1,
    )

    resp = await client.get("/api/v1/counterparties", headers=headers2)
    assert resp.status_code == 200
    data = resp.json()
    names = [item["name"] for item in data["items"]]
    assert "КА Проект1" not in names


@pytest.mark.asyncio
async def test_list_counterparties_tolerates_malformed_inn(client, auth_headers, db_session):
    """Regression: a stored row with malformed inn (e.g. '-') must not 500 the list.

    The response model echoes DB data; INN/KPP format constraints belong on the
    write models only. A bank-import row with inn='-' previously crashed the whole
    list with a ValidationError (surfaced as 'Failed to fetch' — the 500 carried
    no CORS header).
    """
    from backend.models.counterparty import Counterparty

    headers, project_id = await _project_headers(client, auth_headers)

    db_session.add(Counterparty(project_id=project_id, inn="-", name="КА битый ИНН", primary_type="OTHER"))
    await db_session.commit()

    resp = await client.get("/api/v1/counterparties", headers=headers)
    assert resp.status_code == 200, resp.text
    assert "-" in [item["inn"] for item in resp.json()["items"]]


# ─── GET /counterparties/{id} ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_counterparty_detail(client, auth_headers):
    """GET detail returns counterparty with stats."""
    headers, _ = await _project_headers(client, auth_headers)

    create_resp = await client.post(
        "/api/v1/counterparties",
        json={"inn": _inn(), "name": "КА Детали", "primary_type": "BANK"},
        headers=headers,
    )
    cp_id = create_resp.json()["id"]

    resp = await client.get(
        f"/api/v1/counterparties/{cp_id}",
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == cp_id
    assert "stats_rub" in data
    assert "stats_cny" in data


# ─── PATCH /counterparties/{id} ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_counterparty(client, auth_headers):
    """PATCH updates counterparty name."""
    headers, _ = await _project_headers(client, auth_headers)

    create_resp = await client.post(
        "/api/v1/counterparties",
        json={"inn": _inn(), "name": "Старое Имя", "primary_type": "OTHER"},
        headers=headers,
    )
    cp_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/counterparties/{cp_id}",
        json={"name": "Новое Имя"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Новое Имя"


# ─── DELETE /counterparties/{id} ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_counterparty(client, auth_headers):
    """DELETE soft-deletes; subsequent GET returns 404."""
    headers, _ = await _project_headers(client, auth_headers)

    create_resp = await client.post(
        "/api/v1/counterparties",
        json={"inn": _inn(), "name": "КА для удаления", "primary_type": "OTHER"},
        headers=headers,
    )
    cp_id = create_resp.json()["id"]

    del_resp = await client.delete(
        f"/api/v1/counterparties/{cp_id}",
        headers=headers,
    )
    assert del_resp.status_code == 204

    get_resp = await client.get(
        f"/api/v1/counterparties/{cp_id}",
        headers=headers,
    )
    assert get_resp.status_code == 404


# ─── Document upload ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_document_mime_not_allowed(client, auth_headers):
    """Upload .exe file returns 415."""
    headers, _ = await _project_headers(client, auth_headers)

    create_resp = await client.post(
        "/api/v1/counterparties",
        json={"inn": _inn(), "name": "КА Документы", "primary_type": "OTHER"},
        headers=headers,
    )
    cp_id = create_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/counterparties/{cp_id}/documents",
        files={"file": ("evil.exe", b"MZ\x90\x00" * 10, "application/octet-stream")},
        data={"doc_type": "CONTRACT"},
        headers=headers,
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_upload_document_too_large(client, auth_headers):
    """Upload >20MB returns 413."""
    headers, _ = await _project_headers(client, auth_headers)

    create_resp = await client.post(
        "/api/v1/counterparties",
        json={"inn": _inn(), "name": "КА Документы", "primary_type": "OTHER"},
        headers=headers,
    )
    cp_id = create_resp.json()["id"]

    # Simulate file > 20MB
    large_data = b"0" * (21 * 1024 * 1024)
    resp = await client.post(
        f"/api/v1/counterparties/{cp_id}/documents",
        files={"file": ("huge.pdf", large_data, "application/pdf")},
        data={"doc_type": "CONTRACT"},
        headers=headers,
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_download_document_missing_404(client, auth_headers):
    """GET download for a non-existent doc returns 404 (routing + project-scoping)."""
    headers, _ = await _project_headers(client, auth_headers)

    create_resp = await client.post(
        "/api/v1/counterparties",
        json={"inn": _inn(), "name": "КА Скачать", "primary_type": "OTHER"},
        headers=headers,
    )
    cp_id = create_resp.json()["id"]

    resp = await client.get(
        f"/api/v1/counterparties/{cp_id}/documents/999999/download",
        headers=headers,
    )
    assert resp.status_code == 404
