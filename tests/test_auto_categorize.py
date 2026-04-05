"""
Tests for auto_categorize service — keyword-based auto-categorization rules.

Uses DB fixtures from conftest_api.py.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Transaction
from backend.services.auto_categorize import (
    apply_auto_categorize,
    create_rule,
    delete_rule,
    get_all_rules,
    get_rules,
    preview_auto_categorize,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


async def _create_account_if_needed(db: AsyncSession, project_id: int, account: str):
    """Ensure account exists for FK constraint."""
    result = await db.execute(
        text("SELECT 1 FROM accounts WHERE account = :acc"),
        {"acc": account},
    )
    if not result.scalar():
        await db.execute(
            text(
                "INSERT INTO accounts (project_id, account, bank, currency, is_our_account, is_deleted, is_customs_payee) "
                "VALUES (:pid, :acc, 'VTB', 'RUB', true, false, false)"
            ),
            {"pid": project_id, "acc": account},
        )
        await db.commit()


async def _create_unassigned_txn(
    db: AsyncSession,
    project_id: int,
    purpose: str = "Оплата за товар",
    counterparty: str = "ООО Ромашка",
    expense: float = 10000,
    income: float = 0,
    account: str = "40702810000000099999",
) -> str:
    """Create an unassigned cashflow transaction, return txn_id."""
    await _create_account_if_needed(db, project_id, account)
    txn_id = f"txn-{uuid.uuid4().hex[:12]}"
    net = income - expense
    await db.execute(
        text(
            "INSERT INTO transactions "
            "(project_id, date, bank, account, currency, income, expense, net, "
            "txn_id, is_cashflow2, is_deleted, counterparty, purpose, cat_lvl1_2, is_internal, is_fx) "
            "VALUES (:pid, :d, 'VTB', :acc, 'RUB', :inc, :exp, :net, "
            ":tid, 1, false, :cp, :purp, NULL, false, false)"
        ),
        {
            "pid": project_id,
            "d": date(2024, 6, 15),
            "acc": account,
            "inc": income,
            "exp": expense,
            "net": net,
            "tid": txn_id,
            "cp": counterparty,
            "purp": purpose,
        },
    )
    await db.commit()
    return txn_id


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: get_rules / get_all_rules
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetRules:
    """Test rule retrieval."""

    @pytest.mark.asyncio
    async def test_empty_project_no_rules(self, db_session: AsyncSession, project):
        """No rules -> empty list."""
        rules = await get_rules(db_session, project.id)
        assert rules == []

    @pytest.mark.asyncio
    async def test_get_active_rules_only(self, db_session: AsyncSession, project):
        """get_rules returns only active, non-deleted rules."""
        await create_rule(db_session, project.id, "логистика", "Логистика")
        r2 = await create_rule(db_session, project.id, "реклама", "Реклама")
        # Deactivate one
        r2.is_active = False
        await db_session.commit()

        rules = await get_rules(db_session, project.id)
        assert len(rules) == 1
        assert rules[0].keyword == "логистика"

    @pytest.mark.asyncio
    async def test_get_all_rules_includes_inactive(self, db_session: AsyncSession, project):
        """get_all_rules returns active + inactive (but not deleted)."""
        await create_rule(db_session, project.id, "логистика", "Логистика")
        r2 = await create_rule(db_session, project.id, "реклама", "Реклама")
        r2.is_active = False
        await db_session.commit()

        rules = await get_all_rules(db_session, project.id)
        assert len(rules) == 2

    @pytest.mark.asyncio
    async def test_project_id_isolation(self, db_session: AsyncSession, project, other_project):
        """Rules from other project not visible."""
        await create_rule(db_session, project.id, "логистика", "Логистика")
        await create_rule(db_session, other_project.id, "зарплата", "Зарплата")

        rules = await get_rules(db_session, project.id)
        assert len(rules) == 1
        assert rules[0].keyword == "логистика"

    @pytest.mark.asyncio
    async def test_rules_sorted_by_priority(self, db_session: AsyncSession, project):
        """Rules should be sorted by priority desc."""
        await create_rule(db_session, project.id, "low", "Low", priority=1)
        await create_rule(db_session, project.id, "high", "High", priority=10)
        await create_rule(db_session, project.id, "mid", "Mid", priority=5)

        rules = await get_rules(db_session, project.id)
        keywords = [r.keyword for r in rules]
        assert keywords == ["high", "mid", "low"]


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: create_rule
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateRule:
    """Test rule creation."""

    @pytest.mark.asyncio
    async def test_create_basic(self, db_session: AsyncSession, project):
        """Create a basic rule."""
        rule = await create_rule(db_session, project.id, "Wildberries", "Выручка WB")

        assert rule.id is not None
        assert rule.keyword == "wildberries"  # lowercased
        assert rule.cat_lvl1 == "Выручка WB"
        assert rule.project_id == project.id
        assert rule.is_active is True

    @pytest.mark.asyncio
    async def test_keyword_normalized(self, db_session: AsyncSession, project):
        """Keywords should be lowercased and stripped."""
        rule = await create_rule(db_session, project.id, "  Логистика  ", "Логистика")
        assert rule.keyword == "логистика"

    @pytest.mark.asyncio
    async def test_create_with_lvl2(self, db_session: AsyncSession, project):
        """Create with cat_lvl2."""
        rule = await create_rule(db_session, project.id, "dhl", "Логистика", cat_lvl2="DHL Express")
        assert rule.cat_lvl2 == "DHL Express"

    @pytest.mark.asyncio
    async def test_create_income_direction(self, db_session: AsyncSession, project):
        """Create income-direction rule."""
        rule = await create_rule(db_session, project.id, "wildberries", "Выручка WB", direction="income")
        assert rule.direction == "income"


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: delete_rule
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeleteRule:
    """Test soft-deletion of rules."""

    @pytest.mark.asyncio
    async def test_delete_existing(self, db_session: AsyncSession, project):
        """Soft-delete a rule."""
        rule = await create_rule(db_session, project.id, "test", "Test")
        result = await delete_rule(db_session, project.id, rule.id)

        assert result["ok"] is True
        assert result["deleted_id"] == rule.id

        # Should not appear in active rules
        rules = await get_rules(db_session, project.id)
        assert len(rules) == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, db_session: AsyncSession, project):
        """Deleting non-existent rule -> error."""
        result = await delete_rule(db_session, project.id, 999999)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delete_other_project_rule_fails(self, db_session: AsyncSession, project, other_project):
        """Cannot delete rule belonging to another project."""
        rule = await create_rule(db_session, other_project.id, "test", "Test")
        result = await delete_rule(db_session, project.id, rule.id)
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: preview_auto_categorize
# ═══════════════════════════════════════════════════════════════════════════════


class TestPreviewAutoCategorize:
    """Test preview matching."""

    @pytest.mark.asyncio
    async def test_no_rules_empty(self, db_session: AsyncSession, project):
        """No rules -> empty preview."""
        result = await preview_auto_categorize(db_session, project.id)
        assert result == []

    @pytest.mark.asyncio
    async def test_keyword_match_in_purpose(self, db_session: AsyncSession, project):
        """Keyword match in purpose field."""
        await create_rule(db_session, project.id, "логистика", "Логистика")
        txn_id = await _create_unassigned_txn(
            db_session,
            project.id,
            purpose="Оплата за логистика DHL",
            expense=15000,
        )

        result = await preview_auto_categorize(db_session, project.id)
        assert len(result) == 1
        assert result[0]["txn_id"] == txn_id
        assert result[0]["suggested_cat_lvl1"] == "Логистика"
        assert result[0]["matched_keyword"] == "логистика"

    @pytest.mark.asyncio
    async def test_keyword_match_in_counterparty(self, db_session: AsyncSession, project):
        """Keyword match in counterparty name."""
        await create_rule(db_session, project.id, "wildberries", "Выручка WB", direction="income")
        _ = await _create_unassigned_txn(
            db_session,
            project.id,
            counterparty="ООО Вайлдберриз Wildberries",
            purpose="Перевод по реестру",
            income=500000,
            expense=0,
        )

        result = await preview_auto_categorize(db_session, project.id)
        assert len(result) == 1
        assert result[0]["suggested_cat_lvl1"] == "Выручка WB"

    @pytest.mark.asyncio
    async def test_direction_filter_expense(self, db_session: AsyncSession, project):
        """Expense rule should not match income transactions."""
        await create_rule(db_session, project.id, "тест", "Тест", direction="expense")
        # Income transaction
        await _create_unassigned_txn(
            db_session,
            project.id,
            purpose="тест поступление",
            income=10000,
            expense=0,
        )

        result = await preview_auto_categorize(db_session, project.id)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_first_rule_wins(self, db_session: AsyncSession, project):
        """Higher priority rule should match first."""
        # Both keywords are different, so unique constraint is OK
        await create_rule(db_session, project.id, "оплата за товар", "Закупка товара", priority=10)
        await create_rule(db_session, project.id, "оплата", "Оплата общая", priority=1)

        _ = await _create_unassigned_txn(
            db_session,
            project.id,
            purpose="Оплата за товар по договору",
            expense=50000,
        )

        result = await preview_auto_categorize(db_session, project.id)
        assert len(result) == 1
        # Higher priority rule "оплата за товар" should match first
        assert result[0]["suggested_cat_lvl1"] == "Закупка товара"

    @pytest.mark.asyncio
    async def test_already_categorized_excluded(self, db_session: AsyncSession, project):
        """Already categorized transactions should not be matched."""
        await create_rule(db_session, project.id, "тест", "Тест")

        account = "40702810000000099999"
        await _create_account_if_needed(db_session, project.id, account)
        txn_id = f"txn-{uuid.uuid4().hex[:12]}"
        await db_session.execute(
            text(
                "INSERT INTO transactions "
                "(project_id, date, bank, account, currency, income, expense, net, "
                "txn_id, is_cashflow2, is_deleted, purpose, cat_lvl1_2, is_internal, is_fx) "
                "VALUES (:pid, :d, 'VTB', :acc, 'RUB', 0, 10000, -10000, "
                ":tid, 1, false, 'тест операция', 'Уже категоризировано', false, false)"
            ),
            {
                "pid": project.id,
                "d": date(2024, 6, 15),
                "acc": account,
                "tid": txn_id,
            },
        )
        await db_session.commit()

        result = await preview_auto_categorize(db_session, project.id)
        assert len(result) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: apply_auto_categorize
# ═══════════════════════════════════════════════════════════════════════════════


class TestApplyAutoCategorize:
    """Test applying auto-categorization."""

    @pytest.mark.asyncio
    async def test_no_matches_zero_updated(self, db_session: AsyncSession, project):
        """No matches -> updated=0."""
        result = await apply_auto_categorize(db_session, project.id)
        assert result["ok"] is True
        assert result["updated"] == 0

    @pytest.mark.asyncio
    async def test_apply_updates_transactions(self, db_session: AsyncSession, project):
        """Apply should update transaction categories."""
        await create_rule(db_session, project.id, "логистика", "Логистика", cat_lvl2="DHL")
        txn_id = await _create_unassigned_txn(
            db_session,
            project.id,
            purpose="Оплата за логистика услуги",
            expense=20000,
        )

        result = await apply_auto_categorize(db_session, project.id)
        assert result["ok"] is True
        assert result["updated"] == 1
        assert result["details"][0]["txn_id"] == txn_id
        assert result["details"][0]["cat_lvl1"] == "Логистика"
        assert result["details"][0]["cat_lvl2"] == "DHL"

        # Verify in DB
        txn_result = await db_session.execute(select(Transaction).where(Transaction.txn_id == txn_id))
        txn = txn_result.scalar_one()
        assert txn.cat_lvl1_2 == "Логистика"
        assert txn.cat_lvl2_2 == "DHL"

    @pytest.mark.asyncio
    async def test_project_id_isolation(self, db_session: AsyncSession, project, other_project):
        """Apply should only affect current project."""
        await create_rule(db_session, project.id, "тест", "Тест")
        # Create unassigned txn in OTHER project
        await _create_unassigned_txn(
            db_session,
            other_project.id,
            purpose="тест операция",
            expense=5000,
            account=f"40702810{uuid.uuid4().hex[:12]}",
        )

        result = await apply_auto_categorize(db_session, project.id)
        assert result["updated"] == 0
