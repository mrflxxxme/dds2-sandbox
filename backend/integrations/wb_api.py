"""
Wildberries Statistics API client.
Docs: https://openapi.wildberries.ru/

Supported endpoints:
- /api/v1/supplier/sales — продажи
- /api/v1/supplier/orders — заказы FBS
- /api/v5/supplier/reportDetailByPeriod — финансовый отчёт (детализация выплат)

Resilience:
- Circuit breaker: stops calling WB API after 5 consecutive failures (60s cooldown)
- Exponential backoff: retries 429/5xx errors up to 3 times
"""

from datetime import date, timedelta
from decimal import Decimal

import httpx
import structlog

from backend.integrations.resilience import (
    CircuitBreaker,
    RateLimitError,
    retry_with_backoff,
)
from backend.utils.time import utcnow

logger = structlog.get_logger("dds.wb_api")

# ─── WB API Base URLs (configurable per version) ───────────────────────────
WB_API_BASE = "https://statistics-api.wildberries.ru"
WB_API_BASE_V2 = "https://common-api.wildberries.ru"
WB_CONTENT_API_BASE = "https://content-api.wildberries.ru"

# Request timeout in seconds
TIMEOUT = 30

# Shared circuit breaker — only 5xx errors trip it, 429 is excluded
_wb_circuit = CircuitBreaker(
    name="wildberries",
    failure_threshold=5,
    recovery_timeout=120.0,  # 2 min for real server failures
    exclude_errors=(RateLimitError,),
)


