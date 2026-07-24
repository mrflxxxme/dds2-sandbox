"""
LoanService — loan CRUD, payment matching, ETL auto-link.

All queries scoped by project_id; filters out soft-deleted rows.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_project_reports
from backend.models.counterparty import Counterparty
from backend.models.loan import Loan, LoanPayment
from backend.models.transactions import Transaction
from backend.schemas.loan import (
    LoanCreate,
    LoanDetail,
    LoanDirectionTotals,
    LoanExtend,
    LoanFilter,
    LoanListResponse,
    LoanPaymentResponse,
    LoanResponse,
    LoanScheduleSummary,
    LoanUpdate,
)
from backend.services import loan_interest
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


def _today() -> date:
    return utcnow().date()


def _next_contract_suffix(contract: str) -> str:
    """Следующий номер в цепочке продлений: «06»→«06-01», «06-01»→«06-02»."""
    m = re.match(r"^(.+?)\s*-\s*(\d+)\s*$", contract)
    if m:
        return f"{m.group(1).rstrip()}-{int(m.group(2)) + 1:02d}"
    return f"{contract.strip()}-01"


def _to_payment_lite(payments: list[LoanPayment]) -> list[loan_interest.PaymentLite]:
    return [
        loan_interest.PaymentLite(
            payment_type=p.payment_type,
            amount=Decimal(str(p.amount or 0)),
            paid_at=p.paid_at,
        )
        for p in payments
    ]


async def _enrich_loans(db: AsyncSession, loans: list[Loan]) -> list[LoanResponse]:
    """Attach counterparty name + computed interest fields to each loan (batched)."""
    if not loans:
        return []
    as_of = _today()
    loan_ids = [loan.id for loan in loans]
    cp_ids = list({loan.counterparty_id for loan in loans})

    cp_rows = await db.execute(
        select(Counterparty.id, Counterparty.name, Counterparty.inn).where(Counterparty.id.in_(cp_ids))
    )
    cp_map = {row.id: (row.name, row.inn) for row in cp_rows.all()}

    pay_rows = await db.execute(select(LoanPayment).where(LoanPayment.loan_id.in_(loan_ids)))
    pay_by_loan: dict[int, list[LoanPayment]] = {}
    for p in pay_rows.scalars().all():
        pay_by_loan.setdefault(p.loan_id, []).append(p)

    # Loans that are a parent of another loan (i.e. were extended)
    ext_rows = await db.execute(
        select(Loan.parent_loan_id)
        .where(Loan.parent_loan_id.in_(loan_ids), Loan.is_deleted == False)  # noqa: E712
        .distinct()
    )
    extended_ids = {pid for (pid,) in ext_rows.all() if pid is not None}

    out: list[LoanResponse] = []
    for loan in loans:
        calc = loan_interest.compute_loan(
            principal=Decimal(str(loan.principal)),
            rate=Decimal(str(loan.rate)) if loan.rate is not None else None,
            start_date=loan.start_date,
            maturity_date=loan.maturity_date,
            status=loan.status,
            payments=_to_payment_lite(pay_by_loan.get(loan.id, [])),
            as_of=as_of,
        )
        name, inn = cp_map.get(loan.counterparty_id, (None, None))
        resp = LoanResponse.model_validate(loan)
        resp.counterparty_name = name
        resp.counterparty_inn = inn
        resp.remaining_principal = calc.remaining_principal
        resp.accrued_interest = calc.accrued_interest
        resp.monthly_interest = calc.monthly_interest
        resp.days_to_maturity = calc.days_to_maturity
        resp.next_interest_date = calc.next_interest_date
        resp.has_extension = loan.id in extended_ids
        out.append(resp)
    return out


# ─── Errors ──────────────────────────────────────────────────────────────────


class LoanNotFoundError(Exception):
    """Raised when a Loan is missing in the given project."""


class LoanPaymentAlreadyExistsError(Exception):
    """Raised when a transaction is already matched to a loan payment."""


class ProjectMismatchError(Exception):
    """Raised when loan.project_id ≠ transaction.project_id."""


# ─── Service ─────────────────────────────────────────────────────────────────


class LoanService:
    """All loan + loan-payment operations. One instance per request."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ─── create ─────────────────────────────────────────────────────────

    async def create(self, *, data: LoanCreate, project_id: int) -> Loan:
        """Create a new Loan. Validates counterparty belongs to project."""
        # Verify counterparty
        cp_res = await self.db.execute(
            select(Counterparty.id).where(
                Counterparty.id == data.counterparty_id,
                Counterparty.project_id == project_id,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
        if cp_res.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=404,
                detail=f"Counterparty {data.counterparty_id} not found in project",
            )

        loan = Loan(
            project_id=project_id,
            counterparty_id=data.counterparty_id,
            direction=data.direction,
            principal=data.principal,
            currency=data.currency,
            rate=data.rate,
            contract_number=data.contract_number,
            contract_date=data.contract_date,
            start_date=data.start_date,
            maturity_date=data.maturity_date,
            status=data.status,
            entity_type=data.entity_type,
            lender_bank=data.lender_bank,
            parent_loan_id=data.parent_loan_id,
            notes=data.notes,
        )
        self.db.add(loan)
        await self.db.commit()
        await self.db.refresh(loan)
        await invalidate_project_reports(project_id)
        return loan

    # ─── list ───────────────────────────────────────────────────────────

    async def list(
        self,
        *,
        project_id: int,
        filters: LoanFilter,
    ) -> LoanListResponse:
        """List loans with direction totals."""
        stmt = select(Loan).where(
            Loan.project_id == project_id,
            Loan.is_deleted == False,  # noqa: E712
        )
        if filters.direction:
            stmt = stmt.where(Loan.direction == filters.direction)
        if filters.status:
            stmt = stmt.where(Loan.status == filters.status)
        if filters.counterparty_id:
            stmt = stmt.where(Loan.counterparty_id == filters.counterparty_id)
        if filters.entity_type:
            stmt = stmt.where(Loan.entity_type == filters.entity_type)

        # Count
        total_stmt = select(func.count()).select_from(stmt.subquery())
        total_res = await self.db.execute(total_stmt)
        total = int(total_res.scalar_one() or 0)

        # Page
        stmt = (
            stmt.order_by(Loan.start_date.desc().nullslast(), Loan.id.desc())
            .limit(filters.limit)
            .offset(filters.offset)
        )
        res = await self.db.execute(stmt)
        loans = list(res.scalars().all())

        # Totals by direction (aggregate in SQL, across all matching filters)
        agg_stmt = (
            select(
                Loan.direction,
                Loan.currency,
                func.count(Loan.id).label("cnt"),
                func.coalesce(func.sum(Loan.principal), 0).label("principal_sum"),
            )
            .where(
                Loan.project_id == project_id,
                Loan.is_deleted == False,  # noqa: E712
            )
            .group_by(Loan.direction, Loan.currency)
        )
        agg_res = await self.db.execute(agg_stmt)
        totals_map: dict[str, LoanDirectionTotals] = {}
        for row in agg_res.all():
            direction = row.direction
            bucket = totals_map.setdefault(direction, LoanDirectionTotals())
            bucket.count += int(row.cnt or 0)
            amount = Decimal(str(row.principal_sum or 0))
            if (row.currency or "").upper() == "CNY":
                bucket.sum_cny += amount
            else:
                bucket.sum_rub += amount

        items = await _enrich_loans(self.db, loans)
        return LoanListResponse(items=items, totals_by_direction=totals_map, total=total)

    # ─── get / get_detail ───────────────────────────────────────────────

    async def get(self, *, loan_id: int, project_id: int) -> Loan:
        """Fetch a single loan (or raise 404)."""
        res = await self.db.execute(
            select(Loan).where(
                Loan.id == loan_id,
                Loan.project_id == project_id,
                Loan.is_deleted == False,  # noqa: E712
            )
        )
        loan = res.scalar_one_or_none()
        if loan is None:
            raise HTTPException(status_code=404, detail="Loan not found")
        return loan

    async def get_detail(self, *, loan_id: int, project_id: int) -> LoanDetail:
        """Return a LoanDetail with counterparty info + payments + schedule summary."""
        loan = await self.get(loan_id=loan_id, project_id=project_id)

        # Counterparty
        cp_res = await self.db.execute(
            select(Counterparty.name, Counterparty.inn).where(
                Counterparty.id == loan.counterparty_id,
            )
        )
        cp_row = cp_res.first()
        cp_name = cp_row.name if cp_row else None
        cp_inn = cp_row.inn if cp_row else None

        # Payments
        pay_res = await self.db.execute(
            select(LoanPayment)
            .where(LoanPayment.loan_id == loan_id)
            .order_by(LoanPayment.paid_at.asc(), LoanPayment.id.asc())
            .limit(1000)
        )
        payments = [LoanPaymentResponse.model_validate(p) for p in pay_res.scalars().all()]

        # Schedule summary
        principal_paid = sum(
            (p.amount for p in payments if p.payment_type == "PRINCIPAL_REPAY"),
            Decimal("0"),
        )
        interest_paid = sum(
            (p.amount for p in payments if p.payment_type == "INTEREST_PAY"),
            Decimal("0"),
        )
        penalty_paid = sum(
            (p.amount for p in payments if p.payment_type == "PENALTY"),
            Decimal("0"),
        )
        remaining = loan.principal - principal_paid
        schedule = LoanScheduleSummary(
            principal_paid=principal_paid,
            interest_paid=interest_paid,
            penalty_paid=penalty_paid,
            remaining=remaining,
        )

        # Computed interest (engine)
        calc = loan_interest.compute_loan(
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
            as_of=_today(),
        )

        # has_extension: is this loan a parent of another?
        ext_res = await self.db.execute(
            select(Loan.id).where(Loan.parent_loan_id == loan.id, Loan.is_deleted == False).limit(1)  # noqa: E712
        )
        has_extension = ext_res.scalar_one_or_none() is not None

        # Build a plain dict (avoids MissingGreenlet when Pydantic tries to
        # access lazy relationships like loan.payments via model_validate).
        detail = LoanDetail(
            id=loan.id,
            counterparty_id=loan.counterparty_id,
            direction=loan.direction,
            principal=loan.principal,
            currency=loan.currency,
            rate=loan.rate,
            contract_number=loan.contract_number,
            contract_date=loan.contract_date,
            start_date=loan.start_date,
            maturity_date=loan.maturity_date,
            status=loan.status,
            entity_type=loan.entity_type,
            lender_bank=loan.lender_bank,
            parent_loan_id=loan.parent_loan_id,
            notes=loan.notes,
            created_at=loan.created_at,
            updated_at=loan.updated_at,
            counterparty_name=cp_name,
            counterparty_inn=cp_inn,
            remaining_principal=calc.remaining_principal,
            accrued_interest=calc.accrued_interest,
            monthly_interest=calc.monthly_interest,
            days_to_maturity=calc.days_to_maturity,
            next_interest_date=calc.next_interest_date,
            has_extension=has_extension,
            payments=payments,
            schedule_summary=schedule,
        )
        return detail

    # ─── update ─────────────────────────────────────────────────────────

    async def update(
        self,
        *,
        loan_id: int,
        data: LoanUpdate,
        project_id: int,
    ) -> Loan:
        """Partial update of Loan fields."""
        loan = await self.get(loan_id=loan_id, project_id=project_id)
        payload = data.model_dump(exclude_unset=True)
        for key, value in payload.items():
            setattr(loan, key, value)
        await self.db.commit()
        await self.db.refresh(loan)
        await invalidate_project_reports(project_id)
        return loan

    # ─── extend (продление + смена ставки) ──────────────────────────────

    async def extend(self, *, loan_id: int, data: LoanExtend, project_id: int) -> Loan:
        """
        Продлить займ: закрыть текущий (CLOSED) и создать преемника с новой
        ставкой/сроком, связанного через parent_loan_id. Возвращает преемника.
        """
        loan = await self.get(loan_id=loan_id, project_id=project_id)
        if loan.status == "CLOSED":
            raise HTTPException(status_code=400, detail="Займ уже закрыт — продлевать нечего")

        # Остаток тела
        pay_res = await self.db.execute(
            select(LoanPayment).where(LoanPayment.loan_id == loan_id).limit(1000)
        )
        payments = list(pay_res.scalars().all())
        principal_repaid = sum(
            (Decimal(str(p.amount or 0)) for p in payments if p.payment_type == "PRINCIPAL_REPAY"),
            Decimal("0"),
        )
        remaining = max(Decimal("0"), Decimal(str(loan.principal)) - principal_repaid)

        new_principal = data.principal if data.principal is not None else remaining
        if new_principal <= 0:
            raise HTTPException(status_code=400, detail="Нет остатка тела для продления")

        new_start = data.new_start_date or loan.maturity_date or _today()
        new_rate = data.new_rate if data.new_rate is not None else loan.rate
        new_contract = (data.new_contract_number or _next_contract_suffix(loan.contract_number)).strip()

        if data.new_maturity_date:
            new_maturity = data.new_maturity_date
        elif loan.maturity_date and loan.start_date:
            term_days = (loan.maturity_date - loan.start_date).days
            new_maturity = new_start + timedelta(days=term_days)
        else:
            new_maturity = None

        # Закрыть текущий
        loan.status = "CLOSED"
        if data.record_repayment and remaining > 0:
            self.db.add(
                LoanPayment(
                    loan_id=loan.id,
                    payment_type="PRINCIPAL_REPAY",
                    amount=remaining,
                    currency=loan.currency,
                    paid_at=new_start,
                )
            )

        successor = Loan(
            project_id=project_id,
            counterparty_id=loan.counterparty_id,
            direction=loan.direction,
            principal=new_principal,
            currency=loan.currency,
            rate=new_rate,
            contract_number=new_contract,
            contract_date=new_start,
            start_date=new_start,
            maturity_date=new_maturity,
            status="ACTIVE",
            entity_type=data.entity_type or loan.entity_type,
            lender_bank=data.lender_bank or loan.lender_bank,
            parent_loan_id=loan.id,
            notes=data.notes,
        )
        self.db.add(successor)
        await self.db.commit()
        await self.db.refresh(successor)
        await invalidate_project_reports(project_id)
        return successor

    # ─── match_transaction (manual) ─────────────────────────────────────

    async def match_transaction(
        self,
        *,
        loan_id: int,
        transaction_id: int,
        payment_type: str,
        amount: Decimal,
        project_id: int,
    ) -> LoanPayment:
        """
        Create a LoanPayment linking an existing Transaction to this Loan.

        Idempotency: one Transaction can be matched to at most one LoanPayment
        (UNIQUE partial index on loan_payment.transaction_id).
        """
        loan = await self.get(loan_id=loan_id, project_id=project_id)

        # Fetch transaction
        tx_res = await self.db.execute(
            select(Transaction).where(
                Transaction.id == transaction_id,
                Transaction.is_deleted == False,  # noqa: E712
            )
        )
        tx = tx_res.scalar_one_or_none()
        if tx is None:
            raise HTTPException(status_code=404, detail="Transaction not found")

        # Cross-project check
        if tx.project_id != project_id:
            raise ProjectMismatchError("Transaction belongs to a different project")

        # Already matched?
        existing = await self.db.execute(select(LoanPayment.id).where(LoanPayment.transaction_id == transaction_id))
        if existing.scalar_one_or_none() is not None:
            raise LoanPaymentAlreadyExistsError(f"Transaction {transaction_id} is already matched to a loan payment")

        payment = LoanPayment(
            loan_id=loan.id,
            transaction_id=tx.id,
            payment_type=payment_type,
            amount=amount,
            currency=tx.currency,
            paid_at=tx.date.date() if hasattr(tx.date, "date") else tx.date,
        )
        self.db.add(payment)
        await self.db.flush()

        # Back-link on transaction for fast joins in reports
        tx.loan_id = loan.id
        tx.loan_payment_id = payment.id
        tx.loan_payment_type = payment_type

        await self.db.commit()
        await self.db.refresh(payment)
        await invalidate_project_reports(project_id)
        return payment

    # ─── auto_link_from_etl ─────────────────────────────────────────────

    async def auto_link_from_etl(
        self,
        *,
        transaction: Transaction,
        purpose_match: dict,
        project_id: int,
    ) -> LoanPayment | None:
        """
        Called during ETL import when a purpose regex fires a loan_payment_type.

        Tries to find a matching ACTIVE loan by (project_id, contract_number).
        If found — creates LoanPayment. If not — still marks
        transaction.loan_payment_type and returns None.

        `purpose_match` shape:
            {"type": "DISBURSEMENT"|"PRINCIPAL_REPAY"|"INTEREST_PAY"|"PENALTY",
             "contract_number": Optional[str]}
        """
        payment_type = purpose_match.get("type")
        contract_number = purpose_match.get("contract_number")

        if not payment_type:
            return None

        # Mark on transaction regardless of match (visibility in UI)
        transaction.loan_payment_type = payment_type

        if not contract_number:
            return None

        # Find active loan in this project
        loan_res = await self.db.execute(
            select(Loan)
            .where(
                Loan.project_id == project_id,
                Loan.contract_number == contract_number,
                Loan.is_deleted == False,  # noqa: E712
                Loan.status == "ACTIVE",
            )
            .limit(1)
        )
        loan = loan_res.scalar_one_or_none()
        if loan is None:
            return None

        # Do not double-match
        existing = await self.db.execute(select(LoanPayment.id).where(LoanPayment.transaction_id == transaction.id))
        if existing.scalar_one_or_none() is not None:
            return None

        amount = transaction.expense if (transaction.expense or 0) > 0 else transaction.income
        amount = Decimal(str(amount or 0))

        payment = LoanPayment(
            loan_id=loan.id,
            transaction_id=transaction.id,
            payment_type=payment_type,
            amount=amount,
            currency=transaction.currency,
            paid_at=transaction.date.date() if hasattr(transaction.date, "date") else transaction.date,
        )
        self.db.add(payment)
        await self.db.flush()

        transaction.loan_id = loan.id
        transaction.loan_payment_id = payment.id

        return payment
