"""
Loan interest engine — чистые расчёты процентов (без БД, легко тестируется).

Модель из реестра пользователя: простой процент, факт/365, ставка годовая (доля:
0.28 = 28 %). Проценты начисляются на остаток тела и выплачиваются ежемесячно;
тело возвращается в конце срока (bullet). На активных займах без частичных
возвратов остаток == исходному телу, поэтому начисление точно повторяет реестр.

Все денежные значения — Decimal; день — `datetime.date`.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0")
_CENTS = Decimal("0.01")
DAYS_PER_YEAR = Decimal("365")

# День месяца, которым режется период начисления процентов. Проценты у нас
# считаются НЕ от годовщины выдачи займа, а по календарной сетке «25 → 25»:
# период (25 прошлого месяца → 25 текущего], метка периода = дата выплаты.
# Сверено с боевым реестром: период «25.06.2026» по Прохорову = 262 863,01 ₽
# копейка-в-копейку (34 заёмщика из 35). Переопределяется настройкой проекта
# `loan_accrual_day`.
ACCRUAL_DAY = 25


def _q(value: Decimal) -> Decimal:
    """Округление денег до копеек."""
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _anchor(year: int, month: int, day: int) -> date:
    """Дата `day`-го числа в месяце, с клампом к длине месяца (31 → 30/28)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def accrual_period(as_of: date, day: int = ACCRUAL_DAY) -> tuple[date, date]:
    """
    Период начисления, содержащий `as_of`: (начало, конец].

    Конец — дата выплаты процентов и одновременно метка периода: период
    «25.08» = 25.07 → 25.08. Якоря считаются от (год, месяц), а не сдвигом
    предыдущей даты, иначе при `day=31` границы уползают (28 фев → 28 мар).
    """
    if as_of >= _anchor(as_of.year, as_of.month, day):
        y, m = as_of.year, as_of.month
    else:
        prev = add_months(date(as_of.year, as_of.month, 1), -1)
        y, m = prev.year, prev.month
    start = _anchor(y, m, day)
    nxt = add_months(date(y, m, 1), 1)
    return start, _anchor(nxt.year, nxt.month, day)


def accrued_in_window(
    *,
    principal: Decimal,
    rate: Decimal | None,
    start_date: date,
    payments: list[PaymentLite] | None,
    win_start: date,
    win_end: date,
) -> Decimal:
    """
    Проценты за окно [win_start, win_end) на ФАКТИЧЕСКИЙ остаток тела.

    Тело ступенчато падает на каждом PRINCIPAL_REPAY, поэтому займ, погашенный
    в середине периода, начисляет ровно за дни, что был жив (боевой кейс:
    Семериков вернул 3 млн на второй день периода → 4 602,74 ₽, а не полный
    месяц и не ноль).
    """
    r = Decimal(str(rate)) if rate is not None else ZERO
    if r <= ZERO or principal <= ZERO:
        return ZERO
    lo = max(win_start, start_date)
    if win_end <= lo:
        return ZERO

    repays = sorted(
        (
            (p.paid_at, Decimal(str(p.amount or 0)))
            for p in (payments or [])
            if p.payment_type == "PRINCIPAL_REPAY"
        ),
        key=lambda x: x[0],
    )
    outstanding = principal - sum((amt for d, amt in repays if d <= lo), ZERO)
    total = ZERO
    cursor = lo
    for d, amt in repays:
        if d <= lo or d >= win_end:
            continue
        if outstanding > ZERO:
            total += outstanding * r * Decimal((d - cursor).days) / DAYS_PER_YEAR
        outstanding -= amt
        cursor = d
    if outstanding > ZERO:
        total += outstanding * r * Decimal((win_end - cursor).days) / DAYS_PER_YEAR
    return _q(max(ZERO, total))


