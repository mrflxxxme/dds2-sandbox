# ruff: noqa: RUF002, RUF003
"""
Лендер-портал — бизнес-логика read-only выборок займов заёмщика.

Кросс-проектно, скоуп по контрагентам (см. backend/lender_context.py).
Переиспользует движок процентов (loan_interest).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.lender_context import LenderContext
from backend.models.loan import Loan, LoanPayment
from backend.schemas.lender_portal import (
    LenderLoanDetail,
    LenderLoanListResponse,
    LenderLoanRow,
    LenderMeResponse,
    LenderPaymentRow,
    LenderProjectInfo,
    LenderTotals,
)
from backend.services import loan_interest
from backend.utils.time import utcnow

ZERO = Decimal("0")
DEFAULT_LIMIT = 200
MAX_LIMIT = 500


def _today() -> date:
    return utcnow().date()


def _calc(loan: Loan, payments: list[LoanPayment], as_of: date) -> loan_interest.LoanCalc:
    return loan_interest.compute_loan(
        principal=Decimal(str(loan.principal)),
        rate=Decimal(str(loan.rate)) if loan.rate is not None else None,
        start_date=loan.start_date,
        maturity_date=loan.maturity_date,
        status=loan.status,
        payments=[
            loan_interest.PaymentLite(
                payment_type=p.payment_type,
                amount=Decimal(str(p.amount or 0)),
                paid_at=p.paid_at,
            )
            for p in payments
        ],
        as_of=as_of,
    )


async def _payments_for(db: AsyncSession, loan_ids: list[int]) -> dict[int, list[LoanPayment]]:
    out: dict[int, list[LoanPayment]] = defaultdict(list)
    if not loan_ids:
        return out
    rows = await db.execute(select(LoanPayment).where(LoanPayment.loan_id.in_(loan_ids)))
    for p in rows.scalars().all():
        out[p.loan_id].append(p)
    return out


def _accumulate(totals: LenderTotals, calc: loan_interest.LoanCalc, loan: Loan) -> None:
    totals.interest_paid += calc.interest_paid
    if loan.status != "ACTIVE":
        return
    totals.active_count += 1
    totals.outstanding += calc.remaining_principal
    totals.accrued_interest += calc.accrued_interest
    totals.monthly_interest += calc.monthly_interest
    if calc.next_interest_date and (
        totals.next_interest_date is None or calc.next_interest_date < totals.next_interest_date
    ):
        totals.next_interest_date = calc.next_interest_date
    if loan.maturity_date and loan.maturity_date >= _today() and (
        totals.next_maturity_date is None or loan.maturity_date < totals.next_maturity_date
    ):
        totals.next_maturity_date = loan.maturity_date


async def me(db: AsyncSession, ctx: LenderContext) -> LenderMeResponse:
    """Profile header + portfolio totals across all the lender's counterparties."""
    cp_ids = ctx.counterparty_ids
    as_of = _today()
    totals = LenderTotals()
    if cp_ids:
        loan_rows = await db.execute(
            select(Loan)
            .where(
                Loan.counterparty_id.in_(cp_ids),
                Loan.is_deleted == False,  # noqa: E712
                Loan.direction == "INCOMING",
            )
            .order_by(Loan.id)
            .limit(MAX_LIMIT)
        )
        loans = list(loan_rows.scalars().all())
        pay = await _payments_for(db, [loan.id for loan in loans])
        for loan in loans:
            _accumulate(totals, _calc(loan, pay.get(loan.id, []), as_of), loan)

    display = ", ".join(sorted({cp.name for cp in ctx.counterparty_by_id.values()})) or ctx.user.username
    projects = [LenderProjectInfo(slug=p.slug, name=p.name) for p in ctx.projects.values()]
    return LenderMeResponse(username=ctx.user.username, display_name=display, projects=projects, totals=totals)


