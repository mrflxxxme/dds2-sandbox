"""
Tests for payroll_agency_service: fee трёх режимов, сплит менеджеры/агентство,
интеграция manager-доли в ведомость ЗП, изоляция, ручные суммы.

БДР кабинета клиента мокается; ведомости зовём через __wrapped__ (мимо redis).
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from backend.schemas.payroll import (
    ClientEntryUpsert,
    ClientProjectIn,
    ClientProjectUpdate,
    EmployeeIn,
    TeamIn,
    TeamMembersReplace,
)
from backend.services import payroll_agency_service as agency
from backend.services import payroll_service
from backend.services.payroll_service import resolve_step

D = Decimal

# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _create_project(client, auth_headers) -> int:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"agency_test_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    return resp.json()["id"]


async def _project_headers(client, auth_headers) -> tuple[dict, int]:
    pid = await _create_project(client, auth_headers)
    return {**auth_headers, "X-Project-Id": str(pid)}, pid


async def _agency_sheet(db, project_id: int, month: str = "2026-06") -> dict:
    return await agency.get_agency_sheet.__wrapped__(db, project_id, month)


async def _owner(db, project_id: int) -> int:
    from sqlalchemy import select

    from backend.models import Project

    return (
        await db.execute(select(Project.owner_id).where(Project.id == project_id))
    ).scalar_one()


async def _create_client_svc(db, project_id: int, data):
    """create_client от имени владельца проекта (валидация linked_project_id)."""
    return await agency.create_client(db, project_id, data, await _owner(db, project_id))


async def _other_user_project(client) -> int:
    """Проект ДРУГОГО юзера — для проверок изоляции привязки кабинета."""
    username = f"agency_other_{uuid.uuid4().hex[:8]}"
    await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "testpass123", "email": f"{username}@test.com"},
    )
    login = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "testpass123"}
    )
    other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"agency_alien_{uuid.uuid4().hex[:6]}"},
        headers=other_headers,
    )
    return resp.json()["id"]


def _client_in(**kw) -> ClientProjectIn:
    base = dict(name=f"Клиент-{uuid.uuid4().hex[:6]}", billing_mode="fixed")
    base.update(kw)
    return ClientProjectIn(**base)


def _fake_bdr_summary(net_payout: float, calls: list | None = None):
    """Мок get_wb_bdr для percent-клиента с внутренним кабинетом."""

    async def fake(
        db, project_id, date_from, date_to, brand=None, article=None,
        group_by="article", period_mode="sale", include_cost_tax=True,
    ):
        assert period_mode == "report"
        assert include_cost_tax is False, "агентству не нужны себестоимость и налог"
        if calls is not None:
            calls.append((project_id, date_from, date_to))
        return {"summary": {"net_payout": net_payout}, "articles": []}

    return fake


async def _ladder_week_fee(db, base: str) -> D:
    """Ожидаемый fee недели: base × team_rate из реальной (сидированной) лестницы."""
    steps = await payroll_service._load_tariff_steps(db, date(2026, 6, 30))
    assert steps, "лестница должна быть засидирована миграцией payroll01_domain"
    step = resolve_step(D(base), steps)
    return (D(base) * step.team_rate).quantize(D("0.01"))


# ─── Fee: три режима ─────────────────────────────────────────────────────────


async def test_fee_fixed_and_split(db_session, client, auth_headers):
    pid = await _create_project(client, auth_headers)
    await _create_client_svc(
        db_session, pid,
        _client_in(name="Фикс-клиент", billing_mode="fixed", fixed_amount=D("100000")),
    )

    sheet = await _agency_sheet(db_session, pid)
    assert len(sheet["clients"]) == 1
    c = sheet["clients"][0]
    assert D(c["fee_total"]) == D("100000.00")
    assert D(c["manager_amount"]) == D("45000.00")  # дефолт manager_share 0.45
    assert D(c["agency_amount"]) == D("55000.00")
    assert c["weeks"] == []
    assert any("команда" in w.lower() for w in c["warnings"])  # team_id не задан
    assert D(sheet["totals_fee"]) == D("100000.00")
    assert D(sheet["totals_manager"]) == D("45000.00")
    assert D(sheet["totals_agency"]) == D("55000.00")


async def test_fee_percent_internal_cabinet_bdr(db_session, client, auth_headers, monkeypatch):
    """percent + linked_project_id: база недели = net_payout ВСЕГО кабинета из БДР."""
    pid = await _create_project(client, auth_headers)
    linked_pid = await _create_project(client, auth_headers)  # кабинет клиента
    await _create_client_svc(
        db_session, pid,
        _client_in(name="Внутренний", billing_mode="percent", linked_project_id=linked_pid),
    )
    calls: list = []
    monkeypatch.setattr(
        "backend.services.wb_bdr_service.get_wb_bdr", _fake_bdr_summary(700000, calls)
    )

    sheet = await _agency_sheet(db_session, pid)
    c = sheet["clients"][0]

    # 4 недели июня, все по кабинету клиента (кросс-проектное чтение)
    assert len(calls) == 4
    assert all(call[0] == linked_pid for call in calls)

    week_fee = await _ladder_week_fee(db_session, "700000")
    assert len(c["weeks"]) == 4
    for week in c["weeks"]:
        assert week["manual"] is False
        assert week["has_entry"] is False  # база из БДР, не из ручной записи
        assert D(week["base_amount"]) == D("700000.00")
        assert D(week["fee"]) == week_fee
    assert D(c["fee_total"]) == week_fee * 4
    assert c["warnings"] == [
        "Не назначена команда — доля менеджеров не попадёт в ведомость"
    ]


async def test_fee_percent_external_manual_weeks(db_session, client, auth_headers):
    """percent без кабинета: базы из week_base-записей; недостающие недели → warning."""
    pid = await _create_project(client, auth_headers)
    cl = await _create_client_svc(
        db_session, pid, _client_in(name="Внешний", billing_mode="percent")
    )
    await agency.upsert_entry(
        db_session, pid, cl.id,
        ClientEntryUpsert(kind="week_base", date_from=date(2026, 6, 1), amount=D("700000")),
    )
    await agency.upsert_entry(
        db_session, pid, cl.id,
        ClientEntryUpsert(kind="week_base", date_from=date(2026, 6, 8), amount=D("500000")),
    )

    sheet = await _agency_sheet(db_session, pid)
    c = sheet["clients"][0]
    weeks = {w["date_from"]: w for w in c["weeks"]}
    assert all(w["manual"] is True for w in c["weeks"])
    assert D(weeks["2026-06-01"]["base_amount"]) == D("700000.00")
    assert D(weeks["2026-06-08"]["base_amount"]) == D("500000.00")
    assert D(weeks["2026-06-15"]["base_amount"]) == D("0.00")
    assert weeks["2026-06-15"]["threshold"] is None  # база 0 → ставка 0
    # has_entry отличает «введён 0» от «не введено» (фронт не префилит нулём)
    assert weeks["2026-06-01"]["has_entry"] is True
    assert weeks["2026-06-08"]["has_entry"] is True
    assert weeks["2026-06-15"]["has_entry"] is False
    assert weeks["2026-06-22"]["has_entry"] is False
    fee_expected = await _ladder_week_fee(db_session, "700000") + await _ladder_week_fee(
        db_session, "500000"
    )
    assert D(c["fee_total"]) == fee_expected
    missing = next(w for w in c["warnings"] if "недельная база" in w)
    assert "2026-06-15" in missing and "2026-06-22" in missing
    assert "2026-06-01" not in missing


async def test_fee_profit_share_with_and_without_entry(db_session, client, auth_headers):
    pid = await _create_project(client, auth_headers)
    with_entry = await _create_client_svc(
        db_session, pid,
        _client_in(name="Долевой", billing_mode="profit_share", fee_percent=D("0.10")),
    )
    await _create_client_svc(
        db_session, pid,
        _client_in(name="Без ЧП", billing_mode="profit_share", fee_percent=D("0.10")),
    )
    await agency.upsert_entry(
        db_session, pid, with_entry.id,
        ClientEntryUpsert(kind="month_profit", date_from=date(2026, 6, 1), amount=D("300000")),
    )

    sheet = await _agency_sheet(db_session, pid)
    by_name = {c["name"]: c for c in sheet["clients"]}
    ok = by_name["Долевой"]
    assert D(ok["profit_amount"]) == D("300000.00")
    assert D(ok["fee_total"]) == D("30000.00")
    empty = by_name["Без ЧП"]
    assert D(empty["fee_total"]) == D("0.00")
    assert any("чистая прибыль" in w.lower() for w in empty["warnings"])


async def test_manager_split_kopeks(db_session, client, auth_headers):
    """Копейки: fee 1000.01 × 0.45 — manager+agency обязаны сойтись в fee."""
    pid = await _create_project(client, auth_headers)
    await _create_client_svc(
        db_session, pid,
        _client_in(billing_mode="fixed", fixed_amount=D("1000.01")),
    )
    sheet = await _agency_sheet(db_session, pid)
    c = sheet["clients"][0]
    assert D(c["manager_amount"]) == D("450.00")  # 450.0045 → HALF_UP
    assert D(c["agency_amount"]) == D("550.01")
    assert D(c["manager_amount"]) + D(c["agency_amount"]) == D(c["fee_total"])


# ─── Интеграция в ведомость ЗП ───────────────────────────────────────────────


async def test_sheet_integration_agency_accruals(db_session, client, auth_headers):
    """Manager-доля клиента делится поровну и попадает в ФОТ «Менеджеры»."""
    pid = await _create_project(client, auth_headers)
    e1 = await payroll_service.create_employee(db_session, pid, EmployeeIn(name="Аня"))
    e2 = await payroll_service.create_employee(db_session, pid, EmployeeIn(name="Боря"))
    team = await payroll_service.create_team(db_session, pid, TeamIn(name="Ковры"))
    await payroll_service.replace_team_members(
        db_session, pid, team.id, TeamMembersReplace(employee_ids=[e1.id, e2.id])
    )
    await _create_client_svc(
        db_session, pid,
        _client_in(
            name="Клиент-X", billing_mode="fixed", fixed_amount=D("100000"),
            team_id=team.id,
        ),
    )

    sheet = await payroll_service.get_sheet.__wrapped__(db_session, pid, "2026-06")
    emps = {e["name"]: e for e in sheet["employees"]}
    for name in ("Аня", "Боря"):
        rows = {r["team_name"]: r for r in emps[name]["team_accruals"]}
        # Обычная строка команды (без скоупов → 0) + отдельная агентская
        agency_row = rows["Ковры · Клиент-X (агентство)"]
        assert agency_row["team_id"] == team.id
        assert D(agency_row["amount"]) == D("22500.00")  # 45000 / 2
        assert D(rows["Ковры"]["amount"]) == D("0.00")
        assert D(emps[name]["accrued_total"]) == D("22500.00")
    assert D(sheet["totals"]["accrued_total"]) == D("45000.00")

    # ФОТ «Менеджеры» подхватывает агентские строки из team_accruals
    acc = await payroll_service.get_month_accrual.__wrapped__(db_session, pid, "2026-06")
    assert D(acc["managers"]) == D("45000.00")
    assert D(acc["total"]) == D("45000.00")


async def test_sheet_integration_skips_clients_without_team(db_session, client, auth_headers):
    pid = await _create_project(client, auth_headers)
    await payroll_service.create_employee(db_session, pid, EmployeeIn(name="Соло"))
    await _create_client_svc(
        db_session, pid, _client_in(billing_mode="fixed", fixed_amount=D("100000"))
    )
    sheet = await payroll_service.get_sheet.__wrapped__(db_session, pid, "2026-06")
    assert sheet["employees"][0]["team_accruals"] == []
    assert D(sheet["totals"]["accrued_total"]) == D("0.00")


async def test_sheet_split_remainder_three_members(db_session, client, auth_headers):
    """Хвост округления делёжа — последнему участнику: Σ строк == manager_amount."""
    pid = await _create_project(client, auth_headers)
    emp_ids = []
    for name in ("Аня", "Боря", "Вера"):
        emp = await payroll_service.create_employee(db_session, pid, EmployeeIn(name=name))
        emp_ids.append(emp.id)
    team = await payroll_service.create_team(db_session, pid, TeamIn(name="Трио"))
    await payroll_service.replace_team_members(
        db_session, pid, team.id, TeamMembersReplace(employee_ids=emp_ids)
    )
    # fee 100000.02 × 0.45 = 45000.009 → manager 45000.01; 45000.01 / 3 не делится
    await _create_client_svc(
        db_session, pid,
        _client_in(
            name="Клиент-Y", billing_mode="fixed", fixed_amount=D("100000.02"),
            team_id=team.id,
        ),
    )

    sheet = await payroll_service.get_sheet.__wrapped__(db_session, pid, "2026-06")
    agency_amounts = sorted(
        D(r["amount"])
        for e in sheet["employees"]
        for r in e["team_accruals"]
        if "(агентство)" in r["team_name"]
    )
    assert agency_amounts == [D("15000.00"), D("15000.00"), D("15000.01")]
    assert sum(agency_amounts) == D("45000.01")  # == manager_amount вкладки
    assert D(sheet["totals"]["accrued_total"]) == D("45000.01")


async def test_linked_project_foreign_binding_404(db_session, client, auth_headers):
    """CRITICAL-фикс: биндить можно только кабинет, где юзер владелец/участник."""
    pid = await _create_project(client, auth_headers)
    alien_pid = await _other_user_project(client)
    uid = await _owner(db_session, pid)

    # create с чужим кабинетом → 404
    with pytest.raises(payroll_service.PayrollNotFoundError):
        await agency.create_client(
            db_session, pid,
            _client_in(billing_mode="percent", linked_project_id=alien_pid),
            uid,
        )

    # update с чужим кабинетом → 404; свой → ок
    cl = await _create_client_svc(db_session, pid, _client_in(billing_mode="percent"))
    with pytest.raises(payroll_service.PayrollNotFoundError):
        await agency.update_client(
            db_session, pid, cl.id,
            ClientProjectUpdate(linked_project_id=alien_pid), uid,
        )
    own_pid = await _create_project(client, auth_headers)
    updated = await agency.update_client(
        db_session, pid, cl.id, ClientProjectUpdate(linked_project_id=own_pid), uid
    )
    assert updated.linked_project_id == own_pid


async def test_ladder_revision_next_month_not_applied(db_session, client, auth_headers):
    """
    Лестница берётся на ПОСЛЕДНИЙ день расчётного месяца (как в ведомости ЗП):
    ревизия с valid_from=1-е СЛЕДУЮЩЕГО месяца не переписывает текущий.
    Песочница в 1995 — не задевает боевой сид 2026-01-01.
    """
    from sqlalchemy import delete as sa_delete

    from backend.models.payroll import PayrollTariffStep

    pid = await _create_project(client, auth_headers)
    cl = await _create_client_svc(db_session, pid, _client_in(billing_mode="percent"))
    monday = payroll_service.weeks_for_month(date(1995, 6, 1))[0][0]
    await agency.upsert_entry(
        db_session, pid, cl.id,
        ClientEntryUpsert(kind="week_base", date_from=monday, amount=D("1000000")),
    )
    try:
        db_session.add_all([
            PayrollTariffStep(
                valid_from=date(1995, 1, 1), threshold=D("100"),
                company_rate=D("0.02"), team_rate=D("0.010000"),
            ),
            PayrollTariffStep(
                valid_from=date(1995, 7, 1), threshold=D("100"),
                company_rate=D("0.02"), team_rate=D("0.020000"),
            ),
        ])
        await db_session.commit()

        sheet = await _agency_sheet(db_session, pid, "1995-06")
        week = next(
            w for w in sheet["clients"][0]["weeks"]
            if w["date_from"] == monday.isoformat()
        )
        assert D(week["team_rate"]) == D("0.010000")  # июньский набор, не июльский
        assert D(week["fee"]) == D("10000.00")
    finally:
        await db_session.execute(
            sa_delete(PayrollTariffStep).where(
                PayrollTariffStep.valid_from.in_([date(1995, 1, 1), date(1995, 7, 1)])
            )
        )
        await db_session.commit()


# ─── Изоляция / soft-delete / entries ────────────────────────────────────────


async def test_isolation_and_softdelete(db_session, client, auth_headers):
    pid1 = await _create_project(client, auth_headers)
    pid2 = await _create_project(client, auth_headers)
    cl = await _create_client_svc(
        db_session, pid1, _client_in(billing_mode="fixed", fixed_amount=D("1000"))
    )

    # Чужой проект не видит клиента и получает 404 на мутации
    sheet2 = await _agency_sheet(db_session, pid2)
    assert sheet2["clients"] == []
    with pytest.raises(payroll_service.PayrollNotFoundError):
        await agency.update_client(
            db_session, pid2, cl.id, ClientProjectUpdate(name="Взлом"),
            await _owner(db_session, pid2),
        )

    # Soft-delete убирает из списка и ведомости
    await agency.delete_client(db_session, pid1, cl.id)
    assert (await agency.list_clients(db_session, pid1)).items == []
    sheet1 = await _agency_sheet(db_session, pid1)
    assert sheet1["clients"] == []


async def test_upsert_entry_idempotent_and_delete(db_session, client, auth_headers):
    """Повторный upsert того же ключа — обновление одной строки, не дубль."""
    pid = await _create_project(client, auth_headers)
    cl = await _create_client_svc(db_session, pid, _client_in(billing_mode="percent"))
    for amount in ("100", "200"):
        await agency.upsert_entry(
            db_session, pid, cl.id,
            ClientEntryUpsert(kind="week_base", date_from=date(2026, 6, 1), amount=D(amount)),
        )
    items = (await agency.list_clients(db_session, pid)).items
    assert [(e.kind, str(e.date_from), e.amount) for e in items[0].entries] == [
        ("week_base", "2026-06-01", D("200.00"))
    ]

    await agency.delete_entry(db_session, pid, cl.id, "week_base", date(2026, 6, 1))
    items = (await agency.list_clients(db_session, pid)).items
    assert items[0].entries == []


async def test_entry_date_validation(db_session, client, auth_headers):
    pid = await _create_project(client, auth_headers)
    cl = await _create_client_svc(db_session, pid, _client_in(billing_mode="percent"))
    with pytest.raises(ValueError):  # 2026-06-03 — среда, не понедельник
        await agency.upsert_entry(
            db_session, pid, cl.id,
            ClientEntryUpsert(kind="week_base", date_from=date(2026, 6, 3), amount=D("1")),
        )
    with pytest.raises(ValueError):  # month_profit не с 1-го числа
        await agency.upsert_entry(
            db_session, pid, cl.id,
            ClientEntryUpsert(kind="month_profit", date_from=date(2026, 6, 2), amount=D("1")),
        )


# ─── API ─────────────────────────────────────────────────────────────────────


async def test_agency_api_crud_flow(client, auth_headers):
    headers, _ = await _project_headers(client, auth_headers)

    resp = await client.post(
        "/api/v1/payroll/agency/clients",
        json={"name": "API-клиент", "billing_mode": "profit_share", "fee_percent": "0.1"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    cl = resp.json()
    assert cl["billing_mode"] == "profit_share"

    # PATCH + entry PUT/DELETE
    resp = await client.patch(
        f"/api/v1/payroll/agency/clients/{cl['id']}",
        json={"manager_share": "0.5"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert float(resp.json()["manager_share"]) == 0.5

    resp = await client.put(
        f"/api/v1/payroll/agency/clients/{cl['id']}/entry",
        json={"kind": "month_profit", "date_from": "2026-06-01", "amount": "300000"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.get("/api/v1/payroll/agency/sheet?month=2026-06", headers=headers)
    assert resp.status_code == 200, resp.text
    c = resp.json()["clients"][0]
    assert float(c["fee_total"]) == 30000.0
    assert float(c["manager_amount"]) == 15000.0  # share 0.5

    resp = await client.delete(
        f"/api/v1/payroll/agency/clients/{cl['id']}/entry"
        "?kind=month_profit&date_from=2026-06-01",
        headers=headers,
    )
    assert resp.status_code == 200
    resp = await client.get("/api/v1/payroll/agency/clients", headers=headers)
    assert resp.json()["items"][0]["entries"] == []

    # Невалидная дата недели → 422
    resp = await client.put(
        f"/api/v1/payroll/agency/clients/{cl['id']}/entry",
        json={"kind": "week_base", "date_from": "2026-06-03", "amount": "1"},
        headers=headers,
    )
    assert resp.status_code == 422

    resp = await client.delete(
        f"/api/v1/payroll/agency/clients/{cl['id']}", headers=headers
    )
    assert resp.status_code == 200
    resp = await client.get("/api/v1/payroll/agency/clients", headers=headers)
    assert resp.json()["items"] == []


async def test_agency_api_foreign_client_404(client, auth_headers):
    headers1, _ = await _project_headers(client, auth_headers)
    headers2, _ = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/payroll/agency/clients",
        json={"name": "Свой", "billing_mode": "fixed", "fixed_amount": "1000"},
        headers=headers1,
    )
    cl_id = resp.json()["id"]
    resp = await client.patch(
        f"/api/v1/payroll/agency/clients/{cl_id}", json={"name": "Взлом"}, headers=headers2
    )
    assert resp.status_code == 404


async def test_project_options_only_own_projects(client, auth_headers):
    headers, pid1 = await _project_headers(client, auth_headers)
    pid2 = await _create_project(client, auth_headers)
    alien_pid = await _other_user_project(client)  # проект другого юзера

    resp = await client.get("/api/v1/payroll/agency/project-options", headers=headers)
    assert resp.status_code == 200, resp.text
    ids = {p["id"] for p in resp.json()["items"]}
    assert pid1 in ids and pid2 in ids
    assert alien_pid not in ids
    assert all({"id", "name", "slug"} <= set(p) for p in resp.json()["items"])
