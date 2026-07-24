"""
Tests for the extended loan service: interest engine, extend (продление),
analytics (dashboard/by-lender/forecast), Excel import, lender portal scoping.
Uses real PG database for service-level tests; engine tests are pure.
"""

import io
import uuid
from datetime import date
from decimal import Decimal

import openpyxl
import pytest

from backend.schemas.counterparty import CounterpartyCreate
from backend.schemas.loan import LenderAccessCreate, LoanCreate, LoanExtend, LoanUpdate
from backend.services import lender_access_service, loan_analytics, loan_interest
from backend.services import lender_portal_service as lps
from backend.services.counterparty_service import CounterpartyService
from backend.services.loan_import import import_loans_from_xlsx
from backend.services.loan_service import LoanService

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _inn() -> str:
    return str(int(uuid.uuid4().hex[:9], 16) % 9000000000 + 1000000000)


async def _create_project(client, auth_headers) -> int:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"loanx_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    return resp.json()["id"]


async def _create_cp(db_session, project_id: int, name: str = "Кредитор") -> int:
    svc = CounterpartyService(db_session)
    cp = await svc.create(
        CounterpartyCreate(inn=_inn(), name=f"{name} {uuid.uuid4().hex[:4]}", primary_type="OTHER"),
        project_id=project_id,
    )
    return cp.id


def _incoming(cp_id: int, contract: str, principal="1000000", rate="0.28") -> LoanCreate:
    return LoanCreate(
        counterparty_id=cp_id,
        direction="INCOMING",
        principal=Decimal(principal),
        currency="RUB",
        rate=Decimal(rate),
        contract_number=contract,
        contract_date=date(2025, 1, 1),
        start_date=date(2025, 1, 1),
        maturity_date=date(2025, 7, 1),
        entity_type="IP",
    )


# ─── Interest engine (pure) ───────────────────────────────────────────────────


def test_engine_current_period_accrual():
    calc = loan_interest.compute_loan(
        principal=Decimal("300000"),
        rate=Decimal("0.28"),
        start_date=date(2024, 3, 20),
        maturity_date=date(2024, 9, 19),
        status="ACTIVE",
        payments=[],
        as_of=date(2024, 4, 15),
    )
    # current period = since 2024-03-20 (last monthly anniversary) = 26 days
    assert calc.accrued_interest == Decimal("5983.56")
    assert calc.monthly_interest == Decimal("7000.00")
    assert calc.daily_interest == Decimal("230.14")
    assert calc.next_interest_date == date(2024, 4, 20)
    # full-term projection: 300000 * 0.28 * 183/365
    assert calc.total_interest_projected == Decimal("42115.07")
    assert calc.is_overdue is False


def test_engine_overdue_and_closed():
    overdue = loan_interest.compute_loan(
        principal=Decimal("100000"), rate=Decimal("0.3"),
        start_date=date(2024, 1, 1), maturity_date=date(2024, 6, 1),
        status="ACTIVE", payments=[], as_of=date(2024, 8, 1),
    )
    assert overdue.is_overdue is True
    assert overdue.days_to_maturity < 0

    closed = loan_interest.compute_loan(
        principal=Decimal("100000"), rate=Decimal("0.3"),
        start_date=date(2024, 1, 1), maturity_date=date(2024, 6, 1),
        status="CLOSED", payments=[], as_of=date(2024, 5, 1),
    )
    assert closed.remaining_principal == Decimal("0.00")
    assert closed.accrued_interest == Decimal("0.00")
    assert closed.monthly_interest == Decimal("0.00")


def test_engine_partial_repayment_reduces_remaining():
    calc = loan_interest.compute_loan(
        principal=Decimal("1000000"), rate=Decimal("0.2"),
        start_date=date(2025, 1, 1), maturity_date=date(2026, 1, 1),
        status="ACTIVE",
        payments=[loan_interest.PaymentLite("PRINCIPAL_REPAY", Decimal("400000"), date(2025, 6, 1))],
        as_of=date(2025, 7, 1),
    )
    assert calc.remaining_principal == Decimal("600000.00")
    assert calc.principal_repaid == Decimal("400000.00")


# ─── extend (продление) ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extend_creates_linked_successor(db_session, client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    cp_id = await _create_cp(db_session, project_id)
    svc = LoanService(db_session)
    loan = await svc.create(data=_incoming(cp_id, "06"), project_id=project_id)

    successor = await svc.extend(
        loan_id=loan.id,
        data=LoanExtend(new_rate=Decimal("0.31"), record_repayment=True),
        project_id=project_id,
    )
    assert successor.parent_loan_id == loan.id
    assert successor.status == "ACTIVE"
    assert successor.rate == Decimal("0.31")
    assert successor.contract_number == "06-01"
    assert successor.principal == Decimal("1000000.00")

    # old loan closed + has a repayment recorded
    detail = await svc.get_detail(loan_id=loan.id, project_id=project_id)
    assert detail.status == "CLOSED"
    assert detail.has_extension is True
    assert any(p.payment_type == "PRINCIPAL_REPAY" for p in detail.payments)


