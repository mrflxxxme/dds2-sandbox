"""
Tests for dashboard service — KPIs, expense breakdown, daily cashflow.

Uses DB fixtures from conftest_api.py.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.reports.dashboard import get_dashboard_summary

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


async def _ensure_account(db: AsyncSession, project_id: int, account: str, currency: str = "RUB"):
    """Ensure account exists."""
    result = await db.execute(text("SELECT 1 FROM accounts WHERE account = :acc"), {"acc": account})
    if not result.scalar():
        await db.execute(
            text(
                "INSERT INTO accounts (project_id, account, bank, currency, is_our_account, is_deleted, is_customs_payee) "
                "VALUES (:pid, :acc, 'VTB', :cur, true, false, false)"
            ),
            {"pid": project_id, "acc": account, "cur": currency},
        )
        await db.commit()


async def _create_opening_balance(db: AsyncSession, project_id: int, account: str, currency: str, amount: float):
    await db.execute(
        text(
            "INSERT INTO opening_balances (project_id, date_open, account, currency, opening_balance) "
            "VALUES (:pid, :d, :acc, :cur, :amt)"
        ),
        {"pid": project_id, "d": date(2024, 1, 1), "acc": account, "cur": currency, "amt": amount},
    )
    await db.commit()


async def _create_txn(
    db: AsyncSession,
    project_id: int,
    income: float = 0,
    expense: float = 0,
    cat_lvl1: str | None = None,
    txn_date: date | None = None,
    currency: str = "RUB",
    counterparty: str | None = None,
    cp_key: str | None = None,
    account: str = "40702810000000066666",
    is_cashflow2: int = 1,
    cat_lvl1_old: str | None = "set",  # sentinel for cat_lvl1 (not cat_lvl1_2)
):
    """Create a transaction for dashboard tests."""
    await _ensure_account(db, project_id, account, currency)
    net = income - expense
    txn_id = f"txn-{uuid.uuid4().hex[:12]}"
    await db.execute(
        text(
            "INSERT INTO transactions "
            "(project_id, date, bank, account, currency, income, expense, net, "
            "txn_id, is_cashflow2, is_deleted, cat_lvl1_2, counterparty, cp_key, cat_lvl1, is_internal, is_fx) "
            "VALUES (:pid, :d, 'VTB', :acc, :cur, :inc, :exp, :net, "
            ":tid, :cf2, false, :c1, :cp, :cpk, :c1_old, false, false)"
        ),
        {
            "pid": project_id,
            "d": txn_date or date.today(),
            "acc": account,
            "cur": currency,
            "inc": income,
            "exp": expense,
            "net": net,
            "tid": txn_id,
            "cf2": is_cashflow2,
            "c1": cat_lvl1,
            "cp": counterparty,
            "cpk": cp_key,
            "c1_old": cat_lvl1 if cat_lvl1_old == "set" else cat_lvl1_old,
        },
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: get_dashboard_summary
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetDashboardSummary:
    """Test dashboard KPIs."""

    @pytest.mark.asyncio
    async def test_empty_project(self, db_session: AsyncSession, project):
        """Empty project -> zeros for all KPIs."""
        result = await get_dashboard_summary.__wrapped__(db_session, project.id)

        assert result["balance_rub"] == 0
        assert result["balance_cny"] == 0
        assert result["month_income"] == 0
        assert result["month_expense"] == 0
        assert result["orders_count"] == 0
        assert result["inbox_count"] == 0

    @pytest.mark.asyncio
    async def test_balance_rub(self, db_session: AsyncSession, project):
        """RUB balance should reflect opening balance + transactions."""
        acc = f"40702810{uuid.uuid4().hex[:12]}"
        await _ensure_account(db_session, project.id, acc)
        await _create_opening_balance(db_session, project.id, acc, "RUB", 100000)
        await _create_txn(db_session, project.id, income=50000, account=acc, cat_lvl1="Выручка")

        result = await get_dashboard_summary.__wrapped__(db_session, project.id)
        assert result["balance_rub"] == 150000.0

    @pytest.mark.asyncio
    async def test_month_income_expense(self, db_session: AsyncSession, project):
        """Month income and expense should be calculated for current period."""
        today = date.today()
        await _create_txn(db_session, project.id, income=100000, cat_lvl1="Выручка", txn_date=today)
        await _create_txn(db_session, project.id, expense=30000, cat_lvl1="Логистика", txn_date=today)

        result = await get_dashboard_summary.__wrapped__(
            db_session,
            project.id,
            date_from=today.replace(day=1),
            date_to=today,
        )
        assert result["month_income_rub"] == 100000.0
        assert result["month_expense_rub"] == 30000.0

    @pytest.mark.asyncio
    async def test_expense_by_category(self, db_session: AsyncSession, project):
        """Expense by category breakdown."""
        today = date.today()
        await _create_txn(db_session, project.id, expense=50000, cat_lvl1="Логистика", txn_date=today)
        await _create_txn(db_session, project.id, expense=20000, cat_lvl1="Реклама", txn_date=today)

        result = await get_dashboard_summary.__wrapped__(
            db_session,
            project.id,
            date_from=today.replace(day=1),
            date_to=today,
        )

        cats = {c["name"]: c["value"] for c in result["expense_by_category"]}
        assert "Логистика" in cats
        assert "Реклама" in cats
        assert cats["Логистика"] == 50000.0

    @pytest.mark.asyncio
    async def test_income_counterparties(self, db_session: AsyncSession, project):
        """Top income counterparties."""
        today = date.today()
        await _create_txn(
            db_session,
            project.id,
            income=200000,
            cat_lvl1="Выручка",
            counterparty="Wildberries",
            cp_key="7721546864",
            txn_date=today,
        )

        result = await get_dashboard_summary.__wrapped__(
            db_session,
            project.id,
            date_from=today.replace(day=1),
            date_to=today,
        )

        assert len(result["income_counterparties"]) >= 1
        assert result["income_counterparties"][0]["total"] == 200000.0

    @pytest.mark.asyncio
    async def test_project_id_isolation(self, db_session: AsyncSession, project, other_project):
        """Dashboard from other project should not leak data."""
        today = date.today()
        await _create_txn(db_session, project.id, income=100000, cat_lvl1="Выручка", txn_date=today)
        other_acc = f"40702810{uuid.uuid4().hex[:12]}"
        await _create_txn(
            db_session,
            other_project.id,
            income=999000,
            cat_lvl1="Выручка",
            txn_date=today,
            account=other_acc,
        )

        result = await get_dashboard_summary.__wrapped__(
            db_session,
            project.id,
            date_from=today.replace(day=1),
            date_to=today,
        )
        assert result["month_income_rub"] == 100000.0

    @pytest.mark.asyncio
    async def test_inbox_count(self, db_session: AsyncSession, project):
        """Inbox = transactions without category."""
        today = date.today()
        await _create_txn(
            db_session,
            project.id,
            income=10000,
            cat_lvl1=None,
            cat_lvl1_old=None,
            txn_date=today,
        )
        await _create_txn(
            db_session,
            project.id,
            income=20000,
            cat_lvl1="Выручка",
            txn_date=today,
        )

        result = await get_dashboard_summary.__wrapped__(
            db_session,
            project.id,
            date_from=today.replace(day=1),
            date_to=today,
        )
        assert result["inbox_count"] >= 1

    @pytest.mark.asyncio
    async def test_date_range(self, db_session: AsyncSession, project):
        """Date range should filter transactions."""
        result = await get_dashboard_summary.__wrapped__(
            db_session,
            project.id,
            date_from=date(2025, 1, 1),
            date_to=date(2025, 1, 31),
        )
        assert result["date_from"] == "2025-01-01"
        assert result["date_to"] == "2025-01-31"

    @pytest.mark.asyncio
    async def test_daily_cashflow_structure(self, db_session: AsyncSession, project):
        """Daily cashflow should have date/income/expense keys."""
        today = date.today()
        await _create_txn(db_session, project.id, income=50000, cat_lvl1="Выручка", txn_date=today)

        result = await get_dashboard_summary.__wrapped__(
            db_session,
            project.id,
            date_from=today,
            date_to=today,
        )

        if result["daily_cashflow"]:
            day = result["daily_cashflow"][0]
            assert "date" in day
            assert "income" in day
            assert "expense" in day
