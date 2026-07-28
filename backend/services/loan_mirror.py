"""
Займ между своими проектами: две книги, один договор.

Заём ИП учредителя в своё ООО — одна сделка, но в учёте это две записи: у ООО
долг (INCOMING), у ИП актив (OUTGOING). Слить их в одну строку нельзя — правило
«каждый запрос фильтрует project_id» иначе сломается, и чужой проект начнёт
видеть чужие деньги. Поэтому записи две, а `Loan.mirror_loan_id` держит их
вместе: движение вводится один раз и зеркалится на вторую сторону.

Зеркалим только тело договора и ставки. `transaction_id` НЕ копируется: банковская
проводка принадлежит книге того проекта, где она прошла, и уникальна на неё.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_project_reports
from backend.models.auth import Project, ProjectMember
from backend.models.counterparty import Counterparty
from backend.models.loan import Loan, LoanPayment, LoanRatePeriod
from backend.schemas.loan import (
    LoanAccrualMonth,
    LoanChain,
    LoanChainListResponse,
    LoanChainMovement,
    LoanMirrorCreate,
    LoanMirrorSide,
    LoanRatePeriodResponse,
)
from backend.services import loan_interest
from backend.utils.time import utcnow

ZERO = Decimal("0")
BODY_KINDS = ("DISBURSEMENT", "PRINCIPAL_REPAY")
FLIP = {"INCOMING": "OUTGOING", "OUTGOING": "INCOMING", "AFFILIATED": "AFFILIATED"}
PAYMENTS_LIMIT = 5000
CHAINS_LIMIT = 200


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


async def _payments(db: AsyncSession, loan_id: int) -> list[LoanPayment]:
    return list(
        (
            await db.execute(
                select(LoanPayment)
                .where(LoanPayment.loan_id == loan_id)
                .order_by(LoanPayment.paid_at, LoanPayment.id)
                .limit(PAYMENTS_LIMIT)
            )
        )
        .scalars()
        .all()
    )


async def _rates(db: AsyncSession, loan_id: int) -> list[LoanRatePeriod]:
    return list(
        (
            await db.execute(
                select(LoanRatePeriod)
                .where(LoanRatePeriod.loan_id == loan_id)
                .order_by(LoanRatePeriod.valid_from)
                .limit(500)
            )
        )
        .scalars()
        .all()
    )


def _pay_key(p: LoanPayment) -> tuple:
    return (p.payment_type, Decimal(str(p.amount or 0)), p.paid_at)


def _pay_counts(payments: list[LoanPayment]) -> Counter:
    """
    МУЛЬТИмножество движений: одинаковые суммы одного дня — норма, не дубль.

    В боевом займе учредителя таких пар три (19.09 два транша по 3 млн, 11.12 два
    по 450 тыс., 18.05 два по 900 тыс.). На множестве вторая копия каждой пары
    молча не доехала бы до второй книги — 4,35 млн ₽ разницы, причём индикатор
    рассинхрона показал бы «всё сходится».
    """
    return Counter(_pay_key(p) for p in payments)


# ─── Связка ──────────────────────────────────────────────────────────────────


async def _resolve_counterparty(
    db: AsyncSession, project_id: int, data: LoanMirrorCreate
) -> Counterparty:
    """Кем мы числимся в чужой книге: найти контрагента или завести."""
    if data.counterparty_id is not None:
        cp = (
            await db.execute(
                select(Counterparty).where(
                    Counterparty.id == data.counterparty_id,
                    Counterparty.project_id == project_id,
                    Counterparty.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if cp is None:
            raise HTTPException(status_code=400, detail="Контрагент не найден в целевом проекте")
        return cp

    if data.counterparty_inn:
        # Уникальный индекс частичный: `(project_id, inn) WHERE inn IS NOT NULL
        # AND is_deleted = false`. Значит мягко удалённых с одним ИНН может быть
        # сколько угодно — `scalar_one_or_none()` по всем строкам упал бы
        # MultipleResultsFound. Берём живого; удалённого воскрешаем, только если
        # живого нет (иначе получим IntegrityError на том же индексе).
        cp = (
            await db.execute(
                select(Counterparty)
                .where(
                    Counterparty.project_id == project_id,
                    Counterparty.inn == data.counterparty_inn,
                )
                .order_by(Counterparty.is_deleted, Counterparty.id)
                .limit(1)
            )
        ).scalars().first()
        if cp is not None:
            if cp.is_deleted:
                cp.restore()
            return cp
    if not data.counterparty_name:
        raise HTTPException(
            status_code=400, detail="Укажите контрагента второй стороны: id, ИНН или название"
        )
    cp = Counterparty(
        project_id=project_id,
        inn=data.counterparty_inn,
        name=data.counterparty_name,
        primary_type="AFFILIATED",
    )
    db.add(cp)
    await db.flush()
    return cp


async def create_mirror(
    db: AsyncSession, *, loan_id: int, project_id: int, user_id: int, data: LoanMirrorCreate
) -> LoanChain:
    """
    Завести вторую сторону договора в другом проекте и связать записи.

    Целевой проект приходит из тела запроса, а `get_current_project` проверяет
    членство только в ТЕКУЩЕМ проекте — поэтому доступ ко второму проверяем здесь
    явно. Без этого участник одного проекта мог бы создать займ и контрагента
    в любом чужом проекте, зная только его id.
    """
    loan = await _get_loan(db, loan_id, project_id)
    if loan.mirror_loan_id is not None:
        raise HTTPException(status_code=409, detail="У займа уже есть вторая сторона")
    if data.target_project_id == project_id:
        raise HTTPException(status_code=400, detail="Вторая сторона должна быть в другом проекте")
    target = (
        await db.execute(
            select(Project.id)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(
                Project.id == data.target_project_id,
                ProjectMember.user_id == user_id,
                Project.is_deleted == False,  # noqa: E712
                ProjectMember.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=403, detail="Нет доступа к целевому проекту или проект не найден"
        )

    cp = await _resolve_counterparty(db, data.target_project_id, data)
    mirror = Loan(
        project_id=data.target_project_id,
        counterparty_id=cp.id,
        direction=FLIP.get(loan.direction, "OUTGOING"),
        principal=loan.principal,
        currency=loan.currency,
        rate=loan.rate,
        contract_number=loan.contract_number,
        contract_date=loan.contract_date,
        start_date=loan.start_date,
        maturity_date=loan.maturity_date,
        status=loan.status,
        entity_type=loan.entity_type,
        loan_kind=loan.loan_kind,
        accrual_kind=loan.accrual_kind,
        credit_limit=loan.credit_limit,
        unused_limit_rate=loan.unused_limit_rate,
        payment_day=loan.payment_day,
        notes=loan.notes,
        mirror_loan_id=loan.id,
    )
    db.add(mirror)
    await db.flush()
    loan.mirror_loan_id = mirror.id
    await db.commit()
    await _sync(db, loan)
    await invalidate_project_reports(project_id)
    await invalidate_project_reports(data.target_project_id)
    return await get_chain(db, loan_id=loan_id, project_id=project_id)


async def _live_mirror(db: AsyncSession, loan: Loan) -> Loan | None:
    """
    Живая вторая сторона: не удалённая и ссылающаяся обратно на нас.

    `Loan` — SoftDelete: без фильтра мягко удалённая книга продолжала бы получать
    копии платежей и рисоваться в цепочке живой. Взаимность ссылки проверяем,
    потому что односторонняя связь (правка руками, скрипт) означала бы, что мы
    сливаем движения в чужой договор.
    """
    if loan.mirror_loan_id is None:
        return None
    return (
        await db.execute(
            select(Loan).where(
                Loan.id == loan.mirror_loan_id,
                Loan.is_deleted == False,  # noqa: E712
                Loan.mirror_loan_id == loan.id,
            )
        )
    ).scalar_one_or_none()


async def _sync(db: AsyncSession, loan: Loan) -> int:
    """
    Свести движения и ставки обеих сторон к объединению. Идемпотентно.

    Копируем в ОБЕ стороны: платёж могли завести с любой книги, и «источником
    истины» тут не является ни одна — договор один.
    """
    mirror = await _live_mirror(db, loan)
    if mirror is None:
        return 0

    added = 0
    a_pays, b_pays = await _payments(db, loan.id), await _payments(db, mirror.id)
    a_counts, b_counts = _pay_counts(a_pays), _pay_counts(b_pays)
    # Копируем НЕДОСТАЮЩУЮ КРАТНОСТЬ, а не «ключа нет»: два одинаковых транша
    # одного дня — обычное дело, и на множестве второй молча терялся бы.
    for missing, dst, src_pays in (
        (a_counts - b_counts, mirror, a_pays),
        (b_counts - a_counts, loan, b_pays),
    ):
        for p in src_pays:
            key = _pay_key(p)
            if missing[key] <= 0:
                continue
            missing[key] -= 1
            db.add(
                LoanPayment(
                    loan_id=dst.id,
                    payment_type=p.payment_type,
                    amount=p.amount,
                    currency=p.currency,
                    paid_at=p.paid_at,
                    # transaction_id намеренно не копируем: проводка принадлежит
                    # книге своего проекта и уникальна на неё.
                )
            )
            added += 1

    a_rates, b_rates = await _rates(db, loan.id), await _rates(db, mirror.id)
    a_from = {r.valid_from for r in a_rates}
    b_from = {r.valid_from for r in b_rates}
    for src, dst, seen in ((a_rates, mirror, b_from), (b_rates, loan, a_from)):
        for r in src:
            if r.valid_from in seen:
                continue
            db.add(
                LoanRatePeriod(
                    loan_id=dst.id,
                    valid_from=r.valid_from,
                    rate=r.rate,
                    base_rate=r.base_rate,
                    spread=r.spread,
                    note=r.note,
                )
            )
            seen.add(r.valid_from)
            added += 1

    if added:
        await db.commit()
        await invalidate_project_reports(loan.project_id)
        await invalidate_project_reports(mirror.project_id)
    return added


async def sync_mirror(db: AsyncSession, *, loan_id: int, project_id: int) -> LoanChain:
    """Досинхронизировать стороны: недостающие движения и ставки едут на обе."""
    loan = await _get_loan(db, loan_id, project_id)
    if loan.mirror_loan_id is None:
        raise HTTPException(status_code=400, detail="У займа нет второй стороны")
    await _sync(db, loan)
    return await get_chain(db, loan_id=loan_id, project_id=project_id)


# ─── Чтение ──────────────────────────────────────────────────────────────────


def _accrue(
    payments: list[LoanPayment],
    rates: list[LoanRatePeriod],
    loan: Loan,
    win_start: date,
    win_end: date,
) -> Decimal:
    return loan_interest.accrued_in_window(
        principal=Decimal(str(loan.principal)),
        rate=Decimal(str(loan.rate)) if loan.rate is not None else None,
        start_date=loan.start_date,
        payments=[
            loan_interest.PaymentLite(p.payment_type, Decimal(str(p.amount or 0)), p.paid_at)
            for p in payments
        ],
        win_start=win_start,
        win_end=win_end,
        rate_periods=[
            loan_interest.RatePeriod(valid_from=r.valid_from, rate=Decimal(str(r.rate)))
            for r in rates
        ],
    )


async def _side(
    db: AsyncSession, loan: Loan, payments: list[LoanPayment], rates: list[LoanRatePeriod], as_of: date
) -> LoanMirrorSide:
    from backend.services.loan_analytics import month_window

    row = (
        await db.execute(select(Project.name, Project.slug).where(Project.id == loan.project_id))
    ).first()
    cp_name = (
        await db.execute(select(Counterparty.name).where(Counterparty.id == loan.counterparty_id))
    ).scalar_one_or_none()

    drawn = sum((Decimal(str(p.amount or 0)) for p in payments if p.payment_type == "DISBURSEMENT"), ZERO)
    repaid = sum(
        (Decimal(str(p.amount or 0)) for p in payments if p.payment_type == "PRINCIPAL_REPAY"), ZERO
    )
    paid = sum(
        (Decimal(str(p.amount or 0)) for p in payments if p.payment_type == "INTEREST_PAY"), ZERO
    )
    # Закрытый займ не держит тела — канон движка (`compute_loan`). Без этого
    # закрытая цепочка попадала бы в тоталы вкладки и расходилась с реестром.
    outstanding = (
        ZERO
        if loan.status == "CLOSED"
        else max(ZERO, Decimal(str(loan.principal)) + drawn - repaid)
    )
    start = date.fromordinal(loan.start_date.toordinal() - 1)
    total = _accrue(payments, rates, loan, start, as_of)
    mtd_start, mtd_end = month_window(as_of.year, as_of.month, as_of)
    rate = Decimal(str(loan.rate)) if loan.rate is not None else None
    for r in sorted(rates, key=lambda x: x.valid_from):
        if r.valid_from <= as_of:
            rate = Decimal(str(r.rate))
    return LoanMirrorSide(
        loan_id=loan.id,
        project_id=loan.project_id,
        project_name=row.name if row else None,
        project_slug=row.slug if row else None,
        counterparty_name=cp_name,
        direction=loan.direction,
        status=loan.status,
        outstanding=outstanding,
        accrued_interest=_accrue(payments, rates, loan, mtd_start, mtd_end),
        accrued_total=total,
        interest_paid=paid,
        interest_debt=max(ZERO, total - paid),
        current_rate=rate,
    )


async def _build_chain(db: AsyncSession, loan: Loan, as_of: date) -> LoanChain:
    from backend.services.loan_analytics import month_window
    from backend.services.loan_interest import add_months

    payments = await _payments(db, loan.id)
    rates = await _rates(db, loan.id)
    sides = [await _side(db, loan, payments, rates, as_of)]
    in_sync = True
    note = None
    if loan.mirror_loan_id is not None:
        mirror = await _live_mirror(db, loan)
        if mirror is None:
            # Ссылка есть, живой второй книги нет — молчать нельзя, иначе цепочка
            # из одной стороны выглядит «сошедшейся».
            in_sync = False
            note = "Вторая сторона удалена или связь односторонняя"
        else:
            m_pays = await _payments(db, mirror.id)
            m_rates = await _rates(db, mirror.id)
            sides.append(await _side(db, mirror, m_pays, m_rates, as_of))
            # Разница по МУЛЬТИмножеству: одинаковые суммы одного дня легитимны,
            # и на обычном set недостача кратности была бы невидимой.
            a, b = _pay_counts(payments), _pay_counts(m_pays)
            diff = sum((a - b).values()) + sum((b - a).values())
            rate_diff = {(r.valid_from, Decimal(str(r.rate))) for r in rates} ^ {
                (r.valid_from, Decimal(str(r.rate))) for r in m_rates
            }
            if diff or rate_diff:
                in_sync = False
                parts = []
                if diff:
                    parts.append(f"{diff} движ.")
                if rate_diff:
                    parts.append(f"{len(rate_diff)} ставок")
                note = f"Стороны разошлись: {', '.join(parts)}; нажмите «Синхронизировать»"

    balance = ZERO
    movements: list[LoanChainMovement] = []
    for p in payments:
        amount = Decimal(str(p.amount or 0))
        after = None
        if p.payment_type == "DISBURSEMENT":
            balance += amount
            after = balance
        elif p.payment_type == "PRINCIPAL_REPAY":
            balance -= amount
            after = balance
        movements.append(
            LoanChainMovement(
                happened_at=p.paid_at, kind=p.payment_type, amount=amount, balance_after=after
            )
        )

    # Начисление по КАЛЕНДАРНЫМ месяцам — та же база, что у ОПиУ
    monthly: list[LoanAccrualMonth] = []
    cur = date(loan.start_date.year, loan.start_date.month, 1)
    guard = 0
    while cur <= date(as_of.year, as_of.month, 1) and guard < 240:
        guard += 1
        win_start, win_end = month_window(cur.year, cur.month, as_of)
        if win_end > win_start:
            interest = _accrue(payments, rates, loan, win_start, win_end)
            monthly.append(
                LoanAccrualMonth(
                    month=cur.strftime("%Y-%m"),
                    interest=interest,
                    total=interest,
                    days=(win_end - win_start).days,
                )
            )
        cur = add_months(cur, 1)

    main = sides[0]
    return LoanChain(
        contract_number=loan.contract_number,
        contract_date=loan.contract_date,
        start_date=loan.start_date,
        maturity_date=loan.maturity_date,
        sides=sides,
        total_disbursed=sum(
            (Decimal(str(p.amount or 0)) for p in payments if p.payment_type == "DISBURSEMENT"), ZERO
        ),
        total_repaid=sum(
            (Decimal(str(p.amount or 0)) for p in payments if p.payment_type == "PRINCIPAL_REPAY"),
            ZERO,
        ),
        outstanding=main.outstanding,
        accrued_total=main.accrued_total,
        interest_paid=main.interest_paid,
        interest_debt=main.interest_debt,
        total_debt=main.outstanding + main.interest_debt,
        rate_periods=[LoanRatePeriodResponse.model_validate(r) for r in rates],
        movements=movements,
        monthly=monthly,
        in_sync=in_sync,
        sync_note=note,
    )


async def get_chain(db: AsyncSession, *, loan_id: int, project_id: int) -> LoanChain:
    """Договор целиком: обе книги, движения, начисление по месяцам."""
    loan = await _get_loan(db, loan_id, project_id)
    return await _build_chain(db, loan, _today())


async def list_chains(db: AsyncSession, *, project_id: int) -> LoanChainListResponse:
    """Все займы проекта, у которых есть вторая сторона в другом проекте."""
    loans = list(
        (
            await db.execute(
                select(Loan)
                .where(
                    Loan.project_id == project_id,
                    Loan.is_deleted == False,  # noqa: E712
                    Loan.mirror_loan_id.is_not(None),
                )
                .order_by(Loan.start_date.desc())
                .limit(CHAINS_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    as_of = _today()
    items = [await _build_chain(db, loan, as_of) for loan in loans]
    return LoanChainListResponse(
        items=items,
        total_outstanding=sum((i.outstanding for i in items), ZERO),
        total_interest_debt=sum((i.interest_debt for i in items), ZERO),
    )
