"""
Начисление процентов ПО ДНЯМ — расшифровка того, что считает движок.

Все своды (месяц, период выплат, ОПиУ) стоят на одном дневном начислении, но
показывают уже итог. Когда цифра расходится с банком или с бухгалтерией, спорить
можно только на уровне дня: видно тело на дату, ставку этого дня и сумму за
сутки. Именно так и находились расхождения — день смены ставки и день выборки.

Скоуп по `project_id` займа: чужой займ отдаёт 404.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.loan import Loan, LoanPayment, LoanRatePeriod
from backend.schemas.loan import LoanAccrualDay, LoanAccrualDaysResponse
from backend.services import loan_interest, loan_schedule
from backend.utils.time import utcnow

ZERO = Decimal("0")
# Больше года подневно смотреть незачем: это расшифровка спорной суммы, а не
# выгрузка. Год закрывает любой период начисления с запасом.
MAX_DAYS = 400
BODY_KINDS = ("DISBURSEMENT", "PRINCIPAL_REPAY")


async def daily_accrual(
    db: AsyncSession,
    *,
    loan_id: int,
    project_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> LoanAccrualDaysResponse:
    """Подневное начисление: тело на дату, ставка дня, проценты за сутки."""
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

    today = utcnow().date()
    # По умолчанию — текущий календарный месяц по сегодня: чаще всего спорят
    # именно про «сколько набежало в этом месяце».
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = max(loan.start_date, date(date_to.year, date_to.month, 1))
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="Конец периода раньше начала")
    if (date_to - date_from).days + 1 > MAX_DAYS:
        raise HTTPException(status_code=400, detail=f"Период больше {MAX_DAYS} дней")

    payments = list(
        (
            await db.execute(
                select(LoanPayment)
                .where(LoanPayment.loan_id == loan.id)
                .order_by(LoanPayment.paid_at, LoanPayment.id)
                .limit(5000)
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
    schedule = loan_schedule.rows_lite(
        list(
            (
                await db.execute(
                    select(loan_schedule.LoanScheduleEntry)
                    .where(loan_schedule.LoanScheduleEntry.loan_id == loan.id)
                    .order_by(loan_schedule.LoanScheduleEntry.seq)
                    .limit(600)
                )
            )
            .scalars()
            .all()
        )
    )
    has_schedule_interest = any(
        r.interest_due and not r.is_fee and not r.is_debt_plan for r in schedule
    )

    lites = [
        loan_interest.PaymentLite(p.payment_type, Decimal(str(p.amount or 0)), p.paid_at)
        for p in payments
    ]
    rate_lite = [
        loan_interest.RatePeriod(valid_from=r.valid_from, rate=Decimal(str(r.rate)))
        for r in rates
    ]

    def body_on(day: date) -> Decimal:
        """Тело на конец дня: `principal` плюс выборки минус возвраты по этот день."""
        moved = sum(
            (
                Decimal(str(p.amount or 0))
                if p.payment_type == "DISBURSEMENT"
                else -Decimal(str(p.amount or 0))
            )
            for p in payments
            if p.payment_type in BODY_KINDS and p.paid_at <= day
        )
        return max(ZERO, Decimal(str(loan.principal)) + moved)

    def rate_on(day: date) -> Decimal | None:
        current = Decimal(str(loan.rate)) if loan.rate is not None else None
        for rp in rate_lite:
            if rp.valid_from <= day:
                current = rp.rate
        return current

    rows: list[LoanAccrualDay] = []
    cumulative = ZERO
    body_days = ZERO
    day = date_from
    while day <= date_to:
        prev = day - timedelta(days=1)
        # Одно окно шириной в сутки — та же функция, что считает месяц и год,
        # поэтому сумма дней сходится со сводом бит-в-бит.
        if has_schedule_interest:
            interest = loan_interest.schedule_interest_in_window(
                schedule, win_start=prev, win_end=day
            )
        else:
            interest = loan_interest.accrued_in_window(
                principal=Decimal(str(loan.principal)),
                rate=Decimal(str(loan.rate)) if loan.rate is not None else None,
                start_date=loan.start_date,
                payments=lites,
                win_start=prev,
                win_end=day,
                rate_periods=rate_lite,
            )
        cumulative += interest
        balance = body_on(prev)
        body_days += balance
        moves = [
            p for p in payments if p.paid_at == day and p.payment_type in BODY_KINDS
        ]
        rows.append(
            LoanAccrualDay(
                date=day,
                balance=balance,
                rate=rate_on(day),
                interest=interest,
                cumulative=cumulative,
                movement=sum(
                    (
                        Decimal(str(p.amount or 0))
                        if p.payment_type == "DISBURSEMENT"
                        else -Decimal(str(p.amount or 0))
                    )
                    for p in moves
                )
                if moves
                else None,
            )
        )
        day += timedelta(days=1)

    days = len(rows) or 1
    avg_body = (body_days / Decimal(days)).quantize(Decimal("0.01"))
    effective = (
        (cumulative * loan_interest.DAYS_PER_YEAR / body_days).quantize(Decimal("0.0001"))
        if body_days > ZERO
        else None
    )
    return LoanAccrualDaysResponse(
        loan_id=loan.id,
        contract_number=loan.contract_number,
        date_from=date_from,
        date_to=date_to,
        rows=rows,
        total_interest=cumulative,
        avg_balance=avg_body,
        effective_rate=effective,
        from_schedule=has_schedule_interest,
    )


async def portfolio_daily(
    db: AsyncSession,
    *,
    project_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    direction: str = "INCOMING",
) -> LoanAccrualDaysResponse:
    """
    Подневное начисление по ВСЕМУ портфелю проекта: сколько капает каждый день.

    Тот же расчёт, что и по одному займу, только просуммированный. Отвечает на
    вопрос «сколько стоит день» — цифра, которой нет ни в одном месячном своде,
    хотя именно ей меряют, дорого ли стоят деньги прямо сейчас.
    """
    from backend.services import loan_analytics

    today = utcnow().date()
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = date(date_to.year, date_to.month, 1)
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="Конец периода раньше начала")
    if (date_to - date_from).days + 1 > MAX_DAYS:
        raise HTTPException(status_code=400, detail=f"Период больше {MAX_DAYS} дней")

    bundle = await loan_analytics._load(db, project_id, direction=direction)
    rows: list[LoanAccrualDay] = []
    cumulative = ZERO
    body_days = ZERO
    day = date_from
    while day <= date_to:
        prev = day - timedelta(days=1)
        interest = ZERO
        balance = ZERO
        movement = ZERO
        for loan in bundle.loans:
            i, f = loan_analytics.loan_cost_in_window(
                bundle, loan, win_start=prev, win_end=day
            )
            interest += i + f
            payments = bundle.payments.get(loan.id, [])
            moved = sum(
                (
                    Decimal(str(p.amount or 0))
                    if p.payment_type == "DISBURSEMENT"
                    else -Decimal(str(p.amount or 0))
                )
                for p in payments
                if p.payment_type in BODY_KINDS and p.paid_at <= prev
            )
            # Тело НА ДАТУ, а не «на сегодня»: статус — свойство текущего момента,
            # и гасить им прошлые дни значит показывать замороженную линию при
            # честно убывающих процентах. Исключаем только закрытые займы без
            # единой строки возврата — дату закрытия у них взять неоткуда.
            if loan.status != "CLOSED" or any(
                p.payment_type == "PRINCIPAL_REPAY" for p in payments
            ):
                balance += max(ZERO, Decimal(str(loan.principal)) + moved)
            movement += sum(
                (
                    Decimal(str(p.amount or 0))
                    if p.payment_type == "DISBURSEMENT"
                    else -Decimal(str(p.amount or 0))
                )
                for p in payments
                if p.payment_type in BODY_KINDS and p.paid_at == day
            )
        cumulative += interest
        body_days += balance
        rows.append(
            LoanAccrualDay(
                date=day,
                balance=balance,
                rate=None,  # у портфеля ставка не одна — смотри эффективную в итогах
                interest=interest,
                cumulative=cumulative,
                movement=movement or None,
            )
        )
        day += timedelta(days=1)

    days = len(rows) or 1
    return LoanAccrualDaysResponse(
        loan_id=0,
        contract_number=None,
        date_from=date_from,
        date_to=date_to,
        rows=rows,
        total_interest=cumulative,
        avg_balance=(body_days / Decimal(days)).quantize(Decimal("0.01")),
        effective_rate=(
            (cumulative * loan_interest.DAYS_PER_YEAR / body_days).quantize(Decimal("0.0001"))
            if body_days > ZERO
            else None
        ),
        from_schedule=False,
    )
