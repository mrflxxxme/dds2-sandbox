# ruff: noqa: RUF001, RUF002, RUF003
"""
Security: блок активного содержимого в upload (stored XSS через SVG/HTML).

Системный паттерн фикса: клиентский Content-Type больше не доминирует —
итоговый MIME берётся по расширению (mimetypes.guess_type), клиентский лишь
фолбэк; расширения svg/html/xml-семейства в EXEC_BLOCKLIST; активные MIME
(image/svg+xml и пр.) в BLOCKED_MIME_EXACT. Download-ручки stored-документов
отдают X-Content-Type-Options: nosniff + Content-Disposition: attachment.
"""

import pytest

from backend.utils.file_validation import resolve_upload_mime, validate_upload_type

_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # валидный PNG magic
_PDF = b"%PDF-1.4\n%test\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


# ─── Unit: хелпер ────────────────────────────────────────────────────────────


def test_resolve_mime_extension_beats_client():
    """Расширение приоритетно: .png с клиентским svg-типом → image/png."""
    assert resolve_upload_mime("pic.png", "image/svg+xml") == "image/png"


def test_resolve_mime_client_fallback_when_no_extension():
    assert resolve_upload_mime("scan", "image/jpeg") == "image/jpeg"
    assert resolve_upload_mime("scan", None) is None


def test_validate_upload_type_blocks_svg_extension():
    """svg-расширение отбивается независимо от клиентского MIME."""
    from fastapi import HTTPException

    for client_mime in ("image/svg+xml", "image/png", None):
        with pytest.raises(HTTPException) as exc:
            validate_upload_type("evil.svg", client_mime)
        assert exc.value.status_code == 415


def test_validate_upload_type_blocks_active_mime_without_extension():
    """Файл без расширения + клиентский активный MIME → 415 (фолбэк-путь)."""
    from fastapi import HTTPException

    for client_mime in ("image/svg+xml", "text/html", "application/xml"):
        with pytest.raises(HTTPException) as exc:
            validate_upload_type("evil", client_mime)
        assert exc.value.status_code == 415


def test_validate_upload_type_blocks_html_and_executables():
    from fastapi import HTTPException

    for name in ("page.html", "page.htm", "doc.xhtml", "feed.xml", "run.exe", "s.ps1", "m.mjs", "x.php"):
        with pytest.raises(HTTPException) as exc:
            validate_upload_type(name, None)
        assert exc.value.status_code == 415


def test_validate_upload_type_allows_legit_types():
    assert validate_upload_type("doc.pdf", "application/pdf") == "application/pdf"
    assert validate_upload_type("pic.jpg", "image/jpeg") == "image/jpeg"
    # клиент врёт text/html при легитимном расширении → расширение побеждает
    assert validate_upload_type("doc.pdf", "text/html") == "application/pdf"


# ─── Фикстуры: MinIO-моки (CI без живого MinIO) ──────────────────────────────


@pytest.fixture
def _mock_storage(monkeypatch):
    """Мокаем get_minio/download_file в обоих сервисных модулях; put сохраняет
    байты в память, download отдаёт их же — хватает для проверки заголовков."""
    store: dict[str, bytes] = {}

    class _FakeMinio:
        async def put_object(self, bucket, object_name, stream, length, content_type=None, **kw):
            store[object_name] = stream.read()

    async def _fake_get_minio():
        return _FakeMinio()

    async def _fake_download(path):
        return store.get(path)

    for mod in ("backend.services.counterparty_service", "backend.services.payment_request_documents"):
        monkeypatch.setattr(f"{mod}.get_minio", _fake_get_minio)
        monkeypatch.setattr(f"{mod}.download_file", _fake_download)
    return store


async def _cp_headers(client, auth_headers):
    resp = await client.post("/api/v1/projects", json={"name": "SVG-fix CP"}, headers=auth_headers)
    headers = {**auth_headers, "X-Project-Id": str(resp.json()["id"])}
    cp = await client.post(
        "/api/v1/counterparties",
        json={"inn": "7712345678", "name": "КА SVG", "primary_type": "OTHER"},
        headers=headers,
    )
    return headers, cp.json()["id"]


async def _pr_headers(client, auth_headers):
    resp = await client.post("/api/v1/projects", json={"name": "SVG-fix PR"}, headers=auth_headers)
    headers = {**auth_headers, "X-Project-Id": str(resp.json()["id"])}
    pr = await client.post(
        "/api/v1/payment-requests",
        json={
            "source": "MANUAL",
            "payee_inn": "7700000001",
            "payee_name": "ООО Тест",
            "payee_account": "40702810900000000001",
            "payee_bik": "044525225",
            "amount": "1000.00",
        },
        headers=headers,
    )
    assert pr.status_code == 201, pr.text
    return headers, pr.json()["id"]


