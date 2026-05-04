"""
Tests for bdr_loaders — DB loaders for ads, orders/stocks, costs, tax settings.

Uses DB fixtures from conftest_api.py.
"""

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.bdr_loaders import (
    load_ads,
    load_avg_costs,
    load_cost_overrides,
    load_orders_stocks,
    load_tax_settings,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


async def _create_funnel_daily(
    db: AsyncSession,
    project_id: int,
    nm_id: int,
    funnel_date: date,
    adv_sum: float = 0,
    orders_count: int = 0,
    orders_sum_rub: float = 0,
    stocks_wb: int = 0,
    cost_price: float | None = None,
    vendor_code: str | None = None,
):
    """Insert a WbFunnelDaily record with all NOT NULL columns."""
    await db.execute(
        text(
            "INSERT INTO wb_funnel_daily "
            "(project_id, nm_id, date, adv_sum, orders_count, orders_sum_rub, "
            "stocks_wb, cost_price, vendor_code, open_card, add_to_cart, "
            "stocks_mp, adv_views, adv_clicks) "
            "VALUES (:pid, :nm, :d, :adv, :oc, :os, "
            ":st, :cp, :vc, 0, 0, 0, 0, 0)"
        ),
        {
            "pid": project_id,
            "nm": nm_id,
            "d": funnel_date,
            "adv": adv_sum,
            "oc": orders_count,
            "os": orders_sum_rub,
            "st": stocks_wb,
            "cp": cost_price,
            "vc": vendor_code,
        },
    )
    await db.commit()


async def _create_cost_order_with_item(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    article_seller: str,
    total_rub: float,
    qty: int,
):
    """Insert a CostOrder + CostOrderItem pair."""
    await db.execute(
        text(
            "INSERT INTO cost_orders (project_id, order_no, is_deleted, "
            "delivery_cost_cny, delivery_cost_usd, rate_cny, rate_eur, rate_usd, created_at) "
            "VALUES (:pid, :ono, false, 0, 0, 0, 0, 0, NOW()) ON CONFLICT DO NOTHING"
        ),
        {"pid": project_id, "ono": order_no},
    )
    # Get project_id from cost_order
    r = await db.execute(
        text("SELECT project_id FROM cost_orders WHERE order_no = :ono LIMIT 1"),
        {"ono": order_no},
    )
    pid = r.scalar() or 0
    await db.execute(
        text(
            "INSERT INTO cost_order_items (order_no, article_seller, total_rub, qty, barcode, price_cny, unrecognized, project_id) "
            "VALUES (:ono, :art, :cost, :qty, :bar, 0, false, :pid)"
        ),
        {"ono": order_no, "art": article_seller, "cost": total_rub, "qty": qty, "bar": article_seller, "pid": pid},
    )
    await db.commit()


async def _create_cost_override(db: AsyncSession, project_id: int, nm_id: int, cost_price: float):
    """Insert a WbCostOverride."""
    await db.execute(
        text(
            "INSERT INTO wb_cost_override (project_id, nm_id, cost_price, updated_at) " "VALUES (:pid, :nm, :cp, NOW())"
        ),
        {"pid": project_id, "nm": nm_id, "cp": cost_price},
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: load_ads
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoadAds:
    """Test ad spend loader."""

    @pytest.mark.asyncio
    async def test_empty(self, db_session: AsyncSession, project):
        """No funnel data -> empty dict."""
        result = await load_ads(db_session, project.id, date(2025, 1, 1), date(2025, 1, 31))
        assert result == {}

    @pytest.mark.asyncio
    async def test_sums_per_nm_id(self, db_session: AsyncSession, project):
        """Ad spend should be summed per nm_id."""
        await _create_funnel_daily(db_session, project.id, nm_id=1001, funnel_date=date(2025, 1, 10), adv_sum=500)
        await _create_funnel_daily(db_session, project.id, nm_id=1001, funnel_date=date(2025, 1, 11), adv_sum=700)
        await _create_funnel_daily(db_session, project.id, nm_id=1002, funnel_date=date(2025, 1, 10), adv_sum=300)

        result = await load_ads(db_session, project.id, date(2025, 1, 1), date(2025, 1, 31))
        assert result[1001] == 1200.0
        assert result[1002] == 300.0

    @pytest.mark.asyncio
    async def test_date_filter(self, db_session: AsyncSession, project):
        """Only ads within date range should be included."""
        await _create_funnel_daily(db_session, project.id, nm_id=1001, funnel_date=date(2025, 1, 15), adv_sum=500)
        await _create_funnel_daily(db_session, project.id, nm_id=1001, funnel_date=date(2025, 2, 15), adv_sum=999)

        result = await load_ads(db_session, project.id, date(2025, 1, 1), date(2025, 1, 31))
        assert result.get(1001, 0) == 500.0

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session: AsyncSession, project, other_project):
        """Ads from other project not visible."""
        await _create_funnel_daily(db_session, project.id, nm_id=1001, funnel_date=date(2025, 1, 10), adv_sum=500)
        await _create_funnel_daily(db_session, other_project.id, nm_id=1001, funnel_date=date(2025, 1, 10), adv_sum=999)

        result = await load_ads(db_session, project.id, date(2025, 1, 1), date(2025, 1, 31))
        assert result[1001] == 500.0


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: load_orders_stocks
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoadOrdersStocks:
    """Test orders and stocks loader."""

    @pytest.mark.asyncio
    async def test_empty(self, db_session: AsyncSession, project):
        """No data -> empty dict."""
        result = await load_orders_stocks(db_session, project.id, date(2025, 1, 1), date(2025, 1, 31))
        assert result == {}

    @pytest.mark.asyncio
    async def test_orders_aggregated(self, db_session: AsyncSession, project):
        """Orders should be aggregated per nm_id."""
        await _create_funnel_daily(
            db_session,
            project.id,
            nm_id=2001,
            funnel_date=date(2025, 1, 10),
            orders_count=5,
            orders_sum_rub=25000,
        )
        await _create_funnel_daily(
            db_session,
            project.id,
            nm_id=2001,
            funnel_date=date(2025, 1, 11),
            orders_count=3,
            orders_sum_rub=15000,
        )

        result = await load_orders_stocks(db_session, project.id, date(2025, 1, 1), date(2025, 1, 31))
        assert result[2001]["orders_count"] == 8
        assert result[2001]["orders_sum"] == 40000.0

    @pytest.mark.asyncio
    async def test_latest_stocks(self, db_session: AsyncSession, project):
        """Stocks should come from the latest day in period."""
        await _create_funnel_daily(
            db_session,
            project.id,
            nm_id=2001,
            funnel_date=date(2025, 1, 10),
            stocks_wb=100,
        )
        await _create_funnel_daily(
            db_session,
            project.id,
            nm_id=2001,
            funnel_date=date(2025, 1, 20),
            stocks_wb=80,  # later date, should be used
        )

        result = await load_orders_stocks(db_session, project.id, date(2025, 1, 1), date(2025, 1, 31))
        assert result[2001]["stocks_wb"] == 80


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: load_avg_costs
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoadAvgCosts:
    """Test average cost loader."""

    @pytest.mark.asyncio
    async def test_empty(self, db_session: AsyncSession, project):
        """No cost data -> empty dict."""
        result = await load_avg_costs(db_session, project.id)
        assert result == {}

    @pytest.mark.asyncio
    async def test_weighted_average(self, db_session: AsyncSession, project):
        """Weighted average cost calculation."""
        import uuid as _uuid

        ono1 = f"ORD-BDR-{_uuid.uuid4().hex[:6]}"
        ono2 = f"ORD-BDR-{_uuid.uuid4().hex[:6]}"
        await _create_cost_order_with_item(db_session, project.id, ono1, "ART-001", total_rub=100, qty=10)
        await _create_cost_order_with_item(db_session, project.id, ono2, "ART-001", total_rub=200, qty=5)

        result = await load_avg_costs(db_session, project.id)
        # Weighted avg = (100*10 + 200*5) / (10+5) = 2000/15 = 133.33
        # Keys are lowercased so callers can do case-insensitive lookup against
        # WB-side strings (vendor_code/sa_name) that may differ in case.
        assert "art-001" in result
        assert round(result["art-001"], 2) == round((100 * 10 + 200 * 5) / 15, 2)

    @pytest.mark.asyncio
    async def test_case_insensitive_aggregation(self, db_session: AsyncSession, project):
        """Same article in different casing aggregates into one weighted-avg entry."""
        import uuid as _uuid

        ono1 = f"ORD-BDR-{_uuid.uuid4().hex[:6]}"
        ono2 = f"ORD-BDR-{_uuid.uuid4().hex[:6]}"
        # User entered same article with different casing across two orders
        await _create_cost_order_with_item(db_session, project.id, ono1, "PALATKA_зеленая", total_rub=100, qty=10)
        await _create_cost_order_with_item(db_session, project.id, ono2, "palatka_зеленая", total_rub=200, qty=5)

        result = await load_avg_costs(db_session, project.id)
        # Both orders aggregate into single lowercase key
        assert "palatka_зеленая" in result
        assert "PALATKA_зеленая" not in result
        assert round(result["palatka_зеленая"], 2) == round((100 * 10 + 200 * 5) / 15, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: load_cost_overrides
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoadCostOverrides:
    """Test cost overrides loader."""

    @pytest.mark.asyncio
    async def test_empty(self, db_session: AsyncSession, project):
        """No overrides -> empty dict."""
        result = await load_cost_overrides(db_session, project.id)
        assert result == {}

    @pytest.mark.asyncio
    async def test_loads_overrides(self, db_session: AsyncSession, project):
        """Should return nm_id -> cost_price mapping."""
        await _create_cost_override(db_session, project.id, nm_id=3001, cost_price=150.50)
        await _create_cost_override(db_session, project.id, nm_id=3002, cost_price=200.0)

        result = await load_cost_overrides(db_session, project.id)
        assert result[3001] == 150.50
        assert result[3002] == 200.0

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session: AsyncSession, project, other_project):
        """Overrides from other project not visible."""
        await _create_cost_override(db_session, project.id, nm_id=3001, cost_price=100)
        await _create_cost_override(db_session, other_project.id, nm_id=3001, cost_price=999)

        result = await load_cost_overrides(db_session, project.id)
        assert result[3001] == 100.0


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: load_tax_settings
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoadTaxSettings:
    """Test tax settings loader."""

    @pytest.mark.asyncio
    async def test_no_settings_returns_defaults(self, db_session: AsyncSession, project):
        """No tax rate set -> returns defaults."""
        result = await load_tax_settings(db_session, project.id, date(2025, 1, 1), date(2025, 1, 31))
        assert result["tax_regime"] == "usn_income"
        assert result["usn_rate"] == 0
        assert result["nds_rate"] == 0
        assert result["cost_as_expense"] is False

    @pytest.mark.asyncio
    async def test_saved_settings_returned(self, db_session: AsyncSession, project):
        """Saved tax settings should be returned."""
        from backend.models.tax import TaxRate

        rate = TaxRate(
            project_id=project.id,
            brand="__project__",
            tax_regime="usn_income_expense_vat",
            year=2025,
            month=3,
            usn_rate=15,
            nds_rate=5,
            cost_as_expense=True,
        )
        db_session.add(rate)
        await db_session.commit()

        result = await load_tax_settings(db_session, project.id, date(2025, 3, 1), date(2025, 3, 31))
        assert result["tax_regime"] == "usn_income_expense_vat"
        assert result["usn_rate"] == 15.0
        assert result["nds_rate"] == 5.0
        assert result["cost_as_expense"] is True

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session: AsyncSession, project, other_project):
        """Tax settings from other project not visible."""
        from backend.models.tax import TaxRate

        rate = TaxRate(
            project_id=other_project.id,
            brand="__project__",
            tax_regime="usn_income",
            year=2025,
            month=1,
            usn_rate=6,
            nds_rate=0,
        )
        db_session.add(rate)
        await db_session.commit()

        result = await load_tax_settings(db_session, project.id, date(2025, 1, 1), date(2025, 1, 31))
        assert result["usn_rate"] == 0  # not visible
