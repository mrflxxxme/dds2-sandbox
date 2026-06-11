# ruff: noqa: RUF002, RUF003
"""
WMS Celicom («Целиком») fulfillment API client.

Base: клиентский инстанс `https://{client}.wmscelicom.ru` (wildcard-домена нет,
адрес вводится при подключении). Аутентификация: персональный токен ПРЯМО
В URL-ПУТИ — `/api/{token}/...`. Токен = секрет в path: НИКОГДА не логируем
полный URL, только суффикс пути после токена.

Endpoints used (https://api-doc.wmscelicom.ru/):
- GET /api/{t}/items/get/           — товары + остатки (bare JSON array)
- GET /api/{t}/shipmentsfbo/list/   — отгрузки FBO («ФФ сборка»); with_packages=1
  + with_items=1 кладут состав коробов внутрь; data — dict {id: row}
- GET /api/{t}/unloadingorders/list/ — заявки на приёмку; data — dict {id: row}

Ограничения API: 150 req/min на всё; max 30 элементов на страницу.

Resilience:
- Per-project circuit breaker (5 failures → 120s cooldown), 4xx исключены
- Exponential backoff retry на fetch-методах (429/5xx)
"""

from urllib.parse import urlparse

import httpx
import structlog

from backend.integrations.resilience import (
    CircuitBreakerRegistry,
    RateLimitError,
    retry_with_backoff,
)

logger = structlog.get_logger("dds.wmscelicom_api")

# Разрешённый суффикс хоста инстанса — защита от SSRF через user-supplied URL
ALLOWED_HOST_SUFFIX = ".wmscelicom.ru"

# Request timeout in seconds
TIMEOUT = 30

# API отдаёт максимум 30 элементов на страницу
PAGE_LIMIT = 30

# Защита от бесконечной пагинации (30/стр × 400 = 12k записей максимум)
MAX_PAGES = 400


class WmsCelicomApiError(ValueError):
    """4xx или {status: ERROR} от wmscelicom: проблема запроса/токена, НЕ деградация.

    Исключён из circuit breaker — плохой токен не должен блокировать
    интеграцию на recovery_timeout.
    """

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


# Per-project circuit breakers — one project's failures don't block others
_wms_circuits = CircuitBreakerRegistry(
    name_prefix="wmscelicom",
    failure_threshold=5,
    recovery_timeout=120.0,
    exclude_errors=(RateLimitError, WmsCelicomApiError),
)


def normalize_base_url(raw: str) -> str:
    """«client.wmscelicom.ru» / «https://client.wmscelicom.ru/» → «https://client.wmscelicom.ru».

    Хост обязан оканчиваться на .wmscelicom.ru (SSRF-guard: URL вводит
    пользователь, а ходит по нему backend). Raises ValueError.
    """
    value = (raw or "").strip().rstrip("/")
    if not value:
        raise ValueError("Укажите адрес инстанса wmscelicom (например client.wmscelicom.ru)")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(ALLOWED_HOST_SUFFIX) or host == ALLOWED_HOST_SUFFIX.lstrip("."):
        raise ValueError("Адрес инстанса должен быть вида https://{client}.wmscelicom.ru")
    if parsed.port not in (None, 443) or parsed.path or parsed.query or parsed.params or parsed.username:
        raise ValueError("Укажите только домен инстанса, без пути и порта")
    return f"https://{host}"


class WmsCelicomClient:
    """WMS Celicom API client: circuit breaker, retry, rate limit handling."""

    def __init__(self, base_url: str, token: str, project_id: int | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.project_id = project_id
        self._circuit = _wms_circuits.get(project_id)

    async def _request(self, path: str, params: dict | None = None) -> dict | list:
        """GET `{base}/api/{token}/{path}`; raise on errors.

        В логи и тексты ошибок попадает только `path` (после токена) — сам URL
        содержит секрет.
        """
        url = f"{self.base_url}/api/{self.token}/{path.lstrip('/')}"
        async with self._circuit, httpx.AsyncClient(timeout=TIMEOUT) as client:
            logger.info("wmscelicom_api.request", path=path, params=params)
            response = await client.get(url, params=params, headers={"Accept": "application/json"})

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", "60"))
                raise RateLimitError("WMS Celicom API rate limited (429)", retry_after=retry_after)
            if 400 <= response.status_code < 500:
                raise WmsCelicomApiError(
                    f"WMS Celicom API error ({response.status_code}) at {path}: {response.text[:200]}",
                    status_code=response.status_code,
                )
            if response.status_code != 200:
                # 5xx — деградация сервиса: считается circuit breaker'ом и ретраится
                raise ValueError(f"WMS Celicom API error ({response.status_code}) at {path}: {response.text[:200]}")

            data = response.json()
            # API может вернуть 200 + {status: ERROR, message} (см. спеку oneOf)
            if isinstance(data, dict) and str(data.get("status", "")).upper() == "ERROR":
                raise WmsCelicomApiError(
                    f"WMS Celicom API error at {path}: {str(data.get('message'))[:200]}",
                    status_code=response.status_code,
                )
            return data

    async def test_connection(self) -> bool:
        """Validate the token: items/get с limit=1 отвечает без ошибки.

        4xx/{status: ERROR} и сетевые ошибки → False («токен/адрес невалидны»);
        5xx и CircuitOpenError пробрасываются — это не проблема токена.
        """
        try:
            await self._request("items/get/", params={"page": 1, "limit": 1})
        except (WmsCelicomApiError, httpx.HTTPError):
            return False
        return True

    @staticmethod
    def _rows(data: dict | list) -> list[dict]:
        """Ответ списочных методов → список строк.

        items/get — bare array; shipmentsfbo/unloadingorders — {status, data:
        {id: row}}; пустой data у PHP-бэкенда может быть и [] вместо {}.
        """
        if isinstance(data, dict):
            data = data.get("data") or []
        if isinstance(data, dict):
            return [row for row in data.values() if isinstance(row, dict)]
        return [row for row in data if isinstance(row, dict)]

    async def _fetch_paginated(self, path: str, params: dict | None = None) -> list[dict]:
        """Fetch ALL pages: останавливаемся на неполной/пустой странице."""
        rows: list[dict] = []
        page = 1
        while True:
            data = await self._request(path, params={**(params or {}), "page": page, "limit": PAGE_LIMIT})
            batch = self._rows(data)
            rows.extend(batch)
            if len(batch) < PAGE_LIMIT:
                break
            if page >= MAX_PAGES:
                logger.warning("wmscelicom_api.page_cap", path=path, cap=MAX_PAGES)
                break
            page += 1
        logger.info("wmscelicom_api.fetched", path=path, total=len(rows))
        return rows

    @retry_with_backoff(max_retries=3)
    async def fetch_all_items(self) -> list[dict]:
        """Все товары с остатками: {Id, Name, Count, CountVirtual, Article, Barcodes[], Complect}."""
        return await self._fetch_paginated("items/get/")

    @retry_with_backoff(max_retries=3)
    async def fetch_shipments_fbo(self) -> list[dict]:
        """Все отгрузки FBO с коробами и составом (with_packages=1 + with_items=1)."""
        return await self._fetch_paginated("shipmentsfbo/list/", params={"with_packages": 1, "with_items": 1})

    @retry_with_backoff(max_retries=3)
    async def fetch_unloading_orders(self) -> list[dict]:
        """Все заявки на приёмку (items включены в ответ списка)."""
        return await self._fetch_paginated("unloadingorders/list/")