# ─── Counterparty documents ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cp_upload_svg_rejected(client, auth_headers):
    """.svg + image/svg+xml → 415 (раньше проходил allowlist image/)."""
    headers, cp_id = await _cp_headers(client, auth_headers)
    resp = await client.post(
        f"/api/v1/counterparties/{cp_id}/documents",
        files={"file": ("evil.svg", _SVG, "image/svg+xml")},
        data={"doc_type": "CONTRACT"},
        headers=headers,
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_cp_upload_svg_with_png_mime_rejected(client, auth_headers):
    """Клиентский mime image/png при расширении .svg → отбит по расширению."""
    headers, cp_id = await _cp_headers(client, auth_headers)
    resp = await client.post(
        f"/api/v1/counterparties/{cp_id}/documents",
        files={"file": ("evil.svg", _SVG, "image/png")},
        data={"doc_type": "CONTRACT"},
        headers=headers,
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_cp_upload_svg_content_as_png_rejected_by_magic(client, auth_headers):
    """SVG-содержимое под именем .png → 400 по magic bytes."""
    headers, cp_id = await _cp_headers(client, auth_headers)
    resp = await client.post(
        f"/api/v1/counterparties/{cp_id}/documents",
        files={"file": ("evil.png", _SVG, "image/png")},
        data={"doc_type": "CONTRACT"},
        headers=headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_cp_upload_pdf_still_works_and_download_headers(client, auth_headers, _mock_storage):
    """Легитимный PDF грузится; download — attachment + nosniff."""
    headers, cp_id = await _cp_headers(client, auth_headers)
    up = await client.post(
        f"/api/v1/counterparties/{cp_id}/documents",
        files={"file": ("contract.pdf", _PDF, "application/pdf")},
        data={"doc_type": "CONTRACT"},
        headers=headers,
    )
    assert up.status_code == 201, up.text
    assert up.json()["mime_type"] == "application/pdf"

    doc_id = up.json()["id"]
    dl = await client.get(
        f"/api/v1/counterparties/{cp_id}/documents/{doc_id}/download", headers=headers
    )
    assert dl.status_code == 200
    assert dl.headers["x-content-type-options"] == "nosniff"
    assert dl.headers["content-disposition"].startswith("attachment")


@pytest.mark.asyncio
async def test_cp_upload_lying_html_mime_on_pdf_stores_pdf(client, auth_headers, _mock_storage):
    """Клиент врёт text/html при contract.pdf → хранится application/pdf (не text/html)."""
    headers, cp_id = await _cp_headers(client, auth_headers)
    up = await client.post(
        f"/api/v1/counterparties/{cp_id}/documents",
        files={"file": ("contract.pdf", _PDF, "text/html")},
        data={"doc_type": "CONTRACT"},
        headers=headers,
    )
    assert up.status_code == 201, up.text
    assert up.json()["mime_type"] == "application/pdf"


# ─── Payment request documents ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pr_upload_svg_rejected(client, auth_headers):
    headers, pid = await _pr_headers(client, auth_headers)
    resp = await client.post(
        f"/api/v1/payment-requests/{pid}/documents",
        files={"file": ("evil.svg", _SVG, "image/svg+xml")},
        data={"doc_type": "INVOICE"},
        headers=headers,
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_pr_upload_svg_with_png_mime_rejected(client, auth_headers):
    headers, pid = await _pr_headers(client, auth_headers)
    resp = await client.post(
        f"/api/v1/payment-requests/{pid}/documents",
        files={"file": ("evil.svg", _SVG, "image/png")},
        data={"doc_type": "INVOICE"},
        headers=headers,
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_pr_upload_html_rejected(client, auth_headers):
    """Раньше x.html с клиентским application/pdf проходил MIME-allowlist."""
    headers, pid = await _pr_headers(client, auth_headers)
    resp = await client.post(
        f"/api/v1/payment-requests/{pid}/documents",
        files={"file": ("invoice.html", b"<html><script>alert(1)</script></html>", "application/pdf")},
        data={"doc_type": "INVOICE"},
        headers=headers,
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_pr_download_headers(client, auth_headers, _mock_storage):
    headers, pid = await _pr_headers(client, auth_headers)
    up = await client.post(
        f"/api/v1/payment-requests/{pid}/documents",
        files={"file": ("invoice.pdf", _PDF, "application/pdf")},
        data={"doc_type": "INVOICE"},
        headers=headers,
    )
    assert up.status_code == 201, up.text
    doc_id = up.json()["id"]
    dl = await client.get(
        f"/api/v1/payment-requests/{pid}/documents/{doc_id}/download", headers=headers
    )
    assert dl.status_code == 200
    assert dl.headers["x-content-type-options"] == "nosniff"
    assert dl.headers["content-disposition"].startswith("attachment")


# ─── parse-invoice (не хранит, но тот же паттерн валидации) ─────────────────


@pytest.mark.asyncio
async def test_parse_invoice_svg_rejected(client, auth_headers):
    resp0 = await client.post("/api/v1/projects", json={"name": "SVG-fix parse"}, headers=auth_headers)
    headers = {**auth_headers, "X-Project-Id": str(resp0.json()["id"])}
    resp = await client.post(
        "/api/v1/payment-requests/parse-invoice",
        files={"file": ("invoice.svg", _SVG, "image/png")},
        headers=headers,
    )
    assert resp.status_code == 415


# ─── FF billing invoice upload ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ff_invoice_upload_svg_rejected(client, auth_headers):
    resp0 = await client.post("/api/v1/projects", json={"name": "SVG-fix FF"}, headers=auth_headers)
    headers = {**auth_headers, "X-Project-Id": str(resp0.json()["id"])}
    for fname, mime in (("evil.svg", "image/svg+xml"), ("evil.svg", "image/png")):
        resp = await client.post(
            "/api/v1/ff-invoices/upload",
            files={"file": (fname, _SVG, mime)},
            headers=headers,
        )
        assert resp.status_code == 415, f"{fname}/{mime}: {resp.status_code}"