class WBApiClient:
    """Wildberries API client with circuit breaker, retry, and rate limit handling."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"Authorization": api_key}

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def _get(self, base_url: str, path: str, params: dict = None) -> list[dict]:
        """Make GET request to WB API with circuit breaker and retry."""
        async with _wb_circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                url = f"{base_url}{path}"
                logger.info("wb_api.request", method="GET", path=path, params=params)
                response = await client.get(url, headers=self.headers, params=params)

                if response.status_code == 401:
                    raise ValueError("WB API: неверный API-ключ (401)")
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    raise RateLimitError(
                        "WB API rate limited (429)",
                        retry_after=retry_after,
                    )
                if response.status_code >= 500:
                    raise ValueError(f"WB API server error: HTTP {response.status_code}")
                if response.status_code != 200:
                    raise ValueError(f"WB API error: HTTP {response.status_code} — {response.text[:200]}")

                data = response.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "data" in data:
                    return data["data"] if isinstance(data["data"], list) else [data["data"]]
                return [data] if data else []

    async def test_connection(self) -> bool:
        """Test if the API key is valid by fetching a minimal sales request."""
        try:
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            await self._get(WB_API_BASE, "/api/v1/supplier/sales", {"dateFrom": yesterday})
            return True
        except ValueError:
            return False

    async def get_sales(
        self,
        date_from: date,
        flag: int = 0,
    ) -> list[dict]:
        """
        Fetch sales data.
        flag=0: data since date_from; flag=1: only updated since date_from.
        """
        params = {"dateFrom": date_from.isoformat(), "flag": flag}
        return await self._get(WB_API_BASE, "/api/v1/supplier/sales", params)

    async def get_orders(
        self,
        date_from: date,
        flag: int = 0,
    ) -> list[dict]:
        """Fetch FBS orders."""
        params = {"dateFrom": date_from.isoformat(), "flag": flag}
        return await self._get(WB_API_BASE, "/api/v1/supplier/orders", params)

    async def get_finance_report(
        self,
        date_from: date,
        date_to: date,
        limit: int = 100000,
        rrdid: int = 0,
        period: str | None = None,
    ) -> list[dict]:
        """
        Fetch detailed finance report (payout details).
        Returns line-level data with commission, logistics, penalties, etc.

        period: "daily" for daily reports, None for weekly (default).
        """
        params = {
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
            "limit": limit,
            "rrdid": rrdid,
        }
        if period:
            params["period"] = period
        return await self._get(
            WB_API_BASE,
            "/api/v5/supplier/reportDetailByPeriod",
            params,
        )

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_cards_list(self, limit: int = 100) -> list[dict]:
        """
        Fetch ALL product cards using cursor-based pagination.
        WB Content API: POST /content/v2/get/cards/list
        Max 100 per page. Continues until all pages fetched.
        Returns flattened list of all cards.
        """
        all_cards = []
        cursor = {"limit": limit}

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            while True:
                body = {
                    "settings": {
                        "cursor": cursor,
                        "filter": {"withPhoto": -1},
                    }
                }
                async with _wb_circuit:
                    url = f"{WB_CONTENT_API_BASE}/content/v2/get/cards/list"
                    logger.info("wb_api.request", method="POST", path="cards/list", cursor=cursor)
                    response = await client.post(url, headers=self.headers, json=body)

                    if response.status_code == 401:
                        raise ValueError("WB API: неверный API-ключ (401)")
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", "60"))
                        raise RateLimitError(
                            "WB API rate limited (429)",
                            retry_after=retry_after,
                        )
                    if response.status_code >= 500:
                        raise ValueError(f"WB Content API server error: HTTP {response.status_code}")
                    if response.status_code != 200:
                        raise ValueError(f"WB Content API error: HTTP {response.status_code} — {response.text[:200]}")

                    data = response.json()
                    cards = data.get("cards", [])
                    if not cards:
                        break

                    all_cards.extend(cards)

                    # Stop if this was the last page (fewer cards than limit)
                    if len(cards) < limit:
                        break

                    # Cursor pagination — use nmID and updatedAt for next page
                    next_cursor = data.get("cursor", {})
                    cursor = {
                        "updatedAt": next_cursor.get("updatedAt"),
                        "nmID": next_cursor.get("nmID"),
                        "limit": limit,
                    }

        logger.info("wb_api.cards_fetched", total=len(all_cards))
        return all_cards


def parse_wb_cards_to_nomenclature(cards: list[dict]) -> list[dict]:
    """
    Transform WB Content API cards response into our Nomenclature format.
    One card may have multiple barcodes (sizes), so we flatten them.

    WB card → Nomenclature mapping:
      - nmID → article_wb
      - brand → brand
      - subjectName → subject
      - vendorCode → article_seller
      - sizes[].skus[] → barcode (one row per barcode)
      - dimensions.length × width × height / 1000 → volume_l
    """
    nomenclature = []
    seen_barcodes = set()

    for card in cards:
        nm_id = card.get("nmID")
        brand = card.get("brand", "")
        subject = card.get("subjectName", "")
        vendor_code = card.get("vendorCode", "")

        # Calculate volume in liters from dimensions (cm → liters)
        dims = card.get("dimensions", {})
        length_cm = dims.get("length", 0) or 0
        width_cm = dims.get("width", 0) or 0
        height_cm = dims.get("height", 0) or 0
        volume_l = (length_cm * width_cm * height_cm) / 1000.0  # cm³ → liters

        # Each size has its own barcodes (skus)
        sizes = card.get("sizes", [])
        for size in sizes:
            skus = size.get("skus", [])
            for barcode in skus:
                barcode = str(barcode).strip()
                if not barcode or barcode in seen_barcodes:
                    continue
                seen_barcodes.add(barcode)

                nomenclature.append(
                    {
                        "barcode": barcode,
                        "brand": brand,
                        "subject": subject,
                        "article_seller": vendor_code,
                        "article_wb": nm_id,
                        "volume_l": round(volume_l, 4),
                    }
                )

    return nomenclature


def parse_wb_sales_to_payouts(sales: list[dict]) -> list[dict]:
    """
    Transform WB sales API response into our wb_payouts format.
    Groups by saleID and maps fields.
    """
    payouts = []
    seen_ids = set()
    for sale in sales:
        sale_id = sale.get("saleID", "")
        if not sale_id or sale_id in seen_ids:
            continue
        seen_ids.add(sale_id)

        total_price = Decimal(str(sale.get("totalPrice", 0)))
        if total_price <= 0:
            continue

        payouts.append(
            {
                "request_id": sale_id,
                "amount_rub": total_price,
                "currency": "RUB",
                "created_at": sale.get("date", utcnow().isoformat()),
                "wb_status_raw": sale.get("saleID", ""),
                "status": "TRANSIT",
            }
        )
    return payouts
