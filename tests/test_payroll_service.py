"""
Tests for payroll_service: недели месяца, лестница, ведомость, ОПиУ-хук.

БДР (get_wb_bdr) в тестах ведомости мокается — расчёт не зависит от данных
wb_finance_rows. Ведомость зовём через get_sheet.__wrapped__ — мимо redis-кэша.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest

from backend.models.counterparty import Counterparty
from backend.models.payroll import PayrollTariffStep
from backend.models.transactions import Transaction
from backend.schemas.payroll import (
    EmployeeIn,
    PayDayShare,
    PayoutMarkIn,
    SalaryPeriodIn,
    TeamIn,
    TeamMembersReplace,
    TeamScopeIn,
    TeamScopesReplace,
)
from backend.services import payroll_service
from backend.services.payroll_service import (
    build_payout_plan,
    resolve_step,
    weeks_for_month,
)

D = Decimal

# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _create_project(client, auth_headers) -> int:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"payroll_test_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    return resp.json()["id"]


def _inn() -> str:
    return str(int(uuid.uuid4().hex[:9], 16) % 9000000000 + 1000000000)


async def _create_counterparty(db_session, project_id: int, primary_type: str = "LEGAL") -> int:
    cp = Counterparty(
        project_id=project_id,
        inn=_inn(),
        name=f"КА-{uuid.uuid4().hex[:6]}",
        primary_type=primary_type,
    )
    db_session.add(cp)
    await db_session.commit()
    await db_session.refresh(cp)
    return cp.id


def _fix(amount: str, month: str = "2020-01") -> list[SalaryPeriodIn]:
    """Один оклад-период с далёкого прошлого = поведение «оклад всегда был»."""
    return [SalaryPeriodIn(month=month, amount=D(amount))]


def _step(threshold: str, team_rate: str) -> PayrollTariffStep:
    return PayrollTariffStep(
        valid_from=date(2026, 1, 1),
        threshold=D(threshold),
        company_rate=D("0.02"),
        team_rate=D(team_rate),
    )


def _fake_bdr(by_brand=None, by_subject=None, calls=None):
    """Мок get_wb_bdr: net_payout по группам, одинаковый для всех недель."""

    async def fake(
        db, project_id, date_from, date_to, brand=None, article=None,
        group_by="article", period_mode="sale", include_cost_tax=True,
    ):
        assert period_mode == "report", "ведомость обязана считать целыми WB-отчётами"
        assert include_cost_tax is False, "ведомости не нужны себестоимость и налог"
        if calls is not None:
            calls.append((date_from, date_to, group_by))
        data = by_brand if group_by == "brand" else by_subject
        return {
            "articles": [
                {"sa_name": name, "net_payout": value} for name, value in (data or {}).items()
            ]
        }

    return fake


async def _sheet(db, project_id: int, month: str) -> dict:
    """Ведомость мимо redis-кэша (детерминизм в тестах)."""
    return await payroll_service.get_sheet.__wrapped__(db, project_id, month)


def _txn(project_id: int, cp_id: int, dt: datetime, expense: str, **kw) -> Transaction:
    base = dict(
        project_id=project_id,
        date=dt,
        bank="WB_BANK",
        account="40702810800000001893",
        currency="RUB",
        income=D("0"),
        expense=D(expense),
        net=D(expense) * -1,
        purpose="Выплата по договору",
        txn_id=f"payroll_t_{uuid.uuid4().hex[:12]}",
        status="OK",
        is_cashflow2=1,
        counterparty_id=cp_id,
    )
    base.update(kw)
    return Transaction(**base)


# ─── weeks_for_month ──────────────────────────────────────────────────────────


def test_weeks_for_month_june_2026():
    """Июнь-2026: ровно 4 недели 01–28.06; неделя 29.06–05.07 уходит в июль."""
    weeks = weeks_for_month(date(2026, 6, 1))
    assert weeks == [
        (date(2026, 6, 1), date(2026, 6, 7)),
        (date(2026, 6, 8), date(2026, 6, 14)),
        (date(2026, 6, 15), date(2026, 6, 21)),
        (date(2026, 6, 22), date(2026, 6, 28)),
    ]


def test_weeks_for_month_july_2026_takes_boundary_week():
    """Неделя 29.06–05.07 (четверг 02.07) принадлежит июлю."""
    weeks = weeks_for_month(date(2026, 7, 15))
    assert weeks[0] == (date(2026, 6, 29), date(2026, 7, 5))


def test_weeks_for_month_dec_jan_boundary():
    """Стык годов: неделя 29.12.2025–04.01.2026 (четверг 01.01) — январская."""
    dec = weeks_for_month(date(2025, 12, 1))
    assert dec[-1] == (date(2025, 12, 22), date(2025, 12, 28))
    assert (date(2025, 12, 29), date(2026, 1, 4)) not in dec

    jan = weeks_for_month(date(2026, 1, 1))
    assert jan[0] == (date(2025, 12, 29), date(2026, 1, 4))
    assert len(jan) == 5  # четверги 1, 8, 15, 22, 29 января


# ─── resolve_step ─────────────────────────────────────────────────────────────


def test_resolve_step_exact_and_between():
    steps = [_step("200000", "0.012"), _step("375000", "0.011"), _step("500000", "0.010")]
    assert resolve_step(D("375000"), steps).team_rate == D("0.011")  # точный порог
    assert resolve_step(D("400000"), steps).team_rate == D("0.011")  # между → нижний


def test_resolve_step_below_min_is_min_step():
    steps = [_step("200000", "0.012"), _step("375000", "0.011")]
    assert resolve_step(D("150000"), steps).team_rate == D("0.012")


def test_resolve_step_zero_and_negative_none():
    steps = [_step("200000", "0.012")]
    assert resolve_step(D("0"), steps) is None
    assert resolve_step(D("-50000"), steps) is None
    assert resolve_step(D("100"), []) is None  # пустая лестница


def test_resolve_step_above_max_is_max_step():
    steps = [_step("200000", "0.012"), _step("500000", "0.010")]
    assert resolve_step(D("99000000"), steps).team_rate == D("0.010")


# ─── build_payout_plan ────────────────────────────────────────────────────────


def test_payout_plan_percent_and_fixed_merge_5050():
    """Процент 50/50 + дефолтный фикс 50/50 мержатся по дням 10 и 25."""
    plan = build_payout_plan(D("10000"), D("30000"), None, date(2026, 6, 1))
    assert [(r["pay_day"], r["amount"]) for r in plan] == [
        (10, D("20000.00")),
        (25, D("20000.00")),
    ]
    assert plan[0]["pay_date"] == date(2026, 7, 10)
    assert plan[1]["pay_date"] == date(2026, 7, 25)


def test_payout_plan_custom_fixed_day_15():
    """fixed_pay_days [{15, 1}]: фикс одним платежом 15-го, процент — 10/25."""
    plan = build_payout_plan(
        D("10000"), D("30000"), [{"day": 15, "share": 1}], date(2026, 6, 1)
    )
    assert [(r["pay_day"], r["amount"]) for r in plan] == [
        (10, D("5000.00")),
        (15, D("30000.00")),
        (25, D("5000.00")),
    ]


def test_payout_plan_rounding_preserves_total():
    plan = build_payout_plan(D("100.01"), D("0"), None, date(2026, 6, 1))
    assert sum(r["amount"] for r in plan) == D("100.01")


def test_payout_plan_december_rolls_to_january():
    plan = build_payout_plan(D("1000"), D("0"), None, date(2025, 12, 1))
    assert plan[0]["pay_date"] == date(2026, 1, 10)


def test_payout_plan_empty_when_nothing_accrued():
    assert build_payout_plan(D("0"), D("0"), None, date(2026, 6, 1)) == []


# ─── Ведомость: команды, равный делёж ────────────────────────────────────────


async def test_sheet_team_accrual_equal_split(db_session, client, auth_headers, monkeypatch):
    pid = await _create_project(client, auth_headers)

    e1 = await payroll_service.create_employee(db_session, pid, EmployeeIn(name="Аня"))
    e2 = await payroll_service.create_employee(db_session, pid, EmployeeIn(name="Боря"))
    team = await payroll_service.create_team(db_session, pid, TeamIn(name="Ковры"))
    await payroll_service.replace_team_scopes(
        db_session, pid, team.id,
        TeamScopesReplace(scopes=[TeamScopeIn(kind="brand", value="BrandA")]),
    )
    await payroll_service.replace_team_members(
        db_session, pid, team.id, TeamMembersReplace(employee_ids=[e1.id, e2.id])
    )

    calls: list = []
    monkeypatch.setattr(
        "backend.services.wb_bdr_service.get_wb_bdr",
        _fake_bdr(by_brand={"BrandA": 700000, "BrandIgnored": 999999}, calls=calls),
    )

    sheet = await _sheet(db_session, pid, "2026-06")

    # 4 недели × только kind=brand (subject-скоупов нет) → 4 вызова БДР
    assert len(calls) == 4
    assert all(c[2] == "brand" for c in calls)

    # Ставка — из реальной (сидированной) лестницы на конец июня-2026
    steps = await payroll_service._load_tariff_steps(db_session, date(2026, 6, 30))
    assert steps, "лестница должна быть засидирована миграцией payroll01_domain"
    step = resolve_step(D("700000"), steps)
    week_amount = (D("700000") * step.team_rate).quantize(D("0.01"))

    assert len(sheet["teams"]) == 1
    t = sheet["teams"][0]
    assert len(t["weeks"]) == 4
    for week in t["weeks"]:
        assert D(week["base_amount"]) == D("700000.00")
        assert D(week["threshold"]) == step.threshold
        assert D(week["team_amount"]) == week_amount
        assert D(week["per_member"]) == (week_amount / 2).quantize(D("0.01"))
    assert D(t["total_amount"]) == week_amount * 4

    # Сотрудники получили поровну
    per_emp = (week_amount / 2).quantize(D("0.01")) * 4
    emps = {e["name"]: e for e in sheet["employees"]}
    assert D(emps["Аня"]["accrued_total"]) == per_emp
    assert D(emps["Боря"]["accrued_total"]) == per_emp
    assert D(sheet["totals"]["accrued_total"]) == per_emp * 2


async def test_sheet_negative_week_zero_rate(db_session, client, auth_headers, monkeypatch):
    """Отрицательная неделя: ставка 0, threshold=None, начисление 0."""
    pid = await _create_project(client, auth_headers)
    emp = await payroll_service.create_employee(db_session, pid, EmployeeIn(name="Соло"))
    team = await payroll_service.create_team(db_session, pid, TeamIn(name="Минус"))
    await payroll_service.replace_team_scopes(
        db_session, pid, team.id,
        TeamScopesReplace(scopes=[TeamScopeIn(kind="subject", value="Ковры")]),
    )
    await payroll_service.replace_team_members(
        db_session, pid, team.id, TeamMembersReplace(employee_ids=[emp.id])
    )
    monkeypatch.setattr(
        "backend.services.wb_bdr_service.get_wb_bdr",
        _fake_bdr(by_subject={"Ковры": -50000}),
    )

    sheet = await _sheet(db_session, pid, "2026-06")
    week = sheet["teams"][0]["weeks"][0]
    assert week["threshold"] is None
    assert D(week["team_amount"]) == 0
    # Активный сотрудник с нулём — в ведомости присутствует
    assert len(sheet["employees"]) == 1
    assert D(sheet["employees"][0]["accrued_total"]) == 0


# ─── Ведомость: фикс, официалка, план выплат ─────────────────────────────────


async def test_sheet_fixed_salary_official_window_and_dues(db_session, client, auth_headers):
    """Фикс за месяц; официалка — ТОЛЬКО календарный месяц M+1; unofficial = разница."""
    pid = await _create_project(client, auth_headers)
    cp_id = await _create_counterparty(db_session, pid)
    await payroll_service.create_employee(
        db_session, pid,
        EmployeeIn(name="Бухгалтер", counterparty_id=cp_id, salary_periods=_fix("100000")),
    )
    db_session.add_all([
        _txn(pid, cp_id, datetime(2026, 7, 10, 12, 0), "40000"),   # окно M+1 ✓
        _txn(pid, cp_id, datetime(2026, 7, 25, 12, 0), "20000"),   # окно M+1 ✓
        _txn(pid, cp_id, datetime(2026, 6, 15, 12, 0), "30000"),   # расчётный месяц ✗
        _txn(pid, cp_id, datetime(2026, 8, 1, 0, 0), "50000"),     # M+2 ✗
        _txn(pid, cp_id, datetime(2026, 7, 5, 12, 0), "7777", is_deleted=True),  # удалена ✗
    ])
    await db_session.commit()

    sheet = await _sheet(db_session, pid, "2026-06")
    emp = sheet["employees"][0]
    assert D(emp["fixed_accrual"]) == D("100000.00")
    assert D(emp["accrued_total"]) == D("100000.00")
    assert D(emp["official_paid"]) == D("60000.00")
    assert D(emp["unofficial_due"]) == D("40000.00")
    assert len(emp["official_txns"]) == 2
    assert [D(t["amount"]) for t in emp["official_txns"]] == [D("40000.00"), D("20000.00")]

    # План: 50/50 на 10-е и 25-е июля
    assert [(p["pay_day"], p["pay_date"], D(p["amount"])) for p in emp["payouts"]] == [
        (10, "2026-07-10", D("50000.00")),
        (25, "2026-07-25", D("50000.00")),
    ]

    assert D(sheet["totals"]["official_total"]) == D("60000.00")
    assert D(sheet["totals"]["unofficial_total"]) == D("40000.00")


async def test_sheet_unofficial_due_can_be_negative(db_session, client, auth_headers):
    """Переплата официалкой не клампится в ноль."""
    pid = await _create_project(client, auth_headers)
    cp_id = await _create_counterparty(db_session, pid)
    await payroll_service.create_employee(
        db_session, pid,
        EmployeeIn(name="Переплата", counterparty_id=cp_id, salary_periods=_fix("10000")),
    )
    db_session.add(_txn(pid, cp_id, datetime(2026, 7, 3, 9, 0), "15000"))
    await db_session.commit()

    sheet = await _sheet(db_session, pid, "2026-06")
    assert D(sheet["employees"][0]["unofficial_due"]) == D("-5000.00")


async def test_sheet_custom_fixed_pay_days(db_session, client, auth_headers):
    """fixed_pay_days [{15, 1}] — весь фикс одним платежом 15-го числа M+1."""
    pid = await _create_project(client, auth_headers)
    await payroll_service.create_employee(
        db_session, pid,
        EmployeeIn(
            name="Логист",
            position="Логист",
            salary_periods=_fix("60000"),
            fixed_pay_days=[PayDayShare(day=15, share=D("1"))],
        ),
    )
    sheet = await _sheet(db_session, pid, "2026-06")
    payouts = sheet["employees"][0]["payouts"]
    assert sheet["employees"][0]["position"] == "Логист"  # должность едет в ведомость
    assert [(p["pay_day"], D(p["amount"])) for p in payouts] == [(15, D("60000.00"))]
    assert payouts[0]["pay_date"] == "2026-07-15"


# ─── Изоляция и soft-delete ──────────────────────────────────────────────────


async def test_sheet_project_isolation(db_session, client, auth_headers):
    pid1 = await _create_project(client, auth_headers)
    pid2 = await _create_project(client, auth_headers)
    await payroll_service.create_employee(
        db_session, pid1, EmployeeIn(name="Чужой", salary_periods=_fix("50000"))
    )

    sheet2 = await _sheet(db_session, pid2, "2026-06")
    assert sheet2["employees"] == []
    assert sheet2["teams"] == []
    assert D(sheet2["totals"]["accrued_total"]) == 0


async def test_sheet_softdeleted_and_inactive_filtered(db_session, client, auth_headers):
    pid = await _create_project(client, auth_headers)
    alive = await payroll_service.create_employee(
        db_session, pid, EmployeeIn(name="Живой", salary_periods=_fix("10000"))
    )
    dead = await payroll_service.create_employee(
        db_session, pid, EmployeeIn(name="Удалённый", salary_periods=_fix("99999"))
    )
    await payroll_service.delete_employee(db_session, pid, dead.id)
    # Неактивный без начислений — скрыт; фикс неактивному не начисляется
    await payroll_service.create_employee(
        db_session, pid, EmployeeIn(name="Спящий", salary_periods=_fix("77777"), is_active=False)
    )

    sheet = await _sheet(db_session, pid, "2026-06")
    names = [e["name"] for e in sheet["employees"]]
    assert names == ["Живой"]
    assert D(sheet["totals"]["accrued_total"]) == D("10000.00")

    listed = await payroll_service.list_employees(db_session, pid)
    assert {e.name for e in listed.items} == {"Живой", "Спящий"}
    assert alive.id in {e.id for e in listed.items}


async def test_team_softdelete_member_excluded_from_split(
    db_session, client, auth_headers, monkeypatch
):
    """Удалённый/неактивный участник не делит процент — вся сумма живому."""
    pid = await _create_project(client, auth_headers)
    e1 = await payroll_service.create_employee(db_session, pid, EmployeeIn(name="Живой"))
    e2 = await payroll_service.create_employee(db_session, pid, EmployeeIn(name="Ушедший"))
    team = await payroll_service.create_team(db_session, pid, TeamIn(name="Т"))
    await payroll_service.replace_team_scopes(
        db_session, pid, team.id,
        TeamScopesReplace(scopes=[TeamScopeIn(kind="brand", value="B")]),
    )
    await payroll_service.replace_team_members(
        db_session, pid, team.id, TeamMembersReplace(employee_ids=[e1.id, e2.id])
    )
    await payroll_service.delete_employee(db_session, pid, e2.id)

    monkeypatch.setattr(
        "backend.services.wb_bdr_service.get_wb_bdr", _fake_bdr(by_brand={"B": 500000})
    )
    sheet = await _sheet(db_session, pid, "2026-06")
    week = sheet["teams"][0]["weeks"][0]
    assert D(week["per_member"]) == D(week["team_amount"])  # делить не с кем
    assert sheet["teams"][0]["member_names"] == ["Живой"]


# ─── Отметки выплат ──────────────────────────────────────────────────────────


async def test_payout_mark_upsert_enriches_sheet(db_session, client, auth_headers):
    pid = await _create_project(client, auth_headers)
    emp = await payroll_service.create_employee(
        db_session, pid, EmployeeIn(name="Отмеченный", salary_periods=_fix("40000"))
    )
    await payroll_service.upsert_payout_mark(
        db_session, pid,
        PayoutMarkIn(employee_id=emp.id, month="2026-06", pay_day=10, paid=True,
                     amount=D("19000"), comment="частично"),
    )
    # Повторный upsert той же строки — обновление, не дубль
    await payroll_service.upsert_payout_mark(
        db_session, pid,
        PayoutMarkIn(employee_id=emp.id, month="2026-06", pay_day=10, paid=True,
                     amount=D("20000"), comment="доплатили"),
    )

    sheet = await _sheet(db_session, pid, "2026-06")
    payouts = {p["pay_day"]: p for p in sheet["employees"][0]["payouts"]}
    assert payouts[10]["paid"] is True
    assert D(payouts[10]["paid_amount"]) == D("20000.00")
    assert payouts[10]["comment"] == "доплатили"
    assert payouts[25]["paid"] is False
    assert payouts[25]["paid_amount"] is None


# ─── get_monthly_accruals ────────────────────────────────────────────────────


async def test_monthly_accruals_empty_project_short_circuit(db_session, client, auth_headers):
    """Short-circuit без активных сотрудников — пустая разбивка той же формы."""
    pid = await _create_project(client, auth_headers)
    result = await payroll_service.get_monthly_accruals(db_session, pid, ["2026-06", "2026-07"])
    assert result == {
        "2026-06": {"managers": D("0"), "by_position": {}, "total": D("0")},
        "2026-07": {"managers": D("0"), "by_position": {}, "total": D("0")},
    }


async def test_monthly_accruals_fixed_salary(db_session, client, auth_headers):
    pid = await _create_project(client, auth_headers)
    await payroll_service.create_employee(
        db_session, pid,
        EmployeeIn(name="Фикс", position="Бухгалтер", salary_periods=_fix("100000")),
    )
    result = await payroll_service.get_monthly_accruals(db_session, pid, ["2026-06"])
    acc = result["2026-06"]
    assert acc["total"] == D("100000.00")
    assert acc["managers"] == D("0")
    assert acc["by_position"] == {"Бухгалтер": D("100000.00")}


# ─── История окладов (PayrollSalaryPeriod) ───────────────────────────────────


async def test_salary_period_history_in_sheet(db_session, client, auth_headers):
    """Оклад 50к с 2026-01, 80к с 2026-07: июнь=50к, июль=80к, декабрь-2025=0."""
    pid = await _create_project(client, auth_headers)
    await payroll_service.create_employee(
        db_session, pid,
        EmployeeIn(
            name="Бух",
            salary_periods=[
                SalaryPeriodIn(month="2026-01", amount=D("50000")),
                SalaryPeriodIn(month="2026-07", amount=D("80000")),
            ],
        ),
    )

    june = await _sheet(db_session, pid, "2026-06")
    assert D(june["employees"][0]["fixed_accrual"]) == D("50000.00")

    july = await _sheet(db_session, pid, "2026-07")
    assert D(july["employees"][0]["fixed_accrual"]) == D("80000.00")

    dec = await _sheet(db_session, pid, "2025-12")  # до первого периода
    assert D(dec["employees"][0]["fixed_accrual"]) == D("0.00")
    assert D(dec["totals"]["accrued_total"]) == D("0.00")


async def test_salary_period_resolve_pure():
    from backend.models.payroll import PayrollSalaryPeriod

    periods = [
        PayrollSalaryPeriod(valid_from=date(2026, 1, 1), amount=D("50000")),
        PayrollSalaryPeriod(valid_from=date(2026, 7, 1), amount=D("80000")),
    ]
    assert payroll_service.resolve_salary(periods, date(2025, 12, 1)) is None
    assert payroll_service.resolve_salary(periods, date(2026, 1, 1)) == D("50000")
    assert payroll_service.resolve_salary(periods, date(2026, 6, 15)) == D("50000")
    assert payroll_service.resolve_salary(periods, date(2026, 7, 1)) == D("80000")
    assert payroll_service.resolve_salary([], date(2026, 7, 1)) is None


async def test_opiu_fot_respects_salary_history(db_session, client, auth_headers):
    """ОПиУ прошлых месяцев: до первого периода ФОТ = 0, не сегодняшний оклад."""
    from backend.services.opiu_service import get_opiu

    pid = await _create_project(client, auth_headers)
    await payroll_service.create_employee(
        db_session, pid,
        EmployeeIn(
            name="Бух",
            salary_periods=[SalaryPeriodIn(month="2026-01", amount=D("50000"))],
        ),
    )

    result = await get_opiu.__wrapped__(
        db_session, pid, date(2025, 12, 1), date(2026, 1, 31)
    )
    rows = {r["key"]: r for r in result["rows"]}
    fot = rows["opex_payroll_fot"]
    assert fot["monthly"]["2025-12"] == 0.0  # истории задним числом нет
    assert fot["monthly"]["2026-01"] == pytest.approx(50000.0)


async def test_opex_exclusion_scoped_by_accruals(db_session, client, auth_headers):
    """
    Кейс Ирины-логиста: исключаем из opex только выплаты, которым соответствует
    начисление. Окладник — с min(valid_from) периодов (до — остаётся в opex);
    процентщик (в команде) — вся история; привязанный без начислений — ничего.
    """
    from backend.services.opiu_service import get_opiu

    pid = await _create_project(client, auth_headers)
    cp_log = await _create_counterparty(db_session, pid, primary_type="LEGAL")
    cp_pct = await _create_counterparty(db_session, pid, primary_type="LEGAL")
    cp_none = await _create_counterparty(db_session, pid, primary_type="LEGAL")

    # Ирина: оклад-периоды с марта-2026
    await payroll_service.create_employee(
        db_session, pid,
        EmployeeIn(
            name="Ирина", position="Логист", counterparty_id=cp_log,
            salary_periods=[SalaryPeriodIn(month="2026-03", amount=D("50000"))],
        ),
    )
    # Процентщик: в команде, без окладов
    pct = await payroll_service.create_employee(
        db_session, pid, EmployeeIn(name="Процентщик", counterparty_id=cp_pct)
    )
    team = await payroll_service.create_team(db_session, pid, TeamIn(name="Т"))
    await payroll_service.replace_team_members(
        db_session, pid, team.id, TeamMembersReplace(employee_ids=[pct.id])
    )
    # Привязан, но без команд и без окладов — начислений нет вовсе
    await payroll_service.create_employee(
        db_session, pid, EmployeeIn(name="Просто привязан", counterparty_id=cp_none)
    )

    db_session.add_all([
        _txn(pid, cp_log, datetime(2026, 1, 15, 10, 0), "40000"),   # ДО периода → opex
        _txn(pid, cp_log, datetime(2026, 4, 10, 10, 0), "30000"),   # после → исключена
        _txn(pid, cp_pct, datetime(2026, 1, 20, 10, 0), "20000"),   # процентщик → искл.
        _txn(pid, cp_none, datetime(2026, 4, 5, 10, 0), "5000"),    # без начислений → opex
    ])
    await db_session.commit()

    result = await get_opiu.__wrapped__(
        db_session, pid, date(2026, 1, 1), date(2026, 4, 30)
    )
    rows = {r["key"]: r for r in result["rows"]}
    legal = rows["opex_legal"]
    assert legal["monthly"]["2026-01"] == 40000.0  # Ирина-до-периода; процентщик исключён
    assert legal["monthly"]["2026-04"] == 5000.0   # только «просто привязан»


async def test_opiu_fot_children_largest_remainder(db_session, client, auth_headers, monkeypatch):
    """
    Под-месячное окно (factor 10/31): независимое округление детей даёт ±0.01 —
    reconciliation обязан свести родителя к Σ детей (largest-remainder).
    """
    from backend.services import payroll_service as ps
    from backend.services.opiu_service import get_opiu

    pid = await _create_project(client, auth_headers)

    async def fake_accruals(db, project_id, months):
        return {
            mk: {
                "managers": D("30000"),
                "by_position": {"Бухгалтер": D("70000")},
                "total": D("100000"),
            }
            for mk in months
        }

    monkeypatch.setattr(ps, "get_monthly_accruals", fake_accruals)

    result = await get_opiu.__wrapped__(
        db_session, pid, date(2026, 7, 1), date(2026, 7, 10)  # 10 из 31 дня
    )
    rows = {r["key"]: r for r in result["rows"]}
    parent = rows["opex_payroll_fot"]["monthly"]["2026-07"]
    managers = rows["opex_payroll_fot_managers"]["monthly"]["2026-07"]
    accountant = rows["opex_payroll_fot_бухгалтер"]["monthly"]["2026-07"]

    # Независимое округление: 9677.42 + 22580.65 = 32258.07 ≠ 32258.06 (родитель)
    assert parent == pytest.approx(32258.06, abs=0.001)
    assert managers + accountant == pytest.approx(parent, abs=0.001)  # сведено
    assert managers == pytest.approx(9677.42, abs=0.001)
    assert accountant == pytest.approx(22580.64, abs=0.001)  # −0.01 у крупнейшего


async def test_month_accrual_breakdown_managers_positions_and_sum(
    db_session, client, auth_headers, monkeypatch
):
    """
    Разбивка: «Менеджеры» = ВСЕ процентные начисления команд (должность не
    важна); фиксы — по должности, пустая → «Без должности»; total = сумма
    подстрок (родитель ОПиУ = сумма детей).
    """
    pid = await _create_project(client, auth_headers)
    # e1 в команде И с фиксом (должность Логист): процент уходит в «Менеджеры»,
    # фикс — в свою должность
    e1 = await payroll_service.create_employee(
        db_session, pid,
        EmployeeIn(name="Аня", position="Логист", salary_periods=_fix("50000")),
    )
    e2 = await payroll_service.create_employee(db_session, pid, EmployeeIn(name="Боря"))
    await payroll_service.create_employee(
        db_session, pid, EmployeeIn(name="Вера", salary_periods=_fix("70000"))  # без должности
    )
    team = await payroll_service.create_team(db_session, pid, TeamIn(name="Ковры"))
    await payroll_service.replace_team_scopes(
        db_session, pid, team.id,
        TeamScopesReplace(scopes=[TeamScopeIn(kind="brand", value="BrandA")]),
    )
    await payroll_service.replace_team_members(
        db_session, pid, team.id, TeamMembersReplace(employee_ids=[e1.id, e2.id])
    )
    monkeypatch.setattr(
        "backend.services.wb_bdr_service.get_wb_bdr", _fake_bdr(by_brand={"BrandA": 700000})
    )

    acc = await payroll_service.get_month_accrual.__wrapped__(db_session, pid, "2026-06")
    managers = D(acc["managers"])
    by_position = {k: D(v) for k, v in acc["by_position"].items()}
    total = D(acc["total"])

    # «Менеджеры» = вся процентная часть команды (оба участника, независимо от должности)
    sheet = await _sheet(db_session, pid, "2026-06")
    team_total = D(sheet["teams"][0]["total_amount"])
    assert managers > 0
    assert managers == team_total
    assert by_position == {"Логист": D("50000.00"), "Без должности": D("70000.00")}
    assert total == managers + sum(by_position.values(), D("0"))  # родитель = Σ детей


# ─── ОПиУ: строка ФОТ + исключение задвоения ─────────────────────────────────


async def test_opiu_fot_row_and_no_double_count(db_session, client, auth_headers):
    """
    ОПиУ содержит строку «ФОТ (начислено)» = начисление payroll, а фактические
    выплаты сотруднику из выписки ИСКЛЮЧЕНЫ из opex по типам (нет задвоения).
    """
    from backend.services.opiu_service import get_opiu

    pid = await _create_project(client, auth_headers)
    cp_emp = await _create_counterparty(db_session, pid, primary_type="LEGAL")
    cp_other = await _create_counterparty(db_session, pid, primary_type="LEGAL")
    await payroll_service.create_employee(
        db_session, pid,
        EmployeeIn(name="Сотрудник", counterparty_id=cp_emp, salary_periods=_fix("100000")),
    )
    db_session.add_all([
        # Выплата ЗП сотруднику в июле — НЕ должна попасть в opex_legal
        _txn(pid, cp_emp, datetime(2026, 7, 12, 10, 0), "88000"),
        # Обычный LEGAL-расход — должен остаться в opex_legal
        _txn(pid, cp_other, datetime(2026, 7, 16, 10, 0), "5000"),
    ])
    await db_session.commit()

    result = await get_opiu.__wrapped__(
        db_session, pid, date(2026, 6, 1), date(2026, 7, 31)
    )
    rows = {r["key"]: r for r in result["rows"]}

    fot = rows["opex_payroll_fot"]
    assert fot["parent_key"] == "operating_expenses"
    assert fot["monthly"]["2026-06"] == 100000.0  # фикс начисляется каждый месяц
    assert fot["monthly"]["2026-07"] == 100000.0

    # Подстроки: сотрудник без должности → «Без должности»; процентов нет →
    # строки «Менеджеры» нет; родитель = сумма детей
    assert fot["expandable"] is True
    assert "opex_payroll_fot_managers" not in rows
    child = rows["opex_payroll_fot_без_должности"]
    assert child["parent_key"] == "opex_payroll_fot"
    assert child["label"] == "Без должности"
    assert child["monthly"] == fot["monthly"]

    legal = rows["opex_legal"]
    assert legal["monthly"]["2026-07"] == 5000.0  # 88000 сотрудника исключены

    ops = rows["operating_expenses"]
    assert ops["monthly"]["2026-07"] == pytest.approx(105000.0)  # ФОТ + прочий opex
    assert ops["total"] == pytest.approx(205000.0)  # дети НЕ задваивают сумму


async def test_opiu_inactive_employee_payments_stay_in_opex(db_session, client, auth_headers):
    """
    Зеркальность: неактивному сотруднику начисление 0 (строки ФОТ нет),
    поэтому его фактические выплаты ОСТАЮТСЯ в opex — иначе расход пропал бы
    из ОПиУ совсем.
    """
    from backend.services.opiu_service import get_opiu

    pid = await _create_project(client, auth_headers)
    cp_id = await _create_counterparty(db_session, pid, primary_type="LEGAL")
    await payroll_service.create_employee(
        db_session, pid,
        EmployeeIn(name="Бывший", counterparty_id=cp_id,
                   salary_periods=_fix("100000"), is_active=False),
    )
    db_session.add(_txn(pid, cp_id, datetime(2026, 7, 12, 10, 0), "30000"))
    await db_session.commit()

    result = await get_opiu.__wrapped__(
        db_session, pid, date(2026, 6, 1), date(2026, 7, 31)
    )
    rows = {r["key"]: r for r in result["rows"]}
    assert "opex_payroll_fot" not in rows  # начислений нет
    assert rows["opex_legal"]["monthly"]["2026-07"] == 30000.0  # выплата в opex


async def test_opiu_fot_prorata_submonth_window(db_session, client, auth_headers):
    """Под-месячное окно получает ФОТ пропорционально дням, не весь месяц."""
    from backend.services.opiu_service import get_opiu

    pid = await _create_project(client, auth_headers)
    await payroll_service.create_employee(
        db_session, pid, EmployeeIn(name="Фикс", salary_periods=_fix("100000"))
    )

    result = await get_opiu.__wrapped__(
        db_session, pid, date(2026, 6, 1), date(2026, 6, 15)
    )
    rows = {r["key"]: r for r in result["rows"]}
    # 15 дней из 30 → половина месячного начисления, У КАЖДОЙ подстроки тоже
    assert rows["opex_payroll_fot"]["monthly"]["2026-06"] == pytest.approx(50000.0)
    assert rows["opex_payroll_fot_без_должности"]["monthly"]["2026-06"] == pytest.approx(50000.0)

    full = await get_opiu.__wrapped__(
        db_session, pid, date(2026, 6, 1), date(2026, 6, 30)
    )
    full_rows = {r["key"]: r for r in full["rows"]}
    assert full_rows["opex_payroll_fot"]["monthly"]["2026-06"] == pytest.approx(100000.0)
    assert full_rows["opex_payroll_fot_без_должности"]["monthly"]["2026-06"] == pytest.approx(
        100000.0
    )


async def test_month_accrual_cache_roundtrip_new_prefix(db_session, client, auth_headers):
    """
    Кэш-совместимость: get_month_accrual (префикс payroll:accruals2) отдаёт
    одинаковую JSON-safe форму на мисс и на хит (dict со строками-Decimal).
    Регистрацию префикса в invalidate_project_reports сторожит test_conventions_sync.
    """
    pid = await _create_project(client, auth_headers)
    await payroll_service.create_employee(
        db_session, pid,
        EmployeeIn(name="Кэш", position="Бухгалтер", salary_periods=_fix("100000")),
    )
    first = await payroll_service.get_month_accrual(db_session, pid, "2026-06")  # miss → set
    second = await payroll_service.get_month_accrual(db_session, pid, "2026-06")  # hit
    assert first == second
    assert second["total"] == "100000.00"
    assert second["by_position"] == {"Бухгалтер": "100000.00"}
    assert isinstance(second["managers"], str)


async def test_opiu_fot_children_managers_and_position(
    db_session, client, auth_headers, monkeypatch
):
    """
    Дети «ФОТ (начислено)»: «Менеджеры» (весь процент команд) + должность фикса;
    родитель = сумма детей; в «Операционные расходы» идёт только родитель.
    """
    from backend.services.opiu_service import get_opiu

    pid = await _create_project(client, auth_headers)
    e1 = await payroll_service.create_employee(db_session, pid, EmployeeIn(name="Аня"))
    await payroll_service.create_employee(
        db_session, pid,
        EmployeeIn(name="Бухгалтер", position="Бухгалтер", salary_periods=_fix("40000")),
    )
    team = await payroll_service.create_team(db_session, pid, TeamIn(name="Ковры"))
    await payroll_service.replace_team_scopes(
        db_session, pid, team.id,
        TeamScopesReplace(scopes=[TeamScopeIn(kind="brand", value="BrandA")]),
    )
    await payroll_service.replace_team_members(
        db_session, pid, team.id, TeamMembersReplace(employee_ids=[e1.id])
    )
    monkeypatch.setattr(
        "backend.services.wb_bdr_service.get_wb_bdr", _fake_bdr(by_brand={"BrandA": 700000})
    )

    result = await get_opiu.__wrapped__(
        db_session, pid, date(2026, 6, 1), date(2026, 6, 30)
    )
    rows = {r["key"]: r for r in result["rows"]}

    managers = rows["opex_payroll_fot_managers"]["monthly"]["2026-06"]
    accountant = rows["opex_payroll_fot_бухгалтер"]["monthly"]["2026-06"]
    parent = rows["opex_payroll_fot"]["monthly"]["2026-06"]
    assert managers > 0
    assert accountant == pytest.approx(40000.0)
    assert parent == pytest.approx(managers + accountant)  # родитель = Σ детей
    assert rows["opex_payroll_fot_managers"]["label"] == "Менеджеры"
    assert rows["opex_payroll_fot_managers"]["parent_key"] == "opex_payroll_fot"
    # Опекс проекта пуст (нет других расходов) → строка ops == только родитель,
    # дети сумму НЕ задваивают
    assert rows["operating_expenses"]["monthly"]["2026-06"] == pytest.approx(parent)


# ─── include_cost_tax: паритет net_payout ────────────────────────────────────


def _finance_row(project_id: int, rrd_id: int, nm_id: int, brand: str, subject: str,
                 sa: str, doc_type: str = "Продажа") -> object:
    from backend.models.wb_finance import WbFinanceRow

    return WbFinanceRow(
        project_id=project_id,
        rrd_id=rrd_id,
        realizationreport_id=610001,
        date_from=date(2026, 6, 1),
        date_to=date(2026, 6, 7),
        rr_dt=date(2026, 6, 5),
        sale_dt=date(2026, 6, 4),
        sa_name=sa,
        nm_id=nm_id,
        brand_name=brand,
        subject_name=subject,
        doc_type_name=doc_type,
        supplier_oper_name=doc_type,
        bonus_type_name=None,
        quantity=1,
        retail_price_withdisc_rub=D("1000"),
        retail_amount=D("1000"),
        ppvz_for_pay=D("800"),
        ppvz_sales_commission=D("200"),
        delivery_rub=D("50"),
        penalty=D("0"),
        additional_payment=D("0"),
        storage_fee=D("10"),
        acceptance=D("0"),
        deduction=D("0"),
        ppvz_reward=D("0"),
        rebill_logistic_cost=D("0"),
        ppvz_vw=D("0"),
        ppvz_vw_nds=D("0"),
    )


async def test_bdr_net_payout_parity_without_cost_tax(db_session, client, auth_headers):
    """
    include_cost_tax=False (режим ведомости) даёт ТОТ ЖЕ net_payout/to_pay,
    что и полный расчёт — себестоимость и налог на «Чистую выплату» не влияют.
    """
    from backend.services.wb_bdr_service import get_wb_bdr

    pid = await _create_project(client, auth_headers)
    base_rrd = int(uuid.uuid4().hex[:10], 16)
    db_session.add_all([
        _finance_row(pid, base_rrd + 1, 111, "BrandA", "Ковры", "sku-a1"),
        _finance_row(pid, base_rrd + 2, 112, "BrandA", "Дорожки", "sku-a2"),
        _finance_row(pid, base_rrd + 3, 113, "BrandB", "Ковры", "sku-b1"),
    ])
    await db_session.commit()

    kwargs = dict(group_by="brand", period_mode="report")
    full = await get_wb_bdr.__wrapped__(
        db_session, pid, date(2026, 6, 1), date(2026, 6, 7), include_cost_tax=True, **kwargs
    )
    lite = await get_wb_bdr.__wrapped__(
        db_session, pid, date(2026, 6, 1), date(2026, 6, 7), include_cost_tax=False, **kwargs
    )

    def _np(res):
        return {a["sa_name"]: a["net_payout"] for a in res["articles"]}

    assert _np(lite) == _np(full)
    assert lite["summary"]["net_payout"] == full["summary"]["net_payout"]
    assert lite["summary"]["to_pay"] == full["summary"]["to_pay"]
    assert _np(full), "фикстура должна дать непустой БДР"
    # Лёгкий режим не считает налог
    assert lite["tax_info"] == {}


async def test_sheet_month_validation():
    with pytest.raises(ValueError):
        payroll_service._parse_month("июнь")
    assert payroll_service._parse_month("2026-06") == date(2026, 6, 1)
