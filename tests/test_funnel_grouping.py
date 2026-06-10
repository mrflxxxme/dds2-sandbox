"""
Tests for backend/services/funnel/queries.py — get_funnel_by_brand and get_funnel_by_subject.

Covers:
- get_funnel_by_brand — groups rows by brand, returns list with "brand" key
- get_funnel_by_subject — groups rows by subject, returns list with "subject" key
- Empty DB rows → empty list
- Multiple brands/subjects aggregated correctly
- Rows without brand/subject → "Без бренда" / "Без категории"
- Traffic metrics computed correctly (CTR, CPC, CPM, DRR, CR)
- project_id isolation (filters applied)
- No ZeroDivisionError on zero orders/views/clicks
"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ID = 1
TAX_INFO = {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}


def _make_funnel_row(
    nm_id: int = 111,
    brand: str = "BrandA",
    subject: str = "Ковры",
    orders_sum_rub: float = 10000.0,
    orders_count: int = 10,
    adv_sum: float = 500.0,
    adv_views: int = 1000,
    adv_clicks: int = 50,
    cost_price: float = 200.0,
    open_card: int = 500,
    add_to_cart: int = 100,
    cart_to_order_pct: float = 0.0,
    add_to_cart_pct: float = 0.0,
    avg_price: float = 1000.0,
    row_date: date = date(2024, 1, 15),
) -> MagicMock:
    """Build a mock WbFunnelDaily ORM row."""
    row = MagicMock()
    row.nm_id = nm_id
    row.brand = brand
    row.subject = subject
    row.orders_sum_rub = Decimal(str(orders_sum_rub))
    row.orders_count = orders_count
    row.adv_sum = Decimal(str(adv_sum))
    row.adv_views = adv_views
    row.adv_clicks = adv_clicks
    row.cost_price = Decimal(str(cost_price))
    row.open_card = open_card
    row.add_to_cart = add_to_cart
    row.cart_to_order_pct = cart_to_order_pct or None
    row.add_to_cart_pct = add_to_cart_pct or None
    row.avg_price = avg_price
    row.date = row_date
    return row


def _make_db_with_rows(rows: list) -> AsyncMock:
    """Create a mock DB session that returns given rows from scalars().all()."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


# ─── get_funnel_by_brand ─────────────────────────────────────────────────────


