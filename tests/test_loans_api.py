"""
Integration tests for /api/v1/loans endpoints.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _inn() -> str:
    return str(int(uuid.uuid4().hex[:9], 16) % 9000000000 + 1000000000)


async def _project_headers(client, auth_headers) -> tuple[dict, int]:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"loan_api_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}, project["id"]


async def _create_counterparty(client, headers) -> int:
    resp = await client.post(
        "/api/v1/counterparties",
        json={"inn": _inn(), "name": "КА для займа", "primary_type": "AFFILIATED"},
        headers=headers,
    )
    return resp.json()["id"]


def _loan_payload(cp_id: int, direction: str = "OUTGOING") -> dict:
    return {
        "counterparty_id": cp_id,
        "direction": direction,
        "principal": "1000000.00",
        "currency": "RUB",
        "rate": "0.1850",
        "contract_number": f"ЗМ-{uuid.uuid4().hex[:6]}",
        "contract_date": "2026-01-01",
        "start_date": "2026-01-10",
        "maturity_date": "2027-01-10",
    }


# ─── POST /loans ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_loan_201(client, auth_headers):
    """POST /loans returns 201 with loan data."""
    headers, _ = await _project_headers(client, auth_headers)
    cp_id = await _create_counterparty(client, headers)

    resp = await client.post(
        "/api/v1/loans",
        json=_loan_payload(cp_id, direction="OUTGOING"),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "ACTIVE"
    assert data["direction"] == "OUTGOING"


@pytest.mark.asyncio
async def test_create_loan_principal_zero_ok_negative_422(client, auth_headers):
    """
    principal=0 разрешён, отрицательный — нет.

    У револьверных займов (кредитная линия, займ учредителя) тело задают движения —
    выборки и возвраты, — а `principal` символический. Прежний запрет на ноль
    заставлял заводить фиктивный рубль, лишь бы пройти валидацию.
    """
    headers, _ = await _project_headers(client, auth_headers)
    cp_id = await _create_counterparty(client, headers)

    payload = _loan_payload(cp_id)
    payload["principal"] = "0"
    resp = await client.post("/api/v1/loans", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    assert Decimal(resp.json()["principal"]) == Decimal("0")

    payload["principal"] = "-1"
    resp = await client.post("/api/v1/loans", json=payload, headers=headers)
    assert resp.status_code == 422


# ─── GET /loans ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_loans(client, auth_headers):
    """GET /loans returns list with totals."""
    headers, _ = await _project_headers(client, auth_headers)
    cp_id = await _create_counterparty(client, headers)

    await client.post("/api/v1/loans", json=_loan_payload(cp_id, "OUTGOING"), headers=headers)
    await client.post("/api/v1/loans", json=_loan_payload(cp_id, "INCOMING"), headers=headers)

    resp = await client.get("/api/v1/loans", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "totals_by_direction" in data
    assert len(data["items"]) >= 2


@pytest.mark.asyncio
async def test_list_loans_filter_direction(client, auth_headers):
    """GET /loans?direction=OUTGOING returns only OUTGOING."""
    headers, _ = await _project_headers(client, auth_headers)
    cp_id = await _create_counterparty(client, headers)

    await client.post("/api/v1/loans", json=_loan_payload(cp_id, "OUTGOING"), headers=headers)
    await client.post("/api/v1/loans", json=_loan_payload(cp_id, "INCOMING"), headers=headers)

    resp = await client.get("/api/v1/loans?direction=OUTGOING", headers=headers)
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        assert item["direction"] == "OUTGOING"


# ─── GET /loans/{id} ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_loan_detail(client, auth_headers):
    """GET /loans/{id} returns LoanDetail with schedule_summary."""
    headers, _ = await _project_headers(client, auth_headers)
    cp_id = await _create_counterparty(client, headers)

    create_resp = await client.post("/api/v1/loans", json=_loan_payload(cp_id), headers=headers)
    loan_id = create_resp.json()["id"]

    resp = await client.get(f"/api/v1/loans/{loan_id}", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == loan_id
    assert "payments" in data
    assert "schedule_summary" in data
    assert "counterparty_name" in data


# ─── PATCH /loans/{id} ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_loan_status(client, auth_headers):
    """PATCH /loans/{id} updates status."""
    headers, _ = await _project_headers(client, auth_headers)
    cp_id = await _create_counterparty(client, headers)

    create_resp = await client.post("/api/v1/loans", json=_loan_payload(cp_id), headers=headers)
    loan_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/loans/{loan_id}",
        json={"status": "CLOSED"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "CLOSED"


# ─── POST /loans/{id}/payments/match ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_match_payment_already_matched_409(client, auth_headers, db_session):
    """POST match with already-matched tx returns 409."""
    headers, project_id = await _project_headers(client, auth_headers)
    cp_id = await _create_counterparty(client, headers)

    create_resp = await client.post("/api/v1/loans", json=_loan_payload(cp_id), headers=headers)
    loan_id = create_resp.json()["id"]

    # Create a transaction in DB
    from backend.models.transactions import Transaction

    tx = Transaction(
        project_id=project_id,
        date=date(2026, 3, 1),
        bank="WB",
        account="40702810800000001893",
        currency="RUB",
        income=Decimal("0"),
        expense=Decimal("1000000"),
        net=Decimal("-1000000"),
        purpose="Займ тест",
        txn_id=f"txn_{uuid.uuid4().hex[:8]}",
        status="OK",
        is_cashflow2=1,
    )
    db_session.add(tx)
    await db_session.commit()
    await db_session.refresh(tx)

    # First match
    resp1 = await client.post(
        f"/api/v1/loans/{loan_id}/payments/match",
        json={"transaction_id": tx.id, "payment_type": "DISBURSEMENT", "amount": "1000000"},
        headers=headers,
    )
    assert resp1.status_code == 201

    # Second match — 409
    resp2 = await client.post(
        f"/api/v1/loans/{loan_id}/payments/match",
        json={"transaction_id": tx.id, "payment_type": "DISBURSEMENT", "amount": "1000000"},
        headers=headers,
    )
    assert resp2.status_code == 409
