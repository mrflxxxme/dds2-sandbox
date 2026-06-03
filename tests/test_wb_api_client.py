"""
Tests for WB API client (backend/integrations/wb_api.py).

Covers: WBApiClient methods, parse_wb_cards_to_nomenclature, parse_wb_sales_to_payouts.
All HTTP calls are mocked — no real WB API calls.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.integrations.resilience import RateLimitError
from backend.integrations.wb_api import (
    WBApiClient,
    parse_wb_cards_to_nomenclature,
    parse_wb_sales_to_payouts,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _mock_response(status_code: int = 200, json_data=None, headers=None, text=""):
    """Create a mock httpx.Response."""
    resp = AsyncMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else []
    resp.headers = headers or {}
    resp.text = text
    return resp


# ─── Fixture: fresh circuit breaker ────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    """Reset the per-project WB circuit breaker registry before each test."""
    from backend.integrations.wb_api import _wb_circuits

    # Clear all per-project breakers so each test starts fresh
    _wb_circuits._breakers.clear()
    # Reset the fallback breaker too
    from backend.integrations.resilience import CircuitState

    _wb_circuits._fallback._state = CircuitState.CLOSED
    _wb_circuits._fallback._failure_count = 0
    _wb_circuits._fallback._last_failure_time = None


# ═══════════════════════════════════════════════════════════════════════════════
# WBApiClient._get
# ═══════════════════════════════════════════════════════════════════════════════


class TestWBApiClientGet:
    """Tests for the _get method (core HTTP layer)."""

    @pytest.mark.asyncio
    async def test_get_returns_list(self):
        """Normal 200 response with list body."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(200, json_data=[{"id": 1}, {"id": 2}])
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            result = await client._get.__wrapped__(client, "https://api.test", "/path")
        assert result == [{"id": 1}, {"id": 2}]

    @pytest.mark.asyncio
    async def test_get_returns_data_key(self):
        """200 response with {data: [...]} wrapper."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(200, json_data={"data": [{"x": 1}]})
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            result = await client._get.__wrapped__(client, "https://api.test", "/path")
        assert result == [{"x": 1}]

    @pytest.mark.asyncio
    async def test_get_204_returns_empty(self):
        """204 No Content returns empty list."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(204)
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            result = await client._get.__wrapped__(client, "https://api.test", "/path")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_401_raises(self):
        """401 raises ValueError with message about API key."""
        client = WBApiClient("bad-key", project_id=1)
        mock_resp = _mock_response(401)
        with patch("httpx.AsyncClient.get", return_value=mock_resp), pytest.raises(ValueError, match="401"):
            await client._get.__wrapped__(client, "https://api.test", "/path")

    @pytest.mark.asyncio
    async def test_get_429_raises_rate_limit(self):
        """429 raises RateLimitError with Retry-After."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(429, headers={"Retry-After": "30"})
        with patch("httpx.AsyncClient.get", return_value=mock_resp), pytest.raises(RateLimitError) as exc_info:
            await client._get.__wrapped__(client, "https://api.test", "/path")
        assert exc_info.value.retry_after == 30

    @pytest.mark.asyncio
    async def test_get_500_raises(self):
        """500 server error raises ValueError."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(500)
        with patch("httpx.AsyncClient.get", return_value=mock_resp), pytest.raises(ValueError, match="server error"):
            await client._get.__wrapped__(client, "https://api.test", "/path")


# ═══════════════════════════════════════════════════════════════════════════════
# WBApiClient high-level methods
# ═══════════════════════════════════════════════════════════════════════════════


class TestWBApiClientMethods:
    """Tests for get_sales, get_orders, get_finance_report, test_connection.

    These methods use _get internally (which has @retry_with_backoff),
    so we mock at the _get level to bypass the decorator chain.
    """

    @pytest.mark.asyncio
    async def test_get_sales(self):
        """get_sales returns sale data."""
        client = WBApiClient("test-key", project_id=1)
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=[{"saleID": "S1"}]):
            result = await client.get_sales(date_from=date(2024, 1, 1))
        assert len(result) == 1
        assert result[0]["saleID"] == "S1"

    @pytest.mark.asyncio
    async def test_get_orders(self):
        """get_orders returns order data."""
        client = WBApiClient("test-key", project_id=1)
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=[{"orderId": "O1"}]):
            result = await client.get_orders(date_from=date(2024, 1, 1))
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_finance_report(self):
        """get_finance_report returns report data."""
        client = WBApiClient("test-key", project_id=1)
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=[{"rrd_id": 1}]):
            result = await client.get_finance_report(date_from=date(2024, 1, 1), date_to=date(2024, 1, 31))
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_test_connection_valid(self):
        """test_connection returns True on valid key."""
        client = WBApiClient("test-key", project_id=1)
        with patch.object(client, "_get", new_callable=AsyncMock, return_value=[]):
            result = await client.test_connection()
        assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_invalid(self):
        """test_connection returns False on invalid key."""
        client = WBApiClient("bad-key", project_id=1)
        with patch.object(client, "_get", new_callable=AsyncMock, side_effect=ValueError("401")):
            result = await client.test_connection()
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# FBO Supplies methods
# ═══════════════════════════════════════════════════════════════════════════════


