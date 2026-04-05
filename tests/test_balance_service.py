"""
Tests for balance service — account balance calculations.

Uses DB fixtures from conftest_api.py.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.reports.balance import get_balance, get_balance_daily

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


async def _create_account(db: AsyncSession, project_id: int, account: str, currency: str = "RUB", bank: str = "VTB"):
    """Create an Account record."""
    await db.execute(
        text(
            "INSERT INTO accounts (project_id, account, bank, currency, is_our_account, is_deleted, is_customs_payee) "
            "VALUES (:pid, :acc, :bank, :cur, true, false, false)"
        ),
        {"pid": project_id, "acc": account, "bank": bank, "cur": currency},
    )
    await db.commit()


async def _create_opening_balance(db: AsyncSession, project_id: int, account: str, currency: str, amount: float):
    """Create an OpeningBalance record."""
    await db.execute(
        text(
            "INSERT INTO opening_balances (project_id, date_open, account, currency, opening_balance) "
            "VALUES (:pid, :d, :acc, :cur, :amt)"
        ),
        {"pid": project_id, "d": date(2024, 1, 1), "acc": account, "cur": currency, "amt": amount},
    )
    await db.commit()


async def _create_transaction(
    db: AsyncSession,
    project_id: int,
    account: str,
    currency: str,
    income: float = 0,
    expense: float = 0,
    txn_date: date | None = None,
):
    """Create a Transaction record."""
    txn_date = txn_date or date(2024, 6, 15)
    net = income - expense
    txn_id = f"txn-{uuid.uuid4().hex[:12]}"
    await db.execute(
        text(
            "INSERT INTO transactions "
            "(project_id, date, bank, account, currency, income, expense, net, txn_id, is_cashflow2, is_deleted, is_internal, is_fx) "
            "VALUES (:pid, :d, :bank, :acc, :cur, :inc, :exp, :net, :tid, 1, false, false, false)"
        ),
        {
            "pid": project_id,
            "d": txn_date,
            "bank": "VTB",
            "acc": account,
            "cur": currency,
            "inc": income,
            "exp": expense,
            "net": net,
            "tid": txn_id,
        },
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: get_balance
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetBalance:
    """Test current balance per account/currency."""

    @pytest.mark.asyncio
    async def test_empty_project_no_balances(self, db_session: AsyncSession, project):
        """Project with no accounts -> empty list."""
        result = await get_balance.__wrapped__(db_session, project.id)
        assert result == []

    @pytest.mark.asyncio
    async def test_opening_balance_only(self, db_session: AsyncSession, project):
        """Account with opening balance, no transactions."""
        acc = f"40702810{uuid.uuid4().hex[:12]}"
        await _create_account(db_session, project.id, acc, "RUB")
        await _create_opening_balance(db_session, project.id, acc, "RUB", 100000.0)

        result = await get_balance.__wrapped__(db_session, project.id)
        assert len(result) == 1
        assert result[0]["balance"] == 100000.0
        assert result[0]["currency"] == "RUB"

    @pytest.mark.asyncio
    async def test_balance_with_transactions(self, db_session: AsyncSession, project):
        """Balance = opening + sum(net)."""
        acc = f"40702810{uuid.uuid4().hex[:12]}"
        await _create_account(db_session, project.id, acc, "RUB")
        await _create_opening_balance(db_session, project.id, acc, "RUB", 50000.0)
        await _create_transaction(db_session, project.id, acc, "RUB", income=30000)
        await _create_transaction(db_session, project.id, acc, "RUB", expense=10000)

        result = await get_balance.__wrapped__(db_session, project.id)
        assert len(result) == 1
        # 50000 + 30000 - 10000 = 70000
        assert result[0]["balance"] == 70000.0

    @pytest.mark.asyncio
    async def test_multi_currency_accounts(self, db_session: AsyncSession, project):
        """Multiple accounts in different currencies."""
        acc_rub = f"40702810{uuid.uuid4().hex[:12]}"
        acc_cny = f"40702156{uuid.uuid4().hex[:12]}"
        await _create_account(db_session, project.id, acc_rub, "RUB")
        await _create_account(db_session, project.id, acc_cny, "CNY", bank="VTB")
        await _create_opening_balance(db_session, project.id, acc_rub, "RUB", 100000.0)
        await _create_opening_balance(db_session, project.id, acc_cny, "CNY", 5000.0)

        result = await get_balance.__wrapped__(db_session, project.id)
        assert len(result) == 2
        currencies = {r["currency"] for r in result}
        assert "RUB" in currencies
        assert "CNY" in currencies

    @pytest.mark.asyncio
    async def test_project_id_isolation(self, db_session: AsyncSession, project, other_project):
        """Accounts from other project should not be visible."""
        acc_main = f"40702810{uuid.uuid4().hex[:12]}"
        acc_other = f"40702810{uuid.uuid4().hex[:12]}"
        await _create_account(db_session, project.id, acc_main, "RUB")
        await _create_account(db_session, other_project.id, acc_other, "RUB")
        await _create_opening_balance(db_session, project.id, acc_main, "RUB", 100000.0)
        await _create_opening_balance(db_session, other_project.id, acc_other, "RUB", 200000.0)

        result = await get_balance.__wrapped__(db_session, project.id)
        assert len(result) == 1
        assert result[0]["balance"] == 100000.0

    @pytest.mark.asyncio
    async def test_is_deleted_accounts_excluded(self, db_session: AsyncSession, project):
        """Deleted accounts should not appear."""
        acc = f"40702810{uuid.uuid4().hex[:12]}"
        await db_session.execute(
            text(
                "INSERT INTO accounts (project_id, account, bank, currency, is_our_account, is_deleted, is_customs_payee) "
                "VALUES (:pid, :acc, 'VTB', 'RUB', true, true, false)"
            ),
            {"pid": project.id, "acc": acc},
        )
        await db_session.commit()

        result = await get_balance.__wrapped__(db_session, project.id)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_deleted_transactions_excluded(self, db_session: AsyncSession, project):
        """Soft-deleted transactions should not affect balance."""
        acc = f"40702810{uuid.uuid4().hex[:12]}"
        await _create_account(db_session, project.id, acc, "RUB")
        await _create_opening_balance(db_session, project.id, acc, "RUB", 100000.0)

        # Create a deleted transaction
        txn_id = f"txn-del-{uuid.uuid4().hex[:8]}"
        await db_session.execute(
            text(
                "INSERT INTO transactions "
                "(project_id, date, bank, account, currency, income, expense, net, txn_id, is_cashflow2, is_deleted, is_internal, is_fx) "
                "VALUES (:pid, :d, 'VTB', :acc, 'RUB', 50000, 0, 50000, :tid, 1, true, false, false)"
            ),
            {"pid": project.id, "d": date(2024, 6, 15), "acc": acc, "tid": txn_id},
        )
        await db_session.commit()

        result = await get_balance.__wrapped__(db_session, project.id)
        assert result[0]["balance"] == 100000.0  # deleted txn excluded


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: get_balance_daily
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetBalanceDaily:
    """Test running daily balance for a specific account."""

    @pytest.mark.asyncio
    async def test_empty_account(self, db_session: AsyncSession, project):
        """No transactions -> empty list."""
        acc = f"40702810{uuid.uuid4().hex[:12]}"
        await _create_account(db_session, project.id, acc, "RUB")

        result = await get_balance_daily.__wrapped__(db_session, project.id, acc, "RUB")
        assert result == []

    @pytest.mark.asyncio
    async def test_running_balance(self, db_session: AsyncSession, project):
        """Running balance accumulates correctly."""
        acc = f"40702810{uuid.uuid4().hex[:12]}"
        await _create_account(db_session, project.id, acc, "RUB")
        await _create_opening_balance(db_session, project.id, acc, "RUB", 10000.0)

        await _create_transaction(db_session, project.id, acc, "RUB", income=5000, txn_date=date(2024, 6, 1))
        await _create_transaction(db_session, project.id, acc, "RUB", expense=3000, txn_date=date(2024, 6, 2))
        await _create_transaction(db_session, project.id, acc, "RUB", income=2000, txn_date=date(2024, 6, 3))

        result = await get_balance_daily.__wrapped__(db_session, project.id, acc, "RUB")

        assert len(result) == 3
        # Day 1: opening 10000 + 5000 = 15000
        assert result[0]["balance"] == 15000.0
        # Day 2: 15000 - 3000 = 12000
        assert result[1]["balance"] == 12000.0
        # Day 3: 12000 + 2000 = 14000
        assert result[2]["balance"] == 14000.0

    @pytest.mark.asyncio
    async def test_date_range_filter(self, db_session: AsyncSession, project):
        """date_from/date_to should filter transactions."""
        acc = f"40702810{uuid.uuid4().hex[:12]}"
        await _create_account(db_session, project.id, acc, "RUB")
        await _create_opening_balance(db_session, project.id, acc, "RUB", 0)

        await _create_transaction(db_session, project.id, acc, "RUB", income=1000, txn_date=date(2024, 1, 15))
        await _create_transaction(db_session, project.id, acc, "RUB", income=2000, txn_date=date(2024, 6, 15))
        await _create_transaction(db_session, project.id, acc, "RUB", income=3000, txn_date=date(2024, 12, 15))

        result = await get_balance_daily.__wrapped__(
            db_session,
            project.id,
            acc,
            "RUB",
            date_from=date(2024, 6, 1),
            date_to=date(2024, 6, 30),
        )

        assert len(result) == 1
        assert result[0]["daily_net"] == 2000.0
