"""
Tests for cost/duty — Duty Rules CRUD.
Uses DB fixtures from conftest.py.
"""

import pytest

from backend.services.cost.duty import (
    delete_duty_rule,
    get_duty_rules,
    upsert_duty_rule,
)

# ═══════════════════════════════════════════════════════════════════════════════
# get_duty_rules
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetDutyRules:
    @pytest.mark.asyncio
    async def test_empty_for_new_project(self, db_session, project):
        """New project has no duty rules."""
        rules = await get_duty_rules(db_session, project.id)
        assert rules == []

    @pytest.mark.asyncio
    async def test_respects_limit(self, db_session, project):
        """Limit parameter works."""
        rules = await get_duty_rules(db_session, project.id, limit=5)
        assert len(rules) <= 5


# ═══════════════════════════════════════════════════════════════════════════════
# upsert_duty_rule
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpsertDutyRule:
    @pytest.mark.asyncio
    async def test_create_new(self, db_session, project):
        """Create a new duty rule."""
        ok, err = await upsert_duty_rule(
            db_session,
            project.id,
            {
                "subject": "Футболки",
                "basis": "INVOICE",
                "rate": 0.10,
                "util_collect_rub": 150,
                "note": "Test duty rule",
            },
        )
        assert ok is True
        assert err is None

        rules = await get_duty_rules(db_session, project.id)
        assert any(r.subject == "Футболки" for r in rules)

    @pytest.mark.asyncio
    async def test_update_existing(self, db_session, project):
        """Upserting same subject updates the existing rule."""
        await upsert_duty_rule(
            db_session,
            project.id,
            {
                "subject": "Штаны",
                "rate": 0.05,
                "util_collect_rub": 100,
            },
        )
        ok, err = await upsert_duty_rule(
            db_session,
            project.id,
            {
                "subject": "Штаны",
                "rate": 0.08,
                "util_collect_rub": 200,
            },
        )
        assert ok is True

        rules = await get_duty_rules(db_session, project.id)
        rule = next(r for r in rules if r.subject == "Штаны")
        from decimal import Decimal

        assert rule.rate == Decimal("0.08")

    @pytest.mark.asyncio
    async def test_empty_subject_rejected(self, db_session, project):
        """Empty subject returns error."""
        result, err = await upsert_duty_rule(
            db_session,
            project.id,
            {
                "subject": "",
                "rate": 0.1,
            },
        )
        assert result is None
        assert err == "subject required"

    @pytest.mark.asyncio
    async def test_missing_subject_rejected(self, db_session, project):
        """Missing subject key returns error."""
        result, err = await upsert_duty_rule(
            db_session,
            project.id,
            {
                "rate": 0.1,
            },
        )
        assert result is None
        assert err == "subject required"


# ═══════════════════════════════════════════════════════════════════════════════
# delete_duty_rule
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeleteDutyRule:
    @pytest.mark.asyncio
    async def test_soft_delete(self, db_session, project):
        """Soft delete a duty rule."""
        await upsert_duty_rule(
            db_session,
            project.id,
            {
                "subject": "ToDelete",
                "rate": 0.01,
                "util_collect_rub": 0,
            },
        )
        rules = await get_duty_rules(db_session, project.id)
        rule_id = next(r.id for r in rules if r.subject == "ToDelete")

        result = await delete_duty_rule(db_session, project.id, rule_id)
        assert result is True

        rules_after = await get_duty_rules(db_session, project.id)
        assert not any(r.subject == "ToDelete" for r in rules_after)

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, db_session, project):
        """Deleting non-existent rule returns None."""
        result = await delete_duty_rule(db_session, project.id, 999999)
        assert result is None

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Cannot delete rule from another project."""
        await upsert_duty_rule(
            db_session,
            project.id,
            {
                "subject": "Isolated",
                "rate": 0.05,
                "util_collect_rub": 50,
            },
        )
        rules = await get_duty_rules(db_session, project.id)
        rule_id = next(r.id for r in rules if r.subject == "Isolated")

        # Try to delete from other project
        result = await delete_duty_rule(db_session, other_project.id, rule_id)
        assert result is None

        # Original project still has it
        rules_after = await get_duty_rules(db_session, project.id)
        assert any(r.subject == "Isolated" for r in rules_after)
