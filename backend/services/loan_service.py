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
from backend.models.loan import Loan, LoanPayment, LoanRatePeriod
from backend.models.transactions import Transaction
from backend.schemas.loan import (
    CreditLineDetail,
    CreditLineDraw,
    CreditLineListResponse,
    CreditLineMovement,
    LenderDetail,
    LenderPeriodPoint,
    LoanCreate,
    LoanDetail,
    LoanDirectionTotals,
    LoanExtend,
    LoanFilter,
    LoanListResponse,
    LoanPaymentResponse,
    LoanRatePeriodIn,
    LoanRatePeriodResponse,
    LoanRepay,
    LoanResponse,
    LoanScheduleSummary,
    LoanStuckItem,
    LoanStuckResponse,
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


def compute_new_money(loans: list[Loan], pay_by_loan: dict[int, list[LoanPayment]]) -> Decimal:
    """
    Сколько заёмщик РЕАЛЬНО занёс за всё время (без учёта продлений).

    Продление — это те же деньги под новым номером, поэтому простая сумма тел
    двоит: у заёмщика с цепочкой 57 → 57-01 → 57-02 → 57-03 одни 2 млн
    превращаются в 8. Продление опознаём двумя способами, потому что ни один
    не полон:
      1. `parent_loan_id` — надёжно, но проставлен не везде: связка в импорте
         ищет предшественника по точному номеру, а «10 -01» и «10-01» для неё
         разные строки, и цепочка рвётся со второго звена.
      2. Возврат тела в день выдачи нового займа — ловит неслинкованные звенья
         (там, где деньги физически не двигались).
    Доплата при продлении (вернули 2 млн, выдали 3) считается новыми деньгами
    на разницу.
    """
    avail: dict[date, Decimal] = {}
    for pays in pay_by_loan.values():
        for p in pays:
            if p.payment_type == "PRINCIPAL_REPAY":
                avail[p.paid_at] = avail.get(p.paid_at, Decimal("0")) + Decimal(str(p.amount or 0))

    by_id = {loan.id: loan for loan in loans}
    total = Decimal("0")
    for loan in sorted(loans, key=lambda x: (x.start_date, x.id)):
        principal = Decimal(str(loan.principal))
        parent = by_id.get(loan.parent_loan_id) if loan.parent_loan_id else None
        if parent is not None:
            carried = min(principal, Decimal(str(parent.principal)))
        else:
            carried = min(principal, avail.get(loan.start_date, Decimal("0")))
        # Съедаем использованный возврат, чтобы он не зачёлся второй раз
        pool = avail.get(loan.start_date, Decimal("0"))
        avail[loan.start_date] = max(Decimal("0"), pool - carried)
        total += principal - carried
    return total


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

    # История ставок нужна кредитным линиям: без неё плавающая ставка схлопнется
    # в одно поле `rate` и начисления разойдутся с банком.
    rate_rows = await db.execute(
        select(LoanRatePeriod).where(LoanRatePeriod.loan_id.in_(loan_ids)).limit(2000)
    )
    rates_by_loan: dict[int, list[LoanRatePeriod]] = {}
    for rp in rate_rows.scalars().all():
        rates_by_loan.setdefault(rp.loan_id, []).append(rp)

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
            period=loan_period(loan, as_of),
            rate_periods=_rate_periods_lite(rates_by_loan.get(loan.id, [])),
        )
        name, inn = cp_map.get(loan.counterparty_id, (None, None))
        resp = LoanResponse.model_validate(loan)
        resp.counterparty_name = name
        resp.counterparty_inn = inn
        resp.remaining_principal = calc.remaining_principal
        resp.accrued_interest = calc.accrued_interest
        resp.interest_due_period = calc.interest_due_period
        resp.accrual_period_start = calc.accrual_period_start
        resp.accrual_period_end = calc.accrual_period_end
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
            interest_due_period=calc.interest_due_period,
            accrual_period_start=calc.accrual_period_start,
            accrual_period_end=calc.accrual_period_end,
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

    async def repay(self, *, loan_id: int, data: LoanRepay, project_id: int) -> Loan:
        """
        Отметить возврат тела: создать PRINCIPAL_REPAY и закрыть займ, если
        возвращён весь остаток. Частичный возврат оставляет займ активным —
        движок процентов сам пересчитает начисление на уменьшенное тело.
        """
        loan = await self.get(loan_id=loan_id, project_id=project_id)
        pay_res = await self.db.execute(
            select(LoanPayment).where(LoanPayment.loan_id == loan_id).limit(1000)
        )
        payments = list(pay_res.scalars().all())
        already = sum(
            (Decimal(str(p.amount or 0)) for p in payments if p.payment_type == "PRINCIPAL_REPAY"),
            Decimal("0"),
        )
        remaining = max(Decimal("0"), Decimal(str(loan.principal)) - already)
        if remaining <= 0:
            raise HTTPException(status_code=400, detail="Тело займа уже возвращено полностью")

        amount = data.amount if data.amount is not None else remaining
        if amount > remaining:
            raise HTTPException(
                status_code=400,
                detail=f"Возврат {amount} больше остатка тела {remaining}",
            )

        paid_at = data.paid_at or _today()
        if paid_at < loan.start_date:
            raise HTTPException(status_code=400, detail="Дата возврата раньше выдачи займа")

        self.db.add(
            LoanPayment(
                loan_id=loan.id,
                payment_type="PRINCIPAL_REPAY",
                amount=amount,
                currency=loan.currency,
                paid_at=paid_at,
            )
        )
        if data.close and amount >= remaining:
            loan.status = "CLOSED"
        await self.db.commit()
        await self.db.refresh(loan)
        await invalidate_project_reports(project_id)
        return loan

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
        elif data.term_months:
            # Пресет из модалки: 1/2/3 месяца от даты продления, с клампом дня
            # (31 янв + 1 мес → 28 фев), а не «+30 дней».
            new_maturity = loan_interest.add_months(new_start, data.term_months)
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


