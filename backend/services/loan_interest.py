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


@dataclass
class RatePeriod:
    """Ставка, действующая С даты `valid_from` (годовая доля: 0.195 = 19.5 %)."""

    valid_from: date
    rate: Decimal


def accrued_in_window(
    *,
    principal: Decimal,
    rate: Decimal | None,
    start_date: date,
    payments: list[PaymentLite] | None,
    win_start: date,
    win_end: date,
    rate_periods: list[RatePeriod] | None = None,
) -> Decimal:
    """
    Проценты за окно [win_start, win_end) на ФАКТИЧЕСКИЙ остаток тела.

    Тело меняется ступенчато: падает на PRINCIPAL_REPAY и РАСТЁТ на DISBURSEMENT
    (выборка транша по кредитной линии). Поэтому займ, погашенный в середине
    периода, начисляет ровно за дни, что был жив (Семериков вернул 3 млн на
    второй день периода → 4 602,74 ₽), а ВКЛ — по фактически выбранной сумме.

    `rate_periods` — история ставок для плавающих: ставка может смениться прямо
    внутри окна. Сверено с выпиской ВБ Банка: июнь 2026 по шести траншам сошёлся
    копейка-в-копейку при 19.5 % до 21.06 и 19.25 % с 22.06. Пусто → берём `rate`.
    """
    base = Decimal(str(rate)) if rate is not None else ZERO
    periods = sorted(rate_periods or [], key=lambda x: x.valid_from)
    if base <= ZERO and not periods:
        return ZERO
    if principal <= ZERO and not any(
        p.payment_type == "DISBURSEMENT" for p in (payments or [])
    ):
        return ZERO
    lo = max(win_start, start_date)
    if win_end <= lo:
        return ZERO

    def rate_for_segment(seg_start: date) -> Decimal:
        """
        Ставка сегмента [seg_start, …). Проценты капают за дни ПОСЛЕ seg_start,
        поэтому ставку берём на день seg_start + 1 — иначе день смены ставки
        уедет под старый процент (расхождение с банком ровно в один день).
        """
        day = date.fromordinal(seg_start.toordinal() + 1)
        current = base
        for rp in periods:
            if rp.valid_from <= day:
                current = Decimal(str(rp.rate))
            else:
                break
        return current

    # Движения тела: возврат уменьшает, выборка увеличивает
    moves: list[tuple[date, Decimal]] = []
    for p in payments or []:
        amount = Decimal(str(p.amount or 0))
        if p.payment_type == "PRINCIPAL_REPAY":
            moves.append((p.paid_at, -amount))
        elif p.payment_type == "DISBURSEMENT":
            moves.append((p.paid_at, amount))
    moves.sort(key=lambda x: x[0])

    outstanding = principal + sum((delta for d, delta in moves if d <= lo), ZERO)
    # Точки разрыва: движения тела и смены ставки. Смену ставки ставим на день
    # РАНЬШЕ `valid_from`: сегмент начисляет за дни после своей левой границы,
    # так что разрыв в valid_from−1 оставляет сам день смены уже под новой ставкой.
    rate_breaks = {
        date.fromordinal(rp.valid_from.toordinal() - 1)
        for rp in periods
        if lo < date.fromordinal(rp.valid_from.toordinal() - 1) < win_end
    }
    breaks = sorted({d for d, _ in moves if lo < d < win_end} | rate_breaks)
    total = ZERO
    cursor = lo
    for point in breaks:
        if outstanding > ZERO:
            total += (
                outstanding
                * rate_for_segment(cursor)
                * Decimal((point - cursor).days)
                / DAYS_PER_YEAR
            )
        outstanding += sum((delta for d, delta in moves if d == point), ZERO)
        cursor = point
    if outstanding > ZERO:
        total += (
            outstanding
            * rate_for_segment(cursor)
            * Decimal((win_end - cursor).days)
            / DAYS_PER_YEAR
        )
    return _q(max(ZERO, total))


