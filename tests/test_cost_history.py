"""
Tests for cost_history_service — cost price history per article across orders.

Uses DB fixtures from conftest_api.py.
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.cost_history_service import get_cost_history

# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _ono() -> str:
    """Generate a unique order_no."""
    return f"ORD-{uuid.uuid4().hex[:8]}"


async def _create_cost_order(db: AsyncSession, project_id: int, order_no: str, ship_date: date | None = None):
    """Create a CostOrder."""
    await db.execute(
        text(
            "INSERT INTO cost_orders (project_id, order_no, ship_date, is_deleted, "
            "delivery_cost_cny, delivery_cost_usd, rate_cny, rate_eur, rate_usd, created_at) "
            "VALUES (:pid, :ono, :sd, false, 0, 0, 0, 0, 0, NOW())"
        ),
        {"pid": project_id, "ono": order_no, "sd": ship_date},
    )
    await db.commit()


async def _create_cost_item(
    db: AsyncSession,
    order_no: str,
    article_seller: str,
    total_rub: float,
    qty: int,
    project_id: int = 0,
    barcode: str | None = None,
    subject: str | None = None,
    price_cny: float | None = None,
):
    """Create a CostOrderItem."""
    # Get project_id from cost_order if not provided
    if project_id == 0:
        r = await db.execute(
            text("SELECT project_id FROM cost_orders WHERE order_no = :ono LIMIT 1"),
            {"ono": order_no},
        )
        project_id = r.scalar() or 0

    await db.execute(
        text(
            "INSERT INTO cost_order_items "
            "(order_no, article_seller, total_rub, qty, barcode, subject, price_cny, unrecognized, project_id) "
            "VALUES (:ono, :art, :cost, :qty, :bar, :subj, :pcny, false, :pid)"
        ),
        {
            "ono": order_no,
            "art": article_seller,
            "cost": total_rub,
            "qty": qty,
            "bar": barcode or article_seller,
            "subj": subject,
            "pcny": price_cny or 0,
            "pid": project_id,
        },
    )
    await db.commit()


async def _create_nomenclature(db: AsyncSession, project_id: int, article_seller: str, article_wb: int, brand: str):
    """Create a Nomenclature record."""
    await db.execute(
        text(
            "INSERT INTO nomenclature (project_id, article_seller, article_wb, brand, barcode, updated_at) "
            "VALUES (:pid, :as, :aw, :b, :bar, NOW())"
        ),
        {"pid": project_id, "as": article_seller, "aw": article_wb, "b": brand, "bar": article_seller},
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: get_cost_history
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetCostHistory:
    """Test cost history pivot."""

    @pytest.mark.asyncio
    async def test_empty_project(self, db_session: AsyncSession, project):
        """No cost orders -> empty result."""
        result = await get_cost_history(db_session, project.id)
        assert result["orders"] == []
        assert result["articles"] == []
        assert result["brands"] == []

    @pytest.mark.asyncio
    async def test_single_order_single_article(self, db_session: AsyncSession, project):
        """One order with one article."""
        ono = _ono()
        await _create_cost_order(db_session, project.id, ono, date(2025, 1, 15))
        await _create_cost_item(db_session, ono, "ART-001", total_rub=150.0, qty=100)

        result = await get_cost_history(db_session, project.id)
        assert len(result["orders"]) == 1
        assert result["orders"][0]["order_no"] == ono
        assert len(result["articles"]) == 1
        assert result["articles"][0]["article_seller"] == "ART-001"
        assert result["articles"][0]["costs"][ono]["cost"] == 150.0
        assert result["articles"][0]["costs"][ono]["qty"] == 100
        assert result["articles"][0]["avg_cost"] == 150.0
        assert result["articles"][0]["latest_cost"] == 150.0

    @pytest.mark.asyncio
    async def test_multiple_orders_avg_cost(self, db_session: AsyncSession, project):
        """Multiple orders -> weighted avg cost (by qty)."""
        ono1 = _ono()
        ono2 = _ono()
        await _create_cost_order(db_session, project.id, ono1, date(2025, 1, 1))
        await _create_cost_order(db_session, project.id, ono2, date(2025, 2, 1))
        await _create_cost_item(db_session, ono1, "ART-002", total_rub=100.0, qty=50)
        await _create_cost_item(db_session, ono2, "ART-002", total_rub=200.0, qty=30)

        result = await get_cost_history(db_session, project.id)
        art = result["articles"][0]
        # Weighted: (100*50 + 200*30) / (50+30) = 11000/80 = 137.5
        assert art["avg_cost"] == 137.5
        # Latest cost = from most recent order (ono2)
        assert art["latest_cost"] == 200.0

    @pytest.mark.asyncio
    async def test_weighted_avg_skewed_batches(self, db_session: AsyncSession, project):
        """Large cheap batch + small expensive batch: weighted avg stays close to cheap."""
        ono1 = _ono()
        ono2 = _ono()
        await _create_cost_order(db_session, project.id, ono1, date(2025, 1, 1))
        await _create_cost_order(db_session, project.id, ono2, date(2025, 2, 1))
        # 2475 units @ 623.95 (bulk) + 45 units @ 945.97 (sample)
        await _create_cost_item(db_session, ono1, "ART-SKEW", total_rub=623.95, qty=2475)
        await _create_cost_item(db_session, ono2, "ART-SKEW", total_rub=945.97, qty=45)

        result = await get_cost_history(db_session, project.id)
        art = next(a for a in result["articles"] if a["article_seller"] == "ART-SKEW")
        # Weighted: (623.95*2475 + 945.97*45) / 2520 = (1544276.25 + 42568.65) / 2520 ≈ 629.70
        # (Simple mean would be 784.96 — wrong.)
        assert abs(art["avg_cost"] - 629.70) < 0.1

    @pytest.mark.asyncio
    async def test_article_search_filter(self, db_session: AsyncSession, project):
        """Article search should filter by article_seller."""
        ono = _ono()
        await _create_cost_order(db_session, project.id, ono, date(2025, 1, 1))
        await _create_cost_item(db_session, ono, "PHONE-001", total_rub=5000, qty=10)
        await _create_cost_item(db_session, ono, "CASE-001", total_rub=200, qty=50)

        result = await get_cost_history(db_session, project.id, article_search="phone")
        assert len(result["articles"]) == 1
        assert result["articles"][0]["article_seller"] == "PHONE-001"

    @pytest.mark.asyncio
    async def test_brand_filter(self, db_session: AsyncSession, project):
        """Brand filter should work via nomenclature join."""
        await _create_nomenclature(db_session, project.id, "ART-A", 1001, "BrandA")
        await _create_nomenclature(db_session, project.id, "ART-B", 1002, "BrandB")
        ono = _ono()
        await _create_cost_order(db_session, project.id, ono, date(2025, 1, 1))
        await _create_cost_item(db_session, ono, "ART-A", total_rub=100, qty=10)
        await _create_cost_item(db_session, ono, "ART-B", total_rub=200, qty=5)

        result = await get_cost_history(db_session, project.id, brand_filter="BrandA")
        assert len(result["articles"]) == 1
        assert result["articles"][0]["article_seller"] == "ART-A"

    @pytest.mark.asyncio
    async def test_brands_list(self, db_session: AsyncSession, project):
        """Brands list should be populated from nomenclature."""
        await _create_nomenclature(db_session, project.id, "ART-X", 2001, "Alpha")
        await _create_nomenclature(db_session, project.id, "ART-Y", 2002, "Beta")
        ono = _ono()
        await _create_cost_order(db_session, project.id, ono, date(2025, 1, 1))
        await _create_cost_item(db_session, ono, "ART-X", total_rub=100, qty=10)

        result = await get_cost_history(db_session, project.id)
        assert "Alpha" in result["brands"]
        assert "Beta" in result["brands"]

    @pytest.mark.asyncio
    async def test_project_id_isolation(self, db_session: AsyncSession, project, other_project):
        """Cost orders from other project should not be visible."""
        ono1 = _ono()
        ono2 = _ono()
        await _create_cost_order(db_session, project.id, ono1, date(2025, 1, 1))
        await _create_cost_item(db_session, ono1, "ART-MINE", total_rub=100, qty=10)

        await _create_cost_order(db_session, other_project.id, ono2, date(2025, 1, 1))
        await _create_cost_item(db_session, ono2, "ART-OTHER", total_rub=999, qty=1)

        result = await get_cost_history(db_session, project.id)
        articles = [a["article_seller"] for a in result["articles"]]
        assert "ART-MINE" in articles
        assert "ART-OTHER" not in articles

    @pytest.mark.asyncio
    async def test_deleted_orders_excluded(self, db_session: AsyncSession, project):
        """Soft-deleted cost orders should be excluded."""
        ono = _ono()
        await db_session.execute(
            text(
                "INSERT INTO cost_orders (project_id, order_no, is_deleted, "
                "delivery_cost_cny, delivery_cost_usd, rate_cny, rate_eur, rate_usd, created_at) "
                "VALUES (:pid, :ono, true, 0, 0, 0, 0, 0, NOW())"
            ),
            {"pid": project.id, "ono": ono},
        )
        await db_session.commit()
        await _create_cost_item(db_session, ono, "ART-DEL", total_rub=100, qty=10)

        result = await get_cost_history(db_session, project.id)
        articles = [a["article_seller"] for a in result["articles"]]
        assert "ART-DEL" not in articles

    @pytest.mark.asyncio
    async def test_articles_sorted_by_name(self, db_session: AsyncSession, project):
        """Articles should be sorted alphabetically."""
        ono = _ono()
        await _create_cost_order(db_session, project.id, ono, date(2025, 1, 1))
        await _create_cost_item(db_session, ono, "ZZZ-001", total_rub=100, qty=1)
        await _create_cost_item(db_session, ono, "AAA-001", total_rub=200, qty=1)
        await _create_cost_item(db_session, ono, "MMM-001", total_rub=150, qty=1)

        result = await get_cost_history(db_session, project.id)
        names = [a["article_seller"] for a in result["articles"]]
        assert names == sorted(names)

    @pytest.mark.asyncio
    async def test_zero_cost_not_counted_in_avg(self, db_session: AsyncSession, project):
        """Zero cost items should not affect avg (cost_count check)."""
        ono1 = _ono()
        ono2 = _ono()
        await _create_cost_order(db_session, project.id, ono1, date(2025, 1, 1))
        await _create_cost_item(db_session, ono1, "ART-ZERO", total_rub=0, qty=10)
        await _create_cost_order(db_session, project.id, ono2, date(2025, 2, 1))
        await _create_cost_item(db_session, ono2, "ART-ZERO", total_rub=300, qty=5)

        result = await get_cost_history(db_session, project.id)
        art = next(a for a in result["articles"] if a["article_seller"] == "ART-ZERO")
        assert art["avg_cost"] == 300.0  # only non-zero counted
