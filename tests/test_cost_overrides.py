"""
Tests for funnel/cost_overrides service — manual cost price management.

Uses DB fixtures from conftest_api.py.
Tests: set_cost_override, get_cost_overrides.
"""

from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbCostOverride, WbFunnelDaily
from backend.services.funnel.cost_overrides import (
    get_cost_overrides,
    set_cost_override,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


async def _create_funnel_entry(
    db: AsyncSession,
    project_id: int,
    nm_id: int,
    cost_price: float | None = None,
    vendor_code: str | None = None,
    orders_sum_rub: float = 0,
):
    """Create a WbFunnelDaily record with all NOT NULL columns."""
    await db.execute(
        text(
            "INSERT INTO wb_funnel_daily "
            "(project_id, nm_id, date, cost_price, vendor_code, orders_sum_rub, orders_count, "
            "adv_sum, open_card, add_to_cart, stocks_wb, stocks_mp, adv_views, adv_clicks) "
            "VALUES (:pid, :nm, :d, :cp, :vc, :os, 0, 0, 0, 0, 0, 0, 0, 0)"
        ),
        {
            "pid": project_id,
            "nm": nm_id,
            "d": date(2025, 1, 15),
            "cp": cost_price,
            "vc": vendor_code,
            "os": orders_sum_rub,
        },
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: set_cost_override
# ═══════════════════════════════════════════════════════════════════════════════


class TestSetCostOverride:
    """Test setting manual cost price."""

    @pytest.mark.asyncio
    async def test_create_new_override(self, db_session: AsyncSession, project):
        """Create a new cost override."""
        result = await set_cost_override(db_session, project.id, nm_id=5001, cost_price=250.0)
        assert result["status"] == "ok"

        # Verify in DB
        row = await db_session.execute(
            select(WbCostOverride).where(
                WbCostOverride.project_id == project.id,
                WbCostOverride.nm_id == 5001,
            )
        )
        override = row.scalar_one()
        assert float(override.cost_price) == 250.0

    @pytest.mark.asyncio
    async def test_update_existing_override(self, db_session: AsyncSession, project):
        """Updating an existing override (upsert)."""
        await set_cost_override(db_session, project.id, nm_id=5002, cost_price=100.0)
        await set_cost_override(db_session, project.id, nm_id=5002, cost_price=200.0)

        row = await db_session.execute(
            select(WbCostOverride).where(
                WbCostOverride.project_id == project.id,
                WbCostOverride.nm_id == 5002,
            )
        )
        override = row.scalar_one()
        assert float(override.cost_price) == 200.0

    @pytest.mark.asyncio
    async def test_updates_funnel_daily(self, db_session: AsyncSession, project):
        """Setting override should also update WbFunnelDaily.cost_price."""
        await _create_funnel_entry(db_session, project.id, nm_id=5003, cost_price=None)

        await set_cost_override(db_session, project.id, nm_id=5003, cost_price=300.0)

        funnel = await db_session.execute(
            select(WbFunnelDaily).where(
                WbFunnelDaily.project_id == project.id,
                WbFunnelDaily.nm_id == 5003,
            )
        )
        row = funnel.scalar_one()
        assert float(row.cost_price) == 300.0

    @pytest.mark.asyncio
    async def test_zero_cost_allowed(self, db_session: AsyncSession, project):
        """Zero cost should be allowed (user might want to reset)."""
        result = await set_cost_override(db_session, project.id, nm_id=5004, cost_price=0)
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_decimal_precision(self, db_session: AsyncSession, project):
        """Cost price should maintain decimal precision."""
        await set_cost_override(db_session, project.id, nm_id=5005, cost_price=123.45)

        row = await db_session.execute(
            select(WbCostOverride).where(
                WbCostOverride.project_id == project.id,
                WbCostOverride.nm_id == 5005,
            )
        )
        override = row.scalar_one()
        assert float(override.cost_price) == 123.45


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: get_cost_overrides
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetCostOverrides:
    """Test getting cost overrides and missing costs."""

    @pytest.mark.asyncio
    async def test_empty_project(self, db_session: AsyncSession, project):
        """Empty project -> empty overrides and missing."""
        result = await get_cost_overrides(db_session, project.id)
        assert result["overrides"] == []
        assert result["missing"] == []

    @pytest.mark.asyncio
    async def test_returns_overrides(self, db_session: AsyncSession, project):
        """Should return existing overrides."""
        await set_cost_override(db_session, project.id, nm_id=6001, cost_price=150.0)
        await set_cost_override(db_session, project.id, nm_id=6002, cost_price=200.0)

        result = await get_cost_overrides(db_session, project.id)
        nm_ids = {o["nm_id"] for o in result["overrides"]}
        assert 6001 in nm_ids
        assert 6002 in nm_ids

    @pytest.mark.asyncio
    async def test_missing_items_detected(self, db_session: AsyncSession, project):
        """Items with no cost in funnel should appear in missing."""
        await _create_funnel_entry(db_session, project.id, nm_id=7001, cost_price=None, vendor_code="ART-NO-COST")

        result = await get_cost_overrides(db_session, project.id)
        missing_nms = {m["nm_id"] for m in result["missing"]}
        assert 7001 in missing_nms

    @pytest.mark.asyncio
    async def test_items_with_override_not_in_missing(self, db_session: AsyncSession, project):
        """Items that have an override should NOT appear in missing."""
        await _create_funnel_entry(db_session, project.id, nm_id=7002, cost_price=None)
        await set_cost_override(db_session, project.id, nm_id=7002, cost_price=100.0)

        result = await get_cost_overrides(db_session, project.id)
        missing_nms = {m["nm_id"] for m in result["missing"]}
        assert 7002 not in missing_nms

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session: AsyncSession, project, other_project):
        """Overrides from other project not visible."""
        await set_cost_override(db_session, project.id, nm_id=8001, cost_price=100.0)
        await set_cost_override(db_session, other_project.id, nm_id=8002, cost_price=999.0)

        result = await get_cost_overrides(db_session, project.id)
        nm_ids = {o["nm_id"] for o in result["overrides"]}
        assert 8001 in nm_ids
        assert 8002 not in nm_ids


class TestOverrideBeatsEngineInUnifiedStock:
    @pytest.mark.asyncio
    async def test_manual_override_wins_over_engine_price(self, db_session: AsyncSession, project):
        """Ручная цена (WbCostOverride) ПОБЕЖДАЕТ цену движка по закупкам
        (канон юзера 2026-07-28): раньше цена движка молча затеняла живые
        override'ы у SKU, где заведены партии."""
        from datetime import date
        from decimal import Decimal

        from backend.models import CostOrder, CostOrderItem, Nomenclature, VehicleStatus
        from backend.models.integrations import WbWarehouseStock
        from backend.services.warehouse_stock_engine import get_unified_stock_summary

        pid = project.id
        art = f"OVR-WIN-{pid}"
        bc = f"BC-OVR-WIN-{pid}"
        nm = 900000 + pid
        db_session.add(Nomenclature(project_id=pid, barcode=bc, article_seller=art, article_wb=nm))
        # партия 10 шт по 200 ₽ — цена движка была бы 200
        db_session.add(
            CostOrder(
                order_no=f"OVR-{pid}", project_id=pid, country="CHINA",
                status=VehicleStatus.DELIVERED, actual_arrival_date=date(2025, 1, 1),
                rate_cny=Decimal("1"), rate_eur=Decimal("1"), rate_usd=Decimal("1"),
                delivery_cost_cny=Decimal("0"), delivery_cost_usd=Decimal("0"),
            )
        )
        db_session.add(
            CostOrderItem(
                project_id=pid, order_no=f"OVR-{pid}", barcode=bc, subject="Ковры",
                article_seller=art, qty=10, price_cny=Decimal("0"),
                volume_m3=Decimal("0"), duty_rub=Decimal("0"), total_rub=Decimal("200.00"),
            )
        )
        # ручная цена 155 ₽ + остаток на WB, чтобы строка попала в сводку
        db_session.add(WbWarehouseStock(project_id=pid, nm_id=nm, warehouse_name="Коледино", quantity=5, quantity_full=5))
        await db_session.commit()
        await set_cost_override(db_session, pid, nm, Decimal("155.00"))

        rows = await get_unified_stock_summary(db_session, pid, group_by="sku")
        row = next(r for r in rows if r["barcode"] == bc)
        assert row["avg_cost"] == 155.0  # не 200 (движок)