def unused_limit_fee(
    *,
    credit_limit: Decimal,
    rate: Decimal | None,
    payments: list[PaymentLite] | None,
    win_start: date,
    win_end: date,
    start_date: date | None = None,
    end_date: date | None = None,
) -> Decimal:
    """
    Комиссия за НЕиспользованный лимит за окно (win_start, win_end].

    Банк берёт плату за зарезервированные, но не выбранные деньги. Считается
    на остаток лимита по состоянию на ПРЕДЫДУЩИЙ день: транш перестаёт числиться
    неиспользованным со следующего дня после выборки — симметрично тому, как на
    него начинают капать проценты. В день выборки деньги «ещё не работают».

    Сверено с выпиской ВБ Банка (2 % годовых): май 10 191,78 ₽, июнь 27 123,29 ₽ —
    копейка в копейку. Без сдвига на день июнь расходился на 1 698,63 ₽.
    """
    r = Decimal(str(rate)) if rate is not None else ZERO
    # Комиссия идёт только пока линия жива: до открытия и после закрытия банк
    # ничего не резервирует. Без клампа она капала бы на весь лимит с начала
    # времён — в ОПиУ это дало бы расход в месяцах, когда договора ещё не было.
    if start_date is not None:
        win_start = max(win_start, start_date)
    if end_date is not None:
        win_end = min(win_end, end_date)
    if r <= ZERO or credit_limit <= ZERO or win_end <= win_start:
        return ZERO

    moves: list[tuple[date, Decimal]] = []
    for p in payments or []:
        amount = Decimal(str(p.amount or 0))
        if p.payment_type == "DISBURSEMENT":
            moves.append((p.paid_at, amount))
        elif p.payment_type == "PRINCIPAL_REPAY":
            moves.append((p.paid_at, -amount))

    total = ZERO
    day = win_start
    while day < win_end:
        day = date.fromordinal(day.toordinal() + 1)
        ref = date.fromordinal(day.toordinal() - 1)
        drawn = sum((delta for d, delta in moves if d <= ref), ZERO)
        free = credit_limit - max(ZERO, drawn)
        if free > ZERO:
            total += free * r / DAYS_PER_YEAR
    return _q(max(ZERO, total))


@dataclass
class ScheduleRowLite:
    """Строка планового графика для движка (без ORM)."""

    period_start: date | None
    period_end: date | None
    due_date: date
    days: int | None
    interest_due: Decimal
    principal_due: Decimal = ZERO
    is_fee: bool = False
    # Строка плана погашения накопленного долга: говорит, КОГДА платить, а не
    # сколько начислено. Для начисления невидима — проценты по ней капают
    # формулой по ставке займа.
    is_debt_plan: bool = False


def schedule_interest_in_window(
    rows: list[ScheduleRowLite] | None, *, win_start: date, win_end: date
) -> Decimal:
    """
    Проценты по ГРАФИКУ за окно (win_start, win_end].

    У аннуитета правда — график, а не формула: банк даёт «льготные» дни (у Симпл
    Финанса первые 7 дней процентов нет, за них взята комиссия за выдачу) и своё
    округление. Простая ставка × дни разошлась бы с договором.

    Внутри строки проценты линейны по дням (тело и ставка постоянны), поэтому
    делим сумму строки пропорционально дням, попавшим в окно. Строки-комиссии
    пропускаем — они учитываются как `LoanFee`, иначе расход задвоится. Строки
    плана погашения — тоже: они гасят уже начисленное, а не начисляют.
    """
    total = ZERO
    for row in rows or []:
        if row.is_fee or row.is_debt_plan or not row.interest_due:
            continue
        start = row.period_start or row.due_date
        end = row.period_end or row.due_date
        if end < start:
            continue
        span = Decimal(row.days or ((end - start).days + 1))
        if span <= ZERO:
            continue
        # Окно (win_start, win_end] — это дни [win_start+1, win_end]
        lo = max(start, date.fromordinal(win_start.toordinal() + 1))
        hi = min(end, win_end)
        overlap = (hi - lo).days + 1
        if overlap <= 0:
            continue
        total += Decimal(str(row.interest_due)) * Decimal(overlap) / span
    return _q(total)