# ─── Карточка заёмщика ───────────────────────────────────────────────────────


async def get_lender_detail(
    db: AsyncSession, *, counterparty_id: int, project_id: int, months_back: int = 12
) -> LenderDetail:
    """
    Всё по одному заёмщику: итоги, история займов и проценты по периодам 25→25.

    Архив вычисляемый (нет активных займов) — флага в БД нет, контрагент
    остаётся живым в остальных разделах.
    """
    cp = (
        await db.execute(
            select(Counterparty).where(
                Counterparty.id == counterparty_id,
                Counterparty.project_id == project_id,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if cp is None:
        raise HTTPException(status_code=404, detail="Заёмщик не найден")

    loans = list(
        (
            await db.execute(
                select(Loan)
                .where(
                    Loan.counterparty_id == counterparty_id,
                    Loan.project_id == project_id,
                    Loan.is_deleted == False,  # noqa: E712
                )
                .order_by(Loan.start_date.desc(), Loan.id.desc())
                .limit(1000)
            )
        )
        .scalars()
        .all()
    )

    as_of = _today()
    detail = LenderDetail(
        counterparty_id=cp.id,
        name=cp.name,
        inn=cp.inn,
        lender_bank=next((loan.lender_bank for loan in loans if loan.lender_bank), None),
        entity_type=next((loan.entity_type for loan in loans if loan.entity_type), None),
        total_count=len(loans),
    )
    if not loans:
        detail.is_archived = True
        return detail

    pay_rows = list(
        (
            await db.execute(
                select(LoanPayment)
                .where(LoanPayment.loan_id.in_([loan.id for loan in loans]))
                .order_by(LoanPayment.paid_at)
                .limit(5000)
            )
        )
        .scalars()
        .all()
    )
    pay_by_loan: dict[int, list[LoanPayment]] = {}
    for pay in pay_rows:
        pay_by_loan.setdefault(pay.loan_id, []).append(pay)

    p_start, p_end = loan_interest.accrual_period(as_of)
    detail.accrual_period_start = p_start
    detail.accrual_period_end = p_end
    detail.first_loan_date = min(loan.start_date for loan in loans)
    detail.last_loan_date = max(loan.start_date for loan in loans)

    since = loan_interest.add_months(as_of, -months_back)
    periods: dict[date, Decimal] = {}
    weighted_num = Decimal("0")

    for loan in loans:
        lites = _to_payment_lite(pay_by_loan.get(loan.id, []))
        calc = loan_interest.compute_loan(
            principal=Decimal(str(loan.principal)),
            rate=Decimal(str(loan.rate)) if loan.rate is not None else None,
            start_date=loan.start_date,
            maturity_date=loan.maturity_date,
            status=loan.status,
            payments=lites,
            as_of=as_of,
        )
        detail.interest_paid += calc.interest_paid
        detail.accrued_interest += calc.accrued_interest
        detail.interest_due_period += calc.interest_due_period
        if loan.status == "ACTIVE":
            detail.active_count += 1
            detail.outstanding += calc.remaining_principal
            if loan.rate is not None:
                weighted_num += calc.remaining_principal * Decimal(str(loan.rate))
            if loan.maturity_date and (
                detail.next_maturity_date is None or loan.maturity_date < detail.next_maturity_date
            ):
                detail.next_maturity_date = loan.maturity_date

        for pay_date, amount in loan_interest.period_accrual_series(
            principal=Decimal(str(loan.principal)),
            rate=Decimal(str(loan.rate)) if loan.rate is not None else None,
            start_date=loan.start_date,
            maturity_date=loan.maturity_date,
            payments=lites,
            since=since,
            until=loan.maturity_date or p_end,
        ):
            periods[pay_date] = periods.get(pay_date, Decimal("0")) + amount

    # «Занёс» — только новые деньги; «вернули» — то, что физически ушло назад
    # (новые деньги минус то, что до сих пор в займе). Сумма всех тел и сумма
    # всех PRINCIPAL_REPAY для этого не годятся: продления двоят и то, и другое.
    detail.principal_total = compute_new_money(loans, pay_by_loan)
    detail.principal_repaid = max(Decimal("0"), detail.principal_total - detail.outstanding)
    detail.is_archived = detail.active_count == 0
    if detail.outstanding > 0:
        detail.weighted_avg_rate = (weighted_num / detail.outstanding).quantize(Decimal("0.0001"))
    detail.loans = await _enrich_loans(db, loans)
    detail.periods = [
        LenderPeriodPoint(
            period_end=pay_date,
            period_start=loan_interest.add_months(pay_date, -1),
            interest=amount.quantize(Decimal("0.01")),
            is_current=pay_date == p_end,
            is_forecast=pay_date > p_end,
        )
        for pay_date, amount in sorted(periods.items())
    ]
    return detail


# ─── Зависшие займы ──────────────────────────────────────────────────────────


async def list_stuck_loans(db: AsyncSession, *, project_id: int) -> LoanStuckResponse:
    """
    Займы, по которым срок вышел, а решения не приняли: ни возврата, ни продления.

    Продление закрывает предшественника, полный возврат тоже — поэтому «зависший»
    = живой займ с истёкшим сроком и ненулевым остатком. Проценты на нём капают
    дальше, поэтому показываем, сколько набежало уже ПОСЛЕ срока.
    """
    as_of = _today()
    loans = list(
        (
            await db.execute(
                select(Loan)
                .where(
                    Loan.project_id == project_id,
                    Loan.is_deleted == False,  # noqa: E712
                    Loan.status == "ACTIVE",
                    Loan.maturity_date.is_not(None),
                    Loan.maturity_date <= as_of,
                )
                .order_by(Loan.maturity_date)
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    if not loans:
        return LoanStuckResponse()

    pay_rows = list(
        (
            await db.execute(
                select(LoanPayment).where(LoanPayment.loan_id.in_([loan.id for loan in loans])).limit(5000)
            )
        )
        .scalars()
        .all()
    )
    pay_by_loan: dict[int, list[LoanPayment]] = {}
    for pay in pay_rows:
        pay_by_loan.setdefault(pay.loan_id, []).append(pay)

    enriched = {resp.id: resp for resp in await _enrich_loans(db, loans)}
    items: list[LoanStuckItem] = []
    total_amount = Decimal("0")
    total_accrued = Decimal("0")
    for loan in loans:
        resp = enriched.get(loan.id)
        if resp is None or loan.maturity_date is None:
            continue
        remaining = Decimal(str(resp.remaining_principal or 0))
        if remaining <= 0:
            continue  # тело вернули, статус просто не переключили
        since_maturity = loan_interest.accrued_in_window(
            principal=Decimal(str(loan.principal)),
            rate=Decimal(str(loan.rate)) if loan.rate is not None else None,
            start_date=loan.start_date,
            payments=_to_payment_lite(pay_by_loan.get(loan.id, [])),
            win_start=loan.maturity_date,
            win_end=as_of,
        )
        total_amount += remaining
        total_accrued += since_maturity
        items.append(
            LoanStuckItem(
                loan=resp,
                days_overdue=(as_of - loan.maturity_date).days,
                accrued_since_maturity=since_maturity,
            )
        )
    return LoanStuckResponse(
        items=items,
        total_amount=total_amount,
        total_accrued_since_maturity=total_accrued,
        count=len(items),
    )


# ─── Кредитная линия ─────────────────────────────────────────────────────────


def _rate_periods_lite(rows: list[LoanRatePeriod]) -> list[loan_interest.RatePeriod]:
    return [
        loan_interest.RatePeriod(valid_from=rp.valid_from, rate=Decimal(str(rp.rate)))
        for rp in sorted(rows, key=lambda x: x.valid_from)
    ]


def _payment_due(loan: Loan, period_end: date) -> date:
    """
    Когда платят за период. ВБ Банк списывает 5-го числа СЛЕДУЮЩЕГО месяца
    (факт: 05.06 за май, 06.07 за июнь), а по частным займам платёж совпадает
    с концом периода 25→25.
    """
    if loan.payment_day is None:
        return period_end
    nxt = loan_interest.add_months(date(period_end.year, period_end.month, 1), 1)
    return loan_interest._anchor(nxt.year, nxt.month, loan.payment_day)


def loan_period(loan: Loan, as_of: date) -> tuple[date, date]:
    """Период начисления по календарю займа: банк — месяц, частник — 25→25."""
    return _line_period(loan, as_of)


def _line_period(loan: Loan, as_of: date) -> tuple[date, date]:
    """Период начисления линии: банк считает по календарному месяцу, частник — 25→25."""
    if loan.accrual_kind == "CALENDAR_MONTH":
        first = date(as_of.year, as_of.month, 1)
        nxt = loan_interest.add_months(first, 1)
        # Начисление идёт за дни ПОСЛЕ левой границы, поэтому границей месяца
        # берём последний день предыдущего: так первый день месяца не теряется.
        return date.fromordinal(first.toordinal() - 1), date.fromordinal(nxt.toordinal() - 1)
    return loan_interest.accrual_period(as_of)


async def get_credit_line(db: AsyncSession, *, loan_id: int, project_id: int) -> CreditLineDetail:
    """
    Состояние кредитной линии: лимит, выбрано, свободно, движения и ставки.

    Тело линии = Σ выборок − Σ погашений (у линии `principal` не используется как
    тело: деньги приходят траншами). Проценты считает общий движок — он умеет и
    ступенчатое тело, и смену ставки внутри периода.
    """
    loan = (
        await db.execute(
            select(Loan).where(
                Loan.id == loan_id,
                Loan.project_id == project_id,
                Loan.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if loan is None:
        raise HTTPException(status_code=404, detail="Займ не найден")
    if loan.loan_kind != "CREDIT_LINE":
        raise HTTPException(status_code=400, detail="Это не кредитная линия")

    payments = list(
        (
            await db.execute(
                select(LoanPayment)
                .where(LoanPayment.loan_id == loan.id)
                .order_by(LoanPayment.paid_at, LoanPayment.id)
                .limit(2000)
            )
        )
        .scalars()
        .all()
    )
    rates = list(
        (
            await db.execute(
                select(LoanRatePeriod)
                .where(LoanRatePeriod.loan_id == loan.id)
                .order_by(LoanRatePeriod.valid_from)
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    cp_name = (
        await db.execute(select(Counterparty.name).where(Counterparty.id == loan.counterparty_id))
    ).scalar_one_or_none()

    as_of = _today()
    p_start, p_end = _line_period(loan, as_of)
    lites = _to_payment_lite(payments)
    rate_lite = _rate_periods_lite(rates)

    balance = Decimal("0")
    movements: list[CreditLineMovement] = []
    for pay in payments:
        amount = Decimal(str(pay.amount or 0))
        if pay.payment_type == "DISBURSEMENT":
            balance += amount
        elif pay.payment_type == "PRINCIPAL_REPAY":
            balance -= amount
        else:
            continue
        movements.append(
            CreditLineMovement(
                payment_id=pay.id,
                kind=pay.payment_type,
                amount=amount,
                happened_at=pay.paid_at,
                balance_after=balance,
            )
        )

    drawn = max(Decimal("0"), balance)
    limit = Decimal(str(loan.credit_limit)) if loan.credit_limit is not None else None
    current_rate = rate_lite[-1].rate if rate_lite else (
        Decimal(str(loan.rate)) if loan.rate is not None else None
    )
    for rp in rate_lite:
        if rp.valid_from <= as_of:
            current_rate = rp.rate

    def fee(win_end: date) -> Decimal:
        if loan.credit_limit is None or loan.unused_limit_rate is None:
            return Decimal("0")
        return loan_interest.unused_limit_fee(
            credit_limit=Decimal(str(loan.credit_limit)),
            rate=Decimal(str(loan.unused_limit_rate)),
            payments=lites,
            win_start=p_start,
            win_end=win_end,
            start_date=loan.start_date,
            end_date=loan.maturity_date,
        )

    def accrue(win_end: date) -> Decimal:
        return loan_interest.accrued_in_window(
            principal=Decimal("0"),
            rate=Decimal(str(loan.rate)) if loan.rate is not None else None,
            start_date=loan.start_date,
            payments=lites,
            win_start=p_start,
            win_end=win_end,
            rate_periods=rate_lite,
        )

    return CreditLineDetail(
        loan_id=loan.id,
        contract_number=loan.contract_number,
        counterparty_name=cp_name,
        credit_limit=limit,
        drawn=drawn,
        available=(limit - drawn) if limit is not None else None,
        utilization=(drawn / limit).quantize(Decimal("0.0001")) if limit and limit > 0 else None,
        current_rate=current_rate,
        accrual_kind=loan.accrual_kind,
        accrual_period_start=p_start,
        accrual_period_end=p_end,
        accrued_interest=accrue(min(as_of, p_end)),
        interest_due_period=accrue(p_end),
        interest_paid=sum(
            (Decimal(str(p.amount or 0)) for p in payments if p.payment_type == "INTEREST_PAY"),
            Decimal("0"),
        ),
        unused_limit_rate=Decimal(str(loan.unused_limit_rate))
        if loan.unused_limit_rate is not None
        else None,
        unused_fee_accrued=fee(min(as_of, p_end)),
        unused_fee_period=fee(p_end),
        commission_paid=sum(
            (Decimal(str(p.amount or 0)) for p in payments if p.payment_type == "COMMISSION"),
            Decimal("0"),
        ),
        total_cost_period=accrue(p_end) + fee(p_end),
        payment_due_date=_payment_due(loan, p_end),
        status=loan.status,
        maturity_date=loan.maturity_date,
        movements=movements,
        rate_periods=[LoanRatePeriodResponse.model_validate(rp) for rp in rates],
    )


async def draw_credit_line(
    db: AsyncSession, *, loan_id: int, project_id: int, data: CreditLineDraw
) -> CreditLineDetail:
    """Выборка транша: увеличивает тело линии. Проверяет лимит."""
    loan = (
        await db.execute(
            select(Loan).where(
                Loan.id == loan_id,
                Loan.project_id == project_id,
                Loan.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if loan is None:
        raise HTTPException(status_code=404, detail="Займ не найден")
    if loan.loan_kind != "CREDIT_LINE":
        raise HTTPException(status_code=400, detail="Выборка возможна только по кредитной линии")
    if loan.status != "ACTIVE":
        raise HTTPException(status_code=400, detail="Линия закрыта")
    if data.drawn_at < loan.start_date:
        raise HTTPException(status_code=400, detail="Дата выборки раньше начала линии")

    if loan.credit_limit is not None:
        moves = (
            await db.execute(
                select(LoanPayment.payment_type, LoanPayment.amount).where(
                    LoanPayment.loan_id == loan.id
                )
            )
        ).all()
        drawn = sum(
            (
                Decimal(str(a or 0)) if t == "DISBURSEMENT" else -Decimal(str(a or 0))
                for t, a in moves
                if t in ("DISBURSEMENT", "PRINCIPAL_REPAY")
            ),
            Decimal("0"),
        )
        free = Decimal(str(loan.credit_limit)) - max(Decimal("0"), drawn)
        if data.amount > free:
            raise HTTPException(
                status_code=400,
                detail=f"Выборка {data.amount} превышает свободный лимит {free}",
            )

    db.add(
        LoanPayment(
            loan_id=loan.id,
            payment_type="DISBURSEMENT",
            amount=data.amount,
            currency=loan.currency,
            paid_at=data.drawn_at,
        )
    )
    await db.commit()
    await invalidate_project_reports(project_id)
    return await get_credit_line(db, loan_id=loan_id, project_id=project_id)


async def set_rate_period(
    db: AsyncSession, *, loan_id: int, project_id: int, data: LoanRatePeriodIn
) -> CreditLineDetail:
    """Добавить/обновить ставку с даты. Повтор той же даты перезаписывает строку."""
    loan = (
        await db.execute(
            select(Loan).where(
                Loan.id == loan_id,
                Loan.project_id == project_id,
                Loan.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if loan is None:
        raise HTTPException(status_code=404, detail="Займ не найден")

    existing = (
        await db.execute(
            select(LoanRatePeriod).where(
                LoanRatePeriod.loan_id == loan.id,
                LoanRatePeriod.valid_from == data.valid_from,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.rate = data.rate
        existing.base_rate = data.base_rate
        existing.spread = data.spread
        existing.note = data.note
    else:
        db.add(
            LoanRatePeriod(
                loan_id=loan.id,
                valid_from=data.valid_from,
                rate=data.rate,
                base_rate=data.base_rate,
                spread=data.spread,
                note=data.note,
            )
        )
    await db.commit()
    await invalidate_project_reports(project_id)
    return await get_credit_line(db, loan_id=loan_id, project_id=project_id)


async def list_credit_lines(db: AsyncSession, *, project_id: int) -> CreditLineListResponse:
    """Все кредитные линии проекта со сводкой по лимитам и платежу за период."""
    ids = list(
        (
            await db.execute(
                select(Loan.id)
                .where(
                    Loan.project_id == project_id,
                    Loan.is_deleted == False,  # noqa: E712
                    Loan.loan_kind == "CREDIT_LINE",
                )
                .order_by(Loan.start_date.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    items = [await get_credit_line(db, loan_id=i, project_id=project_id) for i in ids]
    return CreditLineListResponse(
        items=items,
        total_limit=sum((i.credit_limit or Decimal("0") for i in items), Decimal("0")),
        total_drawn=sum((i.drawn for i in items), Decimal("0")),
        total_available=sum((i.available or Decimal("0") for i in items), Decimal("0")),
        total_due_period=sum((i.interest_due_period for i in items), Decimal("0")),
    )
