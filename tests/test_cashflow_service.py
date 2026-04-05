"""
Tests for planning/cashflow service — daily cashflow calculation & order summary.

Uses DB fixtures from conftest_api.py.
"""

from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.planning.cashflow import calculate_cashflow_daily, get_order_summary

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


async def _create_planned_income(db: AsyncSession, project_id: int, amount_rub: float, income_date: date):
    """Create a PlannedIncome record."""
    await db.execute(
        text(
            "INSERT INTO planned_incomes (project_id, date, amount_rub, source, is_deleted) "
            "VALUES (:pid, :d, :amt, 'test', false)"
        ),
        {"pid": project_id, "d": income_date, "amt": amount_rub},
    )
    await db.commit()


async def _create_planned_payment(
    db: AsyncSession,
    project_id: int,
    amount_rub: float,
    pay_date: date,
    is_paid: bool = False,
    order_no: int | None = None,
    currency: str = "RUB",
):
    """Create a PlannedPayment record."""
    await db.execute(
        text(
            "INSERT INTO planned_payments "
            "(project_id, pay_date, amount_rub, amount, currency, is_paid, is_deleted, order_no, paid_rub) "
            "VALUES (:pid, :d, :amt, :amt_orig, :cur, :paid, false, :ono, 0)"
        ),
        {
            "pid": project_id,
            "d": pay_date,
            "amt": amount_rub,
            "amt_orig": amount_rub,
            "cur": currency,
            "paid": is_paid,
            "ono": order_no,
        },
    )
    await db.commit()