@pytest.mark.asyncio
async def test_extend_closed_loan_rejected(db_session, client, auth_headers):
    from fastapi import HTTPException

    project_id = await _create_project(client, auth_headers)
    cp_id = await _create_cp(db_session, project_id)
    svc = LoanService(db_session)
    loan = await svc.create(data=_incoming(cp_id, "07"), project_id=project_id)
    await svc.update(loan_id=loan.id, data=LoanUpdate(status="CLOSED"), project_id=project_id)
    with pytest.raises(HTTPException):
        await svc.extend(loan_id=loan.id, data=LoanExtend(), project_id=project_id)


# ─── analytics ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dashboard_and_by_lender(db_session, client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    cp_id = await _create_cp(db_session, project_id)
    svc = LoanService(db_session)
    await svc.create(data=_incoming(cp_id, "A1", principal="2000000", rate="0.30"), project_id=project_id)
    await svc.create(data=_incoming(cp_id, "A2", principal="1000000", rate="0.20"), project_id=project_id)

    dash = await loan_analytics.dashboard(db_session, project_id, date(2025, 3, 1))
    assert dash["kpis"]["active_count"] == 2
    assert Decimal(str(dash["kpis"]["total_outstanding"])) == Decimal("3000000")
    # weighted rate = (2M*0.3 + 1M*0.2)/3M = 0.2667
    assert abs(Decimal(str(dash["kpis"]["weighted_avg_rate"])) - Decimal("0.2667")) < Decimal("0.001")

    by = await loan_analytics.by_lender(db_session, project_id, date(2025, 3, 1))
    assert len(by["items"]) == 1
    assert by["items"][0]["active_count"] == 2
    assert Decimal(str(by["items"][0]["outstanding"])) == Decimal("3000000")

    fc = await loan_analytics.forecast(db_session, project_id, date(2025, 3, 1), 12)
    assert len(fc["months"]) == 12
    # principal due in 2025-07 (maturity)
    jul = next(m for m in fc["months"] if m["month"] == "2025-07")
    assert Decimal(str(jul["principal_due"])) == Decimal("3000000")


# ─── Excel import idempotency ─────────────────────────────────────────────────


