"""
Wildberries API client.
Docs: https://openapi.wildberries.ru/

Supported endpoints:
- /api/v1/supplier/sales — продажи
- /api/v1/supplier/orders — заказы FBS
- /api/v5/supplier/reportDetailByPeriod — финансовый отчёт (детализация выплат)
- /api/v3/supplies — FBO поставки (Marketplace API, legacy)
- /api/v3/supplies/{id}/orders — позиции FBO поставки (legacy)
- /api/v1/supplies — FBW поставки (Suppliers API)
- /api/v1/supplies/{id} — детали FBW поставки
- /api/v1/supplies/{id}/goods — товары FBW поставки
- /api/v1/warehouses — склады WB (Suppliers API)
- /api/v1/acceptance/options — доступность приёмки по баркодам (Suppliers API)

Resilience:
- Per-project circuit breaker: stops calling WB API after 5 consecutive failures (120s cooldown)
- Exponential backoff: retries 429/5xx errors up to 3 times
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import structlog

from backend.integrations.resilience import (
    CircuitBreakerRegistry,
    RateLimitError,
    retry_with_backoff,
)
from backend.utils.time import utcnow

logger = structlog.get_logger("dds.wb_api")

# ─── WB API Base URLs (configurable per version) ───────────────────────────
WB_API_BASE = "https://statistics-api.wildberries.ru"
WB_API_BASE_V2 = "https://common-api.wildberries.ru"
WB_CONTENT_API_BASE = "https://content-api.wildberries.ru"
WB_MARKETPLACE_API_BASE = "https://marketplace-api.wildberries.ru"
WB_SUPPLIERS_API_BASE = "https://supplies-api.wildberries.ru"
WB_SELLER_ANALYTICS_API_BASE = "https://seller-analytics-api.wildberries.ru"
WB_PRICES_API_BASE = "https://discounts-prices-api.wildberries.ru"
WB_FEEDBACKS_API_BASE = "https://feedbacks-api.wildberries.ru"

# Request timeout in seconds
TIMEOUT = 30


async def check_feedbacks_scope(api_key: str) -> str:
    """Есть ли у токена категория «Вопросы и отзывы». Возвращает "ok" | "no_scope" | "unknown".

    Лёгкий пробник — GET /api/v1/feedbacks?take=1: валидирует именно feedbacks-scope.
    "unknown" (429/5xx/сеть) НЕ считать невалидным ключом — зеркало check_content_scope.
    """
    params = {"isAnswered": "true", "take": "1", "skip": "0"}
    url = f"{WB_FEEDBACKS_API_BASE}/api/v1/feedbacks"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers={"Authorization": api_key}, params=params)
    except httpx.HTTPError as e:
        logger.warning("feedbacks_scope_check.network_error", error=str(e))
        return "unknown"
    if resp.status_code in (200, 204):
        return "ok"
    if resp.status_code in (401, 403):
        logger.info("feedbacks_scope_check.rejected", status=resp.status_code)
        return "no_scope"
    logger.warning("feedbacks_scope_check.transient", status=resp.status_code)
    return "unknown"

# Per-project circuit breakers — one project's failures don't block others
_wb_circuits = CircuitBreakerRegistry(
    name_prefix="wb",
    failure_threshold=5,
    recovery_timeout=120.0,  # 2 min for real server failures
    exclude_errors=(RateLimitError,),
)


class WBApiClient:
    """Wildberries API client with per-project circuit breaker, retry, and rate limit handling."""

    def __init__(self, api_key: str, project_id: int | None = None):
        self.api_key = api_key
        self.project_id = project_id
        self.headers = {"Authorization": api_key}
        self._circuit = _wb_circuits.get(project_id)

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def _get(self, base_url: str, path: str, params: dict = None) -> list[dict]:
        """Make GET request to WB API with circuit breaker and retry."""
        async with self._circuit:
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
                if response.status_code == 204:
                    return []  # No data for requested period (normal for current day)
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

    # ─── Warehouse measurements / dimension penalties (Analytics API) ────────

    @staticmethod
    def _rfc3339(d: date) -> str:
        """WB analytics measurements require RFC3339 with trailing Z."""
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def _get_analytics_reports(self, path: str, params: dict) -> list[dict]:
        """
        GET a seller-analytics report whose payload is {"data": {"reports": [...]}}.
        Single page — pagination is handled by the caller via limit/offset.
        """
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                url = f"{WB_SELLER_ANALYTICS_API_BASE}{path}"
                logger.info("wb_api.request", method="GET", path=path, params=params)
                response = await client.get(url, headers=self.headers, params=params)

                if response.status_code == 401:
                    raise ValueError("WB API: неверный API-ключ (401)")
                if response.status_code == 403:
                    raise ValueError("WB API: у ключа нет доступа к категории «Аналитика» (403)")
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    raise RateLimitError("WB API rate limited (429)", retry_after=retry_after)
                if response.status_code >= 500:
                    raise ValueError(f"WB Analytics API server error: HTTP {response.status_code}")
                if response.status_code == 204:
                    return []
                if response.status_code != 200:
                    raise ValueError(f"WB Analytics API error: HTTP {response.status_code} — {response.text[:200]}")

                data = response.json()
                reports = (data or {}).get("data", {}).get("reports")
                return reports if isinstance(reports, list) else []

    async def _paginate_analytics(
        self, path: str, date_from: date, date_to: date, page_size: int = 1000, max_pages: int = 50
    ) -> list[dict]:
        """Walk offset pages of a seller-analytics report until a short page is returned."""
        rows: list[dict] = []
        offset = 0
        for _ in range(max_pages):
            params = {
                "dateFrom": self._rfc3339(date_from),
                "dateTo": self._rfc3339(date_to),
                "limit": page_size,
                "offset": offset,
            }
            page = await self._get_analytics_reports(path, params)
            rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return rows

    async def get_warehouse_measurements(self, date_from: date, date_to: date) -> list[dict]:
        """
        Контрольные замеры товаров на складах WB за период.
        WB Analytics API: GET /api/analytics/v1/warehouse-measurements
        Row: {nmId, subjectName, dimId, volume, width, length, height, photoUrls[], dt}
        """
        return await self._paginate_analytics(
            "/api/analytics/v1/warehouse-measurements", date_from, date_to
        )

    async def get_measurement_penalties(self, date_from: date, date_to: date) -> list[dict]:
        """
        Удержания за занижение габаритов (по результатам замеров) за период.
        WB Analytics API: GET /api/analytics/v1/measurement-penalties
        """
        return await self._paginate_analytics(
            "/api/analytics/v1/measurement-penalties", date_from, date_to
        )

    # ─── FBO Supplies (Marketplace API) ─────────────────────────────────────

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_fbo_supplies(self, next: int = 0, limit: int = 1000) -> dict:
        """
        Fetch FBO supplies list.
        WB Marketplace API: GET /api/v3/supplies

        Returns: {next: int, supplies: [dict]}
        """
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                params = {"limit": limit, "next": next}
                url = f"{WB_MARKETPLACE_API_BASE}/api/v3/supplies"
                logger.info("wb_api.request", method="GET", path="/api/v3/supplies", params=params)
                response = await client.get(url, headers=self.headers, params=params)

                if response.status_code == 401:
                    raise ValueError("WB API: неверный API-ключ (401)")
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    raise RateLimitError("WB API rate limited (429)", retry_after=retry_after)
                if response.status_code >= 500:
                    raise ValueError(f"WB Marketplace API server error: HTTP {response.status_code}")
                if response.status_code != 200:
                    raise ValueError(f"WB Marketplace API error: HTTP {response.status_code} — {response.text[:200]}")

                data = response.json()
                return {
                    "next": data.get("next", 0),
                    "supplies": data.get("supplies", []),
                }

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_fbo_supply_orders(self, supply_id: str) -> list[dict]:
        """
        Fetch orders (items) for a specific FBO supply.
        WB Marketplace API: GET /api/v3/supplies/{supplyId}/orders
        """
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                url = f"{WB_MARKETPLACE_API_BASE}/api/v3/supplies/{supply_id}/orders"
                logger.info("wb_api.request", method="GET", path=f"/api/v3/supplies/{supply_id}/orders")
                response = await client.get(url, headers=self.headers)

                if response.status_code == 401:
                    raise ValueError("WB API: неверный API-ключ (401)")
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    raise RateLimitError("WB API rate limited (429)", retry_after=retry_after)
                if response.status_code >= 500:
                    raise ValueError(f"WB Marketplace API server error: HTTP {response.status_code}")
                if response.status_code == 404:
                    return []
                if response.status_code != 200:
                    raise ValueError(f"WB Marketplace API error: HTTP {response.status_code} — {response.text[:200]}")

                data = response.json()
                if isinstance(data, list):
                    return data
                return data.get("orders", [])

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_fbo_supply(self, supply_id: str) -> dict | None:
        """
        Fetch single FBO supply details.
        WB Marketplace API: GET /api/v3/supplies/{supplyId}
        """
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                url = f"{WB_MARKETPLACE_API_BASE}/api/v3/supplies/{supply_id}"
                logger.info("wb_api.request", method="GET", path=f"/api/v3/supplies/{supply_id}")
                response = await client.get(url, headers=self.headers)

                if response.status_code == 404:
                    return None
                if response.status_code == 401:
                    raise ValueError("WB API: неверный API-ключ (401)")
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    raise RateLimitError("WB API rate limited (429)", retry_after=retry_after)
                if response.status_code >= 500:
                    raise ValueError(f"WB Marketplace API server error: HTTP {response.status_code}")
                if response.status_code != 200:
                    raise ValueError(f"WB Marketplace API error: HTTP {response.status_code} — {response.text[:200]}")

                return response.json()

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_warehouses_list(self) -> list[dict]:
        """
        Fetch WB warehouses (offices) for mapping destinationOfficeId → name.
        WB Marketplace API: GET /api/v3/offices
        Returns list of {id, name, city, address, ...}
        """
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                url = f"{WB_MARKETPLACE_API_BASE}/api/v3/offices"
                logger.info("wb_api.request", method="GET", path="/api/v3/offices")
                response = await client.get(url, headers=self.headers)

                if response.status_code != 200:
                    logger.warning("wb_api.offices_error", status=response.status_code)
                    return []

                data = response.json()
                return data if isinstance(data, list) else []

    # ─── FBW Supplies (Suppliers API) ────────────────────────────────────────

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_fbw_supplies(
        self,
        date_from: str,
        date_to: str,
        status_ids: list[int] | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict]:
        """
        Fetch FBW supplies list.
        Suppliers API: POST /api/v1/supplies
        Rate limit: 6 req/min — caller must throttle!

        Args:
            date_from: ISO date string (e.g. "2025-01-01")
            date_to: ISO date string (e.g. "2026-04-01")
            status_ids: filter by statuses [1..6], None = all
            limit: page size (default 1000)
            offset: pagination offset

        Returns: list of supply dicts
        """
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                body = {
                    "dates": [{"from": date_from, "till": date_to, "type": "createDate"}],
                }
                if status_ids:
                    body["statusIDs"] = status_ids

                params = {"limit": limit, "offset": offset}
                url = f"{WB_SUPPLIERS_API_BASE}/api/v1/supplies"
                logger.info(
                    "wb_api.request",
                    method="POST",
                    path="/api/v1/supplies",
                    params=params,
                    body_dates=f"{date_from}..{date_to}",
                )
                response = await client.post(
                    url,
                    headers=self.headers,
                    json=body,
                    params=params,
                )

                if response.status_code == 401:
                    raise ValueError("WB API: неверный API-ключ (401)")
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    raise RateLimitError("WB Suppliers API rate limited (429)", retry_after=retry_after)
                if response.status_code >= 500:
                    raise ValueError(f"WB Suppliers API server error: HTTP {response.status_code}")
                if response.status_code != 200:
                    raise ValueError(f"WB Suppliers API error: HTTP {response.status_code} — {response.text[:200]}")

                data = response.json()
                return data if isinstance(data, list) else []

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_fbw_supply_detail(self, supply_id: int) -> dict | None:
        """
        Fetch single FBW supply details (warehouse, quantities, acceptance cost).
        Suppliers API: GET /api/v1/supplies/{supply_id}
        Rate limit: 6 req/min — caller must throttle!
        """
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                url = f"{WB_SUPPLIERS_API_BASE}/api/v1/supplies/{supply_id}"
                logger.info("wb_api.request", method="GET", path=f"/api/v1/supplies/{supply_id}")
                response = await client.get(url, headers=self.headers)

                if response.status_code == 404:
                    return None
                if response.status_code == 401:
                    raise ValueError("WB API: неверный API-ключ (401)")
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    raise RateLimitError("WB Suppliers API rate limited (429)", retry_after=retry_after)
                if response.status_code >= 500:
                    raise ValueError(f"WB Suppliers API server error: HTTP {response.status_code}")
                if response.status_code != 200:
                    raise ValueError(f"WB Suppliers API error: HTTP {response.status_code} — {response.text[:200]}")

                return response.json()

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_fbw_supply_goods(
        self,
        supply_id: int,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """
        Fetch items (goods) for a specific FBW supply.
        Suppliers API: GET /api/v1/supplies/{supply_id}/goods
        Rate limit: 6 req/min — caller must throttle!
        """
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                params = {"limit": limit, "offset": offset}
                url = f"{WB_SUPPLIERS_API_BASE}/api/v1/supplies/{supply_id}/goods"
                logger.info(
                    "wb_api.request",
                    method="GET",
                    path=f"/api/v1/supplies/{supply_id}/goods",
                )
                response = await client.get(url, headers=self.headers, params=params)

                if response.status_code == 404:
                    return []
                if response.status_code == 401:
                    raise ValueError("WB API: неверный API-ключ (401)")
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    raise RateLimitError("WB Suppliers API rate limited (429)", retry_after=retry_after)
                if response.status_code >= 500:
                    raise ValueError(f"WB Suppliers API server error: HTTP {response.status_code}")
                if response.status_code != 200:
                    raise ValueError(f"WB Suppliers API error: HTTP {response.status_code} — {response.text[:200]}")

                data = response.json()
                return data if isinstance(data, list) else []

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_acceptance_options(self, items: list[dict]) -> dict:
        """
        Check WB FBO acceptance availability for a batch of (barcode, quantity).
        Suppliers API: POST /api/v1/acceptance/options

        Body: list of <=5000 entries [{"quantity": int, "barcode": str}, ...]
        Returns: {"result": [{"barcode", "warehouses": [{warehouseID, canBox,
                  canMonopallet, canSupersafe, isBoxOnPallet}], "error"?}]}

        Rate limit: 6 req/min — caller MUST batch + cache. We slice into ≤150-barcode
        chunks: WB computes options across every warehouse per barcode, so a single
        large body (~405 barcodes) overruns TIMEOUT (30s) → 500. ~405 → 3 chunks,
        well within the 6 req/min budget.
        """
        all_results: list[dict] = []
        chunk_size = 150  # ≤150: larger bodies time out server-side (bug 2026-06)
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                for i in range(0, len(items), chunk_size):
                    chunk = items[i : i + chunk_size]
                    url = f"{WB_SUPPLIERS_API_BASE}/api/v1/acceptance/options"
                    logger.info(
                        "wb_api.request",
                        method="POST",
                        path="/api/v1/acceptance/options",
                        items_count=len(chunk),
                    )
                    response = await client.post(url, headers=self.headers, json=chunk)

                    if response.status_code == 401:
                        raise ValueError("WB API: неверный API-ключ (401)")
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", "60"))
                        raise RateLimitError(
                            "WB Suppliers API rate limited (429)",
                            retry_after=retry_after,
                        )
                    if response.status_code >= 500:
                        raise ValueError(f"WB Suppliers API server error: HTTP {response.status_code}")
                    if response.status_code != 200:
                        raise ValueError(f"WB Suppliers API error: HTTP {response.status_code} — {response.text[:200]}")

                    data = response.json()
                    all_results.extend(data.get("result", []))
        return {"result": all_results}

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_acceptance_coefficients(self, warehouse_ids: list[int] | None = None) -> list[dict]:
        """
        Fetch acceptance coefficients for next 14 days per (warehouse, package_type).
        Common API: GET /api/tariffs/v1/acceptance/coefficients

        Returns: [{
          "date": "2026-05-09T00:00:00Z",
          "coefficient": -1 | 0 | 1 | 2 | 3 | 5 | ...,
          "warehouseID": int,
          "warehouseName": str,
          "allowUnload": bool,
          "boxTypeID": 2 (КОРОБА) | 5 (МОНОПАЛЛЕТЫ) | 6 (СУПЕРСЕЙФ),
          "isSortingCenter": bool,
          ...
        }, ...]

        Доступность: coefficient ∈ {0, 1} И allowUnload=true → бесплатно.
                     coefficient >= 2 И allowUnload=true → платно (×N к базе).
                     coefficient == -1 ИЛИ allowUnload=false → закрыто.

        Rate limit: 6 req/min — caller MUST cache (мы делаем 1 час по умолчанию).
        Migrated 2025-01-30 from supplies-api.wildberries.ru/api/v1/acceptance/coefficients.
        """
        url = f"{WB_API_BASE_V2}/api/tariffs/v1/acceptance/coefficients"
        params = {}
        if warehouse_ids:
            params["warehouseIDs"] = ",".join(str(wid) for wid in warehouse_ids)
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                logger.info("wb_api.request", method="GET", path="/api/tariffs/v1/acceptance/coefficients")
                response = await client.get(url, headers=self.headers, params=params or None)

                if response.status_code == 401:
                    raise ValueError("WB API: неверный API-ключ (401) — нужен scope «Поставки»")
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    raise RateLimitError(
                        "WB Common API rate limited (429)",
                        retry_after=retry_after,
                    )
                if response.status_code >= 500:
                    raise ValueError(f"WB Common API server error: HTTP {response.status_code}")
                if response.status_code != 200:
                    logger.warning(
                        "wb_api.acceptance_coefficients_error",
                        status=response.status_code,
                        body=response.text[:200],
                    )
                    return []

                data = response.json()
                return data if isinstance(data, list) else []

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_fbw_warehouses(self) -> list[dict]:
        """
        Fetch WB warehouses (Suppliers API).
        GET /api/v1/warehouses
        Returns: [{ID, name, address}, ...]
        """
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                url = f"{WB_SUPPLIERS_API_BASE}/api/v1/warehouses"
                logger.info("wb_api.request", method="GET", path="/api/v1/warehouses")
                response = await client.get(url, headers=self.headers)

                if response.status_code != 200:
                    logger.warning("wb_api.fbw_warehouses_error", status=response.status_code)
                    return []

                data = response.json()
                return data if isinstance(data, list) else []

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
                async with self._circuit:
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

    # ─── Prices & Discounts (Discounts-Prices API) ──────────────────────────
    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_prices(self, limit: int = 1000) -> list[dict]:
        """
        Текущие цены витрины по всем товарам (offset-пагинация).
        Discounts-Prices API: GET /api/v2/list/goods/filter?limit=&offset=

        Возвращает плоский список goods-объектов:
          {"nmID": int, "vendorCode": str, "discount": int,
           "sizes": [{"price": int, "discountedPrice": int, ...}],
           "currencyIsoCode4217": "RUB", ...}
        Повторяет запросы пока listGoods непустой (offset += limit).
        """
        all_goods: list[dict] = []
        offset = 0

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            while True:
                params = {"limit": limit, "offset": offset}
                async with self._circuit:
                    url = f"{WB_PRICES_API_BASE}/api/v2/list/goods/filter"
                    logger.info("wb_api.request", method="GET", path="prices/list/goods/filter", offset=offset)
                    response = await client.get(url, headers=self.headers, params=params)

                    if response.status_code == 401:
                        raise ValueError("WB API: неверный API-ключ (401) — нужен scope «Цены и скидки»")
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", "60"))
                        raise RateLimitError("WB Prices API rate limited (429)", retry_after=retry_after)
                    if response.status_code >= 500:
                        raise ValueError(f"WB Prices API server error: HTTP {response.status_code}")
                    if response.status_code != 200:
                        raise ValueError(
                            f"WB Prices API error: HTTP {response.status_code} — {response.text[:200]}"
                        )

                    data = response.json()
                    goods = (data.get("data") or {}).get("listGoods") or []
                    if not goods:
                        break
                    all_goods.extend(goods)
                    if len(goods) < limit:
                        break
                    offset += limit

        logger.info("wb_api.prices_fetched", total=len(all_goods))
        return all_goods

    # ─── Goods Returns (Seller Analytics API) ───────────────────────────────
    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_goods_returns(self, date_from: date, date_to: date) -> list[dict]:
        """
        Fetch goods-returns report (возвраты товаров на ПВЗ).
        WB Seller Analytics API: GET /api/v1/analytics/goods-return
        Rate limit: 1 req/min, max 31-day window.
        Returns list of raw report rows (camelCase keys preserved).
        """
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                params = {
                    "dateFrom": date_from.isoformat(),
                    "dateTo": date_to.isoformat(),
                }
                url = f"{WB_SELLER_ANALYTICS_API_BASE}/api/v1/analytics/goods-return"
                logger.info(
                    "wb_api.request",
                    method="GET",
                    path="/api/v1/analytics/goods-return",
                    params=params,
                )
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
                    raise ValueError(f"WB Seller Analytics API server error: HTTP {response.status_code}")
                if response.status_code == 204:
                    return []
                if response.status_code != 200:
                    raise ValueError(
                        f"WB Seller Analytics API error: HTTP {response.status_code} — {response.text[:200]}"
                    )

                data = response.json()
                # Response: {"report": [...]} or occasionally a bare list.
                if isinstance(data, dict) and "report" in data:
                    report = data["report"]
                    return report if isinstance(report, list) else []
                if isinstance(data, list):
                    return data
                return []

    # ─── Feedbacks / Reviews (Feedbacks API) ────────────────────────────────
    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_feedbacks(
        self,
        is_answered: bool = False,
        take: int = 100,
        skip: int = 0,
    ) -> dict:
        """
        Отзывы покупателей (feedbacks) по товарам продавца.
        WB Feedbacks API: GET /api/v1/feedbacks?isAnswered=&take=&skip=
        take ∈ [1..5000], skip ≥ 0. Нужен scope «Вопросы и отзывы».

        Возвращает внутренний data-dict:
          {countUnanswered, countArchive, feedbacks: [
             {id, text, pros, cons, productValuation, createdDate, userName,
              answer, productDetails: {nmId, productName, supplierArticle, brandName}}
          ]}
        """
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                params = {
                    "isAnswered": str(is_answered).lower(),
                    "take": take,
                    "skip": skip,
                }
                url = f"{WB_FEEDBACKS_API_BASE}/api/v1/feedbacks"
                logger.info("wb_api.request", method="GET", path="/api/v1/feedbacks", params=params)
                response = await client.get(url, headers=self.headers, params=params)

                if response.status_code == 401:
                    raise ValueError("WB API: неверный API-ключ (401) — нужен scope «Вопросы и отзывы»")
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    raise RateLimitError("WB Feedbacks API rate limited (429)", retry_after=retry_after)
                if response.status_code >= 500:
                    raise ValueError(f"WB Feedbacks API server error: HTTP {response.status_code}")
                if response.status_code == 204:
                    return {"countUnanswered": 0, "countArchive": 0, "feedbacks": []}
                if response.status_code != 200:
                    raise ValueError(f"WB Feedbacks API error: HTTP {response.status_code} — {response.text[:200]}")

                data = response.json()
                inner = data.get("data") if isinstance(data, dict) else None
                return inner or {"countUnanswered": 0, "countArchive": 0, "feedbacks": []}

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_feedbacks_archive(
        self,
        take: int = 5000,
        skip: int = 0,
    ) -> dict:
        """
        Архивные отзывы покупателей (feedbacks archive).
        WB Feedbacks API: GET /api/v1/feedbacks/archive?take=&skip=
        take ∈ [1..5000], skip ≥ 0. Тот же формат data-dict, что get_feedbacks
        (архив = обработанные отзывы, без isAnswered-фильтра). Нужен для первичного
        бэкофилла истории — активный список отдаёт только «свежие» отзывы.
        """
        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                params = {"take": take, "skip": skip}
                url = f"{WB_FEEDBACKS_API_BASE}/api/v1/feedbacks/archive"
                logger.info("wb_api.request", method="GET", path="/api/v1/feedbacks/archive", params=params)
                response = await client.get(url, headers=self.headers, params=params)

                if response.status_code == 401:
                    raise ValueError("WB API: неверный API-ключ (401) — нужен scope «Вопросы и отзывы»")
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                    raise RateLimitError("WB Feedbacks API rate limited (429)", retry_after=retry_after)
                if response.status_code >= 500:
                    raise ValueError(f"WB Feedbacks API server error: HTTP {response.status_code}")
                if response.status_code == 204:
                    return {"feedbacks": []}
                if response.status_code != 200:
                    raise ValueError(f"WB Feedbacks archive error: HTTP {response.status_code} — {response.text[:200]}")

                data = response.json()
                inner = data.get("data") if isinstance(data, dict) else None
                return inner or {"feedbacks": []}


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
        imt_id = card.get("imtID")
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
                        "imt_id": imt_id,
                        "volume_l": round(volume_l, 4),
                    }
                )

    return nomenclature


def parse_wb_prices(goods: list[dict]) -> list[dict]:
    """
    WB Discounts-Prices API goods → наш WbPrice-формат (один ряд на nm_id).

    Цена/скидка лежат в sizes[]; для нашей цели (цена за nm_id) берём первый
    размер с непустой ценой (у хозтоваров обычно один размер; у мультиразмерных
    цена, как правило, одинаковая). `discountedPrice` — цена витрины (после
    seller-скидки, ДО СПП) → наше `price`; `price` (база) → `base_price`.
    """
    out: list[dict] = []
    seen: set[int] = set()

    for g in goods:
        nm_id = g.get("nmID")
        if not nm_id or nm_id in seen:
            continue

        sizes = g.get("sizes") or []
        base_price = None
        price = None
        for s in sizes:
            disc = s.get("discountedPrice")
            base = s.get("price")
            if disc or base:
                price = disc if disc else base
                base_price = base
                break

        if price is None and base_price is None:
            continue  # нет цены — пропускаем (артикул без цены не несёт смысла)

        seen.add(nm_id)
        out.append(
            {
                "nm_id": int(nm_id),
                "vendor_code": (g.get("vendorCode") or "").strip() or None,
                "base_price": base_price,
                "price": price if price is not None else base_price,
                "discount": g.get("discount"),
                "currency": g.get("currencyIsoCode4217") or "RUB",
            }
        )

    return out


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

        # created_at maps to a naive DateTime column — parse the WB date string into a
        # datetime (asyncpg won't coerce a str, and rejects tz-aware values too).
        raw_date = sale.get("date")
        try:
            created = datetime.fromisoformat(raw_date) if raw_date else utcnow()
        except (ValueError, TypeError):
            created = utcnow()
        if created.tzinfo is not None:
            created = created.astimezone(timezone.utc).replace(tzinfo=None)

        payouts.append(
            {
                "request_id": sale_id,
                "amount_rub": total_price,
                "currency": "RUB",
                "created_at": created,
                "wb_status_raw": sale.get("saleID", ""),
                "status": "TRANSIT",
            }
        )
    return payouts
