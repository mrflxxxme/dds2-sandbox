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


def _q(value: Decimal) -> Decimal:
    """Округление денег до копеек."""
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


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
    accrued_interest: Decimal = ZERO  # начислено с последней выплаты % до as_of
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

    # Текущий период: с последней выплаты % (если есть) или с последней
    # ежемесячной годовщины — % платятся ежемесячно, поэтому «начислено» = долг
    # за текущий месяц, а не за весь срок (совпадает со снимком реестра).
    interest_dates = sorted(p.paid_at for p in payments if p.payment_type == "INTEREST_PAY")
    calc.last_interest_date = interest_dates[-1] if interest_dates else None
    anchor = _recent_monthly(start_date, as_of)
    if interest_dates and interest_dates[-1] > anchor:
        anchor = interest_dates[-1]
    if anchor < start_date:
        anchor = start_date
    since = Decimal(max(0, (as_of - anchor).days))
    calc.accrued_interest = _q(remaining * r * since / DAYS_PER_YEAR)

    # Следующая ежемесячная дата выплаты процентов (по дню start_date)
    calc.next_interest_date = _next_monthly(start_date, as_of)

    return calc


def _next_monthly(anchor: date, as_of: date) -> date:
    """Ближайшая ежемесячная годовщина дня `anchor` строго после `as_of`."""
    months = (as_of.year - anchor.year) * 12 + (as_of.month - anchor.month)
    candidate = add_months(anchor, months)
    while candidate <= as_of:
        months += 1
        candidate = add_months(anchor, months)
    return candidate


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
