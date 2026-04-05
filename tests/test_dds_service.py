"""
Tests for DDS month report service — get_dds_month.

Uses DB fixtures for integration tests.
The PnL aggregation tests are already in test_dds_report.py.
This file tests the DB-backed get_dds_month function.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.reports.dds import get_dds_month

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


async def _ensure_account(db: AsyncSession, project_id: int, account: str, currency: str = "RUB"):
    """Ensure account exists for FK constraint."""
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


async def _create_txn(
    db: AsyncSession,
    project_id: int,
    income: float = 0,
    expense: float = 0,
    cat_lvl1: str | None = None,
    cat_lvl2: str | None = None,
    txn_date: date = date(2025, 3, 15),
    currency: str = "RUB",
    is_cashflow2: int = 1,
    account: str = "40702810000000077777",
):
    """Create a transaction for DDS report tests."""
    await _ensure_account(db, project_id, account, currency)
    net = income - expense
    txn_id = f"txn-{uuid.uuid4().hex[:12]}"
    await db.execute(
        text(
            "INSERT INTO transactions "
            "(project_id, date, bank, account, currency, income, expense, net, "
            "txn_id, is_cashflow2, is_deleted, cat_lvl1_2, cat_lvl2_2, is_internal, is_fx) "
            "VALUES (:pid, :d, 'VTB', :acc, :cur, :inc, :exp, :net, "
            ":tid, :cf2, false, :c1, :c2, false, false)"
        ),
        {
            "pid": project_id,
            "d": txn_date,
            "acc": account,
            "cur": currency,
            "inc": income,
            "exp": expense,
            "net": net,
            "tid": txn_id,
            "cf2": is_cashflow2,
            "c1": cat_lvl1,
            "c2": cat_lvl2,
        },
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: get_dds_month
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetDdsMonth:
    """Test DDS monthly grouped report."""

    @pytest.mark.asyncio
    async def test_empty_month(self, db_session: AsyncSession, project):
        """Month with no data -> empty list."""
        result = await get_dds_month.__wrapped__(db_session, project.id, 2025, 3)
        assert result == []

    @pytest.mark.asyncio
    async def test_single_income_category(self, db_session: AsyncSession, project):
        """Single income transaction -> one row."""
        await _create_txn(db_session, project.id, income=100000, cat_lvl1="Выручка WB")

        result = await get_dds_month.__wrapped__(db_session, project.id, 2025, 3)
        assert len(result) == 1
        assert result[0]["cat_lvl1"] == "Выручка WB"
        assert result[0]["income"] == 100000.0
        assert result[0]["expense"] == 0

    @pytest.mark.asyncio
    async def test_multiple_categories(self, db_session: AsyncSession, project):
        """Multiple categories -> multiple rows."""
        await _create_txn(db_session, project.id, income=100000, cat_lvl1="Выручка WB")
        await _create_txn(db_session, project.id, expense=30000, cat_lvl1="Логистика")
        await _create_txn(db_session, project.id, expense=10000, cat_lvl1="Реклама")

        result = await get_dds_month.__wrapped__(db_session, project.id, 2025, 3)
        assert len(result) == 3
        cat_names = {r["cat_lvl1"] for r in result}
        assert "Выручка WB" in cat_names
        assert "Логистика" in cat_names
        assert "Реклама" in cat_names

    @pytest.mark.asyncio
    async def test_net_calculated(self, db_session: AsyncSession, project):
        """Net = income - expense per category."""
        await _create_txn(db_session, project.id, income=50000, expense=20000, cat_lvl1="Смешанная")

        result = await get_dds_month.__wrapped__(db_session, project.id, 2025, 3)
        assert len(result) == 1
        assert result[0]["net"] == 30000.0

    @pytest.mark.asyncio
    async def test_only_cashflow_transactions(self, db_session: AsyncSession, project):
        """Non-cashflow transactions (is_cashflow2=0) should be excluded."""
        await _create_txn(db_session, project.id, income=100000, cat_lvl1="Выручка WB", is_cashflow2=1)
        await _create_txn(db_session, project.id, income=50000, cat_lvl1="Внутренний", is_cashflow2=0)

        result = await get_dds_month.__wrapped__(db_session, project.id, 2025, 3)
        cat_names = {r["cat_lvl1"] for r in result}
        assert "Выручка WB" in cat_names
        assert "Внутренний" not in cat_names

    @pytest.mark.asyncio
    async def test_currency_filter(self, db_session: AsyncSession, project):
        """Currency filter should work."""
        acc_cny = f"40702156{uuid.uuid4().hex[:12]}"
        await _create_txn(db_session, project.id, income=100000, cat_lvl1="Выручка", currency="RUB")
        await _create_txn(db_session, project.id, expense=50000, cat_lvl1="Закупка", currency="CNY", account=acc_cny)

        result_rub = await get_dds_month.__wrapped__(db_session, project.id, 2025, 3, currency="RUB")
        result_cny = await get_dds_month.__wrapped__(db_session, project.id, 2025, 3, currency="CNY")

        assert len(result_rub) == 1
        assert result_rub[0]["cat_lvl1"] == "Выручка"
        assert len(result_cny) == 1
        assert result_cny[0]["cat_lvl1"] == "Закупка"

    @pytest.mark.asyncio
    async def test_month_boundary(self, db_session: AsyncSession, project):
        """Only transactions within the month should be included."""
        # March transaction
        await _create_txn(db_session, project.id, income=100000, cat_lvl1="Выручка", txn_date=date(2025, 3, 15))
        # February transaction
        await _create_txn(db_session, project.id, income=50000, cat_lvl1="Выручка", txn_date=date(2025, 2, 28))
        # April transaction
        await _create_txn(db_session, project.id, income=70000, cat_lvl1="Выручка", txn_date=date(2025, 4, 1))

        result = await get_dds_month.__wrapped__(db_session, project.id, 2025, 3)
        assert len(result) == 1
        assert result[0]["income"] == 100000.0

    @pytest.mark.asyncio
    async def test_project_id_isolation(self, db_session: AsyncSession, project, other_project):
        """Transactions from other project should not be included."""
        await _create_txn(db_session, project.id, income=100000, cat_lvl1="Выручка")
        other_acc = f"40702810{uuid.uuid4().hex[:12]}"
        await _create_txn(db_session, other_project.id, income=200000, cat_lvl1="Выручка", account=other_acc)

        result = await get_dds_month.__wrapped__(db_session, project.id, 2025, 3)
        total_income = sum(r["income"] for r in result)
        assert total_income == 100000.0

    @pytest.mark.asyncio
    async def test_subcategory_grouping(self, db_session: AsyncSession, project):
        """Transactions should be grouped by cat_lvl1_2 AND cat_lvl2_2."""
        await _create_txn(db_session, project.id, expense=10000, cat_lvl1="Логистика", cat_lvl2="DHL")
        await _create_txn(db_session, project.id, expense=15000, cat_lvl1="Логистика", cat_lvl2="CDEK")
        await _create_txn(db_session, project.id, expense=5000, cat_lvl1="Логистика", cat_lvl2="DHL")

        result = await get_dds_month.__wrapped__(db_session, project.id, 2025, 3)
        dhl = next(r for r in result if r["cat_lvl2"] == "DHL")
        cdek = next(r for r in result if r["cat_lvl2"] == "CDEK")
        assert dhl["expense"] == 15000.0  # 10000 + 5000
        assert cdek["expense"] == 15000.0

    @pytest.mark.asyncio
    async def test_decimal_precision(self, db_session: AsyncSession, project):
        """Financial amounts should maintain decimal precision."""
        await _create_txn(db_session, project.id, income=99999.99, cat_lvl1="Точность")

        result = await get_dds_month.__wrapped__(db_session, project.id, 2025, 3)
        assert result[0]["income"] == 99999.99
