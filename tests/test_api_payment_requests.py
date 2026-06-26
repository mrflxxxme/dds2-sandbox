# ruff: noqa: RUF002, RUF003
"""
API tests — payment-requests endpoints (создание, документы, submit-гейт, confirm-гейт банка).
"""

import pytest

_ACC = "40702810900000000001"
_BIK = "044525225"
_INN = "7700000001"
_PDF = b"%PDF-1.4\n%test invoice\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


@pytest.fixture(autouse=True)
def _mock_minio(monkeypatch):
    """CI has no reachable MinIO → get_minio() returns None → uploads 503.

    Mock the storage client so successful document uploads work without a live
    MinIO. Rejection tests (mime/size) bail out before get_minio, so the mock is
    harmless there. (test_counterparties_api only exercises rejection paths, which
    is why it stayed green in CI without this.)
    """
    class _FakeMinio:
        async def put_object(self, *args, **kwargs):
            return None

    async def _fake_get_minio():
        return _FakeMinio()

    monkeypatch.setattr("backend.services.payment_request_documents.get_minio", _fake_get_minio)


async def _project_headers(client, auth_headers) -> dict:
    resp = await client.post("/api/v1/projects", json={"name": "PayReq Test"}, headers=auth_headers)
    return {**auth_headers, "X-Project-Id": str(resp.json()["id"])}


async def _create_manual(client, headers, amount="9000.00"):
    return await client.post(
        "/api/v1/payment-requests",
        json={
            "source": "MANUAL",
            "payee_inn": _INN,
            "payee_account": _ACC,
            "payee_bik": _BIK,
            "payee_name": "ООО Перевозчик",
            "amount": amount,
        },
        headers=headers,
    )


@pytest.mark.asyncio
async def test_list_empty(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/payment-requests", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == [] and body["total"] == 0


@pytest.mark.asyncio
async def test_shippable_empty(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/payment-requests/shippable", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_create_manual(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    resp = await _create_manual(client, headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "PENDING_REVIEW"  # сразу «На проверке», без черновика
    assert body["payee_inn"] == _INN
    assert body["number"].startswith("ОПЛ-")


@pytest.mark.asyncio
async def test_create_requires_requisites(client, auth_headers):
    """Без реквизитов получателя заявку не создать (раньше проверял submit)."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/payment-requests",
        json={"source": "MANUAL", "payee_name": "X", "amount": "9000.00"},  # нет ИНН/счёта/БИК
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_submit_idempotent_no_docs(client, auth_headers):
    """Документы опциональны: submit по уже-«На проверке» заявке идемпотентен (200)."""
    headers = await _project_headers(client, auth_headers)
    pr = (await _create_manual(client, headers)).json()
    resp = await client.post(f"/api/v1/payment-requests/{pr['id']}/submit", json={}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "PENDING_REVIEW"


@pytest.mark.asyncio
async def test_full_flow_to_pending_review(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    pr = (await _create_manual(client, headers)).json()
    pid = pr["id"]

    for doc_type in ("INVOICE", "ACT"):
        up = await client.post(
            f"/api/v1/payment-requests/{pid}/documents",
            files={"file": (f"{doc_type.lower()}.pdf", _PDF, "application/pdf")},
            data={"doc_type": doc_type},
            headers=headers,
        )
        assert up.status_code == 201, up.text

    submit = await client.post(f"/api/v1/payment-requests/{pid}/submit", json={}, headers=headers)
    assert submit.status_code == 200, submit.text
    assert submit.json()["status"] == "PENDING_REVIEW"


@pytest.mark.asyncio
async def test_upload_rejects_executable(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    pr = (await _create_manual(client, headers)).json()
    up = await client.post(
        f"/api/v1/payment-requests/{pr['id']}/documents",
        files={"file": ("note.txt", b"hello", "text/plain")},
        data={"doc_type": "INVOICE"},
        headers=headers,
    )
    assert up.status_code == 415


@pytest.mark.asyncio
async def test_upload_accepts_image(client, auth_headers):
    """Счёт/акт можно загрузить фото (JPEG), не только PDF."""
    headers = await _project_headers(client, auth_headers)
    pr = (await _create_manual(client, headers)).json()
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 64  # valid JPEG magic
    up = await client.post(
        f"/api/v1/payment-requests/{pr['id']}/documents",
        files={"file": ("photo.jpg", jpeg, "image/jpeg")},
        data={"doc_type": "INVOICE"},
        headers=headers,
    )
    assert up.status_code == 201, up.text


@pytest.mark.asyncio
async def test_create_draft_requires_confirm(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    pr = (await _create_manual(client, headers)).json()
    resp = await client.post(
        f"/api/v1/payment-requests/{pr['id']}/create-draft", json={"confirm": False}, headers=headers
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_draft_without_faktura_key_fails_cleanly(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    pr = (await _create_manual(client, headers)).json()
    pid = pr["id"]
    for doc_type in ("INVOICE", "ACT"):
        await client.post(
            f"/api/v1/payment-requests/{pid}/documents",
            files={"file": (f"{doc_type.lower()}.pdf", _PDF, "application/pdf")},
            data={"doc_type": doc_type},
            headers=headers,
        )
    await client.post(f"/api/v1/payment-requests/{pid}/submit", json={}, headers=headers)

    # confirm=true but no Faktura integration key in the test project → 400 (clear message, no bank call)
    resp = await client.post(
        f"/api/v1/payment-requests/{pid}/create-draft", json={"confirm": True}, headers=headers
    )
    assert resp.status_code == 400
    msg = resp.json()["error"]["message"].lower()
    assert "ключ" in msg or "faktura" in msg


@pytest.mark.asyncio
async def test_create_draft_blocked_for_non_admin(client, auth_headers, db_session):
    """Реальная запись платёжки в банк (create-draft) — только owner/admin; ниже → 403."""
    from sqlalchemy import text

    headers = await _project_headers(client, auth_headers)
    project_id = int(headers["X-Project-Id"])
    pr = (await _create_manual(client, headers)).json()
    # Понижаем роль вызывающего до editor (ниже admin в иерархии rbac).
    await db_session.execute(
        text("UPDATE project_members SET role = 'editor' WHERE project_id = :p"),
        {"p": project_id},
    )
    await db_session.commit()
    resp = await client.post(
        f"/api/v1/payment-requests/{pr['id']}/create-draft",
        json={"confirm": True},
        headers=headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_drafts_bulk_blocked_for_non_admin(client, auth_headers, db_session):
    """Массовая запись в банк (create-drafts) — тоже только owner/admin."""
    from sqlalchemy import text

    headers = await _project_headers(client, auth_headers)
    project_id = int(headers["X-Project-Id"])
    pr = (await _create_manual(client, headers)).json()
    await db_session.execute(
        text("UPDATE project_members SET role = 'editor' WHERE project_id = :p"),
        {"p": project_id},
    )
    await db_session.commit()
    resp = await client.post(
        "/api/v1/payment-requests/create-drafts",
        json={"ids": [pr["id"]], "confirm": True},
        headers=headers,
    )
    assert resp.status_code == 403
