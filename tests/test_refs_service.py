"""
Tests for refs_service — CRUD operations for reference data.
Uses DB fixtures from conftest.py (project, other_project, db_session).
"""

import uuid
from datetime import date

import pytest

from backend.services.refs_service import (
    add_category,
    delete_account,
    delete_category,
    delete_cp_category,
    delete_override,
    list_accounts,
    list_categories,
    list_cp_categories,
    list_opening_balances,
    list_overrides,
    upsert_account,
    upsert_cp_category,
    upsert_opening_balance,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Accounts
# ═══════════════════════════════════════════════════════════════════════════════


class TestAccountsCrud:
    @pytest.mark.asyncio
    async def test_create_and_list(self, db_session, project):
        """Create an account and verify it appears in the list."""
        account_no = f"40702810{uuid.uuid4().hex[:12]}"
        acc = await upsert_account(
            db_session,
            project.id,
            {
                "account": account_no,
                "bank": "VTB",
                "currency": "RUB",
                "account_name": "Test Account",
                "is_our_account": True,
            },
        )
        assert acc.id is not None
        assert acc.account == account_no

        accounts = await list_accounts(db_session, project.id)
        assert any(a.id == acc.id for a in accounts)

    @pytest.mark.asyncio
    async def test_update_existing(self, db_session, project):
        """Update an account by passing id."""
        account_no = f"40702810{uuid.uuid4().hex[:12]}"
        acc = await upsert_account(
            db_session,
            project.id,
            {
                "account": account_no,
                "bank": "SBER",
                "currency": "RUB",
                "account_name": "Before",
                "is_our_account": True,
            },
        )
        updated = await upsert_account(
            db_session,
            project.id,
            {
                "id": acc.id,
                "account_name": "After",
            },
        )
        assert updated.account_name == "After"
        assert updated.id == acc.id

    @pytest.mark.asyncio
    async def test_soft_delete(self, db_session, project):
        """Soft-deleted account does not appear in list."""
        account_no = f"40702810{uuid.uuid4().hex[:12]}"
        acc = await upsert_account(
            db_session,
            project.id,
            {
                "account": account_no,
                "bank": "VTB",
                "currency": "RUB",
                "account_name": "To Delete",
                "is_our_account": True,
            },
        )
        result = await delete_account(db_session, project.id, acc.id)
        assert result is True

        accounts = await list_accounts(db_session, project.id)
        assert not any(a.id == acc.id for a in accounts)

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, db_session, project):
        """Deleting non-existent account returns False."""
        result = await delete_account(db_session, project.id, 999999)
        assert result is False

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Account from project A not visible in project B."""
        account_no = f"40702810{uuid.uuid4().hex[:12]}"
        acc = await upsert_account(
            db_session,
            project.id,
            {
                "account": account_no,
                "bank": "VTB",
                "currency": "RUB",
                "account_name": "Isolated",
                "is_our_account": True,
            },
        )
        other_accounts = await list_accounts(db_session, other_project.id)
        assert not any(a.id == acc.id for a in other_accounts)


# ═══════════════════════════════════════════════════════════════════════════════
# Counterparty Categories
# ═══════════════════════════════════════════════════════════════════════════════


class TestCpCategoriesCrud:
    @pytest.mark.asyncio
    async def test_create_and_list(self, db_session, project):
        cp_key = f"INN:{uuid.uuid4().hex[:10]}"
        cpc = await upsert_cp_category(
            db_session,
            project.id,
            {
                "cp_key": cp_key,
                "cat_lvl1": "Поставщики",
            },
        )
        assert cpc.id is not None
        categories = await list_cp_categories(db_session, project.id)
        assert any(c.id == cpc.id for c in categories)

    @pytest.mark.asyncio
    async def test_soft_delete(self, db_session, project):
        cp_key = f"INN:{uuid.uuid4().hex[:10]}"
        cpc = await upsert_cp_category(
            db_session,
            project.id,
            {
                "cp_key": cp_key,
                "cat_lvl1": "Temp",
            },
        )
        result = await delete_cp_category(db_session, project.id, cpc.id)
        assert result is True
        categories = await list_cp_categories(db_session, project.id)
        assert not any(c.id == cpc.id for c in categories)

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        cp_key = f"INN:{uuid.uuid4().hex[:10]}"
        cpc = await upsert_cp_category(
            db_session,
            project.id,
            {
                "cp_key": cp_key,
                "cat_lvl1": "Isolated",
            },
        )
        other_cats = await list_cp_categories(db_session, other_project.id)
        assert not any(c.id == cpc.id for c in other_cats)


# ═══════════════════════════════════════════════════════════════════════════════
# Overrides
# ═══════════════════════════════════════════════════════════════════════════════


class TestOverridesCrud:
    @pytest.mark.asyncio
    async def test_list_empty(self, db_session, project):
        """New project has no overrides."""
        overrides = await list_overrides(db_session, project.id)
        assert isinstance(overrides, list)

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, db_session, project):
        result = await delete_override(db_session, project.id, 999999)
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# Opening Balances
# ═══════════════════════════════════════════════════════════════════════════════


class TestOpeningBalancesCrud:
    @pytest.mark.asyncio
    async def test_create_and_list(self, db_session, project):
        account_no = f"407028OB{uuid.uuid4().hex[:8]}"
        ob = await upsert_opening_balance(
            db_session,
            project.id,
            {
                "date_open": date(2024, 1, 1),
                "account": account_no,
                "currency": "RUB",
                "opening_balance": 100000.50,
            },
        )
        assert ob.id is not None
        balances = await list_opening_balances(db_session, project.id)
        assert any(b.id == ob.id for b in balances)

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        account_no = f"407028OB{uuid.uuid4().hex[:8]}"
        ob = await upsert_opening_balance(
            db_session,
            project.id,
            {
                "date_open": date(2024, 1, 1),
                "account": account_no,
                "currency": "RUB",
                "opening_balance": 50000,
            },
        )
        other_balances = await list_opening_balances(db_session, other_project.id)
        assert not any(b.id == ob.id for b in other_balances)


# ═══════════════════════════════════════════════════════════════════════════════
# Categories
# ═══════════════════════════════════════════════════════════════════════════════


class TestCategoriesCrud:
    @pytest.mark.asyncio
    async def test_add_and_list(self, db_session, project):
        cat = await add_category(db_session, project.id, "Зарплата", "Основная", "expense")
        assert cat.id is not None
        assert cat.cat_lvl1 == "Зарплата"
        categories = await list_categories(db_session, project.id)
        assert any(c["id"] == cat.id for c in categories)

    @pytest.mark.asyncio
    async def test_default_direction(self, db_session, project):
        """Default direction is 'expense'."""
        cat = await add_category(db_session, project.id, "Misc")
        assert cat.direction == "expense"

    @pytest.mark.asyncio
    async def test_soft_delete(self, db_session, project):
        cat = await add_category(db_session, project.id, "ToDelete")
        result = await delete_category(db_session, cat.id, project.id)
        assert result is True
        categories = await list_categories(db_session, project.id)
        assert not any(c["id"] == cat.id for c in categories)

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, db_session, project):
        result = await delete_category(db_session, 999999, project.id)
        assert result is False

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        cat = await add_category(db_session, project.id, "Isolated Cat")
        other_cats = await list_categories(db_session, other_project.id)
        assert not any(c["id"] == cat.id for c in other_cats)
