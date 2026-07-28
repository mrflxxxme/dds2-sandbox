"""
Service: payroll — зарплата: сотрудники, команды, тарифная лестница, ведомость.

Схема начисления (канон 2026-07-28):
- Команда владеет скоупами (бренды / категории из wb_finance_rows). База
  начисления команды за WB-неделю (Пн–Вс) = «Чистая выплата» (net_payout) БДР
  по её скоупам, period_mode='report' — сходится с кабинетом WB.
- Неделя принадлежит месяцу своего ЧЕТВЕРГА (большинство дней).
- Ставка — из глобальной тарифной лестницы payroll_tariff_step (действующий
  набор = максимальный valid_from <= последнего дня месяца). Ступень — наибольший
  порог <= базы; ниже минимального порога — минимальная ступень; база <= 0 → 0.
- Сумма команды = база × team_rate, делится поровну между активными участниками.
- Фикс-оклад ведётся ПЕРИОДАМИ (PayrollSalaryPeriod): оклад месяца M = amount
  периода с max(valid_from) <= первое число M; до первого периода — 0
  («у бух была 50к, с июля 80к» — прошлые месяцы не переписываются задним числом).
- План выплат: процентная часть 50/50 на 10-е и 25-е СЛЕДУЮЩЕГО месяца; фикс —
  по fixed_pay_days (NULL = 50/50 на 10/25). Строки мержатся по дню.
- Официалка: факт из банковской выписки по counterparty_id сотрудника за
  КАЛЕНДАРНЫЙ месяц, следующий за расчётным (ЗП за июнь платят в июле).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Sequence

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.cache import cached
from backend.models.counterparty import Counterparty
from backend.models.payroll import (
    PayrollEmployee,
    PayrollPayoutMark,
    PayrollSalaryPeriod,
    PayrollTariffStep,
    PayrollTeam,
    PayrollTeamMember,
    PayrollTeamScope,
)
from backend.models.transactions import Transaction
from backend.schemas.payroll import (
    EmployeeIn,
    EmployeeListResponse,
    EmployeeResponse,
    EmployeeUpdate,
    OfficialTxn,
    OkResponse,
    PayoutMarkIn,
    PayrollSheetResponse,
    SalaryPeriodOut,
    SheetEmployee,
    SheetPayout,
    SheetTeam,
    SheetTeamAccrual,
    SheetTotals,
    SheetWeekAccrual,
    SheetWeekRef,
    TariffReplace,
    TariffResponse,
    TariffStepOut,
    TeamIn,
    TeamListResponse,
    TeamMemberOut,
    TeamMembersReplace,
    TeamResponse,
    TeamScopeIn,
    TeamScopesReplace,
    TeamUpdate,
)
from backend.services import wb_bdr_service
from backend.utils.time import utcnow

ZERO = Decimal("0")
TWO_PLACES = Decimal("0.01")

# Дефолтный график выплат фикса: 50/50 на 10-е и 25-е следующего месяца.
DEFAULT_FIXED_PAY_DAYS: list[dict[str, Any]] = [
    {"day": 10, "share": 0.5},
    {"day": 25, "share": 0.5},
]

# Разумные лимиты выборок (iron rule: .all() без limit запрещён).
_EMPLOYEES_LIMIT = 1000
_TEAMS_LIMIT = 500
_TARIFF_STEPS_LIMIT = 200
_OFFICIAL_TXNS_LIMIT = 5000  # страховочный потолок всей выборки месяца выплат
_OFFICIAL_TXNS_PER_EMPLOYEE = 50  # окно на сотрудника (ROW_NUMBER по дате DESC)
_MARKS_LIMIT = 2000
_SCOPE_OPTIONS_LIMIT = 500


class PayrollNotFoundError(Exception):
    """Сущность не найдена в этом проекте (чужой или несуществующий id)."""


def _money(value: Decimal | int | float | str) -> Decimal:
    """Округление денег до 2 знаков (Decimal, HALF_UP)."""
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _next_month(d: date) -> date:
    """Первое число следующего месяца."""
    if d.month == 12:
        return date(d.year + 1, 1, 1)
    return date(d.year, d.month + 1, 1)


def _parse_month(month: str) -> date:
    """'YYYY-MM' → первое число месяца. ValueError на мусор."""
    try:
        return datetime.strptime(month, "%Y-%m").date().replace(day=1)
    except ValueError as exc:
        raise ValueError("month must be 'YYYY-MM'") from exc


# ─── Чистые функции расчёта ──────────────────────────────────────────────────


def weeks_for_month(month: date) -> list[tuple[date, date]]:
    """
    WB-недели (Пн–Вс), принадлежащие месяцу.

    Неделя относится к месяцу, в котором лежит её ЧЕТВЕРГ (= большинство дней):
    итерируем четверги месяца, неделя = (чт − 3 дня, чт + 3 дня).
    """
    first = month.replace(day=1)
    offset = (3 - first.weekday()) % 7  # 3 = четверг
    thursday = first + timedelta(days=offset)
    weeks: list[tuple[date, date]] = []
    while thursday.month == first.month and thursday.year == first.year:
        weeks.append((thursday - timedelta(days=3), thursday + timedelta(days=3)))
        thursday += timedelta(days=7)
    return weeks


def resolve_step(
    base: Decimal, steps: Sequence[PayrollTariffStep]
) -> PayrollTariffStep | None:
    """
    Ступень лестницы для базы недели: наибольший threshold <= base.

    base < минимального порога → МИНИМАЛЬНАЯ ступень; base <= 0 → None (ставка 0).
    """
    if base <= 0 or not steps:
        return None
    ordered = sorted(steps, key=lambda s: s.threshold)
    chosen = ordered[0]  # ниже минимального порога — минимальная ступень
    for step in ordered:
        if step.threshold <= base:
            chosen = step
        else:
            break
    return chosen


def resolve_salary(
    periods: Sequence[PayrollSalaryPeriod], month: date
) -> Decimal | None:
    """
    Оклад месяца: amount периода с max(valid_from) <= первое число месяца.

    До первого периода — None (начисления нет: история окладов не переписывает
    прошлые месяцы задним числом).
    """
    first = month.replace(day=1)
    best_from: date | None = None
    best_amount: Decimal | None = None
    for period in periods:
        if period.valid_from <= first and (best_from is None or period.valid_from > best_from):
            best_from = period.valid_from
            best_amount = period.amount
    return best_amount


def build_payout_plan(
    percent_total: Decimal,
    fixed_salary: Decimal,
    fixed_pay_days: list[dict[str, Any]] | None,
    month: date,
) -> list[dict[str, Any]]:
    """
    План выплат сотрудника за расчётный месяц.

    Процентная часть — 50/50 на 10-е и 25-е следующего месяца; фикс — по графику
    fixed_pay_days (None → дефолт 50/50 на 10/25). Строки мержатся по дню выплаты.
    Хвост округления уходит в последнюю строку каждой части — сумма сохраняется.
    """
    pay_month = _next_month(month.replace(day=1))
    amounts: dict[int, Decimal] = {}

    percent_total = _money(percent_total)
    if percent_total > 0:
        half = _money(percent_total / 2)
        amounts[10] = amounts.get(10, ZERO) + half
        amounts[25] = amounts.get(25, ZERO) + (percent_total - half)

    fixed_total = _money(fixed_salary)
    if fixed_total > 0:
        days = fixed_pay_days or DEFAULT_FIXED_PAY_DAYS
        acc = ZERO
        for i, item in enumerate(days):
            day = int(item["day"])
            if i == len(days) - 1:
                part = fixed_total - acc  # хвост округления — в последний день
            else:
                part = _money(fixed_total * Decimal(str(item["share"])))
                acc += part
            amounts[day] = amounts.get(day, ZERO) + part

    return [
        {"pay_day": day, "pay_date": pay_month.replace(day=day), "amount": amount}
        for day, amount in sorted(amounts.items())
        if amount != 0
    ]


# ─── Загрузчики ──────────────────────────────────────────────────────────────


async def _load_tariff_steps(db: AsyncSession, on_date: date) -> list[PayrollTariffStep]:
    """Действующий набор лестницы: строки максимального valid_from <= on_date."""
    valid_from = (
        await db.execute(
            select(func.max(PayrollTariffStep.valid_from)).where(
                PayrollTariffStep.valid_from <= on_date
            )
        )
    ).scalar()
    if valid_from is None:
        return []
    rows = (
        await db.execute(
            select(PayrollTariffStep)
            .where(PayrollTariffStep.valid_from == valid_from)
            .order_by(PayrollTariffStep.threshold)
            .limit(_TARIFF_STEPS_LIMIT)
        )
    ).scalars()
    return list(rows)


async def _get_employee(
    db: AsyncSession, project_id: int, employee_id: int
) -> PayrollEmployee:
    emp = (
        await db.execute(
            select(PayrollEmployee)
            .where(
                PayrollEmployee.id == employee_id,
                PayrollEmployee.project_id == project_id,
                PayrollEmployee.is_deleted == False,  # noqa: E712
            )
            .options(selectinload(PayrollEmployee.salary_periods))
            # replace-набор периодов пишется мимо relationship — без
            # populate_existing identity map отдал бы устаревшую коллекцию
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if emp is None:
        raise PayrollNotFoundError(f"Сотрудник {employee_id} не найден")
    return emp


async def _get_team(db: AsyncSession, project_id: int, team_id: int) -> PayrollTeam:
    team = (
        await db.execute(
            select(PayrollTeam)
            .where(
                PayrollTeam.id == team_id,
                PayrollTeam.project_id == project_id,
                PayrollTeam.is_deleted == False,  # noqa: E712
            )
            .options(
                selectinload(PayrollTeam.scopes),
                selectinload(PayrollTeam.members).selectinload(PayrollTeamMember.employee),
            )
            # replace_* удаляет/вставляет строки scopes/members мимо relationship —
            # без populate_existing identity map вернул бы УСТАРЕВШИЕ коллекции.
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if team is None:
        raise PayrollNotFoundError(f"Команда {team_id} не найдена")
    return team


def _team_response(team: PayrollTeam) -> TeamResponse:
    """Team → TeamResponse. Члены — только не удалённые сотрудники (selectinload
    НЕ фильтрует is_deleted — фильтруем в Python)."""
    members = [
        TeamMemberOut(employee_id=m.employee_id, name=m.employee.name)
        for m in sorted(team.members, key=lambda m: m.id)
        if m.employee is not None and not m.employee.is_deleted
    ]
    scopes = [
        TeamScopeIn(kind=s.kind, value=s.value)
        for s in sorted(team.scopes, key=lambda s: (s.kind, s.value))
    ]
    return TeamResponse(
        id=team.id, name=team.name, is_active=team.is_active, scopes=scopes, members=members
    )


# ─── Ведомость ───────────────────────────────────────────────────────────────


@cached(prefix="payroll:sheet", ttl=3600)
async def get_sheet(db: AsyncSession, project_id: int, month: str) -> dict:
    """
    Месячная ведомость: команды × недели, начисления сотрудников, план выплат,
    факт официалки из выписки. Возвращает JSON-safe dict (контракт
    PayrollSheetResponse) — кэш-хит и мисс отдают одинаковые типы.
    """
    month_start = _parse_month(month)
    weeks = weeks_for_month(month_start)
    month_end = _next_month(month_start) - timedelta(days=1)

    # Команды и сотрудники (soft-delete фильтр в SQL, активность — в Python)
    teams_rows = (
        await db.execute(
            select(PayrollTeam)
            .where(
                PayrollTeam.project_id == project_id,
                PayrollTeam.is_deleted == False,  # noqa: E712
            )
            .options(
                selectinload(PayrollTeam.scopes),
                selectinload(PayrollTeam.members).selectinload(PayrollTeamMember.employee),
            )
            .order_by(PayrollTeam.id)
            .limit(_TEAMS_LIMIT)
        )
    ).scalars()
    active_teams = [t for t in teams_rows if t.is_active]

    employees = list(
        (
            await db.execute(
                select(PayrollEmployee)
                .where(
                    PayrollEmployee.project_id == project_id,
                    PayrollEmployee.is_deleted == False,  # noqa: E712
                )
                .options(selectinload(PayrollEmployee.salary_periods))
                .order_by(PayrollEmployee.id)
                .limit(_EMPLOYEES_LIMIT)
            )
        ).scalars()
    )

    steps = await _load_tariff_steps(db, month_end)

    # БДР по неделям: один вызов на (неделя × kind), только если скоупы kind есть
    kinds_needed = {s.kind for t in active_teams for s in t.scopes}
    week_payouts: dict[tuple[date, str], dict[str, Decimal]] = {}
    for week_from, week_to in weeks:
        for kind in ("brand", "subject"):
            if kind not in kinds_needed:
                continue
            bdr = await wb_bdr_service.get_wb_bdr(
                db,
                project_id,
                week_from,
                week_to,
                group_by=kind,
                period_mode="report",
                # Ведомости нужен только net_payout — себестоимость/налог не
                # считаем (fifo-оценка гоняла бы сотни тысяч строк на КАЖДУЮ
                # из ~8 недель-вызовов месяца).
                include_cost_tax=False,
            )
            payouts_map: dict[str, Decimal] = {}
            for art in bdr.get("articles", []):
                name = art.get("sa_name") or ""
                if name:
                    payouts_map[name] = Decimal(str(art.get("net_payout", 0) or 0))
            week_payouts[(week_from, kind)] = payouts_map

    # Начисления команд по неделям
    sheet_teams: list[SheetTeam] = []
    # employee_id → team_id → Σ per_member за месяц
    accrual_by_employee: dict[int, dict[int, Decimal]] = {}
    team_names: dict[int, str] = {}
    team_active_members: dict[int, list[PayrollEmployee]] = {}

    for team in active_teams:
        team_names[team.id] = team.name
        active_members = [
            m.employee
            for m in sorted(team.members, key=lambda m: m.id)
            if m.employee is not None
            and not m.employee.is_deleted
            and m.employee.is_active
        ]
        team_active_members[team.id] = active_members
        n_members = len(active_members)
        week_rows: list[SheetWeekAccrual] = []
        team_total = ZERO
        for week_from, week_to in weeks:
            base = ZERO
            for scope in team.scopes:
                base += week_payouts.get((week_from, scope.kind), {}).get(scope.value, ZERO)
            base = _money(base)
            step = resolve_step(base, steps)
            if step is None:
                threshold: Decimal | None = None
                team_rate = ZERO
                team_amount = _money(ZERO)
            else:
                threshold = step.threshold
                team_rate = step.team_rate
                team_amount = _money(base * team_rate)
            per_member = _money(team_amount / n_members) if n_members else _money(ZERO)
            team_total += team_amount
            week_rows.append(
                SheetWeekAccrual(
                    date_from=week_from,
                    date_to=week_to,
                    base_amount=base,
                    threshold=threshold,
                    team_rate=team_rate,
                    team_amount=team_amount,
                    per_member=per_member,
                )
            )
            for emp in active_members:
                by_team = accrual_by_employee.setdefault(emp.id, {})
                by_team[team.id] = by_team.get(team.id, ZERO) + per_member

        sheet_teams.append(
            SheetTeam(
                team_id=team.id,
                name=team.name,
                scopes=[
                    TeamScopeIn(kind=s.kind, value=s.value)
                    for s in sorted(team.scopes, key=lambda s: (s.kind, s.value))
                ],
                member_names=[e.name for e in active_members],
                weeks=week_rows,
                total_amount=_money(team_total),
            )
        )

    # ── Агентство: manager-доля клиентов уходит команде отдельной строкой ──
    # Лениво (payroll_agency_service импортирует этот модуль — не зациклиться).
    from backend.services import payroll_agency_service

    agency_by_team = await payroll_agency_service.get_team_agency_amounts(
        db, project_id, month
    )
    # employee_id → доп. строки team_accruals «{team} · {client} (агентство)»;
    # попадают в accrued_total / план выплат / ФОТ «Менеджеры» автоматически.
    agency_rows_by_employee: dict[int, list[SheetTeamAccrual]] = {}
    for team in active_teams:
        client_amounts = agency_by_team.get(team.id, [])
        if not client_amounts:
            continue
        members = team_active_members.get(team.id, [])
        if not members:
            continue  # начислять некому — команда без активных участников
        for client_name, manager_amount in client_amounts:
            n_split = len(members)
            per_member = _money(manager_amount / n_split)
            # Хвост округления — последнему участнику (как в build_payout_plan):
            # иначе Σ строк ведомости теряет до n/2 копеек против totals_manager
            last_amount = _money(manager_amount - per_member * (n_split - 1))
            label = f"{team.name} · {client_name} (агентство)"
            for i, emp in enumerate(members):
                amount = last_amount if i == n_split - 1 else per_member
                agency_rows_by_employee.setdefault(emp.id, []).append(
                    SheetTeamAccrual(team_id=team.id, team_name=label, amount=amount)
                )

    # Официалка: транзакции за КАЛЕНДАРНЫЙ месяц, СЛЕДУЮЩИЙ за расчётным
    pay_month_start = _next_month(month_start)
    pay_month_end = _next_month(pay_month_start)
    cp_ids = {e.counterparty_id for e in employees if e.counterparty_id is not None}
    official_sum_by_cp: dict[int, Decimal] = {}
    official_txns_by_cp: dict[int, list[Transaction]] = {}
    if cp_ids:
        date_lo = datetime.combine(pay_month_start, time.min)
        date_hi = datetime.combine(pay_month_end, time.min)
        sum_rows = await db.execute(
            select(Transaction.counterparty_id, func.coalesce(func.sum(Transaction.expense), 0))
            .where(
                Transaction.project_id == project_id,
                Transaction.is_deleted == False,  # noqa: E712
                Transaction.counterparty_id.in_(cp_ids),
                Transaction.expense > 0,
                Transaction.date >= date_lo,
                Transaction.date < date_hi,
            )
            .group_by(Transaction.counterparty_id)
        )
        for cp_id, total in sum_rows:
            official_sum_by_cp[int(cp_id)] = Decimal(str(total))

        # Окно per-сотрудник (ROW_NUMBER по дате DESC): общий лимит с ORDER BY
        # date срезал бы ХВОСТ месяца — сотрудник с выплатой в конце месяца
        # получал бы пустой список при ненулевой сумме.
        rn = (
            func.row_number()
            .over(partition_by=Transaction.counterparty_id, order_by=Transaction.date.desc())
            .label("rn")
        )
        ranked = (
            select(Transaction.id.label("t_id"), rn)
            .where(
                Transaction.project_id == project_id,
                Transaction.is_deleted == False,  # noqa: E712
                Transaction.counterparty_id.in_(cp_ids),
                Transaction.expense > 0,
                Transaction.date >= date_lo,
                Transaction.date < date_hi,
            )
            .subquery()
        )
        txn_rows = (
            await db.execute(
                select(Transaction)
                .join(ranked, ranked.c.t_id == Transaction.id)
                .where(ranked.c.rn <= _OFFICIAL_TXNS_PER_EMPLOYEE)
                .order_by(Transaction.date)
                .limit(_OFFICIAL_TXNS_LIMIT)
            )
        ).scalars()
        for tx in txn_rows:
            if tx.counterparty_id is None:
                continue
            official_txns_by_cp.setdefault(tx.counterparty_id, []).append(tx)

    # Отметки выплат расчётного месяца
    marks_rows = (
        await db.execute(
            select(PayrollPayoutMark)
            .where(
                PayrollPayoutMark.project_id == project_id,
                PayrollPayoutMark.month == month_start,
            )
            .limit(_MARKS_LIMIT)
        )
    ).scalars()
    marks = {(m.employee_id, m.pay_day): m for m in marks_rows}

    # Сотрудники
    sheet_employees: list[SheetEmployee] = []
    totals_accrued = ZERO
    totals_official = ZERO
    for emp in employees:
        by_team = accrual_by_employee.get(emp.id, {})
        team_accruals = [
            SheetTeamAccrual(team_id=tid, team_name=team_names.get(tid, ""), amount=_money(amt))
            for tid, amt in sorted(by_team.items())
        ]
        # Агентские строки — после обычных; процентная часть (и ФОТ «Менеджеры»)
        # суммирует ВСЕ team_accruals, включая агентские
        team_accruals.extend(agency_rows_by_employee.get(emp.id, []))
        percent_total = _money(sum((a.amount for a in team_accruals), ZERO))
        # Оклад расчётного месяца — из истории периодов (не сегодняшний!)
        salary = resolve_salary(emp.salary_periods, month_start) if emp.is_active else None
        fixed_accrual = _money(salary) if salary is not None else _money(ZERO)
        accrued_total = _money(percent_total + fixed_accrual)

        # Неактивный сотрудник без начислений в ведомости не показывается
        if not emp.is_active and accrued_total == 0:
            continue

        official_paid = _money(
            official_sum_by_cp.get(emp.counterparty_id, ZERO)
            if emp.counterparty_id is not None
            else ZERO
        )
        official_txns = [
            OfficialTxn(date=tx.date, amount=tx.expense, purpose=tx.purpose, bank=tx.bank)
            for tx in (
                official_txns_by_cp.get(emp.counterparty_id, [])
                if emp.counterparty_id is not None
                else []
            )
        ]
        unofficial_due = _money(accrued_total - official_paid)  # может быть < 0 — не клампим

        plan = build_payout_plan(
            percent_total, fixed_accrual, emp.fixed_pay_days, month_start
        )
        payouts: list[SheetPayout] = []
        for row in plan:
            mark = marks.get((emp.id, int(row["pay_day"])))
            payouts.append(
                SheetPayout(
                    pay_day=int(row["pay_day"]),
                    pay_date=row["pay_date"],
                    amount=row["amount"],
                    paid=bool(mark.paid) if mark is not None else False,
                    paid_amount=mark.amount if mark is not None else None,
                    comment=mark.comment if mark is not None else None,
                )
            )

        totals_accrued += accrued_total
        totals_official += official_paid
        sheet_employees.append(
            SheetEmployee(
                employee_id=emp.id,
                name=emp.name,
                position=emp.position,
                counterparty_id=emp.counterparty_id,
                team_accruals=team_accruals,
                fixed_accrual=fixed_accrual,
                accrued_total=accrued_total,
                official_paid=official_paid,
                official_txns=official_txns,
                unofficial_due=unofficial_due,
                payouts=payouts,
            )
        )

    response = PayrollSheetResponse(
        month=month,
        weeks=[SheetWeekRef(date_from=wf, date_to=wt) for wf, wt in weeks],
        teams=sheet_teams,
        employees=sheet_employees,
        totals=SheetTotals(
            accrued_total=_money(totals_accrued),
            official_total=_money(totals_official),
            unofficial_total=_money(totals_accrued - totals_official),
        ),
    )
    # JSON-safe dict: кэш (json.dumps/loads) и прямой вызов отдают одно и то же
    return response.model_dump(mode="json")


# Подстрока фикс-окладов для сотрудников без заполненной должности.
NO_POSITION_LABEL = "Без должности"


@cached(prefix="payroll:accruals2", ttl=86400)
async def get_month_accrual(db: AsyncSession, project_id: int, month: str) -> dict:
    """
    Начисление проекта за ОДИН месяц с разбивкой для подстрок ОПиУ:
    {"managers": "...", "by_position": {"Бухгалтер": "..."}, "total": "..."}.

    «Менеджеры» — ВСЕ процентные начисления команд (независимо от должности);
    фикс-оклады группируются по position (пустая → «Без должности»).
    total == managers + Σ by_position (ведомость считает так же).

    Кэш по (project_id, month) — ОПиУ с диапазоном в год не зависит от полного
    каскада ведомостей. Значения строками-Decimal — JSON-safe, кэш-хит и мисс
    отдают одинаковый тип. Префикс accruals2: форма ответа сменилась со строки
    на dict, старые ключи payroll:accruals дочистит TTL.
    """
    # Без активных сотрудников начислений не бывает — не собираем ведомость зря
    has_employees = (
        await db.execute(
            select(PayrollEmployee.id)
            .where(
                PayrollEmployee.project_id == project_id,
                PayrollEmployee.is_deleted == False,  # noqa: E712
                PayrollEmployee.is_active == True,  # noqa: E712
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if has_employees is None:
        return {"managers": "0.00", "by_position": {}, "total": "0.00"}

    sheet = await get_sheet(db, project_id, month)
    managers = ZERO
    # Группировка регистронезависимо («Бухгалтер»/«бухгалтер» — одна подстрока),
    # метка — первое встреченное написание.
    by_position: dict[str, Decimal] = {}
    pos_labels: dict[str, str] = {}
    for emp in sheet["employees"]:
        percent = sum(
            (Decimal(str(a["amount"])) for a in emp.get("team_accruals", [])), ZERO
        )
        managers += percent
        fixed = Decimal(str(emp.get("fixed_accrual", "0")))
        if fixed:
            # .get: в кэше могут жить ведомости, собранные до появления position
            raw = (emp.get("position") or "").strip()
            key = raw.casefold() or "__none__"
            pos_labels.setdefault(key, raw or NO_POSITION_LABEL)
            by_position[key] = by_position.get(key, ZERO) + fixed
    return {
        "managers": str(_money(managers)),
        "by_position": {
            pos_labels[k]: str(_money(v)) for k, v in sorted(by_position.items())
        },
        "total": str(Decimal(str(sheet["totals"]["accrued_total"]))),
    }


async def get_monthly_accruals(
    db: AsyncSession, project_id: int, months: list[str]
) -> dict[str, dict[str, Any]]:
    """
    Начисления по месяцам — база строки «ФОТ (начислено)» и её подстрок в ОПиУ.

    month → {"managers": Decimal, "by_position": {pos: Decimal}, "total": Decimal}.
    Помесячная обёртка над get_month_accrual (и его кэшем payroll:accruals2).
    """
    result: dict[str, dict[str, Any]] = {}
    for month in months:
        raw = await get_month_accrual(db, project_id, month)
        result[month] = {
            "managers": Decimal(str(raw.get("managers", "0"))),
            "by_position": {
                pos: Decimal(str(v)) for pos, v in (raw.get("by_position") or {}).items()
            },
            "total": Decimal(str(raw.get("total", "0"))),
        }
    return result


# ─── CRUD: сотрудники ────────────────────────────────────────────────────────


async def _counterparty_names(
    db: AsyncSession, project_id: int, cp_ids: set[int]
) -> dict[int, str]:
    if not cp_ids:
        return {}
    rows = await db.execute(
        select(Counterparty.id, Counterparty.name).where(
            Counterparty.project_id == project_id,
            Counterparty.id.in_(cp_ids),
            Counterparty.is_deleted == False,  # noqa: E712
        )
    )
    return {int(r[0]): r[1] for r in rows}


async def _team_names_by_employee(
    db: AsyncSession, project_id: int
) -> dict[int, list[str]]:
    rows = await db.execute(
        select(PayrollTeamMember.employee_id, PayrollTeam.name)
        .join(PayrollTeam, PayrollTeam.id == PayrollTeamMember.team_id)
        .where(
            PayrollTeam.project_id == project_id,
            PayrollTeam.is_deleted == False,  # noqa: E712
        )
        .order_by(PayrollTeam.id)
        .limit(_TEAMS_LIMIT * 10)
    )
    result: dict[int, list[str]] = {}
    for employee_id, team_name in rows:
        result.setdefault(int(employee_id), []).append(team_name)
    return result


def _employee_response(
    emp: PayrollEmployee,
    counterparty_name: str | None = None,
    team_names: list[str] | None = None,
) -> EmployeeResponse:
    periods = sorted(emp.salary_periods, key=lambda p: p.valid_from)
    return EmployeeResponse(
        id=emp.id,
        name=emp.name,
        position=emp.position,
        counterparty_id=emp.counterparty_id,
        counterparty_name=counterparty_name,
        salary_periods=[
            SalaryPeriodOut(month=p.valid_from.strftime("%Y-%m"), amount=p.amount)
            for p in periods
        ],
        # Оклад, действующий в ТЕКУЩЕМ месяце (None — период ещё не начался)
        current_salary=resolve_salary(periods, utcnow().date().replace(day=1)),
        fixed_pay_days=emp.fixed_pay_days,
        is_active=emp.is_active,
        notes=emp.notes,
        team_names=team_names or [],
    )


async def list_employees(db: AsyncSession, project_id: int) -> EmployeeListResponse:
    """Сотрудники проекта + имя контрагента + названия команд."""
    employees = list(
        (
            await db.execute(
                select(PayrollEmployee)
                .where(
                    PayrollEmployee.project_id == project_id,
                    PayrollEmployee.is_deleted == False,  # noqa: E712
                )
                .options(selectinload(PayrollEmployee.salary_periods))
                .execution_options(populate_existing=True)
                .order_by(PayrollEmployee.id)
                .limit(_EMPLOYEES_LIMIT)
            )
        ).scalars()
    )
    cp_names = await _counterparty_names(
        db, project_id, {e.counterparty_id for e in employees if e.counterparty_id is not None}
    )
    team_names = await _team_names_by_employee(db, project_id)
    return EmployeeListResponse(
        items=[
            _employee_response(
                emp,
                counterparty_name=(
                    cp_names.get(emp.counterparty_id) if emp.counterparty_id else None
                ),
                team_names=team_names.get(emp.id, []),
            )
            for emp in employees
        ]
    )


async def _validate_counterparty(
    db: AsyncSession, project_id: int, counterparty_id: int
) -> None:
    cp = (
        await db.execute(
            select(Counterparty.id).where(
                Counterparty.id == counterparty_id,
                Counterparty.project_id == project_id,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if cp is None:
        raise PayrollNotFoundError(f"Контрагент {counterparty_id} не найден")


def _dump_pay_days(items: list | None) -> list[dict[str, Any]] | None:
    """PayDayShare-список → JSONB-friendly (float, не Decimal — asyncpg/json)."""
    if items is None:
        return None
    return [{"day": int(item.day), "share": float(item.share)} for item in items]


async def create_employee(
    db: AsyncSession, project_id: int, data: EmployeeIn
) -> EmployeeResponse:
    if data.counterparty_id is not None:
        await _validate_counterparty(db, project_id, data.counterparty_id)
    emp = PayrollEmployee(
        project_id=project_id,
        name=data.name,
        position=data.position,
        counterparty_id=data.counterparty_id,
        fixed_pay_days=_dump_pay_days(data.fixed_pay_days),
        is_active=data.is_active,
        notes=data.notes,
    )
    for period in data.salary_periods or []:
        emp.salary_periods.append(
            PayrollSalaryPeriod(
                valid_from=_parse_month(period.month), amount=period.amount
            )
        )
    db.add(emp)
    await db.flush()
    emp_id = emp.id
    await db.commit()
    # refresh() экспайрит relationship → лениво грузить salary_periods в async
    # нельзя (MissingGreenlet); перечитываем с selectinload
    emp = await _get_employee(db, project_id, emp_id)
    cp_names = await _counterparty_names(
        db, project_id, {emp.counterparty_id} if emp.counterparty_id else set()
    )
    return _employee_response(
        emp, counterparty_name=cp_names.get(emp.counterparty_id or 0)
    )


async def update_employee(
    db: AsyncSession, project_id: int, employee_id: int, data: EmployeeUpdate
) -> EmployeeResponse:
    """Partial update. clear_* флаги — явное обнуление nullable-полей."""
    emp = await _get_employee(db, project_id, employee_id)
    provided = data.model_fields_set

    if data.name is not None:
        emp.name = data.name
    if data.clear_position:
        emp.position = None
    elif data.position is not None:
        emp.position = data.position
    if data.clear_counterparty:
        emp.counterparty_id = None
    elif data.counterparty_id is not None:
        await _validate_counterparty(db, project_id, data.counterparty_id)
        emp.counterparty_id = data.counterparty_id
    if data.salary_periods is not None:
        # None — не трогать; список (в т.ч. пустой) — полная замена истории
        await db.execute(
            delete(PayrollSalaryPeriod).where(
                PayrollSalaryPeriod.employee_id == emp.id
            )
        )
        for period in data.salary_periods:
            db.add(
                PayrollSalaryPeriod(
                    employee_id=emp.id,
                    valid_from=_parse_month(period.month),
                    amount=period.amount,
                )
            )
    if "fixed_pay_days" in provided:
        emp.fixed_pay_days = _dump_pay_days(data.fixed_pay_days)
    if data.is_active is not None:
        emp.is_active = data.is_active
    if "notes" in provided:
        emp.notes = data.notes

    await db.commit()
    # Периоды писались мимо relationship — перечитываем сотрудника целиком
    emp = await _get_employee(db, project_id, employee_id)
    cp_names = await _counterparty_names(
        db, project_id, {emp.counterparty_id} if emp.counterparty_id else set()
    )
    team_names = await _team_names_by_employee(db, project_id)
    return _employee_response(
        emp,
        counterparty_name=cp_names.get(emp.counterparty_id or 0),
        team_names=team_names.get(emp.id, []),
    )


async def delete_employee(
    db: AsyncSession, project_id: int, employee_id: int
) -> OkResponse:
    """Soft-delete сотрудника + удаление его членств в командах."""
    emp = await _get_employee(db, project_id, employee_id)
    emp.soft_delete()
    await db.execute(
        delete(PayrollTeamMember).where(PayrollTeamMember.employee_id == emp.id)
    )
    await db.commit()
    return OkResponse()


# ─── CRUD: команды ───────────────────────────────────────────────────────────


async def list_teams(db: AsyncSession, project_id: int) -> TeamListResponse:
    teams = (
        await db.execute(
            select(PayrollTeam)
            .where(
                PayrollTeam.project_id == project_id,
                PayrollTeam.is_deleted == False,  # noqa: E712
            )
            .options(
                selectinload(PayrollTeam.scopes),
                selectinload(PayrollTeam.members).selectinload(PayrollTeamMember.employee),
            )
            .order_by(PayrollTeam.id)
            .limit(_TEAMS_LIMIT)
        )
    ).scalars()
    return TeamListResponse(items=[_team_response(t) for t in teams])


async def create_team(db: AsyncSession, project_id: int, data: TeamIn) -> TeamResponse:
    team = PayrollTeam(project_id=project_id, name=data.name, is_active=data.is_active)
    db.add(team)
    await db.commit()
    team = await _get_team(db, project_id, team.id)
    return _team_response(team)


async def update_team(
    db: AsyncSession, project_id: int, team_id: int, data: TeamUpdate
) -> TeamResponse:
    team = await _get_team(db, project_id, team_id)
    if data.name is not None:
        team.name = data.name
    if data.is_active is not None:
        team.is_active = data.is_active
    await db.commit()
    team = await _get_team(db, project_id, team_id)
    return _team_response(team)


async def delete_team(db: AsyncSession, project_id: int, team_id: int) -> OkResponse:
    team = await _get_team(db, project_id, team_id)
    team.soft_delete()
    await db.commit()
    return OkResponse()


async def replace_team_scopes(
    db: AsyncSession, project_id: int, team_id: int, data: TeamScopesReplace
) -> TeamResponse:
    """Полная замена скоупов команды (kind+value, дедуп)."""
    team = await _get_team(db, project_id, team_id)
    await db.execute(delete(PayrollTeamScope).where(PayrollTeamScope.team_id == team.id))
    seen: set[tuple[str, str]] = set()
    for scope in data.scopes:
        key = (scope.kind, scope.value)
        if key in seen:
            continue
        seen.add(key)
        db.add(PayrollTeamScope(team_id=team.id, kind=scope.kind, value=scope.value))
    await db.commit()
    team = await _get_team(db, project_id, team_id)
    return _team_response(team)


async def replace_team_members(
    db: AsyncSession, project_id: int, team_id: int, data: TeamMembersReplace
) -> TeamResponse:
    """Полная замена участников. Все employee_ids обязаны принадлежать проекту."""
    team = await _get_team(db, project_id, team_id)
    unique_ids = list(dict.fromkeys(data.employee_ids))
    if unique_ids:
        found = {
            int(r)
            for r in (
                await db.execute(
                    select(PayrollEmployee.id).where(
                        PayrollEmployee.project_id == project_id,
                        PayrollEmployee.id.in_(unique_ids),
                        PayrollEmployee.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalars()
        }
        missing = [eid for eid in unique_ids if eid not in found]
        if missing:
            raise PayrollNotFoundError(f"Сотрудники не найдены: {missing}")
    await db.execute(
        delete(PayrollTeamMember).where(PayrollTeamMember.team_id == team.id)
    )
    for employee_id in unique_ids:
        db.add(PayrollTeamMember(team_id=team.id, employee_id=employee_id))
    await db.commit()
    team = await _get_team(db, project_id, team_id)
    return _team_response(team)


# ─── Скоупы: справочник значений ─────────────────────────────────────────────


@cached(prefix="payroll:scope_options", ttl=3600)
async def get_scope_options(db: AsyncSession, project_id: int) -> dict:
    """
    DISTINCT бренды/категории из wb_finance_rows проекта.

    Кэш обязателен: два DISTINCT-прохода по миллионам строк wb_finance_rows на
    каждое открытие таба. Индексы осознанно не заводим — часовой кэш покрывает
    (инвалидация — через invalidate_project_reports после ETL-синка).
    Возврат dict (не pydantic) — JSON-safe для кэша; форму даёт response_model.
    """
    brands = (
        await db.execute(
            text(
                "SELECT DISTINCT brand_name FROM wb_finance_rows"
                " WHERE project_id = :project_id"
                " AND brand_name IS NOT NULL AND brand_name != ''"
                " ORDER BY 1 LIMIT :lim"
            ),
            {"project_id": project_id, "lim": _SCOPE_OPTIONS_LIMIT},
        )
    ).scalars()
    subjects = (
        await db.execute(
            text(
                "SELECT DISTINCT subject_name FROM wb_finance_rows"
                " WHERE project_id = :project_id"
                " AND subject_name IS NOT NULL AND subject_name != ''"
                " ORDER BY 1 LIMIT :lim"
            ),
            {"project_id": project_id, "lim": _SCOPE_OPTIONS_LIMIT},
        )
    ).scalars()
    return {"brands": list(brands), "subjects": list(subjects)}


# ─── Тарифная лестница ───────────────────────────────────────────────────────


async def get_tariff(db: AsyncSession, on_date: date) -> TariffResponse:
    """Действующий набор ступеней на дату."""
    steps = await _load_tariff_steps(db, on_date)
    return TariffResponse(
        valid_from=steps[0].valid_from if steps else None,
        steps=[TariffStepOut.model_validate(s) for s in steps],
    )


async def replace_tariff(db: AsyncSession, data: TariffReplace) -> TariffResponse:
    """Полная замена набора ступеней для valid_from (глобально, без project_id)."""
    await db.execute(
        delete(PayrollTariffStep).where(PayrollTariffStep.valid_from == data.valid_from)
    )
    for step in data.steps:
        db.add(
            PayrollTariffStep(
                valid_from=data.valid_from,
                threshold=step.threshold,
                company_rate=step.company_rate,
                team_rate=step.team_rate,
            )
        )
    await db.commit()
    rows = (
        await db.execute(
            select(PayrollTariffStep)
            .where(PayrollTariffStep.valid_from == data.valid_from)
            .order_by(PayrollTariffStep.threshold)
            .limit(_TARIFF_STEPS_LIMIT)
        )
    ).scalars()
    return TariffResponse(
        valid_from=data.valid_from,
        steps=[TariffStepOut.model_validate(s) for s in rows],
    )


# ─── Отметки выплат ──────────────────────────────────────────────────────────


async def upsert_payout_mark(
    db: AsyncSession, project_id: int, data: PayoutMarkIn
) -> OkResponse:
    """
    Upsert отметки по (employee, month, pay_day). Модель без SoftDeleteMixin.

    Атомарно через ON CONFLICT (uq_payroll_payout_mark): SELECT→INSERT под
    гонкой двух кликов давал бы IntegrityError/500.
    """
    await _get_employee(db, project_id, data.employee_id)  # 404 на чужой id
    month_start = _parse_month(data.month)
    now = utcnow()
    stmt = (
        pg_insert(PayrollPayoutMark)
        .values(
            project_id=project_id,
            employee_id=data.employee_id,
            month=month_start,
            pay_day=data.pay_day,
            paid=data.paid,
            amount=data.amount,
            comment=data.comment,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_payroll_payout_mark",
            set_={
                "paid": data.paid,
                "amount": data.amount,
                "comment": data.comment,
                "updated_at": now,
            },
        )
    )
    await db.execute(stmt)
    await db.commit()
    return OkResponse()
