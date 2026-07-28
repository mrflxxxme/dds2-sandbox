"""
Integration tests for /api/v1/payroll endpoints.

Тариф глобальный (без project_id) — тестовые наборы пишем на valid_from
1990-01-01 (никогда не станет действующим для реальных месяцев ≥ 2026)
и подчищаем за собой.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import delete

from backend.models.payroll import PayrollTariffStep

_TEST_TARIFF_DATE = "1990-01-01"

# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _project_headers(client, auth_headers) -> tuple[dict, int]:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"payroll_api_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}, project["id"]


async def _create_employee(client, headers, name: str = "Аня", **kw) -> dict:
    resp = await client.post(
        "/api/v1/payroll/employees", json={"name": name, **kw}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_team(client, headers, name: str = "Ковры") -> dict:
    resp = await client.post("/api/v1/payroll/teams", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ─── Employees ────────────────────────────────────────────────────────────────


async def test_employee_crud_flow(client, auth_headers):
    headers, _ = await _project_headers(client, auth_headers)

    emp = await _create_employee(
        client, headers, name="Бухгалтер", position="Бухгалтер",
        salary_periods=[
            {"month": "2026-01", "amount": "50000"},
            {"month": "2026-07", "amount": "80000"},
        ],
        fixed_pay_days=[{"day": 15, "share": 1}], notes="на фиксе",
    )
    assert emp["name"] == "Бухгалтер"
    assert emp["position"] == "Бухгалтер"
    assert [(p["month"], float(p["amount"])) for p in emp["salary_periods"]] == [
        ("2026-01", 50000.0),
        ("2026-07", 80000.0),
    ]
    # Сегодня 2026-07-28 — действует период с июля
    assert float(emp["current_salary"]) == 80000.0
    assert [(d["day"], float(d["share"])) for d in emp["fixed_pay_days"]] == [(15, 1.0)]

    # List
    resp = await client.get("/api/v1/payroll/employees", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["team_names"] == []

    # PATCH: имя + смена должности; salary_periods не передаём — не трогаются
    resp = await client.patch(
        f"/api/v1/payroll/employees/{emp['id']}",
        json={"name": "Гл. бухгалтер", "position": "Гл. бухгалтер"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()
    assert updated["name"] == "Гл. бухгалтер"
    assert updated["position"] == "Гл. бухгалтер"
    assert len(updated["salary_periods"]) == 2  # история не тронута
    # график не тронут (fixed_pay_days не передавали)
    assert [(d["day"], float(d["share"])) for d in updated["fixed_pay_days"]] == [(15, 1.0)]

    # Полная замена истории окладов одним периодом
    resp = await client.patch(
        f"/api/v1/payroll/employees/{emp['id']}",
        json={"salary_periods": [{"month": "2026-03", "amount": "60000"}]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert [(p["month"], float(p["amount"])) for p in resp.json()["salary_periods"]] == [
        ("2026-03", 60000.0)
    ]

    # Пустой список — очистить историю; current_salary пропадает
    resp = await client.patch(
        f"/api/v1/payroll/employees/{emp['id']}",
        json={"salary_periods": []},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["salary_periods"] == []
    assert resp.json()["current_salary"] is None

    # clear_position — явное обнуление; position без флага не трогается
    resp = await client.patch(
        f"/api/v1/payroll/employees/{emp['id']}",
        json={"clear_position": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["position"] is None
    resp = await client.patch(
        f"/api/v1/payroll/employees/{emp['id']}",
        json={"notes": "должность не трогаем"},
        headers=headers,
    )
    assert resp.json()["position"] is None

    # DELETE — и сотрудник пропадает из списка
    resp = await client.delete(f"/api/v1/payroll/employees/{emp['id']}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    resp = await client.get("/api/v1/payroll/employees", headers=headers)
    assert resp.json()["items"] == []


async def test_employee_404_on_foreign_project(client, auth_headers):
    headers1, _ = await _project_headers(client, auth_headers)
    headers2, _ = await _project_headers(client, auth_headers)
    emp = await _create_employee(client, headers1)

    resp = await client.patch(
        f"/api/v1/payroll/employees/{emp['id']}", json={"name": "Взлом"}, headers=headers2
    )
    assert resp.status_code == 404
    resp = await client.delete(f"/api/v1/payroll/employees/{emp['id']}", headers=headers2)
    assert resp.status_code == 404


async def test_employee_bad_fixed_pay_days_422(client, auth_headers):
    headers, _ = await _project_headers(client, auth_headers)
    resp = await client.post(
        "/api/v1/payroll/employees",
        json={"name": "Кривой", "fixed_pay_days": [{"day": 10, "share": 0.3}]},
        headers=headers,
    )
    assert resp.status_code == 422  # доли обязаны суммироваться в 1


# ─── Teams / scopes / members ────────────────────────────────────────────────


async def test_team_scopes_members_flow(client, auth_headers):
    headers, _ = await _project_headers(client, auth_headers)
    e1 = await _create_employee(client, headers, name="Аня")
    e2 = await _create_employee(client, headers, name="Боря")
    team = await _create_team(client, headers)

    # Полная замена скоупов (дубль схлопывается)
    resp = await client.put(
        f"/api/v1/payroll/teams/{team['id']}/scopes",
        json={"scopes": [
            {"kind": "brand", "value": "BrandA"},
            {"kind": "subject", "value": "Ковры"},
            {"kind": "brand", "value": "BrandA"},
        ]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    scopes = resp.json()["scopes"]
    assert len(scopes) == 2

    # Полная замена участников
    resp = await client.put(
        f"/api/v1/payroll/teams/{team['id']}/members",
        json={"employee_ids": [e1["id"], e2["id"]]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert {m["employee_id"] for m in resp.json()["members"]} == {e1["id"], e2["id"]}

    # Замена на одного — второй уходит
    resp = await client.put(
        f"/api/v1/payroll/teams/{team['id']}/members",
        json={"employee_ids": [e2["id"]]},
        headers=headers,
    )
    assert [m["employee_id"] for m in resp.json()["members"]] == [e2["id"]]

    # team_names в списке сотрудников
    resp = await client.get("/api/v1/payroll/employees", headers=headers)
    by_name = {e["name"]: e for e in resp.json()["items"]}
    assert by_name["Боря"]["team_names"] == ["Ковры"]
    assert by_name["Аня"]["team_names"] == []

    # PATCH + soft-delete команды
    resp = await client.patch(
        f"/api/v1/payroll/teams/{team['id']}", json={"is_active": False}, headers=headers
    )
    assert resp.json()["is_active"] is False
    resp = await client.delete(f"/api/v1/payroll/teams/{team['id']}", headers=headers)
    assert resp.json()["ok"] is True
    resp = await client.get("/api/v1/payroll/teams", headers=headers)
    assert resp.json()["items"] == []


async def test_team_members_foreign_employee_404(client, auth_headers):
    headers1, _ = await _project_headers(client, auth_headers)
    headers2, _ = await _project_headers(client, auth_headers)
    foreign_emp = await _create_employee(client, headers2, name="Чужой")
    team = await _create_team(client, headers1)

    resp = await client.put(
        f"/api/v1/payroll/teams/{team['id']}/members",
        json={"employee_ids": [foreign_emp["id"]]},
        headers=headers1,
    )
    assert resp.status_code == 404


async def test_team_scope_invalid_kind_422(client, auth_headers):
    headers, _ = await _project_headers(client, auth_headers)
    team = await _create_team(client, headers)
    resp = await client.put(
        f"/api/v1/payroll/teams/{team['id']}/scopes",
        json={"scopes": [{"kind": "category", "value": "X"}]},
        headers=headers,
    )
    assert resp.status_code == 422


# ─── Scope options / tariff ──────────────────────────────────────────────────


async def test_scope_options_empty_project(client, auth_headers):
    headers, _ = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/payroll/scope-options", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"brands": [], "subjects": []}


async def test_tariff_get_seeded_ladder(client, auth_headers):
    """Действующий набор на 2026-06-30 — сид миграции payroll01_domain."""
    headers, _ = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/payroll/tariff?on_date=2026-06-30", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid_from"] is not None
    thresholds = [float(s["threshold"]) for s in data["steps"]]
    assert thresholds == sorted(thresholds)
    assert len(thresholds) > 0


async def test_tariff_put_replace_and_read_back(client, auth_headers, db_session):
    headers, _ = await _project_headers(client, auth_headers)
    try:
        resp = await client.put(
            "/api/v1/payroll/tariff",
            json={
                "valid_from": _TEST_TARIFF_DATE,
                "steps": [
                    {"threshold": "200000", "company_rate": "0.02", "team_rate": "0.012"},
                    {"threshold": "500000", "company_rate": "0.019", "team_rate": "0.011"},
                ],
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["steps"]) == 2

        # Повторный PUT того же valid_from — полная замена, не дописывание
        resp = await client.put(
            "/api/v1/payroll/tariff",
            json={
                "valid_from": _TEST_TARIFF_DATE,
                "steps": [{"threshold": "300000", "company_rate": "0.02", "team_rate": "0.01"}],
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()["steps"]) == 1

        resp = await client.get("/api/v1/payroll/tariff?on_date=1990-06-01", headers=headers)
        data = resp.json()
        assert data["valid_from"] == _TEST_TARIFF_DATE
        assert float(data["steps"][0]["threshold"]) == 300000.0
    finally:
        # Лестница глобальная — тестовый набор подчищаем сами
        await db_session.execute(
            delete(PayrollTariffStep).where(
                PayrollTariffStep.valid_from == date(1990, 1, 1)
            )
        )
        await db_session.commit()


async def test_tariff_duplicate_thresholds_422(client, auth_headers):
    headers, _ = await _project_headers(client, auth_headers)
    resp = await client.put(
        "/api/v1/payroll/tariff",
        json={
            "valid_from": _TEST_TARIFF_DATE,
            "steps": [
                {"threshold": "200000", "company_rate": "0.02", "team_rate": "0.012"},
                {"threshold": "200000", "company_rate": "0.02", "team_rate": "0.011"},
            ],
        },
        headers=headers,
    )
    assert resp.status_code == 422


# ─── Sheet / payout-mark ─────────────────────────────────────────────────────


async def test_sheet_empty_project(client, auth_headers):
    headers, _ = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/payroll/sheet?month=2026-06", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["month"] == "2026-06"
    assert len(data["weeks"]) == 4
    assert data["weeks"][0] == {"date_from": "2026-06-01", "date_to": "2026-06-07"}
    assert data["teams"] == []
    assert data["employees"] == []
    assert float(data["totals"]["accrued_total"]) == 0


async def test_sheet_bad_month_422(client, auth_headers):
    headers, _ = await _project_headers(client, auth_headers)
    resp = await client.get("/api/v1/payroll/sheet?month=июнь", headers=headers)
    assert resp.status_code == 422  # regex-валидация Query


async def test_payout_mark_upsert_and_foreign_404(client, auth_headers):
    headers1, _ = await _project_headers(client, auth_headers)
    headers2, _ = await _project_headers(client, auth_headers)
    emp = await _create_employee(
        client, headers1, name="Аня",
        salary_periods=[{"month": "2020-01", "amount": "50000"}],
    )

    resp = await client.put(
        "/api/v1/payroll/payout-mark",
        json={"employee_id": emp["id"], "month": "2026-06", "pay_day": 10,
              "paid": True, "amount": "25000", "comment": "нал"},
        headers=headers1,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True

    # Отметка видна в ведомости
    resp = await client.get("/api/v1/payroll/sheet?month=2026-06", headers=headers1)
    payouts = {p["pay_day"]: p for p in resp.json()["employees"][0]["payouts"]}
    assert payouts[10]["paid"] is True
    assert float(payouts[10]["paid_amount"]) == 25000.0

    # Чужой сотрудник → 404
    resp = await client.put(
        "/api/v1/payroll/payout-mark",
        json={"employee_id": emp["id"], "month": "2026-06", "pay_day": 10, "paid": True},
        headers=headers2,
    )
    assert resp.status_code == 404


async def test_payroll_requires_auth(client):
    resp = await client.get("/api/v1/payroll/employees")
    assert resp.status_code in (401, 403)