async def _create_order(db: AsyncSession, project_id: int, order_no: int, order_amount: float = 100000):
    """Create an Order record."""
    await db.execute(
        text("INSERT INTO orders (project_id, order_no, order_amount, is_deleted) " "VALUES (:pid, :ono, :amt, false)"),
        {"pid": project_id, "ono": order_no, "amt": order_amount},
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: calculate_cashflow_daily
# ═══════════════════════════════════════════════════════════════════════════════


class TestCalculateCashflowDaily:
    """Test daily cashflow calculation."""

    @pytest.mark.asyncio
    async def test_empty_project(self, db_session: AsyncSession, project):
        """Empty project -> flat cashflow at starting_balance."""
        result = await calculate_cashflow_daily.__wrapped__(
            db_session,
            project.id,
            days=5,
            starting_balance=100000.0,
        )
        assert len(result) == 6  # today + 5 days
        # All days should have zero planned movements
        for row in result:
            assert row["planned_income"] == 0
            assert row["planned_expense"] == 0

    @pytest.mark.asyncio
    async def test_starting_balance_propagates(self, db_session: AsyncSession, project):
        """Starting balance should be the initial deficit_running."""
        result = await calculate_cashflow_daily.__wrapped__(
            db_session,
            project.id,
            days=3,
            starting_balance=50000.0,
        )
        # First day with no movements -> running = 50000
        assert result[0]["deficit_running"] == 50000.0

    @pytest.mark.asyncio
    async def test_planned_income_increases_running(self, db_session: AsyncSession, project):
        """Planned income on a future day should increase running balance."""
        future = date.today() + timedelta(days=2)
        await _create_planned_income(db_session, project.id, 30000, future)

        result = await calculate_cashflow_daily.__wrapped__(
            db_session,
            project.id,
            days=5,
            starting_balance=10000.0,
        )
        # Find the day with income
        income_day = next(r for r in result if r["date"] == str(future))
        assert income_day["planned_income"] == 30000.0

    @pytest.mark.asyncio
    async def test_planned_expense_decreases_running(self, db_session: AsyncSession, project):
        """Unpaid planned payment should decrease running balance."""
        future = date.today() + timedelta(days=3)
        await _create_planned_payment(db_session, project.id, 20000, future)

        result = await calculate_cashflow_daily.__wrapped__(
            db_session,
            project.id,
            days=5,
            starting_balance=50000.0,
        )
        expense_day = next(r for r in result if r["date"] == str(future))
        assert expense_day["planned_expense"] == 20000.0

    @pytest.mark.asyncio
    async def test_paid_payments_excluded(self, db_session: AsyncSession, project):
        """Already paid payments should not appear in cashflow."""
        future = date.today() + timedelta(days=2)
        await _create_planned_payment(db_session, project.id, 25000, future, is_paid=True)

        result = await calculate_cashflow_daily.__wrapped__(
            db_session,
            project.id,
            days=5,
            starting_balance=50000.0,
        )
        future_day = next(r for r in result if r["date"] == str(future))
        assert future_day["planned_expense"] == 0

    @pytest.mark.asyncio
    async def test_overdue_payment_moved_to_today(self, db_session: AsyncSession, project):
        """Overdue unpaid payment should appear on today."""
        past = date.today() - timedelta(days=5)
        await _create_planned_payment(db_session, project.id, 15000, past)

        result = await calculate_cashflow_daily.__wrapped__(
            db_session,
            project.id,
            days=3,
            starting_balance=50000.0,
        )
        today_row = result[0]
        assert today_row["date"] == str(date.today())
        assert today_row["planned_expense"] == 15000.0

    @pytest.mark.asyncio
    async def test_net_calculation(self, db_session: AsyncSession, project):
        """net = planned_income - planned_expense per day."""
        future = date.today() + timedelta(days=1)
        await _create_planned_income(db_session, project.id, 50000, future)
        await _create_planned_payment(db_session, project.id, 20000, future)

        result = await calculate_cashflow_daily.__wrapped__(
            db_session,
            project.id,
            days=3,
            starting_balance=0,
        )
        day = next(r for r in result if r["date"] == str(future))
        assert day["net"] == 30000.0  # 50000 - 20000

    @pytest.mark.asyncio
    async def test_deleted_incomes_excluded(self, db_session: AsyncSession, project):
        """Soft-deleted planned incomes should be excluded."""
        future = date.today() + timedelta(days=1)
        await db_session.execute(
            text(
                "INSERT INTO planned_incomes (project_id, date, amount_rub, source, is_deleted) "
                "VALUES (:pid, :d, 99999, 'test', true)"
            ),
            {"pid": project.id, "d": future},
        )
        await db_session.commit()

        result = await calculate_cashflow_daily.__wrapped__(
            db_session,
            project.id,
            days=3,
            starting_balance=0,
        )
        day = next(r for r in result if r["date"] == str(future))
        assert day["planned_income"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: get_order_summary
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetOrderSummary:
    """Test order plan vs fact summary."""

    @pytest.mark.asyncio
    async def test_nonexistent_order(self, db_session: AsyncSession, project):
        """Non-existent order -> None."""
        result = await get_order_summary(db_session, project.id, 999999)
        assert result is None

    @pytest.mark.asyncio
    async def test_existing_order_basic(self, db_session: AsyncSession, project):
        """Existing order returns summary dict."""
        import random

        ono = random.randint(100000, 999999)  # noqa: S311
        await _create_order(db_session, project.id, ono, order_amount=200000)

        result = await get_order_summary(db_session, project.id, ono)
        assert result is not None
        assert result["totals"]["plan_order"] == 200000.0

    @pytest.mark.asyncio
    async def test_order_with_planned_payments(self, db_session: AsyncSession, project):
        """Order with linked planned payments."""
        import random

        ono = random.randint(100000, 999999)  # noqa: S311
        await _create_order(db_session, project.id, ono, order_amount=300000)
        await _create_planned_payment(db_session, project.id, 150000, date.today(), order_no=ono)
        await _create_planned_payment(db_session, project.id, 150000, date.today() + timedelta(days=30), order_no=ono)

        result = await get_order_summary(db_session, project.id, ono)
        assert len(result["planned_payments"]) == 2

    @pytest.mark.asyncio
    async def test_project_id_isolation(self, db_session: AsyncSession, project, other_project):
        """Order from other project should not be visible."""
        import random

        ono = random.randint(100000, 999999)  # noqa: S311
        await _create_order(db_session, other_project.id, ono, order_amount=500000)

        result = await get_order_summary(db_session, project.id, ono)
        assert result is None

    @pytest.mark.asyncio
    async def test_deleted_order_not_found(self, db_session: AsyncSession, project):
        """Soft-deleted order -> None."""
        import random

        ono = random.randint(100000, 999999)  # noqa: S311
        await db_session.execute(
            text(
                "INSERT INTO orders (project_id, order_no, order_amount, is_deleted) "
                "VALUES (:pid, :ono, 100000, true)"
            ),
            {"pid": project.id, "ono": ono},
        )
        await db_session.commit()

        result = await get_order_summary(db_session, project.id, ono)
        assert result is None
