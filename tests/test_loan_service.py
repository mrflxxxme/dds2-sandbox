"""
Tests for LoanService.
Uses real PG database.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from backend.schemas.counterparty import CounterpartyCreate
from backend.schemas.loan import LoanCreate
from backend.services.counterparty_service import CounterpartyService
from backend.services.loan_service import (
    LoanPaymentAlreadyExistsError,
    LoanService,
    ProjectMismatchError,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _inn() -> str:
    return str(int(uuid.uuid4().hex[:9], 16) % 9000000000 + 1000000000)


async def _create_project(client, auth_headers) -> int:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"loan_test_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    return resp.json()["id"]


async def _create_counterparty(db_session, project_id: int) -> int:
    svc = CounterpartyService(db_session)
    inn = _inn()
    cp = await svc.create(
        CounterpartyCreate(inn=inn, name="КА для займа", primary_type="AFFILIATED"),
        project_id=project_id,
    )
    return cp.id


def _loan_create(counterparty_id: int, direction: str = "OUTGOING") -> LoanCreate:
    return LoanCreate(
        counterparty_id=counterparty_id,
        direction=direction,
        principal=Decimal("1000000.00"),
        currency="RUB",
        rate=Decimal("0.1850"),
        contract_number=f"ЗМ-{uuid.uuid4().hex[:6]}",
        contract_date=date(2026, 1, 1),
        start_date=date(2026, 1, 10),
        maturity_date=date(2027, 1, 10),
    )


# ─── create ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_loan_outgoing(db_session, client, auth_headers):
    """create() creates OUTGOING loan with status=ACTIVE."""
    project_id = await _create_project(client, auth_headers)
    cp_id = await _create_counterparty(db_session, project_id)
    service = LoanService(db_session)

    loan = await service.create(
        data=_loan_create(cp_id, direction="OUTGOING"),
        project_id=project_id,
    )

    assert loan.id is not None
    assert loan.status == "ACTIVE"
    assert loan.direction == "OUTGOING"
    assert loan.principal == Decimal("1000000.00")


@pytest.mark.asyncio
async def test_create_loan_incoming(db_session, client, auth_headers):
    """create() creates INCOMING loan."""
    project_id = await _create_project(client, auth_headers)
    cp_id = await _create_counterparty(db_session, project_id)
    service = LoanService(db_session)

    loan = await service.create(
        data=_loan_create(cp_id, direction="INCOMING"),
        project_id=project_id,
    )
    assert loan.direction == "INCOMING"


# ─── match_transaction ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_match_transaction_wrong_project(db_session, client, auth_headers):
    """match_transaction raises ProjectMismatchError for tx from different project."""
    project1 = await _create_project(client, auth_headers)
    project2 = await _create_project(client, auth_headers)
    cp_id = await _create_counterparty(db_session, project1)
    service = LoanService(db_session)

    loan = await service.create(data=_loan_create(cp_id), project_id=project1)

    # Create a transaction in project2 directly in DB
    from backend.models.transactions import Transaction

    tx = Transaction(
        project_id=project2,
        date=date(2026, 3, 1),
        bank="WB",
        account="40702810800000001893",
        currency="RUB",
        income=Decimal("1000000"),
        expense=Decimal("0"),
        net=Decimal("1000000"),
        purpose="Предоставление займа",
        txn_id=f"txn_{uuid.uuid4().hex[:8]}",
        status="OK",
        is_cashflow2=1,
    )
    db_session.add(tx)
    await db_session.commit()
    await db_session.refresh(tx)

    with pytest.raises(ProjectMismatchError):
        await service.match_transaction(
            loan_id=loan.id,
            transaction_id=tx.id,
            payment_type="DISBURSEMENT",
            amount=Decimal("1000000"),
            project_id=project1,
        )


@pytest.mark.asyncio
async def test_match_transaction_already_matched(db_session, client, auth_headers):
    """match_transaction raises LoanPaymentAlreadyExistsError if tx already matched."""
    project_id = await _create_project(client, auth_headers)
    cp_id = await _create_counterparty(db_session, project_id)
    service = LoanService(db_session)

    loan = await service.create(data=_loan_create(cp_id), project_id=project_id)

    # Create a transaction
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
        purpose="Предоставление займа",
        txn_id=f"txn_{uuid.uuid4().hex[:8]}",
        status="OK",
        is_cashflow2=1,
    )
    db_session.add(tx)
    await db_session.commit()
    await db_session.refresh(tx)

    # First match — should succeed
    await service.match_transaction(
        loan_id=loan.id,
        transaction_id=tx.id,
        payment_type="DISBURSEMENT",
        amount=Decimal("1000000"),
        project_id=project_id,
    )

    # Second match — should fail
    with pytest.raises(LoanPaymentAlreadyExistsError):
        await service.match_transaction(
            loan_id=loan.id,
            transaction_id=tx.id,
            payment_type="DISBURSEMENT",
            amount=Decimal("1000000"),
            project_id=project_id,
        )


# ─── auto_link_from_etl ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_link_from_etl_matching_loan(db_session, client, auth_headers):
    """auto_link_from_etl creates LoanPayment when matching loan exists."""
    project_id = await _create_project(client, auth_headers)
    cp_id = await _create_counterparty(db_session, project_id)
    service = LoanService(db_session)

    contract_no = f"ЗМ-{uuid.uuid4().hex[:6]}"
    loan = await service.create(
        data=LoanCreate(
            counterparty_id=cp_id,
            direction="OUTGOING",
            principal=Decimal("500000"),
            currency="RUB",
            contract_number=contract_no,
            contract_date=date(2026, 1, 1),
            start_date=date(2026, 1, 1),
        ),
        project_id=project_id,
    )

    from backend.models.transactions import Transaction

    tx = Transaction(
        project_id=project_id,
        date=date(2026, 3, 10),
        bank="WB",
        account="40702810800000001893",
        currency="RUB",
        income=Decimal("0"),
        expense=Decimal("500000"),
        net=Decimal("-500000"),
        purpose=f"Предоставление займа по договору {contract_no}",
        txn_id=f"txn_{uuid.uuid4().hex[:8]}",
        contract_number=contract_no,
        status="OK",
        is_cashflow2=1,
    )
    db_session.add(tx)
    await db_session.commit()
    await db_session.refresh(tx)

    loan_payment = await service.auto_link_from_etl(
        transaction=tx,
        purpose_match={"type": "DISBURSEMENT", "contract_number": contract_no},
        project_id=project_id,
    )

    assert loan_payment is not None
    assert loan_payment.loan_id == loan.id
    assert loan_payment.transaction_id == tx.id


@pytest.mark.asyncio
async def test_auto_link_from_etl_no_matching_loan(db_session, client, auth_headers):
    """auto_link_from_etl returns None when no matching loan found."""
    project_id = await _create_project(client, auth_headers)
    service = LoanService(db_session)

    from backend.models.transactions import Transaction

    tx = Transaction(
        project_id=project_id,
        date=date(2026, 3, 10),
        bank="WB",
        account="40702810800000001893",
        currency="RUB",
        income=Decimal("0"),
        expense=Decimal("100000"),
        net=Decimal("-100000"),
        purpose="Предоставление займа по договору НЕСУЩЕСТВУЮЩИЙ-123",
        txn_id=f"txn_{uuid.uuid4().hex[:8]}",
        contract_number="НЕСУЩЕСТВУЮЩИЙ-123",
        status="OK",
        is_cashflow2=1,
    )
    db_session.add(tx)
    await db_session.commit()
    await db_session.refresh(tx)

    loan_payment = await service.auto_link_from_etl(
        transaction=tx,
        purpose_match={"type": "DISBURSEMENT", "contract_number": "НЕСУЩЕСТВУЮЩИЙ-123"},
        project_id=project_id,
    )

    assert loan_payment is None
    # tx still has loan_payment_type set
    await db_session.refresh(tx)
    assert tx.loan_payment_type == "DISBURSEMENT"


# ─── get_detail ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_loan_detail(db_session, client, auth_headers):
    """get_detail() returns LoanDetail with counterparty info and payments."""
    project_id = await _create_project(client, auth_headers)
    cp_id = await _create_counterparty(db_session, project_id)
    service = LoanService(db_session)

    loan = await service.create(data=_loan_create(cp_id), project_id=project_id)

    detail = await service.get_detail(loan_id=loan.id, project_id=project_id)

    assert detail.id == loan.id
    assert detail.counterparty_name is not None
    assert isinstance(detail.payments, list)
    assert detail.schedule_summary.remaining == loan.principal  # no payments yet
