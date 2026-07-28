"""
Плановый график платежей и разовые комиссии по займу.

Два разных вопроса, которые нельзя мешать:
- **график** — платёжная ведомость по договору: «сколько и когда должны», и
  сверка с тем, что реально ушло со счёта;
- **комиссии** — расход, который относится ко всему сроку договора и в стоимость
  денег заходит амортизацией (см. `LoanFee`).

Всё скоупится по `project_id` займа: чужой займ отдаёт 404, а не чужие данные.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TypedDict

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_project_reports
from backend.models.loan import Loan, LoanFee, LoanPayment, LoanScheduleEntry
from backend.schemas.loan import (
    LoanFeeIn,
    LoanFeeListResponse,
    LoanFeeResponse,
    LoanScheduleReplace,
    LoanScheduleResponse,
    LoanScheduleRow,
)
from backend.services import loan_interest
from backend.utils.time import utcnow

ZERO = Decimal("0")
CENT = Decimal("0.01")
# Платёж ищет свою строку графика по ближайшей дате: по факту платят и раньше
# срока (24.04 при плане 27.04), и позже (26.05 при плане 25.05). Шире половины
# месяца искать нельзя — платёж уедет в соседний период.
MATCH_TOLERANCE_DAYS = 20
SCHEDULE_LIMIT = 600
PAYMENTS_LIMIT = 2000


def _today() -> date:
    return utcnow().date()


async def _get_loan(db: AsyncSession, loan_id: int, project_id: int) -> Loan:
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
    return loan


# ─── Лёгкие представления для движка ─────────────────────────────────────────


def rows_lite(rows: list[LoanScheduleEntry]) -> list[loan_interest.ScheduleRowLite]:
    return [
        loan_interest.ScheduleRowLite(
            period_start=r.period_start,
            period_end=r.period_end,
            due_date=r.due_date,
            days=r.days,
            interest_due=Decimal(str(r.interest_due or 0)),
            principal_due=Decimal(str(r.principal_due or 0)),
            is_fee=bool(r.is_fee),
            is_debt_plan=bool(r.is_debt_plan),
        )
        for r in rows
    ]


def fees_lite(fees: list[LoanFee]) -> list[loan_interest.FeeLite]:
    return [
        loan_interest.FeeLite(
            amount=Decimal(str(f.amount or 0)),
            charged_at=f.charged_at,
            amortize=bool(f.amortize),
            amortize_from=f.amortize_from,
            amortize_to=f.amortize_to,
        )
        for f in fees
    ]


def fees_in_window(
    loan: Loan,
    fees: list[loan_interest.FeeLite],
    *,
    win_start: date,
    win_end: date,
) -> Decimal:
    """Разовые комиссии займа, попавшие в окно (win_start, win_end]."""
    total = ZERO
    for fee in fees:
        total += loan_interest.fee_in_window(
            fee,
            win_start=win_start,
            win_end=win_end,
            fallback_from=loan.start_date,
            fallback_to=loan.maturity_date,
        )
    return total


# ─── График ──────────────────────────────────────────────────────────────────


async def _schedule_rows(db: AsyncSession, loan_id: int) -> list[LoanScheduleEntry]:
    return list(
        (
            await db.execute(
                select(LoanScheduleEntry)
                .where(LoanScheduleEntry.loan_id == loan_id)
                .order_by(LoanScheduleEntry.seq)
                .limit(SCHEDULE_LIMIT)
            )
        )
        .scalars()
        .all()
    )


class _RowFact(TypedDict):
    """
    Факт по строке графика: сколько по ней реально заплатили и когда.

    Именно TypedDict, а не `dict[str, Decimal | date | None]`: с объединением
    mypy видит в `f["principal"]` любой из трёх типов и запрещает складывать
    суммы — «Unsupported operand types for + (Decimal and date)».
    """

    principal: Decimal
    interest: Decimal
    fee: Decimal
    last: date | None


def _empty_fact() -> _RowFact:
    return {"principal": ZERO, "interest": ZERO, "fee": ZERO, "last": None}


def _match_payments(
    rows: list[LoanScheduleEntry], payments: list[LoanPayment]
) -> dict[int, _RowFact]:
    """
    Разложить фактические платежи по строкам графика — по ближайшей дате.

    Ни «по порядку», ни «по сумме» здесь не работает: платят и раньше срока, и
    позже, и с разных счетов, а суммы аннуитета одинаковые. Ближайшая плановая
    дата — единственный устойчивый признак.
    """
    facts: dict[int, _RowFact] = {r.id: _empty_fact() for r in rows}
    if not rows:
        return facts
    for pay in payments:
        if pay.payment_type == "DISBURSEMENT":
            continue
        best = min(rows, key=lambda r: abs((r.due_date - pay.paid_at).days))
        if abs((best.due_date - pay.paid_at).days) > MATCH_TOLERANCE_DAYS:
            continue
        bucket = facts[best.id]
        amount = Decimal(str(pay.amount or 0))
        if pay.payment_type == "PRINCIPAL_REPAY":
            bucket["principal"] += amount
        elif pay.payment_type == "INTEREST_PAY":
            bucket["interest"] += amount
        else:  # PENALTY / COMMISSION
            bucket["fee"] += amount
        last = bucket["last"]
        if last is None or pay.paid_at > last:
            bucket["last"] = pay.paid_at
    return facts


def _build_response(
    loan: Loan,
    rows: list[LoanScheduleEntry],
    payments: list[LoanPayment],
    fees: list[LoanFee],
    as_of: date,
) -> LoanScheduleResponse:
    facts = _match_payments(rows, payments)
    out: list[LoanScheduleRow] = []
    totals = {"principal": ZERO, "interest": ZERO, "payment": ZERO}
    paid = {"principal": ZERO, "interest": ZERO, "total": ZERO}
    overdue_count = 0
    overdue_amount = ZERO
    next_due: LoanScheduleEntry | None = None
    next_due_amount = ZERO

    for row in rows:
        f = facts.get(row.id) or _empty_fact()
        plan = Decimal(str(row.payment_total or 0))
        paid_total = f["principal"] + f["interest"] + f["fee"]
        last: date | None = f["last"]

        if paid_total >= plan - CENT and plan > ZERO:
            state = "PAID"
        elif paid_total > ZERO:
            state = "PARTIAL"
        elif row.due_date < as_of:
            state = "OVERDUE"
        else:
            state = "UPCOMING"

        if state in ("PARTIAL", "OVERDUE") and row.due_date < as_of:
            overdue_count += 1
            overdue_amount += max(ZERO, plan - paid_total)
        if state != "PAID" and next_due is None and row.due_date >= as_of:
            next_due = row
            next_due_amount = max(ZERO, plan - paid_total)

        totals["principal"] += Decimal(str(row.principal_due or 0))
        totals["interest"] += Decimal(str(row.interest_due or 0))
        totals["payment"] += plan
        paid["principal"] += f["principal"]
        paid["interest"] += f["interest"]
        paid["total"] += paid_total

        out.append(
            LoanScheduleRow(
                id=row.id,
                seq=row.seq,
                period_start=row.period_start,
                period_end=row.period_end,
                due_date=row.due_date,
                days=row.days,
                principal_due=Decimal(str(row.principal_due or 0)),
                interest_due=Decimal(str(row.interest_due or 0)),
                payment_total=plan,
                balance_after=Decimal(str(row.balance_after)) if row.balance_after is not None else None,
                is_fee=bool(row.is_fee),
                is_debt_plan=bool(row.is_debt_plan),
                note=row.note,
                principal_paid=f["principal"],
                interest_paid=f["interest"],
                fee_paid=f["fee"],
                paid_total=paid_total,
                paid_at=last,
                delta=paid_total - plan,
                days_late=(last - row.due_date).days if last is not None else None,
                state=state,
            )
        )

    # Ближайший платёж помечаем отдельно от прочих будущих — он «к оплате».
    if next_due is not None:
        for item in out:
            if item.id == next_due.id:
                item.state = "DUE" if item.state == "UPCOMING" else item.state
                break

    fee_total = sum((Decimal(str(f.amount or 0)) for f in fees), ZERO)
    return LoanScheduleResponse(
        loan_id=loan.id,
        contract_number=loan.contract_number,
        rows=out,
        total_principal=totals["principal"],
        total_interest=totals["interest"],
        total_payment=totals["payment"],
        paid_principal=paid["principal"],
        paid_interest=paid["interest"],
        paid_total=paid["total"],
        left_principal=max(ZERO, totals["principal"] - paid["principal"]),
        left_interest=max(ZERO, totals["interest"] - paid["interest"]),
        left_total=max(ZERO, totals["payment"] - paid["total"]),
        next_due_date=next_due.due_date if next_due is not None else None,
        next_due_amount=next_due_amount,
        overdue_count=overdue_count,
        overdue_amount=overdue_amount,
        total_fees=fee_total,
        # Полная стоимость договора: проценты графика (без строк-комиссий) плюс
        # сами комиссии — иначе комиссия, напечатанная в колонке процентов,
        # посчиталась бы дважды.
        total_cost=sum(
            (Decimal(str(r.interest_due or 0)) for r in rows if not r.is_fee), ZERO
        )
        + fee_total,
    )


async def get_schedule(
    db: AsyncSession, *, loan_id: int, project_id: int, as_of: date | None = None
) -> LoanScheduleResponse:
    """График платежей со сверкой факта: что заплатили, когда и с каким отклонением."""
    loan = await _get_loan(db, loan_id, project_id)
    rows = await _schedule_rows(db, loan.id)
    payments = list(
        (
            await db.execute(
                select(LoanPayment)
                .where(LoanPayment.loan_id == loan.id)
                .order_by(LoanPayment.paid_at, LoanPayment.id)
                .limit(PAYMENTS_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    fees = list(
        (await db.execute(select(LoanFee).where(LoanFee.loan_id == loan.id).limit(SCHEDULE_LIMIT)))
        .scalars()
        .all()
    )
    return _build_response(loan, rows, payments, fees, as_of or _today())


async def replace_schedule(
    db: AsyncSession, *, loan_id: int, project_id: int, data: LoanScheduleReplace
) -> LoanScheduleResponse:
    """Заменить график целиком — он приходит из договора одной таблицей."""
    loan = await _get_loan(db, loan_id, project_id)
    seqs = [r.seq for r in data.rows]
    if len(set(seqs)) != len(seqs):
        raise HTTPException(status_code=400, detail="Номера строк графика повторяются")

    for old in await _schedule_rows(db, loan.id):
        # Строка графика — не факт, а копия договора: мягко удалённая занимала бы
        # уникальный слот (loan_id, seq) и ломала перезалив.
        await db.delete(old)  # no-soft-delete-check: LoanScheduleEntry без SoftDeleteMixin
    await db.flush()

    for r in sorted(data.rows, key=lambda x: x.seq):
        db.add(
            LoanScheduleEntry(
                loan_id=loan.id,
                seq=r.seq,
                period_start=r.period_start,
                period_end=r.period_end,
                due_date=r.due_date,
                days=r.days,
                principal_due=r.principal_due,
                interest_due=r.interest_due,
                payment_total=r.payment_total,
                balance_after=r.balance_after,
                is_fee=r.is_fee,
                is_debt_plan=r.is_debt_plan,
                note=r.note,
            )
        )
    await db.commit()
    await invalidate_project_reports(project_id)
    return await get_schedule(db, loan_id=loan_id, project_id=project_id)


# ─── Разовые комиссии ────────────────────────────────────────────────────────


async def list_fees(db: AsyncSession, *, loan_id: int, project_id: int) -> LoanFeeListResponse:
    await _get_loan(db, loan_id, project_id)
    rows = list(
        (
            await db.execute(
                select(LoanFee)
                .where(LoanFee.loan_id == loan_id)
                .order_by(LoanFee.charged_at, LoanFee.id)
                .limit(SCHEDULE_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    return LoanFeeListResponse(
        items=[LoanFeeResponse.model_validate(r) for r in rows],
        total=sum((Decimal(str(r.amount or 0)) for r in rows), ZERO),
    )


async def add_fee(
    db: AsyncSession, *, loan_id: int, project_id: int, data: LoanFeeIn
) -> LoanFeeListResponse:
    """
    Завести разовую комиссию.

    Окно амортизации по умолчанию — срок займа: комиссия за выдачу и за лимит
    покупают доступ к деньгам на весь срок, а не на день уплаты.
    """
    loan = await _get_loan(db, loan_id, project_id)
    if data.payment_id is not None:
        owner = (
            await db.execute(select(LoanPayment.loan_id).where(LoanPayment.id == data.payment_id))
        ).scalar_one_or_none()
        if owner != loan.id:
            raise HTTPException(status_code=400, detail="Платёж относится к другому займу")
    db.add(
        LoanFee(
            loan_id=loan.id,
            fee_kind=data.fee_kind,
            amount=data.amount,
            charged_at=data.charged_at,
            amortize=data.amortize,
            amortize_from=data.amortize_from or (loan.start_date if data.amortize else None),
            amortize_to=data.amortize_to or (loan.maturity_date if data.amortize else None),
            payment_id=data.payment_id,
            note=data.note,
        )
    )
    await db.commit()
    await invalidate_project_reports(project_id)
    return await list_fees(db, loan_id=loan_id, project_id=project_id)


async def delete_fee(
    db: AsyncSession, *, loan_id: int, project_id: int, fee_id: int
) -> LoanFeeListResponse:
    await _get_loan(db, loan_id, project_id)
    fee = (
        await db.execute(select(LoanFee).where(LoanFee.id == fee_id, LoanFee.loan_id == loan_id))
    ).scalar_one_or_none()
    if fee is None:
        raise HTTPException(status_code=404, detail="Комиссия не найдена")
    # Удаление комиссии — исправление ввода, а не событие: мягкая копия исказила
    # бы стоимость денег.
    await db.delete(fee)  # no-soft-delete-check: LoanFee без SoftDeleteMixin
    await db.commit()
    await invalidate_project_reports(project_id)
    return await list_fees(db, loan_id=loan_id, project_id=project_id)