def add_months(d: date, months: int) -> date:
    """Прибавить месяцы, клампя день к длине месяца (31 янв + 1 мес → 28/29 фев)."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@dataclass
class PaymentLite:
    """Лёгкое представление LoanPayment для движка (без ORM)."""

    payment_type: str  # DISBURSEMENT / PRINCIPAL_REPAY / INTEREST_PAY / PENALTY
    amount: Decimal
    paid_at: date


@dataclass
class LoanCalc:
    """Результат расчёта по одному займу на дату `as_of`."""

    remaining_principal: Decimal = ZERO  # тело за вычетом возвратов
    principal_repaid: Decimal = ZERO
    interest_paid: Decimal = ZERO
    accrued_interest: Decimal = ZERO  # начислено в текущем периоде 25→25 до as_of
    interest_due_period: Decimal = ZERO  # за ВЕСЬ текущий период — платёж на дату выплаты
    accrual_period_start: date | None = None  # начало периода (25-е прошлого месяца)
    accrual_period_end: date | None = None  # дата выплаты = метка периода
    accrued_interest_total: Decimal = ZERO  # начислено с начала срока до as_of
    monthly_interest: Decimal = ZERO  # остаток × ставка / 12 (run-rate)
    daily_interest: Decimal = ZERO  # остаток × ставка / 365
    total_interest_projected: Decimal = ZERO  # за весь срок (start→maturity) на исходное тело
    days_elapsed: int = 0  # от start_date до as_of
    days_to_maturity: int | None = None  # до возврата (отрицательное = просрочка)
    next_interest_date: date | None = None
    last_interest_date: date | None = None
    is_overdue: bool = False


def compute_loan(
    *,
    principal: Decimal,
    rate: Decimal | None,
    start_date: date,
    maturity_date: date | None,
    status: str,
    payments: list[PaymentLite] | None = None,
    as_of: date,
    accrual_day: int = ACCRUAL_DAY,
    period: tuple[date, date] | None = None,
) -> LoanCalc:
    """Полный расчёт по займу. `rate` — годовая доля (0.28). CLOSED → нули по остатку."""
    payments = payments or []
    r = Decimal(str(rate)) if rate is not None else ZERO

    principal_repaid = sum((p.amount for p in payments if p.payment_type == "PRINCIPAL_REPAY"), ZERO)
    interest_paid = sum((p.amount for p in payments if p.payment_type == "INTEREST_PAY"), ZERO)

    closed = status in ("CLOSED",)
    remaining = ZERO if closed else max(ZERO, principal - principal_repaid)

    calc = LoanCalc(
        remaining_principal=_q(remaining),
        principal_repaid=_q(principal_repaid),
        interest_paid=_q(interest_paid),
    )

    # Срок / просрочка
    calc.days_elapsed = max(0, (as_of - start_date).days)
    if maturity_date is not None:
        calc.days_to_maturity = (maturity_date - as_of).days
        calc.is_overdue = (not closed) and maturity_date < as_of and remaining > ZERO

    # Период начисления 25→25. Считаем ВСЕГДА, в том числе для закрытых займов:
    # погашенный в середине периода начисляет за дни, что был жив. Исключение —
    # CLOSED без единой строки возврата: дату закрытия взять неоткуда, начислять
    # нечего (иначе закрытый «вручную» займ капал бы проценты вечно).
    # `period` — явный выбор периода (исторический срез «что было к выплате 25.07»).
    # Начисление всё равно ограничиваем сегодняшним днём: у закрытого периода это
    # даёт полную сумму (начислено == к выплате), у текущего — набежавшее на сейчас.
    p_start, p_end = period if period is not None else accrual_period(as_of, accrual_day)
    calc.accrual_period_start = p_start
    calc.accrual_period_end = p_end
    if r > ZERO and (not closed or any(p.payment_type == "PRINCIPAL_REPAY" for p in payments)):
        calc.accrued_interest = accrued_in_window(
            principal=principal,
            rate=r,
            start_date=start_date,
            payments=payments,
            win_start=p_start,
            win_end=min(as_of, p_end),
        )
        calc.interest_due_period = accrued_in_window(
            principal=principal,
            rate=r,
            start_date=start_date,
            payments=payments,
            win_start=p_start,
            win_end=p_end,
        )

    if closed or r <= ZERO or remaining <= ZERO:
        return calc

    # Контрактные проценты за весь срок — только для активного займа (на остаток
    # тела × оставшийся/полный срок). Для CLOSED = 0, чтобы цепочка продлений не
    # двоила прогноз (предшественник закрыт → его проекция не учитывается).
    if maturity_date is not None and maturity_date > start_date:
        term_days = Decimal((maturity_date - start_date).days)
        calc.total_interest_projected = _q(remaining * r * term_days / DAYS_PER_YEAR)

    calc.daily_interest = _q(remaining * r / DAYS_PER_YEAR)
    calc.monthly_interest = _q(remaining * r / Decimal("12"))

    # Накопленные проценты с начала срока (на текущий остаток — для bullet точно)
    elapsed = Decimal(max(0, (as_of - start_date).days))
    calc.accrued_interest_total = _q(remaining * r * elapsed / DAYS_PER_YEAR)

    interest_dates = sorted(p.paid_at for p in payments if p.payment_type == "INTEREST_PAY")
    calc.last_interest_date = interest_dates[-1] if interest_dates else None
    # Проценты платятся в конце периода начисления — это и есть дата выплаты.
    calc.next_interest_date = p_end

    return calc


def _next_monthly(anchor: date, as_of: date) -> date:
    """Ближайшая ежемесячная годовщина дня `anchor` строго после `as_of`."""
    months = (as_of.year - anchor.year) * 12 + (as_of.month - anchor.month)
    candidate = add_months(anchor, months)
    while candidate <= as_of:
        months += 1
        candidate = add_months(anchor, months)
    return candidate


def period_accrual_series(
    *,
    principal: Decimal,
    rate: Decimal | None,
    start_date: date,
    maturity_date: date | None,
    payments: list[PaymentLite] | None,
    since: date,
    until: date,
    accrual_day: int = ACCRUAL_DAY,
) -> list[tuple[date, Decimal]]:
    """
    Проценты по периодам 25→25 в диапазоне [since, until]: [(дата выплаты, сумма)].

    Работает и назад (факт), и вперёд (прогноз): будущие периоды считаются на
    остаток тела до `maturity_date`. Ключ — дата выплаты, она же метка периода.
    """
    r = Decimal(str(rate)) if rate is not None else ZERO
    if r <= ZERO or principal <= ZERO or until < since:
        return []

    out: list[tuple[date, Decimal]] = []
    p_start, p_end = accrual_period(since, accrual_day)
    guard = 0
    while p_start <= until and guard < 600:
        guard += 1
        win_end = p_end
        if maturity_date is not None and maturity_date < win_end:
            win_end = maturity_date
        amount = accrued_in_window(
            principal=principal,
            rate=r,
            start_date=start_date,
            payments=payments,
            win_start=p_start,
            win_end=win_end,
        )
        if amount > ZERO:
            out.append((p_end, amount))
        p_start = p_end
        nxt = add_months(date(p_end.year, p_end.month, 1), 1)
        p_end = _anchor(nxt.year, nxt.month, accrual_day)
    return out


def _recent_monthly(anchor: date, as_of: date) -> date:
    """Последняя ежемесячная годовщина дня `anchor` НЕ позже `as_of`."""
    months = (as_of.year - anchor.year) * 12 + (as_of.month - anchor.month)
    candidate = add_months(anchor, months)
    while candidate > as_of:
        months -= 1
        candidate = add_months(anchor, months)
    return candidate


def monthly_accrual_schedule(
    *,
    remaining_principal: Decimal,
    rate: Decimal | None,
    start_date: date,
    maturity_date: date | None,
    as_of: date,
    horizon_months: int = 12,
) -> list[tuple[str, Decimal]]:
    """
    Помесячное начисление процентов вперёд (для прогноза), от месяца `as_of`
    до min(maturity, as_of+horizon). Возвращает [(YYYY-MM, interest), ...].
    """
    r = Decimal(str(rate)) if rate is not None else ZERO
    if r <= ZERO or remaining_principal <= ZERO:
        return []

    end = add_months(date(as_of.year, as_of.month, 1), horizon_months)
    if maturity_date is not None:
        m_end = add_months(date(maturity_date.year, maturity_date.month, 1), 1)
        if m_end < end:
            end = m_end

    out: list[tuple[str, Decimal]] = []
    cur = date(as_of.year, as_of.month, 1)
    while cur < end:
        nxt = add_months(cur, 1)
        # Окно начисления внутри месяца, пересечённое со сроком займа
        win_start = max(cur, start_date, as_of)
        win_end = min(nxt, maturity_date) if maturity_date is not None else nxt
        days = (win_end - win_start).days
        if days > 0:
            interest = _q(remaining_principal * r * Decimal(days) / DAYS_PER_YEAR)
            out.append((cur.strftime("%Y-%m"), interest))
        cur = nxt
    return out


@dataclass
class PortfolioRollup:
    """Свод по набору займов (для KPI/заёмщиков)."""

    active_count: int = 0
    total_count: int = 0
    outstanding: Decimal = ZERO
    accrued_interest: Decimal = ZERO
    interest_paid: Decimal = ZERO
    monthly_interest: Decimal = ZERO
    weighted_rate_num: Decimal = ZERO  # Σ(остаток×ставка) — для средневзвешенной
    rates: list[Decimal] = field(default_factory=list)

    @property
    def weighted_avg_rate(self) -> Decimal | None:
        if self.outstanding <= ZERO:
            return None
        return (self.weighted_rate_num / self.outstanding).quantize(Decimal("0.0001"))
