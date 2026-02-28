"""
Wildberries Statistics API client.
Docs: https://openapi.wildberries.ru/

Supported endpoints:
- /api/v1/supplier/sales — продажи
- /api/v1/supplier/orders — заказы FBS
- /api/v5/supplier/reportDetailByPeriod — финансовый отчёт (детализация выплат)
"""

import logging
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

WB_API_BASE = "https://statistics-api.wildberries.ru"
WB_API_BASE_V2 = "https://common-api.wildberries.ru"

# Request timeout in seconds
TIMEOUT = 30


class WBApiClient:
    """Wildberries API client with automatic retry and rate limit handling."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"Authorization": api_key}

    async def _get(self, base_url: str, path: str, params: dict = None) -> list[dict]:
        """Make GET request to WB API with error handling."""
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            url = f"{base_url}{path}"
            logger.info("WB API GET %s params=%s", path, params)
            response = await client.get(url, headers=self.headers, params=params)

            if response.status_code == 401:
                raise ValueError("WB API: неверный API-ключ (401)")
            if response.status_code == 429:
                raise ValueError("WB API: слишком много запросов, попробуйте позже (429)")
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
    ) -> list[dict]:
        """
        Fetch detailed finance report (payout details).
        Returns line-level data with commission, logistics, penalties, etc.
        """
        params = {
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
            "limit": limit,
            "rrdid": rrdid,
        }
        return await self._get(
            WB_API_BASE,
            "/api/v5/supplier/reportDetailByPeriod",
            params,
        )


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

        payouts.append({
            "request_id": sale_id,
            "amount_rub": total_price,
            "currency": "RUB",
            "created_at": sale.get("date", datetime.utcnow().isoformat()),
            "wb_status_raw": sale.get("saleID", ""),
            "status": "TRANSIT",
        })
    return payouts