def _build_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Расчет процентов"
    ws.append(["Дата", "Тип", "Сумма", "Контрагент", "Номер договора", "Ставка", "От", "До", "Статус"])
    ws.append([date(2025, 1, 1), "Приход", 500000, "Иванов", "100", 0.28, date(2025, 1, 1), date(2025, 7, 1), "Активен"])
    ws.append([date(2025, 7, 1), "Возврат", 500000, "Иванов", "100", None, date(2025, 7, 1), None, "Не активен"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_import_idempotent(db_session, client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    content = _build_xlsx()

    r1 = await import_loans_from_xlsx(db_session, project_id=project_id, content=content)
    assert r1.created_loans == 1
    assert r1.created_counterparties == 1
    assert r1.created_payments == 1

    r2 = await import_loans_from_xlsx(db_session, project_id=project_id, content=content)
    assert r2.created_loans == 0
    assert r2.updated_loans == 1
    assert r2.created_counterparties == 0
    assert r2.created_payments == 0  # repayment already present (idempotent)


def _build_rollover_xlsx() -> bytes:
    """Продление БЕЗ смены номера договора: старый транш гасится в день старта нового."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Расчет процентов"
    ws.append(["Дата", "Тип", "Сумма", "Контрагент", "Номер договора", "Ставка", "От", "До", "Статус"])
    # старый транш
    ws.append([date(2025, 8, 18), "Приход", 5000000, "Еремин", "64", 0.315, date(2025, 8, 18), date(2026, 2, 16), "Не активен"])
    # возврат датирован ровно днём погашения старого = днём старта нового
    ws.append([date(2026, 2, 16), "Возврат", 5000000, "Еремин", "64", 0.315, date(2026, 2, 16), date(2026, 2, 16), "Не активен"])
    # преемник под ТЕМ ЖЕ номером
    ws.append([date(2026, 2, 16), "Приход", 5000000, "Еремин", "64", 0.265, date(2026, 2, 16), date(2026, 8, 17), "Активен"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_import_rollover_same_contract_closes_old_tranche(db_session, client, auth_headers):
    """Возврат закрывает транш, который в этот день ПОГАШАЕТСЯ, а не тот, что стартует."""
    from sqlalchemy import select

    from backend.models.loan import Loan, LoanPayment

    project_id = await _create_project(client, auth_headers)
    res = await import_loans_from_xlsx(db_session, project_id=project_id, content=_build_rollover_xlsx())
    assert res.created_loans == 2
    assert res.created_payments == 1

    loans = (
        (await db_session.execute(select(Loan).where(Loan.project_id == project_id).order_by(Loan.start_date)))
        .scalars()
        .all()
    )
    old, new = loans
    assert old.start_date == date(2025, 8, 18)
    assert new.start_date == date(2026, 2, 16)
    # старый — закрыт возвратом, новый — живой
    assert old.status == "CLOSED"
    assert new.status == "ACTIVE"

    payment = (await db_session.execute(select(LoanPayment).where(LoanPayment.loan_id.in_([old.id, new.id])))).scalars().one()
    assert payment.loan_id == old.id, "возврат повешен на новый транш вместо погашаемого старого"


def _build_overdue_xlsx() -> bytes:
    """Срок вышел, строки «Возврат» нет. Колонка «Статус» — формула по датам, ей верить нельзя."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Расчет процентов"
    ws.append(["Дата", "Тип", "Сумма", "Контрагент", "Номер договора", "Ставка", "От", "До", "Статус"])
    # формула =IF(AND(TODAY()>=От; TODAY()<=До)…) на просроченном займе даёт «Не активен»
    ws.append([date(2025, 1, 1), "Приход", 3000000, "Петров", "200", 0.28, date(2025, 1, 1), date(2025, 7, 1), "Не активен"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_import_overdue_without_return_stays_active(db_session, client, auth_headers):
    """Просроченный займ без возврата остаётся ACTIVE — иначе долг исчезает из остатка."""
    from sqlalchemy import select

    from backend.models.loan import Loan

    project_id = await _create_project(client, auth_headers)
    res = await import_loans_from_xlsx(db_session, project_id=project_id, content=_build_overdue_xlsx())
    assert res.created_loans == 1
    assert res.created_payments == 0

    loan = (await db_session.execute(select(Loan).where(Loan.project_id == project_id))).scalars().one()
    assert loan.status == "ACTIVE", "займ без строки «Возврат» не должен закрываться по формуле «Статус»"
    assert loan.maturity_date == date(2025, 7, 1)


# ─── Lender portal scoping ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lender_access_and_scoping(db_session, client, auth_headers):
    from fastapi import HTTPException

    from backend.lender_context import LenderContext
    from backend.models.auth import Project
    from backend.models.counterparty import Counterparty
    from sqlalchemy import select

    project_id = await _create_project(client, auth_headers)
    cp1 = await _create_cp(db_session, project_id, "Лендер1")
    cp2 = await _create_cp(db_session, project_id, "Лендер2")
    svc = LoanService(db_session)
    loan1 = await svc.create(data=_incoming(cp1, "L1"), project_id=project_id)
    loan2 = await svc.create(data=_incoming(cp2, "L2"), project_id=project_id)

    created = await lender_access_service.create_access(
        db_session, project_id, LenderAccessCreate(counterparty_id=cp1)
    )
    assert created.password
    assert created.user_id is not None

    # duplicate → 409
    with pytest.raises(HTTPException):
        await lender_access_service.create_access(db_session, project_id, LenderAccessCreate(counterparty_id=cp1))

    # build a lender context scoped to cp1 and verify isolation
    from backend.models.auth import User

    user = (await db_session.execute(select(User).where(User.id == created.user_id))).scalar_one()
    project = (await db_session.execute(select(Project).where(Project.id == project_id))).scalar_one()
    cp1_obj = (await db_session.execute(select(Counterparty).where(Counterparty.id == cp1))).scalar_one()
    ctx = LenderContext(
        user=user,
        projects={project_id: project},
        counterparties={project_id: [cp1]},
        counterparty_by_id={cp1: cp1_obj},
    )

    listing = await lps.list_loans(db_session, ctx)
    ids = {row.id for row in listing.items}
    assert loan1.id in ids
    assert loan2.id not in ids  # isolation

    # scoped fetch of other lender's loan → 404
    with pytest.raises(HTTPException):
        await lps.get_scoped_loan(db_session, ctx, loan2.id)

    # access list reflects the grant
    access = await lender_access_service.list_access(db_session, project_id)
    assert any(a.counterparty_id == cp1 for a in access)


@pytest.mark.asyncio
async def test_lender_context_drops_cross_project_counterparty(db_session, client, auth_headers):
    """get_lender_context must drop a counterparty that belongs to a DIFFERENT project."""
    import json as _json

    from backend.auth import hash_password
    from backend.lender_context import get_lender_context, lender_counterparties_key
    from backend.models.auth import ProjectMember, User
    from backend.models.refs import ProjectSetting

    proj_a = await _create_project(client, auth_headers)
    proj_b = await _create_project(client, auth_headers)
    cp_b = await _create_cp(db_session, proj_b, "ЧужойПроект")

    user = User(username=f"lx_{uuid.uuid4().hex[:8]}", password_hash=hash_password("x123456"), is_active=True, is_external=True)
    db_session.add(user)
    await db_session.flush()
    # lender membership in project A, but scope list points at a cp from project B
    db_session.add(ProjectMember(project_id=proj_a, user_id=user.id, role="lender"))
    db_session.add(ProjectSetting(project_id=proj_a, key=lender_counterparties_key(user.id), value=_json.dumps([cp_b])))
    await db_session.commit()

    ctx = await get_lender_context(user=user, db=db_session)
    # cp_b belongs to project B, not A → must be excluded from the resolved scope
    assert cp_b not in ctx.counterparty_ids
    assert ctx.counterparties.get(proj_a) == []
