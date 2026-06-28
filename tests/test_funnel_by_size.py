# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests for size grouping + color filter (group_by=size, фильтр по цвету).

- get_funnel_by_size — группирует строки воронки по размеру из vendor_code,
  строит per-SKU children, кладёт «Без размера» для артикулов без размера.
- resolve_filter_nm_ids(color=...) — отбирает nm_id по цвету из article_seller.
- get_color_options — distinct цвета (отсортированы), None пропускаются.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ID = 1
TAX_INFO = {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}


def _row(
    nm_id: int,
    vendor_code: str,
    subject: str = "Ковры",
    orders_sum_rub: float = 10000.0,
    orders_count: int = 10,
    adv_sum: float = 500.0,
) -> MagicMock:
    """Mock WbFunnelDaily row with vendor_code (источник размера)."""
    row = MagicMock()
    row.nm_id = nm_id
    row.vendor_code = vendor_code
    row.brand = "BrandA"
    row.subject = subject
    row.orders_sum_rub = Decimal(str(orders_sum_rub))
    row.orders_count = orders_count
    row.adv_sum = Decimal(str(adv_sum))
    row.adv_views = 1000
    row.adv_clicks = 50
    row.cost_price = Decimal("200.0")
    row.open_card = 500
    row.add_to_cart = 100
    row.cart_to_order_pct = None
    row.add_to_cart_pct = None
    row.avg_price = 1000.0
    row.date = date(2024, 1, 15)
    return row


def _db_with_scalars(rows: list) -> AsyncMock:
    """Mock DB: execute().scalars().all() → rows (для _load_funnel_rows)."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = rows
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


def _patch_maps(buyout: dict):
    return (
        patch(
            "backend.services.funnel.queries_grouping.get_tariff_map",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "backend.services.funnel.queries_grouping.get_avg_buyout_map",
            new_callable=AsyncMock,
            return_value=buyout,
        ),
    )


class TestGetFunnelBySize:
    @pytest.mark.asyncio
    async def test_empty_rows_returns_empty_list(self):
        from backend.services.funnel.queries_grouping import get_funnel_by_size

        p1, p2 = _patch_maps({})
        with p1, p2:
            result = await get_funnel_by_size(
                _db_with_scalars([]), PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_groups_by_size(self):
        from backend.services.funnel.queries_grouping import get_funnel_by_size

        rows = [
            _row(1, "200x300_бежевый"),
            _row(2, "200х300_серый"),  # кириллическая х → тот же размер
            _row(3, "120x170_синий"),
        ]
        p1, p2 = _patch_maps({1: 100, 2: 100, 3: 100})
        with p1, p2:
            result = await get_funnel_by_size(
                _db_with_scalars(rows), PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None
            )
        sizes = {r["size"] for r in result}
        assert sizes == {"200x300", "120x170"}

    @pytest.mark.asyncio
    async def test_same_size_aggregated_across_skus(self):
        from backend.services.funnel.queries_grouping import get_funnel_by_size

        rows = [
            _row(1, "200x300_бежевый", orders_sum_rub=5000.0, orders_count=5),
            _row(2, "200x300_серый", orders_sum_rub=3000.0, orders_count=3),
        ]
        p1, p2 = _patch_maps({1: 100, 2: 100})
        with p1, p2:
            result = await get_funnel_by_size(
                _db_with_scalars(rows), PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None
            )
        assert len(result) == 1
        grp = result[0]
        assert grp["size"] == "200x300"
        assert grp["orders_sum_rub"] == 8000.0
        assert grp["orders_count"] == 8
        # children — per-SKU (раскрытие строки)
        assert {c["nm_id"] for c in grp["children"]} == {1, 2}

    @pytest.mark.asyncio
    async def test_article_without_size_goes_to_bucket(self):
        from backend.services.funnel.queries_grouping import get_funnel_by_size

        rows = [_row(1, "mosaic_animals_mem", subject="Алмазная мозаика")]
        p1, p2 = _patch_maps({1: 100})
        with p1, p2:
            result = await get_funnel_by_size(
                _db_with_scalars(rows), PROJECT_ID, TAX_INFO, "2024-01-01", "2024-01-31", None, None
            )
        assert len(result) == 1
        assert result[0]["size"] == "Без размера"


class TestGetFunnelByCategorySize:
    @pytest.mark.asyncio
    async def test_tree_category_size_sku(self):
        from backend.services.funnel.queries_grouping import get_funnel_by_category_size

        rows = [
            _row(1, "200x300_бежевый", subject="Ковры"),
            _row(2, "200x300_серый", subject="Ковры"),
            _row(3, "120x170_синий", subject="Ковры"),
            _row(4, "200x300_белый", subject="Шторы"),  # тот же размер, другая категория
        ]
        p1, p2 = _patch_maps({1: 100, 2: 100, 3: 100, 4: 100})
        with p1, p2:
            result = await get_funnel_by_category_size(
                _db_with_scalars(rows), PROJECT_ID, TAX_INFO, "2026-01-01", "2026-12-31", None, None
            )

        # уровень 1 — категории
        assert {r["subject"] for r in result} == {"Ковры", "Шторы"}
        kovry = next(r for r in result if r["subject"] == "Ковры")
        # уровень 2 — размеры внутри категории
        assert {c["size"] for c in kovry["children"]} == {"200x300", "120x170"}
        # уровень 3 — SKU внутри размера; 200x300 ковров НЕ склеен со шторами
        k200 = next(c for c in kovry["children"] if c["size"] == "200x300")
        assert {s["nm_id"] for s in k200["children"]} == {1, 2}
        shtory = next(r for r in result if r["subject"] == "Шторы")
        s200 = next(c for c in shtory["children"] if c["size"] == "200x300")
        assert {s["nm_id"] for s in s200["children"]} == {4}


class TestResolveColorFilter:
    @pytest.mark.asyncio
    async def test_no_filters_returns_none(self):
        from backend.services.funnel.queries import resolve_filter_nm_ids

        mock_db = AsyncMock()
        result = await resolve_filter_nm_ids(mock_db, PROJECT_ID)
        assert result is None
        mock_db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_color_filters_nm_ids(self):
        from backend.services.funnel.queries import resolve_filter_nm_ids

        # (article_wb, article_seller)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(
            return_value=[
                (111, "200x300_бежевый"),
                (222, "200x300_серый"),
                (333, "120x170_бежевый"),
            ]
        )
        result = await resolve_filter_nm_ids(mock_db, PROJECT_ID, color="бежевый")
        assert result == {111, 333}


class TestColorOptions:
    @pytest.mark.asyncio
    async def test_returns_sorted_distinct_colors(self):
        from backend.services.funnel.queries import get_color_options

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(
            return_value=[
                ("200x300_серый",),
                ("120x170_бежевый",),
                ("150x200_серый",),  # дубль цвета
                ("mosaic_animals_mem",),  # без цвета → пропуск
            ]
        )
        result = await get_color_options(mock_db, PROJECT_ID)
        assert result == {"colors": ["бежевый", "серый"]}
