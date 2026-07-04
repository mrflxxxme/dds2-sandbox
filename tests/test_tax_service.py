"""
Tests for tax_service — CRUD for project-level monthly tax rates.

Uses DB fixtures from conftest_api.py.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.tax import TaxRate
from backend.services.tax_service import _PROJECT_BRAND, get_tax_rates, save_tax_rates

# ═══════════════════════════════════════════════════════════════════════════════
# Tests: get_tax_rates
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetTaxRates:
    """Test reading tax rates."""

    @pytest.mark.asyncio
    async def test_empty_year_returns_12_months(self, db_session: AsyncSession, project):
        """No saved rates -> returns 12 months with zero rates."""
        result = await get_tax_rates(db_session, project.id, 2025)

        assert result["year"] == 2025
        assert result["tax_regime"] == "usn_income"
        assert len(result["months"]) == 12
        for m in result["months"]:
            assert m["usn_rate"] == 0
            assert m["nds_rate"] == 0
            assert m["cost_as_expense"] is False

    @pytest.mark.asyncio
    async def test_months_sorted(self, db_session: AsyncSession, project):
        """Returned months should be sorted 1-12."""
        result = await get_tax_rates(db_session, project.id, 2025)

        months = [m["month"] for m in result["months"]]
        assert months == list(range(1, 13))

    @pytest.mark.asyncio
    async def test_saved_rates_returned(self, db_session: AsyncSession, project):
        """Saved rates should be returned correctly."""
        # Insert a rate for January
        rate = TaxRate(
            project_id=project.id,
            brand=_PROJECT_BRAND,
            tax_regime="usn_income_expense_vat",
            year=2025,
            month=1,
            usn_rate=15,
            nds_rate=5,
            cost_as_expense=True,
        )
        db_session.add(rate)
        await db_session.commit()

        result = await get_tax_rates(db_session, project.id, 2025)

        assert result["tax_regime"] == "usn_income_expense_vat"
        jan = next(m for m in result["months"] if m["month"] == 1)
        assert jan["usn_rate"] == 15.0
        assert jan["nds_rate"] == 5.0
        assert jan["cost_as_expense"] is True

    @pytest.mark.asyncio
    async def test_project_id_isolation(self, db_session: AsyncSession, project, other_project):
        """Rates from other project should not be visible."""
        rate = TaxRate(
            project_id=other_project.id,
            brand=_PROJECT_BRAND,
            tax_regime="usn_income",
            year=2025,
            month=3,
            usn_rate=6,
            nds_rate=0,
        )
        db_session.add(rate)
        await db_session.commit()

        result = await get_tax_rates(db_session, project.id, 2025)
        mar = next(m for m in result["months"] if m["month"] == 3)
        assert mar["usn_rate"] == 0  # Not visible from other project


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: save_tax_rates
# ═══════════════════════════════════════════════════════════════════════════════


class TestSaveTaxRates:
    """Test saving/updating tax rates."""

    @pytest.mark.asyncio
    async def test_save_creates_rows(self, db_session: AsyncSession, project):
        """Save should create TaxRate rows."""
        months = [{"month": i, "usn_rate": 6, "nds_rate": 0, "cost_as_expense": False} for i in range(1, 13)]
        result = await save_tax_rates(db_session, project.id, 2025, "usn_income", months)

        assert result["status"] == "ok"
        assert result["year"] == 2025

        # Verify in DB
        rows = await db_session.execute(
            select(TaxRate).where(
                TaxRate.project_id == project.id,
                TaxRate.year == 2025,
            )
        )
        assert len(rows.scalars().all()) == 12

    @pytest.mark.asyncio
    async def test_save_replaces_existing(self, db_session: AsyncSession, project):
        """Second save should replace all rows."""
        months_v1 = [{"month": i, "usn_rate": 6, "nds_rate": 0, "cost_as_expense": False} for i in range(1, 13)]
        await save_tax_rates(db_session, project.id, 2025, "usn_income", months_v1)

        months_v2 = [{"month": i, "usn_rate": 15, "nds_rate": 5, "cost_as_expense": True} for i in range(1, 13)]
        await save_tax_rates(db_session, project.id, 2025, "usn_income_expense_vat", months_v2)

        result = await get_tax_rates(db_session, project.id, 2025)
        assert result["tax_regime"] == "usn_income_expense_vat"
        for m in result["months"]:
            assert m["usn_rate"] == 15.0
            assert m["nds_rate"] == 5.0

    @pytest.mark.asyncio
    async def test_save_does_not_affect_other_project(self, db_session: AsyncSession, project, other_project):
        """Saving for one project does not affect another."""
        # Save for other_project
        months_other = [{"month": i, "usn_rate": 20, "nds_rate": 10, "cost_as_expense": True} for i in range(1, 13)]
        await save_tax_rates(db_session, other_project.id, 2025, "usn_income_expense_vat", months_other)

        # Save for project
        months_main = [{"month": i, "usn_rate": 6, "nds_rate": 0, "cost_as_expense": False} for i in range(1, 13)]
        await save_tax_rates(db_session, project.id, 2025, "usn_income", months_main)

        # Verify isolation
        result_main = await get_tax_rates(db_session, project.id, 2025)
        result_other = await get_tax_rates(db_session, other_project.id, 2025)

        assert result_main["months"][0]["usn_rate"] == 6.0
        assert result_other["months"][0]["usn_rate"] == 20.0

    @pytest.mark.asyncio
    async def test_save_different_years_independent(self, db_session: AsyncSession, project):
        """Different years are independent."""
        months_2024 = [{"month": i, "usn_rate": 6, "nds_rate": 0, "cost_as_expense": False} for i in range(1, 13)]
        months_2025 = [{"month": i, "usn_rate": 15, "nds_rate": 5, "cost_as_expense": True} for i in range(1, 13)]

        await save_tax_rates(db_session, project.id, 2024, "usn_income", months_2024)
        await save_tax_rates(db_session, project.id, 2025, "usn_income_expense_vat", months_2025)

        r2024 = await get_tax_rates(db_session, project.id, 2024)
        r2025 = await get_tax_rates(db_session, project.id, 2025)

        assert r2024["months"][0]["usn_rate"] == 6.0
        assert r2025["months"][0]["usn_rate"] == 15.0

    @pytest.mark.asyncio
    async def test_save_invalidates_report_caches(self, db_session: AsyncSession, project):
        """Saving tax rates must invalidate BDR/ОПИУ/dashboard/funnel caches.

        Regression: without invalidation, @cached(ttl=3600) on get_wb_bdr/get_opiu
        kept old values for up to 1 hour after the user changed rates in settings.
        """
        months = [{"month": i, "usn_rate": 15, "nds_rate": 7, "cost_as_expense": True} for i in range(1, 13)]

        with patch(
            "backend.services.tax_service.invalidate_cache",
            new_callable=AsyncMock,
        ) as mock_inv:
            await save_tax_rates(db_session, project.id, 2026, "usn_income_expense_vat", months)

        # 5 prefixes: wb_bdr, opiu, dashboard + the two real funnel caches
        # (funnel:tariff_map, funnel:avg_buyout — the old bare "funnel:project_id" matched nothing).
        assert mock_inv.await_count == 5
        call_args = [c.args[0] for c in mock_inv.call_args_list]
        assert any("reports:wb_bdr" in a for a in call_args)
        assert any("reports:opiu" in a for a in call_args)
        assert any("reports:dashboard" in a for a in call_args)
        assert any("funnel:tariff_map" in a for a in call_args)
        assert any("funnel:avg_buyout" in a for a in call_args)
        # Every key must be scoped to the mutating project
        assert all(f"project_id={project.id}" in a for a in call_args)
