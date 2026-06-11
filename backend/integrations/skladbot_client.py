# ruff: noqa: RUF002, RUF003
"""
skladbot.ru fulfillment API client.

Base: https://api.skladbot.ru, заголовки Accept: application/json +
Authorization: Bearer <seller token>.

Endpoints used:
- GET  /v1/customers — валидация токена + customer info
- POST /v1/products  — остатки (сырой Laravel-paginator: верхний уровень
  {current_page, last_page, total, per_page, data, ...}, где data — DICT
  (ключ = system_product_id строкой), значение — СПИСОК item'ов)
- GET  /v1/requests?type_id=N — заявки по типу (пагинация через meta.last_page)

Rate limits: /v1/requests/* — 120 req/min, прочие — 60 req/min;
429 → заголовок Retry-After.

Resilience:
- Per-project circuit breaker (5 failures → 120s cooldown)
- Exponential backoff retry на fetch-методах (429/5xx)
"""

import base64
import binascii
import json
from datetime import UTC, datetime

import httpx
import structlog

from backend.integrations.resilience import (
    CircuitBreakerRegistry,
    RateLimitError,
    retry_with_backoff,
)

logger = structlog.get_logger("dds.skladbot_api")

SKLADBOT_API_BASE = "https://api.skladbot.ru"

# Request timeout in seconds
TIMEOUT = 30

# Защита от бесконечной пагинации (битый last_page и т.п.)
MAX_PAGES = 200

# Типы заявок (см. GET /v1/requests/filter/fields)
ASSEMBLY_TYPE_IDS: set[int] = {851}  # «3. Доставка на склад МП»
INBOUND_TYPE_IDS: set[int] = {
    852,
    2644,
}  # «2.1 Приемка без согласования маркировки», «2.2 Приемка из номинального остатка»


class SkladbotApiError(ValueError):
    """4xx от skladbot (кроме 429): проблема запроса/токена, НЕ деградация сервиса.

    Исключён из circuit breaker — пять connect'ов с плохим токеном не должны
    блокировать интеграцию на recovery_timeout.
    """

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


# Per-project circuit breakers — one project's failures don't block others
_skladbot_circuits = CircuitBreakerRegistry(
    name_prefix="skladbot",
    failure_threshold=5,
    recovery_timeout=120.0,
    exclude_errors=(RateLimitError, SkladbotApiError),
)


def decode_jwt_exp(token: str) -> datetime | None:
    """Extract `exp` from a JWT payload WITHOUT signature verification.

    Токен skladbot — RS256 JWT; нам нужен только срок жизни для предупреждений
    об истечении. base64url payload → json → exp (unix float) → naive UTC.
    Возвращает None для любого невалидного/неполного токена.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode()))
        exp = payload.get("exp")
        if exp is None:
            return None
        # Naive UTC — совместимо с TIMESTAMP WITHOUT TIME ZONE (см. backend.utils.time)
        return datetime.fromtimestamp(float(exp), tz=UTC).replace(tzinfo=None)
    except (ValueError, TypeError, AttributeError, binascii.Error):
        return None


class SkladbotClient:
    """skladbot.ru API client with per-project circuit breaker, retry, and rate limit handling."""

    def __init__(self, token: str, project_id: int | None = None):
        self.token = token
        self.project_id = project_id
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        }
        self._circuit = _skladbot_circuits.get(project_id)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        """Make a request to skladbot API with circuit breaker; raise on errors."""
        async with self._circuit, httpx.AsyncClient(timeout=TIMEOUT) as client:
            logger.info("skladbot_api.request", method=method, path=path, params=params)
            response = await client.request(
                method,
                f"{SKLADBOT_API_BASE}{path}",
                headers=self.headers,
                params=params,
                json=json_body,
            )

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "60"))
                raise RateLimitError(
                    "Skladbot API rate limited (429)",
                    retry_after=retry_after,
                )
            if 400 <= response.status_code < 500:
                raise SkladbotApiError(
                    f"Skladbot API error ({response.status_code}): {response.text[:200]}",
                    status_code=response.status_code,
                )
            if response.status_code != 200:
                # 5xx — деградация сервиса: считается circuit breaker'ом и ретраится
                raise ValueError(f"Skladbot API error ({response.status_code}): {response.text[:200]}")

            return response.json()

    async def test_connection(self) -> dict | None:
        """Validate the token: return the first customer from /v1/customers or None.

        4xx (невалидный токен) и сетевые ошибки → None («токен невалидный»);
        5xx и CircuitOpenError пробрасываются — это не проблема токена.
        """
        try:
            data = await self._request("GET", "/v1/customers")
        except (SkladbotApiError, httpx.HTTPError):
            return None
        customers = data.get("data") or []
        return customers[0] if customers else None

    @retry_with_backoff(max_retries=3)
    async def fetch_all_products(self, customer_id: int) -> list[dict]:
        """Fetch ALL stock items, flattened across pages and product groups.

        POST /v1/products — Laravel-paginator: data — dict
        {system_product_id: [item, ...]}; flatten в плоский список item'ов.
        Пагинация по last_page (?page=N).
        """
        items: list[dict] = []
        page = 1
        while True:
            data = await self._request(
                "POST",
                "/v1/products",
                params={"page": page},
                json_body={"customer_id": customer_id, "limit": 1000},
            )
            groups = data.get("data") or {}
            values = groups.values() if isinstance(groups, dict) else groups
            for group in values:
                if isinstance(group, list):
                    items.extend(group)
                elif isinstance(group, dict):
                    items.append(group)

            last_page = int(data.get("last_page") or 1)
            if page >= last_page:
                break
            if page >= MAX_PAGES:
                logger.warning("skladbot_api.products_page_cap", last_page=last_page, cap=MAX_PAGES)
                break
            page += 1

        logger.info("skladbot_api.products_fetched", total=len(items), customer_id=customer_id)
        return items

    async def fetch_request_detail(self, external_id: str | int) -> dict:
        """Деталка заявки: НЕДОКУМЕНТИРОВАННЫЙ GET /v1/requests/show/{id}.

        Без retry: интерактивный вызов (открытие деталки в UI) — лучше быстрый
        отказ с человеческой ошибкой, чем минуты ожидания backoff'а.

        Возвращает data: products[] (amount/acceptedAmount/repairAmount/...),
        fields[] (динамика типа), stageLogs[], stage{code,name,description},
        customer{id,name}, executor, creator, comment. Найден пробами 2026-06-11;
        в официальных доках skladbot его нет — при 404 проверить, не выпилили ли роут.
        """
        data = await self._request("GET", f"/v1/requests/show/{external_id}")
        return data.get("data") or {}

    @retry_with_backoff(max_retries=3)
    async def fetch_requests(self, type_id: int) -> list[dict]:
        """Fetch ALL requests of the given type_id (paginated, limit=100)."""
        rows: list[dict] = []
        page = 1
        while True:
            data = await self._request(
                "GET",
                "/v1/requests",
                params={"type_id": type_id, "limit": 100, "page": page},
            )
            rows.extend(data.get("data") or [])

            meta = data.get("meta") or {}
            last_page = int(meta.get("last_page") or 1)
            if page >= last_page:
                break
            if page >= MAX_PAGES:
                logger.warning("skladbot_api.requests_page_cap", last_page=last_page, cap=MAX_PAGES)
                break
            page += 1

        logger.info("skladbot_api.requests_fetched", total=len(rows), type_id=type_id)
        return rows