class TestFBOSupplies:
    """Tests for FBO supply-related methods.

    These methods have their own @retry_with_backoff + circuit breaker.
    We use __wrapped__ on the outer decorator to call the inner function directly.
    """

    @pytest.mark.asyncio
    async def test_get_fbo_supplies(self):
        """get_fbo_supplies returns structured dict."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(
            200,
            json_data={"next": 100, "supplies": [{"id": "WB-1"}]},
        )
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            # Call the inner unwrapped function (bypass retry decorator)
            fn = client.get_fbo_supplies.__wrapped__
            result = await fn(client)
        assert result["next"] == 100
        assert len(result["supplies"]) == 1

    @pytest.mark.asyncio
    async def test_get_fbo_supply_orders_404(self):
        """get_fbo_supply_orders returns [] on 404."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(404)
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            fn = client.get_fbo_supply_orders.__wrapped__
            result = await fn(client, "WB-NONEXIST")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_fbo_supply_detail_not_found(self):
        """get_fbo_supply returns None on 404."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(404)
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            fn = client.get_fbo_supply.__wrapped__
            result = await fn(client, "WB-NONEXIST")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_warehouses_list(self):
        """get_warehouses_list returns list of warehouses."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(
            200,
            json_data=[{"id": 1, "name": "Коледино"}],
        )
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            fn = client.get_warehouses_list.__wrapped__
            result = await fn(client)
        assert len(result) == 1
        assert result[0]["name"] == "Коледино"

    @pytest.mark.asyncio
    async def test_get_warehouses_list_error_returns_empty(self):
        """get_warehouses_list returns [] on error."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(500)
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            fn = client.get_warehouses_list.__wrapped__
            result = await fn(client)
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# FBW Supplies methods
# ═══════════════════════════════════════════════════════════════════════════════


class TestFBWSupplies:
    """Tests for FBW (Suppliers API) methods."""

    @pytest.mark.asyncio
    async def test_get_fbw_supplies(self):
        """get_fbw_supplies returns list."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(200, json_data=[{"supplyId": 42}])
        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            fn = client.get_fbw_supplies.__wrapped__
            result = await fn(client, date_from="2024-01-01", date_to="2024-03-01")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_fbw_supply_detail_not_found(self):
        """get_fbw_supply_detail returns None on 404."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(404)
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            fn = client.get_fbw_supply_detail.__wrapped__
            result = await fn(client, 999)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_fbw_supply_goods(self):
        """get_fbw_supply_goods returns items list."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(200, json_data=[{"nmID": 123, "quantity": 10}])
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            fn = client.get_fbw_supply_goods.__wrapped__
            result = await fn(client, supply_id=42)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_fbw_supply_goods_404(self):
        """get_fbw_supply_goods returns [] on 404."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(404)
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            fn = client.get_fbw_supply_goods.__wrapped__
            result = await fn(client, supply_id=999)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_fbw_warehouses(self):
        """get_fbw_warehouses returns list."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(200, json_data=[{"ID": 1, "name": "WH-1"}])
        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            fn = client.get_fbw_warehouses.__wrapped__
            result = await fn(client)
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Cards List
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetCardsList:
    """Tests for get_cards_list (cursor-based pagination)."""

    @pytest.mark.asyncio
    async def test_single_page(self):
        """Single page of cards (fewer than limit)."""
        client = WBApiClient("test-key", project_id=1)
        page_data = {
            "cards": [{"nmID": 1}, {"nmID": 2}],
            "cursor": {"updatedAt": "2024-01-01", "nmID": 2},
        }
        mock_resp = _mock_response(200, json_data=page_data)
        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            fn = client.get_cards_list.__wrapped__
            result = await fn(client, limit=100)
        # Only 2 cards < limit(100), so single page
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty_cards(self):
        """No cards at all."""
        client = WBApiClient("test-key", project_id=1)
        mock_resp = _mock_response(200, json_data={"cards": []})
        with patch("httpx.AsyncClient.post", return_value=mock_resp):
            fn = client.get_cards_list.__wrapped__
            result = await fn(client, limit=100)
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# parse_wb_cards_to_nomenclature
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseWBCards:
    """Tests for parse_wb_cards_to_nomenclature."""

    def test_basic_card(self):
        """Parse a single card with one size/barcode."""
        cards = [
            {
                "nmID": 12345,
                "imtID": 100,
                "brand": "TestBrand",
                "subjectName": "T-shirt",
                "vendorCode": "ART-001",
                "dimensions": {"length": 30, "width": 20, "height": 10},
                "sizes": [{"skus": ["2000000000001"]}],
            }
        ]
        result = parse_wb_cards_to_nomenclature(cards)
        assert len(result) == 1
        assert result[0]["barcode"] == "2000000000001"
        assert result[0]["brand"] == "TestBrand"
        assert result[0]["subject"] == "T-shirt"
        assert result[0]["article_seller"] == "ART-001"
        assert result[0]["article_wb"] == 12345
        assert result[0]["volume_l"] == 6.0  # 30*20*10/1000

    def test_multiple_sizes(self):
        """One card with multiple sizes should produce multiple entries."""
        cards = [
            {
                "nmID": 1,
                "imtID": 2,
                "brand": "B",
                "subjectName": "S",
                "vendorCode": "V",
                "dimensions": {},
                "sizes": [
                    {"skus": ["BC1"]},
                    {"skus": ["BC2"]},
                ],
            }
        ]
        result = parse_wb_cards_to_nomenclature(cards)
        assert len(result) == 2
        barcodes = {r["barcode"] for r in result}
        assert barcodes == {"BC1", "BC2"}

    def test_dedup_barcodes(self):
        """Duplicate barcodes across cards should be deduplicated."""
        cards = [
            {
                "nmID": 1,
                "imtID": 2,
                "brand": "",
                "subjectName": "",
                "vendorCode": "",
                "dimensions": {},
                "sizes": [{"skus": ["SAME_BC"]}],
            },
            {
                "nmID": 3,
                "imtID": 4,
                "brand": "",
                "subjectName": "",
                "vendorCode": "",
                "dimensions": {},
                "sizes": [{"skus": ["SAME_BC"]}],
            },
        ]
        result = parse_wb_cards_to_nomenclature(cards)
        assert len(result) == 1

    def test_empty_cards(self):
        """Empty list returns empty."""
        assert parse_wb_cards_to_nomenclature([]) == []

    def test_missing_dimensions(self):
        """Card without dimensions gets volume_l = 0."""
        cards = [
            {
                "nmID": 1,
                "imtID": 2,
                "brand": "",
                "subjectName": "",
                "vendorCode": "",
                "sizes": [{"skus": ["BC1"]}],
            }
        ]
        result = parse_wb_cards_to_nomenclature(cards)
        assert result[0]["volume_l"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# parse_wb_sales_to_payouts
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseWBSales:
    """Tests for parse_wb_sales_to_payouts."""

    def test_basic_sale(self):
        """Convert a normal sale to payout format."""
        sales = [{"saleID": "S001", "totalPrice": 1500, "date": "2024-01-15T10:00:00"}]
        result = parse_wb_sales_to_payouts(sales)
        assert len(result) == 1
        assert result[0]["request_id"] == "S001"
        assert result[0]["amount_rub"] == Decimal("1500")
        assert result[0]["currency"] == "RUB"

    def test_zero_price_skipped(self):
        """Sales with totalPrice <= 0 are skipped."""
        sales = [{"saleID": "S001", "totalPrice": 0}]
        result = parse_wb_sales_to_payouts(sales)
        assert result == []

    def test_dedup_sale_ids(self):
        """Duplicate saleID entries are deduplicated."""
        sales = [
            {"saleID": "S001", "totalPrice": 100},
            {"saleID": "S001", "totalPrice": 100},
        ]
        result = parse_wb_sales_to_payouts(sales)
        assert len(result) == 1

    def test_missing_sale_id_skipped(self):
        """Sales without saleID are skipped."""
        sales = [{"totalPrice": 100}]
        result = parse_wb_sales_to_payouts(sales)
        assert result == []

    def test_empty_sales(self):
        """Empty input returns empty."""
        assert parse_wb_sales_to_payouts([]) == []


# ═══════════════════════════════════════════════════════════════════════════════
# get_acceptance_options — chunking
# Bug (2026-06): ~405 barcodes in one 5000-chunk timed out (TIMEOUT=30) → 3× retry
# → 500 «Failed to fetch». Fix: chunk ≤150 so each response stays fast.
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetAcceptanceOptionsChunking:
    """get_acceptance_options must split large barcode sets into small chunks."""

    @staticmethod
    def _echo_post(url, headers=None, json=None):
        # Echo one result entry per barcode in the chunk → merging is verifiable.
        return _mock_response(200, json_data={"result": [{"barcode": it["barcode"]} for it in (json or [])]})

    @pytest.mark.asyncio
    async def test_chunks_405_into_calls_of_at_most_150(self):
        """405 barcodes → 3 POSTs of ≤150 each, results merged."""
        client = WBApiClient("test-key", project_id=1)
        items = [{"quantity": 1, "barcode": f"BC{i}"} for i in range(405)]
        with patch("httpx.AsyncClient.post", side_effect=self._echo_post) as mock_post:
            fn = client.get_acceptance_options.__wrapped__
            result = await fn(client, items)
        sizes = [len(call.kwargs["json"]) for call in mock_post.call_args_list]
        assert mock_post.call_count == 3
        assert max(sizes) <= 150
        assert sum(sizes) == 405
        assert len(result["result"]) == 405  # all chunks merged

    @pytest.mark.asyncio
    async def test_small_set_single_call(self):
        """≤150 barcodes → single POST."""
        client = WBApiClient("test-key", project_id=1)
        items = [{"quantity": 1, "barcode": f"BC{i}"} for i in range(120)]
        with patch("httpx.AsyncClient.post", side_effect=self._echo_post) as mock_post:
            fn = client.get_acceptance_options.__wrapped__
            await fn(client, items)
        assert mock_post.call_count == 1
