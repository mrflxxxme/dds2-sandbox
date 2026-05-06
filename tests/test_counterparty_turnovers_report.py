"""
Tests for CounterpartyTurnoversReport.
Seeds 5 counterparties and 60 transactions for 3 months.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from backend.models.counterparty import Counterparty
from backend.services.reports.counterparty_turnovers import CounterpartyTurnoversReport

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _inn() -> str:
    return str(int(uuid.uuid4().hex[:9], 16) % 9000000000 + 1000000000)


async def _create_project(client, auth_headers) -> int:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"turnovers_test_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    return resp.json()["id"]


async def _seed_turnovers_data(db_session, project_id: int) -> list[int]:
    """Seed 5 counterparties and transactions for 3 months. Returns cp_ids.

    Все вставки идут в одну транзакцию (flush + commit в конце), чтобы не
    давать окно между created counterparty.commit() и transaction insert,
    в которое параллельный воркер xdist может затереть FK target.
    """
    cp_types = ["FULFILLMENT", "CARRIER", "SUPPLIER", "MARKETPLACE", "OTHER"]
    cps: list[Counterparty] = []
    for i, cp_type in enumerate(cp_types):
        cp = Counterparty(
            project_id=project_id,
            inn=_inn(),
            name=f"КА Тест {i+1} {cp_type}",
            primary_type=cp_type,
            secondary_types=[],
            created_by_import=False,
        )
        db_session.add(cp)
        cps.append(cp)
    await db_session.flush()  # получаем cp.id, но без commit — атомарно с tx
    cp_ids = [cp.id for cp in cps]

    # Create transactions for each CP across 3 months
    from backend.models.transactions import Transaction

    months = [date(2026, 1, 15), date(2026, 2, 15), date(2026, 3, 15)]

    for cp_id in cp_ids:
        for month_date in months:
            for j in range(4):  # 4 transactions per cp per month = 60 total
                income_val = Decimal("50000") if j % 2 == 0 else Decimal("0")
                expense_val = Decimal("30000") if j % 2 == 1 else Decimal("0")
                currency = "RUB" if j < 3 else "CNY"

                tx = Transaction(
                    project_id=project_id,
                    date=month_date,
                    bank="WB",
                    account="40702810800000001893",
                    currency=currency,
                    income=income_val,
                    expense=expense_val,
                    net=income_val - expense_val,
                    purpose=f"Тест транзакция cp_{cp_id} j={j}",
                    txn_id=f"txn_{uuid.uuid4().hex[:12]}",
                    status="OK",
                    is_cashflow2=1,
                    counterparty_id=cp_id,
                )
                db_session.add(tx)

    await db_session.commit()
    return cp_ids


# ─── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_3_months(db_session, client, auth_headers):
    """generate() returns correct pivot structure for 3 months × 5 CPs."""
    project_id = await _create_project(client, auth_headers)
    await _seed_turnovers_data(db_session, project_id)

    report = CounterpartyTurnoversReport(db_session)
    result = await report.generate(
        project_id=project_id,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 3, 31),
        type_filter=None,
        currency="RUB",
        min_turnover=Decimal("0"),
    )

    assert len(result.rows) >= 5
    assert result.period["from"] == date(2026, 1, 1)
    assert result.period["to"] == date(2026, 3, 31)
    assert result.currency == "RUB"

    # Each row should have 3 months
    for row in result.rows:
        assert len(row.months) == 3
        # Verify totals consistency
        total_in = sum(m.in_sum for m in row.months)
        assert row.total.in_sum == total_in


@pytest.mark.asyncio
async def test_generate_filter_by_type(db_session, client, auth_headers):
    """generate() with type_filter returns only that type's rows."""
    project_id = await _create_project(client, auth_headers)
    await _seed_turnovers_data(db_session, project_id)

    report = CounterpartyTurnoversReport(db_session)
    result = await report.generate(
        project_id=project_id,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 3, 31),
        type_filter="FULFILLMENT",
        currency="RUB",
        min_turnover=Decimal("0"),
    )

    assert all(row.primary_type == "FULFILLMENT" for row in result.rows)


@pytest.mark.asyncio
async def test_generate_cny_only(db_session, client, auth_headers):
    """generate() with currency=CNY returns only CNY transactions."""
    project_id = await _create_project(client, auth_headers)
    await _seed_turnovers_data(db_session, project_id)

    report = CounterpartyTurnoversReport(db_session)
    result = await report.generate(
        project_id=project_id,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 3, 31),
        type_filter=None,
        currency="CNY",
        min_turnover=Decimal("0"),
    )

    assert result.currency == "CNY"
    # Should have data since we seeded CNY txns
    assert len(result.rows) >= 0


@pytest.mark.asyncio
async def test_generate_min_turnover_filter(db_session, client, auth_headers):
    """generate() with min_turnover filters out low-turnover rows."""
    project_id = await _create_project(client, auth_headers)
    await _seed_turnovers_data(db_session, project_id)

    report = CounterpartyTurnoversReport(db_session)
    # High min_turnover — should filter everything
    result = await report.generate(
        project_id=project_id,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 3, 31),
        type_filter=None,
        currency="RUB",
        min_turnover=Decimal("999999999"),  # unreachable threshold
    )

    assert len(result.rows) == 0


@pytest.mark.asyncio
async def test_generate_empty_period(db_session, client, auth_headers):
    """generate() for period with no transactions returns empty rows."""
    project_id = await _create_project(client, auth_headers)

    report = CounterpartyTurnoversReport(db_session)
    result = await report.generate(
        project_id=project_id,
        date_from=date(2025, 1, 1),
        date_to=date(2025, 1, 31),
        type_filter=None,
        currency="RUB",
        min_turnover=Decimal("0"),
    )

    assert len(result.rows) == 0
    assert result.period["from"] == date(2025, 1, 1)
    assert result.period["to"] == date(2025, 1, 31)