class TestGetFunnelByBrand:
    """get_funnel_by_brand — aggregation by brand."""

    @pytest.mark.asyncio
    async def test_empty_rows_returns_empty_list(self):
        """No funnel data → empty list."""
        from backend.services.funnel.queries import get_funnel_by_brand

        mock_db = _make_db_with_rows([])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            result = await get_funnel_by_brand(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        assert result == []

    @pytest.mark.asyncio
    async def test_single_brand_in_result(self):
        """Single brand row returns one-item list with 'brand' key."""
        from backend.services.funnel.queries import get_funnel_by_brand

        row = _make_funnel_row(brand="BrandX")
        mock_db = _make_db_with_rows([row])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={111: 100},
            ),
        ):
            result = await get_funnel_by_brand(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        assert len(result) == 1
        assert result[0]["brand"] == "BrandX"

    @pytest.mark.asyncio
    async def test_two_brands_aggregated_separately(self):
        """Rows from two different brands produce two separate result items."""
        from backend.services.funnel.queries import get_funnel_by_brand

        row_a1 = _make_funnel_row(nm_id=1, brand="BrandA", orders_sum_rub=5000.0, orders_count=5)
        row_a2 = _make_funnel_row(nm_id=2, brand="BrandA", orders_sum_rub=3000.0, orders_count=3)
        row_b = _make_funnel_row(nm_id=3, brand="BrandB", orders_sum_rub=8000.0, orders_count=8)

        mock_db = _make_db_with_rows([row_a1, row_a2, row_b])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={1: 100, 2: 100, 3: 100},
            ),
        ):
            result = await get_funnel_by_brand(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        assert len(result) == 2
        brands = {item["brand"] for item in result}
        assert brands == {"BrandA", "BrandB"}

    @pytest.mark.asyncio
    async def test_orders_sum_aggregated_across_articles(self):
        """orders_sum_rub is summed across all articles in same brand."""
        from backend.services.funnel.queries import get_funnel_by_brand

        row1 = _make_funnel_row(nm_id=1, brand="BrandA", orders_sum_rub=4000.0, orders_count=4)
        row2 = _make_funnel_row(nm_id=2, brand="BrandA", orders_sum_rub=6000.0, orders_count=6)

        mock_db = _make_db_with_rows([row1, row2])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={1: 100, 2: 100},
            ),
        ):
            result = await get_funnel_by_brand(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        brand_a = next(item for item in result if item["brand"] == "BrandA")
        assert brand_a["orders_sum_rub"] == 10000.0
        assert brand_a["orders_count"] == 10

    @pytest.mark.asyncio
    async def test_row_without_brand_grouped_as_bez_brenda(self):
        """Rows with None brand grouped under 'Без бренда'."""
        from backend.services.funnel.queries import get_funnel_by_brand

        row = _make_funnel_row(brand=None, nm_id=10)
        row.brand = None

        mock_db = _make_db_with_rows([row])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={10: 100},
            ),
        ):
            result = await get_funnel_by_brand(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        assert len(result) == 1
        assert result[0]["brand"] == "Без бренда"

    @pytest.mark.asyncio
    async def test_result_contains_expected_fields(self):
        """Each brand item contains all required metric fields."""
        from backend.services.funnel.queries import get_funnel_by_brand

        row = _make_funnel_row(adv_views=500, adv_clicks=25, adv_sum=1000.0, orders_sum_rub=20000.0, orders_count=20)
        mock_db = _make_db_with_rows([row])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={"Ковры": 15.0},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={111: 85},
            ),
        ):
            result = await get_funnel_by_brand(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        assert len(result) == 1
        item = result[0]
        required_fields = [
            "brand",
            "open_card",
            "add_to_cart",
            "orders_count",
            "orders_sum_rub",
            "buyout_percent",
            "revenue",
            "adv_sum",
            "adv_views",
            "adv_clicks",
            "tax",
            "profit",
            "margin",
            "commission",
            "cost_total",
            "ctr",
            "cpc",
            "cpm",
            "cr",
            "drr",
        ]
        for field in required_fields:
            assert field in item, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_zero_views_no_division_error(self):
        """Zero adv_views — CTR, CPM do not raise ZeroDivisionError."""
        from backend.services.funnel.queries import get_funnel_by_brand

        row = _make_funnel_row(adv_views=0, adv_clicks=0, adv_sum=0.0, orders_sum_rub=5000.0)
        mock_db = _make_db_with_rows([row])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={111: 100},
            ),
        ):
            result = await get_funnel_by_brand(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        assert result[0]["ctr"] == 0
        assert result[0]["cpm"] == 0
        assert result[0]["cpc"] == 0

    @pytest.mark.asyncio
    async def test_sorted_by_orders_sum_descending(self):
        """Result is sorted by orders_sum_rub descending."""
        from backend.services.funnel.queries import get_funnel_by_brand

        row_a = _make_funnel_row(nm_id=1, brand="BrandA", orders_sum_rub=1000.0)
        row_b = _make_funnel_row(nm_id=2, brand="BrandB", orders_sum_rub=9000.0)
        row_c = _make_funnel_row(nm_id=3, brand="BrandC", orders_sum_rub=5000.0)

        mock_db = _make_db_with_rows([row_a, row_b, row_c])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={1: 100, 2: 100, 3: 100},
            ),
        ):
            result = await get_funnel_by_brand(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        assert result[0]["brand"] == "BrandB"  # 9000 first
        assert result[1]["brand"] == "BrandC"  # 5000 second
        assert result[2]["brand"] == "BrandA"  # 1000 last

    @pytest.mark.asyncio
    async def test_adv_sum_aggregated_across_articles(self):
        """adv_sum is summed across multiple articles in same brand."""
        from backend.services.funnel.queries import get_funnel_by_brand

        row1 = _make_funnel_row(nm_id=1, brand="BrandA", adv_sum=300.0)
        row2 = _make_funnel_row(nm_id=2, brand="BrandA", adv_sum=700.0)

        mock_db = _make_db_with_rows([row1, row2])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={1: 100, 2: 100},
            ),
        ):
            result = await get_funnel_by_brand(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        assert result[0]["adv_sum"] == 1000.0  # 300 + 700


# ─── get_funnel_by_subject ───────────────────────────────────────────────────


class TestGetFunnelBySubject:
    """get_funnel_by_subject — aggregation by subject (category)."""

    @pytest.mark.asyncio
    async def test_empty_rows_returns_empty_list(self):
        """No funnel data → empty list."""
        from backend.services.funnel.queries import get_funnel_by_subject

        mock_db = _make_db_with_rows([])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            result = await get_funnel_by_subject(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        assert result == []

    @pytest.mark.asyncio
    async def test_single_subject_in_result(self):
        """Single subject row returns one-item list with 'subject' key."""
        from backend.services.funnel.queries import get_funnel_by_subject

        row = _make_funnel_row(subject="Ковры")
        mock_db = _make_db_with_rows([row])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={"Ковры": 15.0},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={111: 100},
            ),
        ):
            result = await get_funnel_by_subject(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        assert len(result) == 1
        assert result[0]["subject"] == "Ковры"

    @pytest.mark.asyncio
    async def test_two_subjects_aggregated_separately(self):
        """Rows from two subjects produce two separate result items."""
        from backend.services.funnel.queries import get_funnel_by_subject

        row_a = _make_funnel_row(nm_id=1, subject="Ковры", orders_sum_rub=6000.0)
        row_b = _make_funnel_row(nm_id=2, subject="Подушки", orders_sum_rub=4000.0)

        mock_db = _make_db_with_rows([row_a, row_b])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={1: 100, 2: 100},
            ),
        ):
            result = await get_funnel_by_subject(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        assert len(result) == 2
        subjects = {item["subject"] for item in result}
        assert subjects == {"Ковры", "Подушки"}

    @pytest.mark.asyncio
    async def test_row_without_subject_grouped_as_bez_kategorii(self):
        """Rows with None subject grouped under 'Без категории'."""
        from backend.services.funnel.queries import get_funnel_by_subject

        row = _make_funnel_row(nm_id=10)
        row.subject = None

        mock_db = _make_db_with_rows([row])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={10: 100},
            ),
        ):
            result = await get_funnel_by_subject(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        assert len(result) == 1
        assert result[0]["subject"] == "Без категории"

    @pytest.mark.asyncio
    async def test_orders_aggregated_across_articles_in_subject(self):
        """orders_sum_rub and orders_count summed across articles in same subject."""
        from backend.services.funnel.queries import get_funnel_by_subject

        row1 = _make_funnel_row(nm_id=1, subject="Ковры", orders_sum_rub=3000.0, orders_count=3)
        row2 = _make_funnel_row(nm_id=2, subject="Ковры", orders_sum_rub=7000.0, orders_count=7)

        mock_db = _make_db_with_rows([row1, row2])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={"Ковры": 15.0},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={1: 100, 2: 100},
            ),
        ):
            result = await get_funnel_by_subject(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        kovry = result[0]
        assert kovry["subject"] == "Ковры"
        assert kovry["orders_sum_rub"] == 10000.0
        assert kovry["orders_count"] == 10

    @pytest.mark.asyncio
    async def test_result_contains_expected_fields(self):
        """Each subject item contains all required metric fields."""
        from backend.services.funnel.queries import get_funnel_by_subject

        row = _make_funnel_row()
        mock_db = _make_db_with_rows([row])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={111: 100},
            ),
        ):
            result = await get_funnel_by_subject(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        item = result[0]
        required_fields = [
            "subject",
            "open_card",
            "add_to_cart",
            "orders_count",
            "orders_sum_rub",
            "buyout_percent",
            "revenue",
            "adv_sum",
            "adv_views",
            "adv_clicks",
            "tax",
            "profit",
            "margin",
            "commission",
            "cost_total",
            "ctr",
            "cpc",
            "cpm",
            "cr",
            "drr",
        ]
        for field in required_fields:
            assert field in item, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_zero_orders_no_division_error(self):
        """Zero orders_sum_rub and zero clicks — no ZeroDivisionError."""
        from backend.services.funnel.queries import get_funnel_by_subject

        row = _make_funnel_row(orders_sum_rub=0.0, orders_count=0, adv_sum=1.0, adv_views=10, adv_clicks=0)
        mock_db = _make_db_with_rows([row])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={111: 100},
            ),
        ):
            # Should not raise
            result = await get_funnel_by_subject(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        item = result[0]
        assert item["drr"] == 0
        assert item["ctr"] == 0
        assert item["margin"] == 0

    @pytest.mark.asyncio
    async def test_sorted_by_orders_sum_descending(self):
        """Result is sorted by orders_sum_rub descending."""
        from backend.services.funnel.queries import get_funnel_by_subject

        row_a = _make_funnel_row(nm_id=1, subject="Ковры", orders_sum_rub=2000.0)
        row_b = _make_funnel_row(nm_id=2, subject="Подушки", orders_sum_rub=8000.0)
        row_c = _make_funnel_row(nm_id=3, subject="Пледы", orders_sum_rub=5000.0)

        mock_db = _make_db_with_rows([row_a, row_b, row_c])

        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={1: 100, 2: 100, 3: 100},
            ),
        ):
            result = await get_funnel_by_subject(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        assert result[0]["subject"] == "Подушки"  # 8000 first
        assert result[1]["subject"] == "Пледы"  # 5000 second
        assert result[2]["subject"] == "Ковры"  # 2000 last

    @pytest.mark.asyncio
    async def test_tariff_rate_applied_for_subject(self):
        """Tariff rate from tariff_map is applied to the correct subject."""
        from backend.services.funnel.queries import get_funnel_by_subject

        row = _make_funnel_row(
            subject="Ковры",
            orders_sum_rub=10000.0,
            orders_count=10,
            adv_sum=0.0,
            cost_price=0.0,
        )
        mock_db = _make_db_with_rows([row])

        # 20% commission rate for Ковры, 100% buyout
        with (
            patch(
                "backend.services.funnel.queries.get_tariff_map",
                new_callable=AsyncMock,
                return_value={"Ковры": 20.0},
            ),
            patch(
                "backend.services.funnel.queries.get_avg_buyout_map",
                new_callable=AsyncMock,
                return_value={111: 100},
            ),
        ):
            result = await get_funnel_by_subject(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

        item = result[0]
        # revenue = orders_sum * 100% buyout = 10000
        # commission = revenue * 20% = 2000
        assert item["revenue"] == 10000.0
        assert item["commission"] == pytest.approx(2000.0, rel=1e-2)


# ─── get_funnel_by_tag / get_funnel_by_imt (queries_grouping.py) ─────────────


def _make_scalar_result(rows: list) -> MagicMock:
    """Mock result for queries consumed via .scalars().all()."""
    m = MagicMock()
    m.scalars.return_value.all.return_value = rows
    return m


def _make_iter_result(tuples: list) -> MagicMock:
    """Mock result for queries iterated directly (tuple unpacking)."""
    m = MagicMock()
    m.__iter__ = MagicMock(side_effect=lambda: iter(tuples))
    return m


def _make_db_seq(*results) -> AsyncMock:
    """Mock DB whose execute() returns the given results in order."""
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=list(results))
    return mock_db


def _patch_grouping_maps(buyout_map: dict | None = None):
    """Patch tariff/buyout maps for queries_grouping module."""
    return (
        patch(
            "backend.services.funnel.queries_grouping.get_tariff_map",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "backend.services.funnel.queries_grouping.get_avg_buyout_map",
            new_callable=AsyncMock,
            return_value=buyout_map or {},
        ),
    )


class TestGetFunnelByTag:
    """get_funnel_by_tag — aggregation by tag with expandable SKU children."""

    async def _run(self, funnel_rows: list, tag_tuples: list):
        """Call get_funnel_by_tag with mocked DB: funnel rows + tag mapping."""
        from backend.services.funnel.queries_grouping import get_funnel_by_tag

        mock_db = _make_db_seq(_make_scalar_result(funnel_rows), _make_iter_result(tag_tuples))
        buyout = {r.nm_id: 100 for r in funnel_rows}
        p1, p2 = _patch_grouping_maps(buyout)
        with p1, p2:
            return await get_funnel_by_tag(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

    @pytest.mark.asyncio
    async def test_groups_by_tag_with_untagged_fallback(self):
        """Tagged product goes to its tag group, untagged — to 'Без ярлыка'."""
        rows = [
            _make_funnel_row(nm_id=1, orders_sum_rub=8000.0),
            _make_funnel_row(nm_id=2, orders_sum_rub=2000.0),
        ]
        result = await self._run(rows, [(1, 10, "Хиты")])

        tags = {item["tag"] for item in result}
        assert tags == {"Хиты", "Без ярлыка"}

    @pytest.mark.asyncio
    async def test_groups_have_children_with_sku_rows(self):
        """Each tag group carries children — per-SKU rows with identity fields."""
        rows = [
            _make_funnel_row(nm_id=1, orders_sum_rub=8000.0),
            _make_funnel_row(nm_id=2, orders_sum_rub=2000.0),
        ]
        rows[0].vendor_code = "ART-1"
        rows[1].vendor_code = "ART-2"
        result = await self._run(rows, [(1, 10, "Хиты"), (2, 10, "Хиты")])

        hits = next(item for item in result if item["tag"] == "Хиты")
        assert "children" in hits
        assert {c["nm_id"] for c in hits["children"]} == {1, 2}
        child1 = next(c for c in hits["children"] if c["nm_id"] == 1)
        assert child1["vendor_code"] == "ART-1"
        assert child1["brand"] == "BrandA"
        assert child1["subject"] == "Ковры"

    @pytest.mark.asyncio
    async def test_multi_tag_product_appears_in_each_groups_children(self):
        """Product with two tags appears in children of both tag groups."""
        rows = [_make_funnel_row(nm_id=1, orders_sum_rub=5000.0)]
        result = await self._run(rows, [(1, 10, "Хиты"), (1, 20, "Новинки")])

        for tag_name in ("Хиты", "Новинки"):
            grp = next(item for item in result if item["tag"] == tag_name)
            assert [c["nm_id"] for c in grp["children"]] == [1]

    @pytest.mark.asyncio
    async def test_children_aggregate_multiple_days_per_sku(self):
        """Two daily rows of the same SKU collapse into one child with summed metrics."""
        rows = [
            _make_funnel_row(nm_id=1, orders_sum_rub=3000.0, orders_count=3, row_date=date(2024, 1, 10)),
            _make_funnel_row(nm_id=1, orders_sum_rub=7000.0, orders_count=7, row_date=date(2024, 1, 11)),
        ]
        result = await self._run(rows, [(1, 10, "Хиты")])

        grp = next(item for item in result if item["tag"] == "Хиты")
        assert len(grp["children"]) == 1
        child = grp["children"][0]
        assert child["orders_sum_rub"] == 10000.0
        assert child["orders_count"] == 10

    @pytest.mark.asyncio
    async def test_children_sorted_by_orders_sum_desc(self):
        """Children inside a group are sorted by orders_sum_rub descending."""
        rows = [
            _make_funnel_row(nm_id=1, orders_sum_rub=1000.0),
            _make_funnel_row(nm_id=2, orders_sum_rub=9000.0),
            _make_funnel_row(nm_id=3, orders_sum_rub=5000.0),
        ]
        result = await self._run(rows, [(1, 10, "Хиты"), (2, 10, "Хиты"), (3, 10, "Хиты")])

        grp = next(item for item in result if item["tag"] == "Хиты")
        assert [c["nm_id"] for c in grp["children"]] == [2, 3, 1]

    @pytest.mark.asyncio
    async def test_children_contain_metric_fields(self):
        """Child rows expose the same metric fields the SKU table renders."""
        rows = [_make_funnel_row(nm_id=1)]
        result = await self._run(rows, [(1, 10, "Хиты")])

        child = result[0]["children"][0]
        for field in [
            "nm_id",
            "vendor_code",
            "brand",
            "subject",
            "open_card",
            "add_to_cart",
            "orders_count",
            "orders_sum_rub",
            "revenue",
            "adv_sum",
            "adv_views",
            "adv_clicks",
            "tax",
            "profit",
            "margin",
            "commission",
            "commission_rate",
            "avg_price",
            "add_to_cart_pct",
            "cart_to_order_pct",
            "spp_rate",
            "buyout_percent",
            "ctr",
            "cpc",
            "cpm",
            "drr",
        ]:
            assert field in child, f"Missing child field: {field}"

    @pytest.mark.asyncio
    async def test_children_sums_match_group_totals(self):
        """Sum of children metrics equals the parent group aggregate."""
        rows = [
            _make_funnel_row(nm_id=1, orders_sum_rub=4000.0, orders_count=4, adv_sum=200.0),
            _make_funnel_row(nm_id=2, orders_sum_rub=6000.0, orders_count=6, adv_sum=300.0),
        ]
        result = await self._run(rows, [(1, 10, "Хиты"), (2, 10, "Хиты")])

        grp = next(item for item in result if item["tag"] == "Хиты")
        assert sum(c["orders_sum_rub"] for c in grp["children"]) == grp["orders_sum_rub"]
        assert sum(c["orders_count"] for c in grp["children"]) == grp["orders_count"]
        assert sum(c["adv_sum"] for c in grp["children"]) == grp["adv_sum"]
        assert sum(c["profit"] for c in grp["children"]) == pytest.approx(grp["profit"], abs=0.05)


class TestGetFunnelByImtChildren:
    """get_funnel_by_imt — regression: children survive the shared-helper refactor."""

    async def _run(self, funnel_rows: list, nom_tuples: list, alias_rows: list | None = None):
        from backend.services.funnel.queries_grouping import get_funnel_by_imt

        mock_db = _make_db_seq(
            _make_scalar_result(funnel_rows),
            _make_iter_result(nom_tuples),
            _make_iter_result(alias_rows or []),
        )
        buyout = {r.nm_id: 100 for r in funnel_rows}
        p1, p2 = _patch_grouping_maps(buyout)
        with p1, p2:
            return await get_funnel_by_imt(mock_db, PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None)

    @pytest.mark.asyncio
    async def test_imt_groups_have_children(self):
        """IMT group exposes per-SKU children; SKU without imt goes to 'Без склейки'."""
        rows = [
            _make_funnel_row(nm_id=1, orders_sum_rub=8000.0),
            _make_funnel_row(nm_id=2, orders_sum_rub=4000.0),
            _make_funnel_row(nm_id=3, orders_sum_rub=2000.0),
        ]
        result = await self._run(rows, [(1, 555), (2, 555)])

        grp = next(item for item in result if item["imt_group"] == "#555")
        assert {c["nm_id"] for c in grp["children"]} == {1, 2}
        loose = next(item for item in result if item["imt_group"] == "Без склейки")
        assert [c["nm_id"] for c in loose["children"]] == [3]

    @pytest.mark.asyncio
    async def test_imt_children_sums_match_group_totals(self):
        """Sum of imt children metrics equals the parent group aggregate."""
        rows = [
            _make_funnel_row(nm_id=1, orders_sum_rub=4000.0, orders_count=4),
            _make_funnel_row(nm_id=2, orders_sum_rub=6000.0, orders_count=6),
        ]
        result = await self._run(rows, [(1, 555), (2, 555)])

        grp = next(item for item in result if item["imt_group"] == "#555")
        assert sum(c["orders_sum_rub"] for c in grp["children"]) == grp["orders_sum_rub"]
        assert sum(c["orders_count"] for c in grp["children"]) == grp["orders_count"]