async def list_loans(
    db: AsyncSession,
    ctx: LenderContext,
    *,
    status: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> LenderLoanListResponse:
    cp_ids = ctx.counterparty_ids
    if not cp_ids:
        return LenderLoanListResponse()
    as_of = _today()
    limit = min(limit, MAX_LIMIT)

    stmt = select(Loan).where(
        Loan.counterparty_id.in_(cp_ids),
        Loan.is_deleted == False,  # noqa: E712
        Loan.direction == "INCOMING",
    )
    if status:
        stmt = stmt.where(Loan.status == status)
    stmt = stmt.order_by(Loan.status.asc(), Loan.start_date.desc(), Loan.id.desc()).limit(limit).offset(offset)
    loans = list((await db.execute(stmt)).scalars().all())
    pay = await _payments_for(db, [loan.id for loan in loans])

    totals = LenderTotals()
    items: list[LenderLoanRow] = []
    for loan in loans:
        calc = _calc(loan, pay.get(loan.id, []), as_of)
        _accumulate(totals, calc, loan)
        items.append(
            LenderLoanRow(
                id=loan.id,
                contract_number=loan.contract_number,
                principal=Decimal(str(loan.principal)),
                currency=loan.currency,
                rate=Decimal(str(loan.rate)) if loan.rate is not None else None,
                status=loan.status,
                entity_type=loan.entity_type,
                start_date=loan.start_date,
                maturity_date=loan.maturity_date,
                remaining_principal=calc.remaining_principal,
                accrued_interest=calc.accrued_interest,
                monthly_interest=calc.monthly_interest,
                next_interest_date=calc.next_interest_date,
                days_to_maturity=calc.days_to_maturity,
                is_overdue=calc.is_overdue,
            )
        )
    return LenderLoanListResponse(items=items, totals=totals)


async def get_scoped_loan(db: AsyncSession, ctx: LenderContext, loan_id: int) -> Loan:
    """Load a loan only if it belongs to one of the lender's counterparties."""
    loan = (
        await db.execute(
            select(Loan).where(
                Loan.id == loan_id,
                Loan.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if loan is None or loan.direction != "INCOMING" or not ctx.allows(loan.project_id, loan.counterparty_id):
        raise HTTPException(404, "Заём не найден")
    return loan


async def loan_detail(db: AsyncSession, ctx: LenderContext, loan: Loan) -> LenderLoanDetail:
    as_of = _today()
    pay_rows = (
        await db.execute(
            select(LoanPayment)
            .where(LoanPayment.loan_id == loan.id)
            .order_by(LoanPayment.paid_at.asc(), LoanPayment.id.asc())
            .limit(1000)
        )
    ).scalars().all()
    payments = list(pay_rows)
    calc = _calc(loan, payments, as_of)
    return LenderLoanDetail(
        id=loan.id,
        contract_number=loan.contract_number,
        contract_date=loan.contract_date,
        principal=Decimal(str(loan.principal)),
        currency=loan.currency,
        rate=Decimal(str(loan.rate)) if loan.rate is not None else None,
        status=loan.status,
        entity_type=loan.entity_type,
        lender_bank=loan.lender_bank,
        start_date=loan.start_date,
        maturity_date=loan.maturity_date,
        remaining_principal=calc.remaining_principal,
        accrued_interest=calc.accrued_interest,
        monthly_interest=calc.monthly_interest,
        next_interest_date=calc.next_interest_date,
        days_to_maturity=calc.days_to_maturity,
        is_overdue=calc.is_overdue,
        interest_paid=calc.interest_paid,
        principal_repaid=calc.principal_repaid,
        total_interest_projected=calc.total_interest_projected,
        payments=[
            LenderPaymentRow(
                payment_type=p.payment_type,
                amount=Decimal(str(p.amount or 0)),
                currency=p.currency,
                paid_at=p.paid_at,
            )
            for p in payments
        ],
        created_at=loan.created_at,
    )