@dataclass
class FeeLite:
    """Разовая комиссия для движка (без ORM)."""

    amount: Decimal
    charged_at: date
    amortize: bool = True
    amortize_from: date | None = None
    amortize_to: date | None = None


def fee_in_window(
    fee: FeeLite,
    *,
    win_start: date,
    win_end: date,
    fallback_from: date | None = None,
    fallback_to: date | None = None,
) -> Decimal:
    """
    Доля разовой комиссии, попадающая в окно (win_start, win_end].

    `amortize=False` — вся сумма падает в день `charged_at` (касса и расход
    совпадают). `amortize=True` — комиссия размазывается по дням окна
    амортизации: это плата за доступ к деньгам на весь срок, и целиком в один
    месяц она ломает сравнение месяцев между собой.

    Считаем через НАКОПЛЕННУЮ сумму на границах окна, а не «дневная × дни»:
    так сумма по всем месяцам ровно равна комиссии, без копеечного хвоста.
    """
    amount = Decimal(str(fee.amount or 0))
    if amount <= ZERO or win_end <= win_start:
        return ZERO
    if not fee.amortize:
        return amount if win_start < fee.charged_at <= win_end else ZERO

    lo = fee.amortize_from or fallback_from or fee.charged_at
    hi = fee.amortize_to or fallback_to
    if hi is None or hi < lo:
        # Срока нет — размазывать не по чему, ведём себя как разовый расход.
        return amount if win_start < fee.charged_at <= win_end else ZERO
    total_days = Decimal((hi - lo).days + 1)

    def cum(d: date) -> Decimal:
        if d < lo:
            return ZERO
        elapsed = Decimal((min(d, hi) - lo).days + 1)
        return _q(amount * elapsed / total_days)

    return max(ZERO, cum(win_end) - cum(win_start))


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
    effective_rate: Decimal | None = None  # ставка на as_of (из истории, если она есть)
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
    rate_periods: list[RatePeriod] | None = None,
) -> LoanCalc:
    """Полный расчёт по займу. `rate` — годовая доля (0.28). CLOSED → нули по остатку."""
    payments = payments or []
    r = Decimal(str(rate)) if rate is not None else ZERO
    if rate_periods:
        for rp in sorted(rate_periods, key=lambda x: x.valid_from):
            if rp.valid_from <= as_of:
                r = Decimal(str(rp.rate))

    principal_repaid = sum((p.amount for p in payments if p.payment_type == "PRINCIPAL_REPAY"), ZERO)
    interest_paid = sum((p.amount for p in payments if p.payment_type == "INTEREST_PAY"), ZERO)
    # Выборки по кредитной линии увеличивают тело: у линии `principal` символический,
    # деньги приходят траншами. Без этого линия везде показывала нулевой долг —
    # дашборд, реестр и топ заёмщиков считали её пустой.
    drawn = sum((p.amount for p in payments if p.payment_type == "DISBURSEMENT"), ZERO)

    closed = status in ("CLOSED",)
    remaining = ZERO if closed else max(ZERO, principal + drawn - principal_repaid)

    calc = LoanCalc(
        effective_rate=r if r > ZERO else None,
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
            rate=rate,
            start_date=start_date,
            payments=payments,
            win_start=p_start,
            win_end=min(as_of, p_end),
            rate_periods=rate_periods,
        )
        calc.interest_due_period = accrued_in_window(
            principal=principal,
            rate=rate,
            start_date=start_date,
            payments=payments,
            win_start=p_start,
            win_end=p_end,
            rate_periods=rate_periods,
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
