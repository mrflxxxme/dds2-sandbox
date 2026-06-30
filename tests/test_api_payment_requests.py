# ruff: noqa: RUF002, RUF003
"""
API tests — payment-requests endpoints (создание, документы, submit-гейт, confirm-гейт банка).
"""

import io
import zipfile

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


# Глобальные категории (project_id NULL) коммитятся и persist в общей БД между тестами →
# сбрасываем кастомные/переименованные перед каждым тестом (см. test_payment_category_service).
@pytest.fixture(autouse=True)
async def _reset_payment_categories(db_session):
    from sqlalchemy import text

    await db_session.execute(text("DELETE FROM payment_categories WHERE is_system = false"))
    await db_session.execute(
        text("UPDATE payment_categories SET label = 'Другое', is_deleted = false WHERE code = 'OTHER'")
    )
    await db_session.commit()
    yield


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


def _make_docx(text: str) -> bytes:
    """Минимальный .docx (zip с word/document.xml) с текстом — для теста распознавания счёта."""
    paras = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in text.split("\n"))
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paras}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", document)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_parse_invoice_endpoint(client, auth_headers):
    """POST /parse-invoice: распознаёт реквизиты из .docx-счёта (роут + валидация + парсер end-to-end)."""
    headers = await _project_headers(client, auth_headers)
    text = (
        "Банк получателя ПАО СБЕРБАНК БИК 044525225\n"
        "к/с 30101810400000000225\n"
        "Получатель ООО Ромашка ИНН 7700000001 КПП 770001001\n"
        "Сч. № 40702810380000100009\n"
        "Всего к оплате 15 250,00\n"
    )
    resp = await client.post(
        "/api/v1/payment-requests/parse-invoice",
        files={"file": ("schet.docx", _make_docx(text),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["payee_inn"] == "7700000001"
    assert body["payee_bik"] == "044525225"
    assert body["payee_account"] == "40702810380000100009"  # прошёл контроль-ключ по БИК
    assert float(body["amount"]) == 15250.0


@pytest.mark.asyncio
async def test_list_empty(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    pid = int(headers["X-Project-Id"])
    resp = await client.get("/api/v1/payment-requests", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    # Свежий проект — своих заявок нет. Общие заявки (без проекта) видны в инбоксе любого
    # проекта (единый список согласования), поэтому проверяем именно отсутствие СВОИХ.
    assert [i for i in body["items"] if i["project_id"] == pid] == []


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
    assert body["category"] == "OTHER"           # дефолт для не-логистики
    assert body["project_name"]                  # деталка несёт имя проекта (не пусто)


@pytest.mark.asyncio
async def test_create_general_no_project(client, auth_headers):
    """project_id=null → «общая» заявка без проекта (своя серия номера, своё назначение)."""
    headers = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/payment-requests",
        json={
            "source": "MANUAL", "project_id": None, "category": "PHOTO_CONTENT",
            "payee_inn": _INN, "payee_account": _ACC, "payee_bik": _BIK,
            "payee_name": "ООО Фотограф", "amount": "12000.00",
        },
        headers=headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["project_id"] is None
    assert body["project_name"] is None          # общая заявка — без имени проекта
    assert body["category"] == "PHOTO_CONTENT"
    assert body["number"].startswith("ОБЩ-")


@pytest.mark.asyncio
async def test_brand_create_filter_and_edit(client, auth_headers):
    """Бренд: сохраняется на заявке (NULL = «Все бренды»), list фильтрует, PATCH правит/сбрасывает."""
    headers = await _project_headers(client, auth_headers)
    # Заявка с брендом.
    r1 = await client.post(
        "/api/v1/payment-requests",
        json={
            "source": "MANUAL", "category": "OTHER", "brand": "Ромашка",
            "payee_inn": _INN, "payee_account": _ACC, "payee_bik": _BIK,
            "payee_name": "ООО Перевозчик", "amount": "5000.00",
        },
        headers=headers,
    )
    assert r1.status_code == 201, r1.text
    branded = r1.json()
    assert branded["brand"] == "Ромашка"
    # Заявка без бренда → «Все бренды» (NULL).
    plain = (await _create_manual(client, headers)).json()
    assert plain["brand"] is None

    # list?brand=Ромашка → только брендированная.
    lst = await client.get("/api/v1/payment-requests", params={"brand": "Ромашка"}, headers=headers)
    assert lst.status_code == 200
    ids = [i["id"] for i in lst.json()["items"]]
    assert branded["id"] in ids and plain["id"] not in ids

    # PATCH: сменить бренд.
    patched = await client.patch(
        f"/api/v1/payment-requests/{branded['id']}", json={"brand": "Лютик"}, headers=headers
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["brand"] == "Лютик"

    # PATCH: сбросить в «Все бренды» (null).
    cleared = await client.patch(
        f"/api/v1/payment-requests/{branded['id']}", json={"brand": None}, headers=headers
    )
    assert cleared.status_code == 200 and cleared.json()["brand"] is None


@pytest.mark.asyncio
async def test_approve_then_mark_paid(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    rid = (await _create_manual(client, headers)).json()["id"]
    r1 = await client.post(f"/api/v1/payment-requests/{rid}/approve", json={"comment": "ок"}, headers=headers)
    assert r1.status_code == 200 and r1.json()["status"] == "APPROVED"
    r2 = await client.post(f"/api/v1/payment-requests/{rid}/mark-paid", json={}, headers=headers)
    assert r2.status_code == 200 and r2.json()["status"] == "PAID"


@pytest.mark.asyncio
async def test_reject(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    rid = (await _create_manual(client, headers)).json()["id"]
    r = await client.post(f"/api/v1/payment-requests/{rid}/reject", json={"comment": "дубль"}, headers=headers)
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_mark_paid_requires_approved(client, auth_headers):
    """PENDING_REVIEW → PAID напрямую запрещён (только через APPROVED) → 409."""
    headers = await _project_headers(client, auth_headers)
    rid = (await _create_manual(client, headers)).json()["id"]
    r = await client.post(f"/api/v1/payment-requests/{rid}/mark-paid", json={}, headers=headers)
    assert r.status_code == 409


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
async def test_upload_act_after_approval(client, auth_headers):
    """Акт приходит позже — его можно догрузить уже к СОГЛАСОВАННОЙ заявке (без правки реквизитов)."""
    headers = await _project_headers(client, auth_headers)
    pid = (await _create_manual(client, headers)).json()["id"]
    inv = await client.post(
        f"/api/v1/payment-requests/{pid}/documents",
        files={"file": ("invoice.pdf", _PDF, "application/pdf")},
        data={"doc_type": "INVOICE"},
        headers=headers,
    )
    assert inv.status_code == 201
    appr = await client.post(f"/api/v1/payment-requests/{pid}/approve", json={}, headers=headers)
    assert appr.status_code == 200 and appr.json()["status"] == "APPROVED"
    # акт догружаем уже к APPROVED-заявке — статус-гейта на загрузку документов нет
    act = await client.post(
        f"/api/v1/payment-requests/{pid}/documents",
        files={"file": ("act.pdf", _PDF, "application/pdf")},
        data={"doc_type": "ACT"},
        headers=headers,
    )
    assert act.status_code == 201, act.text
    detail = (await client.get(f"/api/v1/payment-requests/{pid}", headers=headers)).json()
    assert detail["doc_count"] == 2
    assert {d["doc_type"] for d in detail["documents"]} == {"INVOICE", "ACT"}


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


# ─── Справочник «Назначение оплаты» (категории) ─────────────────────────────────


@pytest.mark.asyncio
async def test_list_payment_categories(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/payment-requests/categories", headers=headers)
    assert resp.status_code == 200
    by_code = {c["code"]: c for c in resp.json()}
    assert "LOGISTICS" in by_code and "OTHER" in by_code
    assert by_code["LOGISTICS"]["is_system"] is True
    assert by_code["LOGISTICS"]["label"]  # есть человекочитаемый лейбл


@pytest.mark.asyncio
async def test_create_and_use_custom_category(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/payment-requests/categories", json={"label": "Маркетинг"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    cat = resp.json()
    assert cat["is_system"] is False and cat["code"]
    # заявка с новым кодом проходит валидацию
    req = await client.post(
        "/api/v1/payment-requests",
        json={"source": "MANUAL", "payee_inn": _INN, "payee_account": _ACC, "payee_bik": _BIK,
              "payee_name": "ООО Дизайн", "amount": "5000.00", "category": cat["code"]},
        headers=headers,
    )
    assert req.status_code == 201, req.text
    assert req.json()["category"] == cat["code"]


@pytest.mark.asyncio
async def test_create_request_unknown_category_rejected(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/payment-requests",
        json={"source": "MANUAL", "payee_inn": _INN, "payee_account": _ACC, "payee_bik": _BIK,
              "payee_name": "X", "amount": "100.00", "category": "ZZZ_UNKNOWN_CODE"},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_delete_system_category_blocked(client, auth_headers):
    headers = await _project_headers(client, auth_headers)
    cats = (await client.get("/api/v1/payment-requests/categories", headers=headers)).json()
    other_id = next(c["id"] for c in cats if c["code"] == "OTHER")
    resp = await client.delete(f"/api/v1/payment-requests/categories/{other_id}", headers=headers)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_category_write_blocked_for_non_admin(client, auth_headers, db_session):
    from sqlalchemy import text

    headers = await _project_headers(client, auth_headers)
    project_id = int(headers["X-Project-Id"])
    await db_session.execute(
        text("UPDATE project_members SET role = 'editor' WHERE project_id = :p"), {"p": project_id}
    )
    await db_session.commit()
    resp = await client.post(
        "/api/v1/payment-requests/categories", json={"label": "Хак"}, headers=headers
    )
    assert resp.status_code == 403
