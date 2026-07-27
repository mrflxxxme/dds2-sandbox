"""
Loan analytics — дашборд, свод по заёмщикам, прогноз процентов и возвратов.

Все запросы скоупятся по project_id и фильтруют soft-delete. Фокус — INCOMING
(полученные займы: наш долг + проценты к выплате), как в реестре пользователя.
Кэш: ключ включает as_of → инвалидация `invalidate_project_reports` срабатывает
(паттерн `<prefix>:project_id=<id>:*` требует хвостовой сегмент).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models.counterparty import Counterparty
from backend.models.loan import Loan, LoanPayment
from backend.models.refs import ProjectSetting
from backend.services import loan_interest
from backend.services.loan_interest import add_months

ZERO = Decimal("0")
LENDER_CP_KEY_PREFIX = "lender_counterparties_"
# Defensive cap for full-project aggregation scans (loans are dozens today; this
# guards a runaway project from OOMing the worker — mirrors STOCKS_LIMIT pattern).
SCAN_LIMIT = 20000

logger = __import__("logging").getLogger(__name__)


# ─── Loader ──────────────────────────────────────────────────────────────────


async def _load(
    db: AsyncSession, project_id: int
) -> tuple[list[Loan], dict[int, list[LoanPayment]], dict[int, tuple[str, str | None]], set[int]]:
    """Load INCOMING loans + payments + cp names + portal-access cp ids for a project."""
    loan_rows = await db.execute(
        select(Loan)
        .where(
            Loan.project_id == project_id,
            Loan.is_deleted == False,  # noqa: E712
            Loan.direction == "INCOMING",
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

    return loans, pay_by_loan, cp_map, access_cp_ids


def _calc(
    loan: Loan,
    payments: list[LoanPayment],
    as_of: date,
    period: tuple[date, date] | None = None,
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
        period=period,
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

    loans, pay_by_loan, cp_map, _access = await _load(db, project_id)

    kpis = LoanKpis()
    weighted_num = ZERO
    lenders_active: set[int] = set()
    by_entity: dict[str | None, LoanEntitySplit] = {}
    by_rate: dict[Decimal, LoanRateBucket] = {}
    lender_agg: dict[int, dict] = {}
    maturities: list[tuple[date, Decimal]] = []

    for loan in loans:
        calc = _calc(loan, pay_by_loan.get(loan.id, []), as_of)
        kpis.interest_paid_total += calc.interest_paid
        # Проценты за период копим по ВСЕМ займам, включая закрытые: погашенный
        # в середине периода отработал свои дни. Иначе дашборд разойдётся со
        # сводом по заёмщикам, где они учтены.
        kpis.accrued_interest += calc.accrued_interest
        ent = loan.entity_type
        es = by_entity.setdefault(ent, LoanEntitySplit(entity_type=ent))
        es.accrued_interest += calc.accrued_interest
        es.interest_due_period += calc.interest_due_period
        if loan.status != "ACTIVE":
            continue

        out = calc.remaining_principal
        kpis.active_count += 1
        kpis.total_outstanding += out
        kpis.monthly_interest += calc.monthly_interest
        lenders_active.add(loan.counterparty_id)
        if loan.rate is not None:
            weighted_num += out * Decimal(str(loan.rate))

        # by entity — тело только по активным, проценты собраны выше по всем
        es.count += 1
        es.outstanding += out

        # by rate
        if loan.rate is not None:
            rb = by_rate.setdefault(Decimal(str(loan.rate)), LoanRateBucket(rate=Decimal(str(loan.rate))))
            rb.count += 1
            rb.outstanding += out

        # top lenders
        la = lender_agg.setdefault(
            loan.counterparty_id, {"out": ZERO, "accrued": ZERO, "wnum": ZERO}
        )
        la["out"] += out
        la["accrued"] += calc.accrued_interest
        if loan.rate is not None:
            la["wnum"] += out * Decimal(str(loan.rate))

        if loan.maturity_date is not None and out > ZERO and loan.maturity_date >= as_of:
            maturities.append((loan.maturity_date, out))

    kpis.lenders_count = len(lenders_active)
    if kpis.total_outstanding > ZERO:
        kpis.weighted_avg_rate = (weighted_num / kpis.total_outstanding).quantize(Decimal("0.0001"))
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

    monthly = _monthly_timeline(loans, pay_by_loan, as_of)

    result = LoanDashboard(
        kpis=kpis,
        by_entity=sorted(by_entity.values(), key=lambda x: x.outstanding, reverse=True),
        by_rate=sorted(by_rate.values(), key=lambda x: x.rate),
        monthly=monthly,
        top_lenders=top_lenders[:10],
    )
    return result.model_dump(mode="json")


def _monthly_timeline(
    loans: list[Loan], pay_by_loan: dict[int, list[LoanPayment]], as_of: date
) -> list:
    """
    По периодам начисления 25→25: выдано / возвращено тело, остаток, начислено %.

    Метка точки — дата выплаты (конец периода), она же подпись на графике:
    «25.08». Проценты считаются на фактический остаток тела с учётом возвратов,
    поэтому займ, погашенный в середине периода, даёт свои реальные дни.
    """
    from backend.schemas.loan import LoanMonthlyPoint

    if not loans:
        return []
    disbursed: dict[str, Decimal] = defaultdict(lambda: ZERO)
    repaid: dict[str, Decimal] = defaultdict(lambda: ZERO)
    interest: dict[str, Decimal] = defaultdict(lambda: ZERO)

    min_date = min(loan.start_date for loan in loans)
    _, first_end = loan_interest.accrual_period(min_date)
    _, last_end = loan_interest.accrual_period(as_of)

    # Сетка периодов: ключ — ISO даты выплаты (конца периода)
    bounds: list[tuple[date, date]] = []
    p_start, p_end = loan_interest.accrual_period(min_date)
    guard = 0
    while p_end <= last_end and guard < 600:
        guard += 1
        bounds.append((p_start, p_end))
        p_start, p_end = loan_interest.accrual_period(p_end)
    keys = [pe.isoformat() for _, pe in bounds]

    def bucket(d: date) -> str:
        return loan_interest.accrual_period(d)[1].isoformat()

    for loan in loans:
        principal = Decimal(str(loan.principal))
        disbursed[bucket(loan.start_date)] += principal
        # Возвраты зажимаем телом займа: в реестре встречаются лишние строки
        # (Прохоров дог. 54 — две строки возврата на 2 млн при теле 1 млн), и без
        # клампа накопительный остаток на графике уезжает ниже KPI, который
        # считает остаток через max(0, тело − возвраты).
        left = principal
        for p in sorted(pay_by_loan.get(loan.id, []), key=lambda x: x.paid_at):
            if p.payment_type != "PRINCIPAL_REPAY" or left <= 0:
                continue
            amount = min(left, Decimal(str(p.amount or 0)))
            repaid[bucket(p.paid_at)] += amount
            left -= amount

    for loan in loans:
        if loan.rate is None:
            continue
        lites = [
            loan_interest.PaymentLite(
                payment_type=p.payment_type,
                amount=Decimal(str(p.amount or 0)),
                paid_at=p.paid_at,
            )
            for p in pay_by_loan.get(loan.id, [])
        ]
        for ps, pe in bounds:
            win_end = min(pe, as_of)
            if loan.maturity_date is not None and loan.maturity_date < win_end:
                win_end = loan.maturity_date
            if win_end <= ps:
                continue
            interest[pe.isoformat()] += loan_interest.accrued_in_window(
                principal=Decimal(str(loan.principal)),
                rate=Decimal(str(loan.rate)),
                start_date=loan.start_date,
                payments=lites,
                win_start=ps,
                win_end=win_end,
            )

    out: list[LoanMonthlyPoint] = []
    running = ZERO
    for m in keys:
        running += disbursed[m] - repaid[m]
        out.append(
            LoanMonthlyPoint(
                month=m,
                disbursed=disbursed[m].quantize(Decimal("0.01")),
                repaid=repaid[m].quantize(Decimal("0.01")),
                outstanding=running.quantize(Decimal("0.01")),
                interest=interest[m].quantize(Decimal("0.01")),
            )
        )
    # последние 24 периода — читаемый график
    return out[-24:]


# ─── By-lender (Заёмщики) ────────────────────────────────────────────────────


@cached(prefix="loan_by_lender", ttl=300)
async def by_lender(
    db: AsyncSession, project_id: int, as_of: date, period_end: date | None = None
) -> dict:
    from backend.schemas.loan import LoanByLenderResponse, LoanEntitySplit, LoanLenderRollup

    loans, pay_by_loan, cp_map, access_cp_ids = await _load(db, project_id)

    # Явный период = исторический срез: «сколько было к выплате 25.07». Начисление
    # ограничено сегодня, поэтому у закрытого периода «начислено» == «к выплате».
    period = (
        (loan_interest.add_months(period_end, -1), period_end) if period_end is not None else None
    )
    agg: dict[int, dict] = {}
    for loan in loans:
        calc = _calc(loan, pay_by_loan.get(loan.id, []), as_of, period)
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
            if loan.rate is not None:
                a["wnum"] += calc.remaining_principal * Decimal(str(loan.rate))
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
    ent_agg: dict[str | None, LoanEntitySplit] = {}
    for cp_id, a in agg.items():
        name, inn = cp_map.get(cp_id, (f"#{cp_id}", None))
        wrate = (a["wnum"] / a["out"]).quantize(Decimal("0.0001")) if a["out"] > ZERO else None
        total_out += a["out"]
        total_accr += a["accrued"]
        total_due += a["due"]
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

    loans, pay_by_loan, cp_map, _access = await _load(db, project_id)

    month_interest: dict[str, Decimal] = defaultdict(lambda: ZERO)
    month_principal: dict[str, Decimal] = defaultdict(lambda: ZERO)
    upcoming: list[LoanForecastEvent] = []

    for loan in loans:
        if loan.status != "ACTIVE":
            continue
        calc = _calc(loan, pay_by_loan.get(loan.id, []), as_of)
        remaining = calc.remaining_principal
        if remaining <= ZERO:
            continue
        name = cp_map.get(loan.counterparty_id, (f"#{loan.counterparty_id}", None))[0]

        # monthly run-rate forward (1/12) — same basis as monthly_interest KPI and
        # the «Ближайшие события» amounts below, so chart and events agree.
        if calc.monthly_interest > ZERO:
            cur_m = date(as_of.year, as_of.month, 1)
            for _ in range(horizon_months):
                nxt_m = add_months(cur_m, 1)
                active_to = min(nxt_m, loan.maturity_date) if loan.maturity_date else nxt_m
                active_from = max(cur_m, loan.start_date)
                if active_to > active_from and active_to > as_of:
                    month_interest[cur_m.strftime("%Y-%m")] += calc.monthly_interest
                cur_m = nxt_m

        # principal due at maturity
        if loan.maturity_date is not None and loan.maturity_date >= as_of:
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
