# ruff: noqa: RUF001
"""
Tests for order geography — parser + service logic.

Tests:
  1. order_city_parser — Excel parsing (srid → city + okrug)
  2. order_geography_service — aggregation logic (mocked WB API + DB)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# Tests: order_city_parser
# ═══════════════════════════════════════════════════════════════════════════════


class TestOrderCityParser:
    """Verify Excel parsing of WB order feed."""

    def _make_xlsx(self, rows: list[list], sheet_name: str = "Заказы") -> bytes:
        """Create a minimal xlsx in memory with given rows."""
        from io import BytesIO

        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet_name
        for row in rows:
            ws.append(row)
        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

    def test_basic_parsing(self):
        """Parse valid rows with srid, city, okrug."""
        from backend.etl.parsers.order_city_parser import parse_order_city_excel

        # Row 1 = title (skipped), Row 2+ = data
        # Col O (15) = okrug, Col P (16) = city, Col U (21) = srid
        row1 = ["Заказы"] + [None] * 20  # merged title
        row2 = [None] * 14 + ["ЦФО", "Москва"] + [None] * 4 + ["srid-001"]
        row3 = [None] * 14 + ["СФО", "Новосибирск"] + [None] * 4 + ["srid-002"]

        data = self._make_xlsx([row1, row2, row3])
        result = parse_order_city_excel(data)

        assert len(result) == 2
        assert result[0] == {"srid": "srid-001", "city": "Москва", "okrug": "ЦФО", "order_date": None}
        assert result[1] == {"srid": "srid-002", "city": "Новосибирск", "okrug": "СФО", "order_date": None}

    def test_missing_city_skipped(self):
        """Rows without city are skipped."""
        from backend.etl.parsers.order_city_parser import parse_order_city_excel

        row1 = ["Заказы"] + [None] * 20
        row2 = [None] * 14 + ["ЦФО", None] + [None] * 4 + ["srid-001"]  # no city
        row3 = [None] * 14 + ["СФО", "Омск"] + [None] * 4 + ["srid-002"]

        data = self._make_xlsx([row1, row2, row3])
        result = parse_order_city_excel(data)

        assert len(result) == 1
        assert result[0]["city"] == "Омск"

    def test_missing_srid_skipped(self):
        """Rows without srid are skipped."""
        from backend.etl.parsers.order_city_parser import parse_order_city_excel

        row1 = ["Заказы"] + [None] * 20
        row2 = [None] * 14 + ["ЦФО", "Москва"] + [None] * 4 + [None]  # no srid

        data = self._make_xlsx([row1, row2])
        result = parse_order_city_excel(data)

        assert len(result) == 0

    def test_dash_city_skipped(self):
        """City = '-' is treated as missing."""
        from backend.etl.parsers.order_city_parser import parse_order_city_excel

        row1 = ["Заказы"] + [None] * 20
        row2 = [None] * 14 + ["ЦФО", "-"] + [None] * 4 + ["srid-001"]

        data = self._make_xlsx([row1, row2])
        result = parse_order_city_excel(data)

        assert len(result) == 0

    def test_no_zakazy_sheet(self):
        """If no sheet named 'Заказы', return empty."""
        from backend.etl.parsers.order_city_parser import parse_order_city_excel

        row1 = ["data"] + [None] * 20
        data = self._make_xlsx([row1], sheet_name="Продажи")
        result = parse_order_city_excel(data)

        assert result == []

    def test_okrug_optional(self):
        """Okrug can be None."""
        from backend.etl.parsers.order_city_parser import parse_order_city_excel

        row1 = ["Заказы"] + [None] * 20
        row2 = [None] * 14 + [None, "Казань"] + [None] * 4 + ["srid-003"]

        data = self._make_xlsx([row1, row2])
        result = parse_order_city_excel(data)

        assert len(result) == 1
        assert result[0]["okrug"] is None
        assert result[0]["city"] == "Казань"

    def test_whitespace_trimmed(self):
        """Leading/trailing whitespace in city/srid is trimmed."""
        from backend.etl.parsers.order_city_parser import parse_order_city_excel

        row1 = ["Заказы"] + [None] * 20
        row2 = [None] * 14 + ["  ЦФО  ", "  Москва  "] + [None] * 4 + ["  srid-001  "]

        data = self._make_xlsx([row1, row2])
        result = parse_order_city_excel(data)

        assert result[0]["srid"] == "srid-001"
        assert result[0]["city"] == "Москва"
        assert result[0]["okrug"] == "ЦФО"


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: order_geography_service — pure aggregation logic
# ═══════════════════════════════════════════════════════════════════════════════


class TestOrderGeographyAggregation:
    """Test the aggregation logic without DB or WB API.

    We test the data processing code by directly calling the function
    with mocked dependencies.
    """

    def _make_order(
        self, date: str, brand: str, subject: str, article: str, region: str, srid: str, is_cancel: bool = False
    ):
        """Build a WB order dict matching API format."""
        return {
            "date": f"{date}T10:00:00",
            "brand": brand,
            "subject": subject,
            "supplierArticle": article,
            "regionName": region,
            "srid": srid,
            "isCancel": is_cancel,
        }

    @pytest.mark.asyncio
    async def test_basic_aggregation(self):
        """Orders aggregate by city correctly."""
        from backend.services.order_geography_service import get_order_geography

        orders = [
            self._make_order("2026-03-01", "BrandA", "Cat1", "ART1", "Московская область", "s1"),
            self._make_order("2026-03-01", "BrandA", "Cat1", "ART1", "Московская область", "s2"),
            self._make_order("2026-03-02", "BrandB", "Cat2", "ART2", "Свердловская область", "s3"),
        ]
        city_map_rows = [
            MagicMock(srid="s1", city="Москва", okrug="ЦФО"),
            MagicMock(srid="s2", city="Москва", okrug="ЦФО"),
            MagicMock(srid="s3", city="Екатеринбург", okrug="УрФО"),
        ]

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = city_map_rows
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("backend.services.funnel.wb_api_client.get_wb_key", return_value="test-key"),
            patch("backend.services.funnel.wb_api_client.fetch_supplier_orders", return_value=orders),
            patch("backend.cache.get_redis", return_value=None),
        ):  # skip cache
            result = await get_order_geography.__wrapped__(mock_db, 1, "2026-03-01", "2026-03-05")

        assert result["totals"]["total_orders"] == 3
        assert result["totals"]["unique_cities"] == 2

        cities = {c["city"]: c["order_count"] for c in result["cities"]}
        assert cities["Москва"] == 2
        assert cities["Екатеринбург"] == 1

    @pytest.mark.asyncio
    async def test_cancelled_orders_excluded(self):
        """Cancelled orders should not be counted."""
        from backend.services.order_geography_service import get_order_geography

        orders = [
            self._make_order("2026-03-01", "B", "C", "A", "МО", "s1"),
            self._make_order("2026-03-01", "B", "C", "A", "МО", "s2", is_cancel=True),
        ]

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("backend.services.funnel.wb_api_client.get_wb_key", return_value="k"),
            patch("backend.services.funnel.wb_api_client.fetch_supplier_orders", return_value=orders),
            patch("backend.cache.get_redis", return_value=None),
        ):
            result = await get_order_geography.__wrapped__(mock_db, 1, "2026-03-01", "2026-03-05")

        assert result["totals"]["total_orders"] == 1

    @pytest.mark.asyncio
    async def test_date_range_filter(self):
        """Orders outside date range are excluded."""
        from backend.services.order_geography_service import get_order_geography

        orders = [
            self._make_order("2026-03-01", "B", "C", "A", "МО", "s1"),
            self._make_order("2026-03-10", "B", "C", "A", "МО", "s2"),  # outside
        ]

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("backend.services.funnel.wb_api_client.get_wb_key", return_value="k"),
            patch("backend.services.funnel.wb_api_client.fetch_supplier_orders", return_value=orders),
            patch("backend.cache.get_redis", return_value=None),
        ):
            result = await get_order_geography.__wrapped__(mock_db, 1, "2026-03-01", "2026-03-05")

        assert result["totals"]["total_orders"] == 1

    @pytest.mark.asyncio
    async def test_brand_filter(self):
        """Brand filter narrows results."""
        from backend.services.order_geography_service import get_order_geography

        orders = [
            self._make_order("2026-03-01", "BrandA", "C", "A1", "МО", "s1"),
            self._make_order("2026-03-01", "BrandB", "C", "A2", "МО", "s2"),
        ]

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("backend.services.funnel.wb_api_client.get_wb_key", return_value="k"),
            patch("backend.services.funnel.wb_api_client.fetch_supplier_orders", return_value=orders),
            patch("backend.cache.get_redis", return_value=None),
        ):
            result = await get_order_geography.__wrapped__(mock_db, 1, "2026-03-01", "2026-03-05", brand="BrandA")

        assert result["totals"]["total_orders"] == 1

    @pytest.mark.asyncio
    async def test_no_api_key_returns_empty(self):
        """No WB API key → empty response."""
        from backend.services.order_geography_service import get_order_geography

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("backend.services.funnel.wb_api_client.get_wb_key", return_value=None),
            patch("backend.cache.get_redis", return_value=None),
        ):
            result = await get_order_geography.__wrapped__(mock_db, 1, "2026-03-01", "2026-03-05")

        assert result["totals"]["total_orders"] == 0
        assert result["cities"] == []

    @pytest.mark.asyncio
    async def test_fallback_to_region_without_city_map(self):
        """Without city map, regionName is used as city."""
        from backend.services.order_geography_service import get_order_geography

        orders = [
            self._make_order("2026-03-01", "B", "C", "A", "Московская область", "s1"),
        ]

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []  # no city map
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("backend.services.funnel.wb_api_client.get_wb_key", return_value="k"),
            patch("backend.services.funnel.wb_api_client.fetch_supplier_orders", return_value=orders),
            patch("backend.cache.get_redis", return_value=None),
        ):
            result = await get_order_geography.__wrapped__(mock_db, 1, "2026-03-01", "2026-03-05")

        assert result["cities"][0]["city"] == "Московская область"

    @pytest.mark.asyncio
    async def test_filters_populated(self):
        """Brands, categories, articles are collected as filter options."""
        from backend.services.order_geography_service import get_order_geography

        orders = [
            self._make_order("2026-03-01", "BrandA", "Cat1", "ART1", "МО", "s1"),
            self._make_order("2026-03-01", "BrandB", "Cat2", "ART2", "МО", "s2"),
        ]

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("backend.services.funnel.wb_api_client.get_wb_key", return_value="k"),
            patch("backend.services.funnel.wb_api_client.fetch_supplier_orders", return_value=orders),
            patch("backend.cache.get_redis", return_value=None),
        ):
            result = await get_order_geography.__wrapped__(mock_db, 1, "2026-03-01", "2026-03-05")

        assert "BrandA" in result["filters"]["brands"]
        assert "BrandB" in result["filters"]["brands"]
        assert "Cat1" in result["filters"]["categories"]
        assert "ART1" in result["filters"]["articles"]

    @pytest.mark.asyncio
    async def test_daily_chart_sorted(self):
        """Daily chart data is sorted by date."""
        from backend.services.order_geography_service import get_order_geography

        orders = [
            self._make_order("2026-03-03", "B", "C", "A", "МО", "s1"),
            self._make_order("2026-03-01", "B", "C", "A", "МО", "s2"),
            self._make_order("2026-03-02", "B", "C", "A", "МО", "s3"),
        ]

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("backend.services.funnel.wb_api_client.get_wb_key", return_value="k"),
            patch("backend.services.funnel.wb_api_client.fetch_supplier_orders", return_value=orders),
            patch("backend.cache.get_redis", return_value=None),
        ):
            result = await get_order_geography.__wrapped__(mock_db, 1, "2026-03-01", "2026-03-05")

        dates = [d["date"] for d in result["daily"]]
        assert dates == sorted(dates)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _empty_response helper
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmptyResponse:
    """Verify empty response structure."""

    def test_structure(self):
        from backend.services.order_geography_service import _empty_response

        result = _empty_response()

        assert result["cities"] == []
        assert result["daily"] == []
        assert result["dates"] == []
        assert result["totals"]["total_orders"] == 0
        assert result["totals"]["unique_cities"] == 0
        assert result["filters"]["brands"] == []
        assert result["filters"]["categories"] == []
        assert result["filters"]["articles"] == []
