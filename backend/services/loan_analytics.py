"""
Loan analytics — дашборд, свод по заёмщикам, прогноз процентов и возвратов.

Все запросы скоупятся по project_id и фильтруют soft-delete. Фокус — INCOMING
(полученные займы: наш долг + проценты к выплате), как в реестре пользователя.
Кэш: ключ включает as_of → инвалидация `invalidate_project_reports` срабатывает
(паттерн `<prefix>:project_id=<id>:*` требует хвостовой сегмент).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models.counterparty import Counterparty
from backend.models.loan import Loan, LoanFee, LoanPayment, LoanRatePeriod, LoanScheduleEntry
from backend.models.refs import ProjectSetting
from backend.services import loan_interest, loan_schedule, loan_service
from backend.services.loan_interest import add_months

ZERO = Decimal("0")
LENDER_CP_KEY_PREFIX = "lender_counterparties_"
# Defensive cap for full-project aggregation scans (loans are dozens today; this
# guards a runaway project from OOMing the worker — mirrors STOCKS_LIMIT pattern).
SCAN_LIMIT = 20000

logger = __import__("logging").getLogger(__name__)


# ─── Loader ──────────────────────────────────────────────────────────────────


@dataclass
class LoanBundle:
    """Всё, что нужно аналитике по проекту, одним снимком (без N+1)."""

    loans: list[Loan] = field(default_factory=list)
    payments: dict[int, list[LoanPayment]] = field(default_factory=dict)
    cp_map: dict[int, tuple[str, str | None]] = field(default_factory=dict)
    access_cp_ids: set[int] = field(default_factory=set)
    rates: dict[int, list[loan_interest.RatePeriod]] = field(default_factory=dict)
    # Плановый график (аннуитет) и разовые комиссии — по ним считается стоимость
    # денег там, где простая ставка врёт.
    schedule: dict[int, list[loan_interest.ScheduleRowLite]] = field(default_factory=dict)
    fees: dict[int, list[loan_interest.FeeLite]] = field(default_factory=dict)


async def _load(
    db: AsyncSession, project_id: int, direction: str = "INCOMING"
) -> LoanBundle:
    """
    Снимок займов проекта: платежи, имена контрагентов, ставки, графики, комиссии.

    `direction` по умолчанию INCOMING — «наш долг», как во всех экранах реестра.
    OUTGOING — деньги, выданные нами: у них своя арифметика (это не расход, а
    доход), поэтому смешивать их в одном своде нельзя.
    """
    loan_rows = await db.execute(
        select(Loan)
        .where(
            Loan.project_id == project_id,
            Loan.is_deleted == False,  # noqa: E712
            Loan.direction == direction,
        )
        .order_by(Loan.id)
        .limit(SCAN_LIMIT)
    )
    loans = list(loan_rows.scalars().all())
    if len(loans) >= SCAN_LIMIT:
        logger.warning("loan_analytics: project %s hit SCAN_LIMIT=%s loans — totals may be capped", project_id, SCAN_LIMIT)

    pay_by_loan: dict[int, list[LoanPayment]] = defaultdict(list)
    if loans:
        loan_ids = [loan.id for loan in loans]
        pay_rows = await db.execute(
            select(LoanPayment).where(LoanPayment.loan_id.in_(loan_ids)).limit(SCAN_LIMIT * 20)
        )
        for p in pay_rows.scalars().all():
            pay_by_loan[p.loan_id].append(p)

    cp_ids = list({loan.counterparty_id for loan in loans})
    cp_map: dict[int, tuple[str, str | None]] = {}
    if cp_ids:
        cp_rows = await db.execute(
            select(Counterparty.id, Counterparty.name, Counterparty.inn).where(Counterparty.id.in_(cp_ids))
        )
        cp_map = {row.id: (row.name, row.inn) for row in cp_rows.all()}

    # Counterparties that already have a lender portal login
    access_cp_ids: set[int] = set()
    import json

    setting_rows = await db.execute(
        select(ProjectSetting.value).where(
            ProjectSetting.project_id == project_id,
            ProjectSetting.key.like(f"{LENDER_CP_KEY_PREFIX}%"),
        )
    )
    for (raw,) in setting_rows.all():
        try:
            parsed = json.loads(raw) if raw else []
            for v in parsed if isinstance(parsed, list) else []:
                access_cp_ids.add(int(v))
        except (ValueError, TypeError):
            continue

    # История ставок — только у займов с плавающей (кредитные линии)
    rates_by_loan: dict[int, list[loan_interest.RatePeriod]] = defaultdict(list)
    sched_by_loan: dict[int, list[loan_interest.ScheduleRowLite]] = defaultdict(list)
    fees_by_loan: dict[int, list[loan_interest.FeeLite]] = defaultdict(list)
    if loans:
        loan_ids = [loan.id for loan in loans]
        rate_rows = await db.execute(
            select(LoanRatePeriod)
            .where(LoanRatePeriod.loan_id.in_(loan_ids))
            .order_by(LoanRatePeriod.valid_from)
            .limit(SCAN_LIMIT)
        )
        for rp in rate_rows.scalars().all():
            rates_by_loan[rp.loan_id].append(
                loan_interest.RatePeriod(valid_from=rp.valid_from, rate=Decimal(str(rp.rate)))
            )

        sched_rows = await db.execute(
            select(LoanScheduleEntry)
            .where(LoanScheduleEntry.loan_id.in_(loan_ids))
            .order_by(LoanScheduleEntry.loan_id, LoanScheduleEntry.seq)
            .limit(SCAN_LIMIT)
        )
        for row in sched_rows.scalars().all():
            sched_by_loan[row.loan_id].extend(loan_schedule.rows_lite([row]))

        fee_rows = await db.execute(
            select(LoanFee).where(LoanFee.loan_id.in_(loan_ids)).limit(SCAN_LIMIT)
        )
        for fee in fee_rows.scalars().all():
            fees_by_loan[fee.loan_id].extend(loan_schedule.fees_lite([fee]))

    return LoanBundle(
        loans=loans,
        payments=pay_by_loan,
        cp_map=cp_map,
        access_cp_ids=access_cp_ids,
        rates=rates_by_loan,
        schedule=sched_by_loan,
        fees=fees_by_loan,
    )


def _lites(payments: list[LoanPayment]) -> list[loan_interest.PaymentLite]:
    return [
        loan_interest.PaymentLite(
            payment_type=p.payment_type,
            amount=Decimal(str(p.amount or 0)),
            paid_at=p.paid_at,
        )
        for p in payments
    ]


def month_window(year: int, month: int, as_of: date | None = None) -> tuple[date, date]:
    """
    Окно КАЛЕНДАРНОГО месяца в конвенции движка: (последний день прошлого месяца,
    последний день месяца]. Начисление идёт за дни ПОСЛЕ левой границы, поэтому
    первое число месяца не теряется. `as_of` подрезает текущий месяц.
    """
    start = date(year, month, 1)
    end = add_months(start, 1)
    win_start = date.fromordinal(start.toordinal() - 1)
    win_end = date.fromordinal(end.toordinal() - 1)
    if as_of is not None:
        win_end = min(win_end, as_of)
    return win_start, win_end


def _schedule_has_interest(schedule: list[loan_interest.ScheduleRowLite] | None) -> bool:
    """
    Несёт ли график проценты, или в нём только тело.

    У аннуитета в графике обе части, у кредитной линии — только возврат тела:
    проценты там платят помесячно по факту выборки, и в графике их нет. Считать
    «график есть → проценты берём из него» нельзя: у линии это обнулило бы
    1,6 млн ₽ в месяц и в стоимости денег, и в ОПиУ.

    План погашения накопленного долга (`is_debt_plan`) графиком начисления тоже
    не считается — он гасит уже начисленное, а не начисляет.
    """
    return any(
        r.interest_due and not r.is_fee and not r.is_debt_plan for r in (schedule or [])
    )


def loan_cost_in_window(
    bundle: LoanBundle, loan: Loan, *, win_start: date, win_end: date
) -> tuple[Decimal, Decimal]:
    """
    Стоимость займа за окно (win_start, win_end]: (проценты, комиссии).

    Если у займа есть плановый график (аннуитет) — проценты берём из него: у
    Симпл Финанса первые 7 дней беспроцентные, и формула «остаток × ставка × дни»
    разошлась бы с договором. Комиссии — за резерв неиспользованного лимита
    (считается по дням) плюс доля разовых.
    """
    if win_end <= win_start:
        return ZERO, ZERO
    lites = _lites(bundle.payments.get(loan.id, []))
    sched = bundle.schedule.get(loan.id)
    if sched and _schedule_has_interest(sched):
        interest = loan_interest.schedule_interest_in_window(
            sched, win_start=win_start, win_end=win_end
        )
    else:
        interest = loan_interest.accrued_in_window(
            principal=Decimal(str(loan.principal)),
            rate=Decimal(str(loan.rate)) if loan.rate is not None else None,
            start_date=loan.start_date,
            payments=lites,
            win_start=win_start,
            win_end=win_end,
            rate_periods=bundle.rates.get(loan.id),
        )

    fee = ZERO
    if loan.credit_limit is not None and loan.unused_limit_rate is not None:
        fee += loan_interest.unused_limit_fee(
            credit_limit=Decimal(str(loan.credit_limit)),
            rate=Decimal(str(loan.unused_limit_rate)),
            payments=lites,
            win_start=win_start,
            win_end=win_end,
            start_date=loan.start_date,
            end_date=loan.maturity_date,
        )
    fee += loan_schedule.fees_in_window(
        loan, bundle.fees.get(loan.id, []), win_start=win_start, win_end=win_end
    )
    return interest, fee


def _lifetime_interest(bundle: LoanBundle, loan: Loan, as_of: date) -> Decimal:
    """
    Проценты, начисленные за ВСЮ жизнь займа до `as_of`.

    Левая граница — день ДО выдачи: начисление идёт за дни после неё, иначе
    первый день займа теряется. Комиссии сюда не входят — «долг по процентам»
    отвечает на вопрос «сколько процентов мы не заплатили», а комиссии живут
    своей кассой.
    """
    interest, _fee = loan_cost_in_window(
        bundle,
        loan,
        win_start=date.fromordinal(loan.start_date.toordinal() - 1),
        win_end=as_of,
    )
    return interest


def _schedule_period(
    schedule: list[loan_interest.ScheduleRowLite], as_of: date
) -> tuple[date, date] | None:
    """
    Текущий период по графику: тот, чей платёж ближайший (иначе последний).

    У аннуитета период — это строка договора (26.06 → 27.07), а не сетка 25→25.
    Левую границу сдвигаем на день назад: движок начисляет за дни ПОСЛЕ неё.
    """
    row = next((r for r in schedule if r.due_date >= as_of), None) or (
        schedule[-1] if schedule else None
    )
    if row is None:
        return None
    start = row.period_start or row.due_date
    end = row.period_end or row.due_date
    return date.fromordinal(start.toordinal() - 1), end


def _calc(
    loan: Loan,
    payments: list[LoanPayment],
    as_of: date,
    period: tuple[date, date] | None = None,
    rate_periods: list[loan_interest.RatePeriod] | None = None,
    schedule: list[loan_interest.ScheduleRowLite] | None = None,
) -> loan_interest.LoanCalc:
    if schedule and period is None:
        # У займа с графиком правда — график: банк даёт льготные дни и считает
        # своё округление, а «остаток × ставка × дни» разошлось бы с договором.
        sched_period = _schedule_period(schedule, as_of)
        if sched_period is not None:
            calc = _compute(loan, payments, as_of, sched_period, rate_periods)
            p_start, p_end = sched_period
            calc.accrued_interest = loan_interest.schedule_interest_in_window(
                schedule, win_start=p_start, win_end=min(as_of, p_end)
            )
            calc.interest_due_period = loan_interest.schedule_interest_in_window(
                schedule, win_start=p_start, win_end=p_end
            )
            nxt = next((r.due_date for r in schedule if r.due_date >= as_of), None)
            if nxt is not None:
                calc.next_interest_date = nxt
            return calc
    return _compute(loan, payments, as_of, period, rate_periods)


def _compute(
    loan: Loan,
    payments: list[LoanPayment],
    as_of: date,
    period: tuple[date, date] | None = None,
    rate_periods: list[loan_interest.RatePeriod] | None = None,
) -> loan_interest.LoanCalc:
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
        # Календарь начисления берём с самого займа: у банка месяц, у частника 25→25.
        period=period or loan_service.loan_period(loan, as_of),
        rate_periods=rate_periods,
    )


# ─── Dashboard ───────────────────────────────────────────────────────────────


@cached(prefix="loan_dashboard", ttl=300)
async def dashboard(db: AsyncSession, project_id: int, as_of: date) -> dict:
    from backend.schemas.loan import (
        LoanDashboard,
        LoanEntitySplit,
        LoanKpis,
        LoanRateBucket,
        LoanTopLender,
    )

    bundle = await _load(db, project_id)
    loans, pay_by_loan, cp_map, rates_by_loan = (
        bundle.loans,
        bundle.payments,
        bundle.cp_map,
        bundle.rates,
    )

    kpis = LoanKpis(accrual_month=as_of.strftime("%Y-%m"))
    weighted_num = ZERO
    lenders_active: set[int] = set()
    by_entity: dict[str | None, LoanEntitySplit] = {}
    by_rate: dict[Decimal, LoanRateBucket] = {}
    lender_agg: dict[int, dict] = {}
    maturities: list[tuple[date, Decimal]] = []
    body_days_mtd = ZERO
    # Дашборд отвечает на вопрос «сколько стоят деньги», поэтому считает по
    # КАЛЕНДАРНОМУ месяцу: у ВКЛ период начисления — месяц с платежом 5-го, у
    # частных займов 25→25, у аннуитета свой график. Складывать их нельзя.
    mtd_start, mtd_end = month_window(as_of.year, as_of.month, as_of)
    full_start, full_end = month_window(as_of.year, as_of.month)

    for loan in loans:
        calc = _calc(
            loan,
            pay_by_loan.get(loan.id, []),
            as_of,
            rate_periods=rates_by_loan.get(loan.id),
            schedule=bundle.schedule.get(loan.id),
        )
        kpis.interest_paid_total += calc.interest_paid
        # Стоимость денег копим по ВСЕМ займам, включая закрытые: погашенный в
        # середине месяца отработал свои дни.
        mtd_interest, mtd_fee = loan_cost_in_window(
            bundle, loan, win_start=mtd_start, win_end=mtd_end
        )
        full_interest, full_fee = loan_cost_in_window(
            bundle, loan, win_start=full_start, win_end=full_end
        )
        kpis.accrued_interest += mtd_interest
        kpis.accrued_fee += mtd_fee
        kpis.interest_debt += max(
            ZERO, _lifetime_interest(bundle, loan, as_of) - calc.interest_paid
        )
        kpis.month_cost_projected += full_interest + full_fee
        body_days_mtd += _body_days(
            loan, _lites(pay_by_loan.get(loan.id, [])), mtd_start, mtd_end
        )
        ent = loan.entity_type
        es = by_entity.setdefault(ent, LoanEntitySplit(entity_type=ent))
        es.accrued_interest += mtd_interest + mtd_fee
        es.interest_due_period += full_interest + full_fee
        if loan.status != "ACTIVE":
            continue

        out = calc.remaining_principal
        kpis.active_count += 1
        kpis.total_outstanding += out
        kpis.monthly_interest += calc.monthly_interest
        lenders_active.add(loan.counterparty_id)
        eff = calc.effective_rate
        if eff is not None:
            weighted_num += out * eff

        # by entity — тело только по активным, проценты собраны выше по всем
        es.count += 1
        es.outstanding += out

        # by rate
        if eff is not None:
            rb = by_rate.setdefault(eff, LoanRateBucket(rate=eff))
            rb.count += 1
            rb.outstanding += out

        # top lenders
        la = lender_agg.setdefault(
            loan.counterparty_id, {"out": ZERO, "accrued": ZERO, "wnum": ZERO}
        )
        la["out"] += out
        la["accrued"] += mtd_interest + mtd_fee
        if eff is not None:
            la["wnum"] += out * eff

        if loan.maturity_date is not None and out > ZERO and loan.maturity_date >= as_of:
            maturities.append((loan.maturity_date, out))

    kpis.lenders_count = len(lenders_active)
    kpis.accrued_cost = kpis.accrued_interest + kpis.accrued_fee
    if kpis.total_outstanding > ZERO:
        kpis.weighted_avg_rate = (weighted_num / kpis.total_outstanding).quantize(Decimal("0.0001"))
    # Эффективная ставка месяца: стоимость / (среднее тело × дни). Отвечает на
    # «во сколько реально обошлись деньги» — с комиссиями, а не по номиналу.
    mtd_days = Decimal((mtd_end - mtd_start).days)
    if body_days_mtd > ZERO and mtd_days > ZERO:
        kpis.effective_rate = (
            kpis.accrued_cost * loan_interest.DAYS_PER_YEAR / body_days_mtd
        ).quantize(Decimal("0.0001"))
    if maturities:
        maturities.sort(key=lambda x: x[0])
        next_date = maturities[0][0]
        kpis.next_maturity_date = next_date
        kpis.next_maturity_amount = sum((amt for d, amt in maturities if d == next_date), ZERO)

    top_lenders = []
    for cp_id, agg in lender_agg.items():
        name = cp_map.get(cp_id, (f"#{cp_id}", None))[0]
        wrate = (agg["wnum"] / agg["out"]).quantize(Decimal("0.0001")) if agg["out"] > ZERO else None
        top_lenders.append(
            LoanTopLender(
                counterparty_id=cp_id,
                name=name,
                outstanding=agg["out"],
                accrued_interest=agg["accrued"],
                weighted_avg_rate=wrate,
            )
        )
    top_lenders.sort(key=lambda x: x.outstanding, reverse=True)

    monthly = _monthly_timeline(bundle, as_of)

    result = LoanDashboard(
        kpis=kpis,
        by_entity=sorted(by_entity.values(), key=lambda x: x.outstanding, reverse=True),
        by_rate=sorted(by_rate.values(), key=lambda x: x.rate),
        monthly=monthly,
        top_lenders=top_lenders[:10],
    )
    return result.model_dump(mode="json")


def _monthly_timeline(bundle: LoanBundle, as_of: date) -> list:
    """
    По КАЛЕНДАРНЫМ месяцам: выдано / возвращено тело, остаток, стоимость денег.

    Раньше сетка была 25→25 — «период выплат» частных займов. Для ВКЛ это враньё:
    банк начисляет по календарному месяцу, а аннуитет живёт по своему графику;
    сложенные в одну точку, они дают сумму, которой не существует ни в одном
    отчёте. Календарный месяц — общий знаменатель и база для ОПиУ.

    Выборки по кредитной линии считаются выдачей: тело линии — это транши,
    а `principal` у неё символический (иначе линия рисуется нулевой).
    """
    from backend.schemas.loan import LoanMonthlyPoint

    loans = bundle.loans
    if not loans:
        return []
    disbursed: dict[str, Decimal] = defaultdict(lambda: ZERO)
    repaid: dict[str, Decimal] = defaultdict(lambda: ZERO)
    interest: dict[str, Decimal] = defaultdict(lambda: ZERO)
    fees: dict[str, Decimal] = defaultdict(lambda: ZERO)

    first = min(loan.start_date for loan in loans)
    months: list[date] = []
    cur = date(first.year, first.month, 1)
    last = date(as_of.year, as_of.month, 1)
    guard = 0
    while cur <= last and guard < 600:
        guard += 1
        months.append(cur)
        cur = add_months(cur, 1)
    keys = [m.strftime("%Y-%m") for m in months]

    def bucket(d: date) -> str:
        return d.strftime("%Y-%m")

    for loan in loans:
        payments = bundle.payments.get(loan.id, [])
        disbursed[bucket(loan.start_date)] += Decimal(str(loan.principal))
        # Возвраты зажимаем выданным телом: в реестре встречаются лишние строки
        # (Прохоров дог. 54 — две строки возврата на 2 млн при теле 1 млн), и без
        # клампа накопительный остаток на графике уезжает ниже KPI, который
        # считает остаток через max(0, тело − возвраты).
        left = Decimal(str(loan.principal))
        for p in sorted(payments, key=lambda x: x.paid_at):
            amount = Decimal(str(p.amount or 0))
            if p.payment_type == "DISBURSEMENT":
                disbursed[bucket(p.paid_at)] += amount
                left += amount
            elif p.payment_type == "PRINCIPAL_REPAY" and left > ZERO:
                capped = min(left, amount)
                repaid[bucket(p.paid_at)] += capped
                left -= capped

    for loan in loans:
        for m in months:
            win_start, win_end = month_window(m.year, m.month, as_of)
            if win_end <= win_start:
                continue
            i, f = loan_cost_in_window(bundle, loan, win_start=win_start, win_end=win_end)
            key = m.strftime("%Y-%m")
            interest[key] += i
            fees[key] += f

    out: list[LoanMonthlyPoint] = []
    running = ZERO
    current = as_of.strftime("%Y-%m")
    for m in keys:
        running += disbursed[m] - repaid[m]
        cost = interest[m] + fees[m]
        out.append(
            LoanMonthlyPoint(
                month=m,
                disbursed=disbursed[m].quantize(Decimal("0.01")),
                repaid=repaid[m].quantize(Decimal("0.01")),
                outstanding=running.quantize(Decimal("0.01")),
                interest=interest[m].quantize(Decimal("0.01")),
                fee=fees[m].quantize(Decimal("0.01")),
                cost=cost.quantize(Decimal("0.01")),
                is_partial=m == current,
            )
        )
    # последние 24 месяца — читаемый график
    return out[-24:]


# ─── By-lender (Заёмщики) ────────────────────────────────────────────────────


@cached(prefix="loan_by_lender", ttl=300)
async def by_lender(
    db: AsyncSession, project_id: int, as_of: date, period_end: date | None = None
) -> dict:
    from backend.schemas.loan import LoanByLenderResponse, LoanEntitySplit, LoanLenderRollup

    bundle = await _load(db, project_id)
    loans, pay_by_loan, cp_map = bundle.loans, bundle.payments, bundle.cp_map
    access_cp_ids, rates_by_loan = bundle.access_cp_ids, bundle.rates

    # Явный период = исторический срез: «сколько было к выплате 25.07». Начисление
    # ограничено сегодня, поэтому у закрытого периода «начислено» == «к выплате».
    period = (
        (loan_interest.add_months(period_end, -1), period_end) if period_end is not None else None
    )
    agg: dict[int, dict] = {}
    for loan in loans:
        calc = _calc(
            loan,
            pay_by_loan.get(loan.id, []),
            as_of,
            period,
            rates_by_loan.get(loan.id),
            schedule=bundle.schedule.get(loan.id),
        )
        a = agg.setdefault(
            loan.counterparty_id,
            {
                "active": 0,
                "total": 0,
                "out": ZERO,
                "wnum": ZERO,
                "accrued": ZERO,
                "due": ZERO,
                "paid": ZERO,
                "debt": ZERO,
                "monthly": ZERO,
                "next_int": None,
                "next_mat": None,
                "first": loan.start_date,
                "last": loan.start_date,
                "entity": None,
                "bank": None,
            },
        )
        a["total"] += 1
        a["paid"] += calc.interest_paid
        a["debt"] += max(ZERO, _lifetime_interest(bundle, loan, as_of) - calc.interest_paid)
        a["accrued"] += calc.accrued_interest
        a["due"] += calc.interest_due_period
        if loan.entity_type and not a["entity"]:
            a["entity"] = loan.entity_type
        a["first"] = min(a["first"], loan.start_date)
        a["last"] = max(a["last"], loan.start_date)
        if loan.lender_bank and not a["bank"]:
            a["bank"] = loan.lender_bank
        if loan.status == "ACTIVE":
            a["active"] += 1
            a["out"] += calc.remaining_principal
            a["monthly"] += calc.monthly_interest
            if calc.effective_rate is not None:
                a["wnum"] += calc.remaining_principal * calc.effective_rate
            if calc.next_interest_date and (a["next_int"] is None or calc.next_interest_date < a["next_int"]):
                a["next_int"] = calc.next_interest_date
            if loan.maturity_date and loan.maturity_date >= as_of and (
                a["next_mat"] is None or loan.maturity_date < a["next_mat"]
            ):
                a["next_mat"] = loan.maturity_date

    items: list[LoanLenderRollup] = []
    total_out = ZERO
    total_accr = ZERO
    total_due = ZERO
    total_debt = ZERO
    ent_agg: dict[str | None, LoanEntitySplit] = {}
    for cp_id, a in agg.items():
        name, inn = cp_map.get(cp_id, (f"#{cp_id}", None))
        wrate = (a["wnum"] / a["out"]).quantize(Decimal("0.0001")) if a["out"] > ZERO else None
        total_out += a["out"]
        total_accr += a["accrued"]
        total_due += a["due"]
        total_debt += a["debt"]
        es = ent_agg.setdefault(a["entity"], LoanEntitySplit(entity_type=a["entity"]))
        es.count += a["active"]
        es.outstanding += a["out"]
        es.accrued_interest += a["accrued"]
        es.interest_due_period += a["due"]
        items.append(
            LoanLenderRollup(
                counterparty_id=cp_id,
                name=name,
                inn=inn,
                entity_type=a["entity"],
                lender_bank=a["bank"],
                active_count=a["active"],
                total_count=a["total"],
                outstanding=a["out"],
                weighted_avg_rate=wrate,
                accrued_interest=a["accrued"],
                interest_paid=a["paid"],
                interest_debt=a["debt"],
                monthly_interest=a["monthly"],
                next_interest_date=a["next_int"],
                next_maturity_date=a["next_mat"],
                first_loan_date=a["first"],
                last_loan_date=a["last"],
                has_portal_access=cp_id in access_cp_ids,
                interest_due_period=a["due"],
                is_archived=a["active"] == 0,
            )
        )
    # Активные — вверх и по убыванию долга; архивные (без активных займов) — в хвост.
    items.sort(key=lambda x: (x.is_archived, -x.outstanding, x.name.lower()))
    p_start, p_end = period if period is not None else loan_interest.accrual_period(as_of)
    result = LoanByLenderResponse(
        items=items,
        total_outstanding=total_out,
        total_accrued=total_accr,
        total_due_period=total_due,
        total_interest_debt=total_debt,
        by_entity=sorted(ent_agg.values(), key=lambda x: x.outstanding, reverse=True),
        accrual_period_start=p_start,
        accrual_period_end=p_end,
        archived_count=sum(1 for i in items if i.is_archived),
    )
    return result.model_dump(mode="json")


# ─── Forecast (Прогноз) ──────────────────────────────────────────────────────


@cached(prefix="loan_forecast", ttl=300)
async def forecast(db: AsyncSession, project_id: int, as_of: date, horizon_months: int = 12) -> dict:
    from backend.schemas.loan import LoanForecastEvent, LoanForecastMonth, LoanForecastResponse

    bundle = await _load(db, project_id)
    loans, pay_by_loan, cp_map, rates_by_loan = (
        bundle.loans,
        bundle.payments,
        bundle.cp_map,
        bundle.rates,
    )

    month_interest: dict[str, Decimal] = defaultdict(lambda: ZERO)
    month_principal: dict[str, Decimal] = defaultdict(lambda: ZERO)
    upcoming: list[LoanForecastEvent] = []

    for loan in loans:
        if loan.status != "ACTIVE":
            continue
        calc = _calc(
            loan,
            pay_by_loan.get(loan.id, []),
            as_of,
            rate_periods=rates_by_loan.get(loan.id),
            schedule=bundle.schedule.get(loan.id),
        )
        remaining = calc.remaining_principal
        if remaining <= ZERO:
            continue
        name = cp_map.get(loan.counterparty_id, (f"#{loan.counterparty_id}", None))[0]

        # У займа с графиком прогноз берётся из графика, а не из run-rate: аннуитет
        # гасится КАЖДЫЙ месяц, и модель «проценты по 1/12 + тело одним куском в
        # конце» рисует несуществующий обрыв (Симпл Финанс: 22,4 млн в феврале
        # вместо двенадцати платежей по 3 021 911,33).
        schedule = bundle.schedule.get(loan.id)
        sched_interest = _schedule_has_interest(schedule)
        # План погашения накопленного долга для НАЧИСЛЕНИЯ невидим, но в прогноз
        # ВЫПЛАТ обязан попасть: иначе экран показывает run-rate по ставке вместо
        # суммы, о которой договорились, а сам займ пропадает из «Ближайших
        # событий». В месяцы плана run-rate не добавляем — задвоило бы платёж.
        plan_months = {
            r.due_date.strftime("%Y-%m") for r in (schedule or []) if r.is_debt_plan
        }
        if schedule:
            for row in schedule:
                if row.due_date < as_of:
                    continue
                mk = row.due_date.strftime("%Y-%m")
                principal_due = Decimal(str(row.principal_due or 0))
                interest_due = Decimal(str(row.interest_due or 0))
                pays_interest = sched_interest or row.is_debt_plan
                month_principal[mk] += principal_due
                if pays_interest:
                    month_interest[mk] += interest_due
                base = dict(
                    date=row.due_date,
                    loan_id=loan.id,
                    counterparty_id=loan.counterparty_id,
                    counterparty_name=name,
                    contract_number=loan.contract_number,
                )
                if pays_interest and interest_due > ZERO:
                    upcoming.append(LoanForecastEvent(**base, kind="INTEREST", amount=interest_due))
                if principal_due > ZERO:
                    upcoming.append(LoanForecastEvent(**base, kind="MATURITY", amount=principal_due))
            if sched_interest:
                continue
            # График только по телу (кредитная линия): проценты считаем на
            # УБЫВАЮЩЕМ теле. Run-rate держал бы полную ставку и после 21.11,
            # когда транш на 70 млн уже вернулся, — декабрь завышался на ~1 млн.
            rate = calc.effective_rate or ZERO
            if rate > ZERO:
                due_by_day: dict[date, Decimal] = defaultdict(lambda: ZERO)
                for row in schedule:
                    due_by_day[row.due_date] += Decimal(str(row.principal_due or 0))
                bal = remaining
                cur_m = date(as_of.year, as_of.month, 1)
                for _ in range(horizon_months):
                    nxt_m = add_months(cur_m, 1)
                    body_days = ZERO
                    day = max(cur_m, as_of)
                    while day < nxt_m:
                        bal -= due_by_day.get(day, ZERO)
                        if bal > ZERO:
                            body_days += bal
                        day = date.fromordinal(day.toordinal() + 1)
                    # Месяцы плана погашения уже дали свою сумму выше; run-rate
                    # поверх плана задвоил бы выплату. После последнего платежа
                    # плана он снова нужен: тело никуда не делось и капает.
                    if body_days > ZERO and cur_m.strftime("%Y-%m") not in plan_months:
                        month_interest[cur_m.strftime("%Y-%m")] += (
                            body_days * rate / loan_interest.DAYS_PER_YEAR
                        )
                    cur_m = nxt_m
            continue

        # monthly run-rate forward (1/12) — same basis as monthly_interest KPI and
        # the «Ближайшие события» amounts below, so chart and events agree.
        if calc.monthly_interest > ZERO:
            cur_m = date(as_of.year, as_of.month, 1)
            for _ in range(horizon_months):
                nxt_m = add_months(cur_m, 1)
                # Срок обрывает начисление, только если он ВПЕРЕДИ: вернут тело —
                # проценты кончатся. Если срок уже прошёл, а заём жив и тело не
                # погашено, деньги остались у нас и продолжают стоить: обрезка
                # выкидывала такой займ из прогноза целиком — ни процентов по
                # месяцам, ни строки в событиях, хотя ОПиУ потом покажет расход.
                overdue = loan.maturity_date is not None and loan.maturity_date < as_of
                active_to = (
                    min(nxt_m, loan.maturity_date)
                    if loan.maturity_date and not overdue
                    else nxt_m
                )
                active_from = max(cur_m, loan.start_date)
                if active_to > active_from and active_to > as_of:
                    month_interest[cur_m.strftime("%Y-%m")] += calc.monthly_interest
                cur_m = nxt_m

        # principal due at maturity — только если график тела не задан
        if not schedule and loan.maturity_date is not None and loan.maturity_date >= as_of:
            month_principal[loan.maturity_date.strftime("%Y-%m")] += remaining
            upcoming.append(
                LoanForecastEvent(
                    date=loan.maturity_date,
                    loan_id=loan.id,
                    counterparty_id=loan.counterparty_id,
                    counterparty_name=name,
                    contract_number=loan.contract_number,
                    kind="MATURITY",
                    amount=remaining,
                )
            )

        # next interest payment
        if calc.next_interest_date and calc.monthly_interest > ZERO:
            upcoming.append(
                LoanForecastEvent(
                    date=calc.next_interest_date,
                    loan_id=loan.id,
                    counterparty_id=loan.counterparty_id,
                    counterparty_name=name,
                    contract_number=loan.contract_number,
                    kind="INTEREST",
                    amount=calc.monthly_interest,
                )
            )

    # assemble month series
    months: list[LoanForecastMonth] = []
    cur = date(as_of.year, as_of.month, 1)
    for _ in range(horizon_months):
        key = cur.strftime("%Y-%m")
        months.append(
            LoanForecastMonth(
                month=key,
                interest=month_interest.get(key, ZERO).quantize(Decimal("0.01")),
                principal_due=month_principal.get(key, ZERO).quantize(Decimal("0.01")),
            )
        )
        cur = add_months(cur, 1)

    upcoming.sort(key=lambda e: e.date)
    result = LoanForecastResponse(months=months, upcoming=upcoming[:30])
    return result.model_dump(mode="json")


def _body_days(
    loan: Loan,
    lites: list[loan_interest.PaymentLite],
    win_start: date,
    win_end: date,
) -> Decimal:
    """Сумма «тело × дни» за окно — знаменатель для средней стоимости денег."""
    moves: list[tuple[date, Decimal]] = []
    for p in lites:
        if p.payment_type == "DISBURSEMENT":
            moves.append((p.paid_at, p.amount))
        elif p.payment_type == "PRINCIPAL_REPAY":
            moves.append((p.paid_at, -p.amount))
    lo = max(win_start, date.fromordinal(loan.start_date.toordinal() - 1))
    # Обрезаем по сроку только ЗАКРЫТЫЙ займ: у него может не быть строк возврата,
    # и дату, когда тело ушло, взять больше неоткуда. У живого просроченного тело
    # реально на руках и проценты по нему капают — выкинув его из знаменателя,
    # мы бы задрали эффективную ставку на ровном месте.
    if loan.maturity_date is not None and loan.status != "ACTIVE":
        win_end = min(win_end, loan.maturity_date)
    if win_end <= lo:
        return ZERO
    total = ZERO
    day = lo
    base = Decimal(str(loan.principal))
    while day < win_end:
        day = date.fromordinal(day.toordinal() + 1)
        body = base + sum((d for dt, d in moves if dt < day), ZERO)
        if body > ZERO:
            total += body
    return total


# ─── Начисление по календарным месяцам (база для ОПиУ) ───────────────────────


@cached(prefix="loan_cost_months", ttl=300)
async def cost_by_month_keys(db: AsyncSession, project_id: int, month_keys: list[str]) -> dict:
    """
    Стоимость денег по ЗАДАННЫМ календарным месяцам: {'YYYY-MM': {interest, fee}}.

    Нужна ОПиУ: тот берёт произвольный диапазон отчёта, а `accrual_by_month`
    умеет только «последние N месяцев от даты». База одна и та же — начисление по
    дням с бакетом в календарный месяц, поэтому цифры сходятся с дашбордом.

    `as_of` намеренно не передаётся: текущий месяц режется сегодняшним днём — в
    ОПиУ незакрытый месяц не должен показывать проценты за будущие дни.
    """
    bundle = await _load(db, project_id)
    as_of = loan_service._today()
    out: dict[str, dict[str, str]] = {}
    for key in month_keys:
        try:
            year, month = int(key[:4]), int(key[5:7])
        except (ValueError, IndexError):
            continue
        win_start, win_end = month_window(year, month, as_of)
        interest = fee = ZERO
        if win_end > win_start:
            for loan in bundle.loans:
                i, f = loan_cost_in_window(bundle, loan, win_start=win_start, win_end=win_end)
                interest += i
                fee += f
        out[key] = {
            "interest": str(interest.quantize(Decimal("0.01"))),
            "fee": str(fee.quantize(Decimal("0.01"))),
        }
    return out



@cached(prefix="loan_lent", ttl=300)
async def lent_summary(db: AsyncSession, project_id: int, as_of: date) -> dict:
    """
    Выданные займы: сколько нам должны тела и процентов.

    Зеркало свода по заёмщикам для направления OUTGOING. Учредитель, передавший
    часть привлечённых денег своему ООО, должен видеть не только свой долг, но и
    встречное требование — иначе половина картины отсутствует.
    """
    from backend.models.auth import Project
    from backend.schemas.loan import LoanLentItem, LoanLentResponse

    bundle = await _load(db, project_id, direction="OUTGOING")
    if not bundle.loans:
        return LoanLentResponse().model_dump(mode="json")

    mirror_ids = [loan.mirror_loan_id for loan in bundle.loans if loan.mirror_loan_id]
    mirror_names: dict[int, str | None] = {}
    if mirror_ids:
        rows = await db.execute(
            select(Loan.id, Project.name)
            .join(Project, Project.id == Loan.project_id)
            .where(Loan.id.in_(mirror_ids), Loan.is_deleted == False)  # noqa: E712
        )
        mirror_names = {r.id: r.name for r in rows.all()}

    mtd_start, mtd_end = month_window(as_of.year, as_of.month, as_of)
    items: list[LoanLentItem] = []
    for loan in bundle.loans:
        payments = bundle.payments.get(loan.id, [])
        drawn = sum(
            (Decimal(str(p.amount or 0)) for p in payments if p.payment_type == "DISBURSEMENT"), ZERO
        )
        repaid = sum(
            (Decimal(str(p.amount or 0)) for p in payments if p.payment_type == "PRINCIPAL_REPAY"),
            ZERO,
        )
        received = sum(
            (Decimal(str(p.amount or 0)) for p in payments if p.payment_type == "INTEREST_PAY"), ZERO
        )
        outstanding = (
            ZERO
            if loan.status == "CLOSED"
            else max(ZERO, Decimal(str(loan.principal)) + drawn - repaid)
        )
        accrued = _lifetime_interest(bundle, loan, as_of)
        mtd, _fee = loan_cost_in_window(bundle, loan, win_start=mtd_start, win_end=mtd_end)
        rate = Decimal(str(loan.rate)) if loan.rate is not None else None
        for rp in sorted(bundle.rates.get(loan.id, []), key=lambda x: x.valid_from):
            if rp.valid_from <= as_of:
                rate = rp.rate
        items.append(
            LoanLentItem(
                counterparty_id=loan.counterparty_id,
                name=bundle.cp_map.get(loan.counterparty_id, (f"#{loan.counterparty_id}", None))[0],
                loan_id=loan.id,
                contract_number=loan.contract_number,
                rate=rate,
                outstanding=outstanding,
                accrued_total=accrued,
                interest_received=received,
                interest_due=max(ZERO, accrued - received),
                total_due=outstanding + max(ZERO, accrued - received),
                accrued_month=mtd,
                mirror_project_name=mirror_names.get(loan.mirror_loan_id or 0),
            )
        )
    items.sort(key=lambda x: x.total_due, reverse=True)
    result = LoanLentResponse(
        items=items,
        total_outstanding=sum((i.outstanding for i in items), ZERO),
        total_accrued=sum((i.accrued_total for i in items), ZERO),
        total_received=sum((i.interest_received for i in items), ZERO),
        total_interest_due=sum((i.interest_due for i in items), ZERO),
        total_due=sum((i.total_due for i in items), ZERO),
        month_income=sum((i.accrued_month for i in items), ZERO),
    )
    return result.model_dump(mode="json")


@cached(prefix="loan_income_months", ttl=300)
async def income_by_month_keys(db: AsyncSession, project_id: int, month_keys: list[str]) -> dict:
    """
    Процентный ДОХОД по выданным займам, по календарным месяцам.

    Зеркало `cost_by_month_keys` для направления OUTGOING. Учредитель, занявший
    деньги под 26 % и передавший их своему ООО под 27 %, платит не 26 % — он
    платит разницу. Без этой половины его ОПиУ показывает только расход и врёт
    на всю сумму дохода.
    """
    bundle = await _load(db, project_id, direction="OUTGOING")
    as_of = loan_service._today()
    out: dict[str, str] = {}
    for key in month_keys:
        try:
            year, month = int(key[:4]), int(key[5:7])
        except (ValueError, IndexError):
            continue
        win_start, win_end = month_window(year, month, as_of)
        total = ZERO
        if win_end > win_start:
            for loan in bundle.loans:
                interest, _fee = loan_cost_in_window(
                    bundle, loan, win_start=win_start, win_end=win_end
                )
                total += interest
        out[key] = str(total.quantize(Decimal("0.01")))
    return out


@cached(prefix="loan_accrual_months", ttl=300)
async def accrual_by_month(db: AsyncSession, project_id: int, as_of: date, months: int = 12) -> dict:
    """
    Начисленные проценты и комиссии по КАЛЕНДАРНЫМ месяцам — база для ОПиУ.

    Периоды ВЫПЛАТ у продуктов разные (частные займы 25→25, ВКЛ — месяц с платежом
    5-го, аннуитет — по графику), и складывать их в один P&L нельзя: период 25.06→25.07
    шестью днями лежит в июне. Поэтому расход считаем ПО ДНЯМ и бакетим в календарный
    месяц — это универсально и не зависит от того, когда деньги реально ушли.
    """
    from backend.schemas.loan import LoanAccrualMonth, LoanAccrualMonthsResponse

    bundle = await _load(db, project_id)
    loans, pay_by_loan = bundle.loans, bundle.payments
    if not loans:
        return LoanAccrualMonthsResponse().model_dump(mode="json")

    first = add_months(date(as_of.year, as_of.month, 1), -(months - 1))
    buckets: dict[str, dict[str, Decimal]] = {}
    cur = first
    while cur <= date(as_of.year, as_of.month, 1):
        buckets[cur.strftime("%Y-%m")] = {
            "interest": ZERO, "fee": ZERO, "ip": ZERO, "physical": ZERO,
            "body_days": ZERO, "days": ZERO,
        }
        cur = add_months(cur, 1)

    for loan in loans:
        lites = _lites(pay_by_loan.get(loan.id, []))
        for key in buckets:
            year, month = int(key[:4]), int(key[5:])
            win_start, win_end = month_window(year, month, as_of)
            if win_end <= win_start:
                continue
            interest, fee = loan_cost_in_window(
                bundle, loan, win_start=win_start, win_end=win_end
            )
            b = buckets[key]
            b["interest"] += interest
            b["fee"] += fee
            # Тело×дни — чтобы получить СРЕДНИЙ остаток за месяц: у линии он
            # скачет на каждой выборке, простое «тело на конец месяца» соврёт.
            b["body_days"] += _body_days(loan, lites, win_start, win_end)
            b["days"] = max(b["days"], Decimal((win_end - win_start).days))
            if loan.entity_type == "IP":
                b["ip"] += interest + fee
            elif loan.entity_type == "PHYSICAL":
                b["physical"] += interest + fee

    items = []
    for key, v in sorted(buckets.items()):
        days = v["days"] or ZERO
        avg_body = (v["body_days"] / days) if days > ZERO else ZERO
        cost = v["interest"] + v["fee"]
        eff = (
            (cost * loan_interest.DAYS_PER_YEAR / (avg_body * days)).quantize(Decimal("0.0001"))
            if avg_body > ZERO and days > ZERO
            else None
        )
        items.append(
            LoanAccrualMonth(
                month=key,
                interest=v["interest"].quantize(Decimal("0.01")),
                fee=v["fee"].quantize(Decimal("0.01")),
                total=cost.quantize(Decimal("0.01")),
                ip=v["ip"].quantize(Decimal("0.01")),
                physical=v["physical"].quantize(Decimal("0.01")),
                avg_body=avg_body.quantize(Decimal("0.01")),
                effective_rate=eff,
                days=int(days),
            )
        )
    body_days_all = sum((v["body_days"] for v in buckets.values()), ZERO)
    cost_all = sum((i.total for i in items), ZERO)
    result = LoanAccrualMonthsResponse(
        items=items,
        total_interest=sum((i.interest for i in items), ZERO),
        total_fee=sum((i.fee for i in items), ZERO),
        effective_rate=(cost_all * loan_interest.DAYS_PER_YEAR / body_days_all).quantize(Decimal("0.0001"))
        if body_days_all > ZERO
        else None,
    )
    return result.model_dump(mode="json")
