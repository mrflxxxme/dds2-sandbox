# ruff: noqa: RUF002, RUF003
"""
migfull.app fulfillment API client — склад «Натали».

Base: `https://migfull.app/api/customer/{tenant_guid}` — хост фиксированный,
пользователь вводит только tenant_guid (валидируется как UUID — guard от
инъекции пути) и Bearer-токен (в заголовке, в URL секретов нет).

API только на чтение: POST разрешён исключительно для `.../search`.
Ответ — Laravel-конверт: {success, message, data, links, meta}; пагинация
?page=N&per_page=N (1..1000), total/last_page в meta.

Endpoints used:
- GET /products?per_page=                — товары + остатки (sku/gtin пустые!)
- GET /products/{guid}                   — карточка товара СО штрихкодами (barcodes[])
- GET /submissions?per_page=             — приёмки (processing → send → closed; canceled)
- GET /submissions/{guid}/lines/{type}   — строки приёмки: incoming | received
- GET /shipments?per_page=               — отгрузки (uploaded → ready → closed; canceled)
- GET /shipments/{guid}/lines/{type}     — строки отгрузки: planned | shipped

Resilience:
- Per-project circuit breaker (5 failures → 120s cooldown), 4xx исключены
- Exponential backoff retry на fetch-методах (429/5xx)
"""

import uuid

import httpx
import structlog

from backend.integrations.resilience import (
    CircuitBreakerRegistry,
    RateLimitError,
    retry_with_backoff,
)

logger = structlog.get_logger("dds.migfull_api")

API_HOST = "https://migfull.app"

# Request timeout in seconds
TIMEOUT = 30

# per_page: API принимает 1..1000; 500 — баланс размера ответа и числа запросов
PAGE_LIMIT = 500

# Защита от бесконечной пагинации (500/стр × 100 = 50k записей максимум)
MAX_PAGES = 100


class MigfullApiError(ValueError):
    """4xx или {success: false} от migfull: проблема запроса/токена, НЕ деградация.

    Исключён из circuit breaker — плохой токен не должен блокировать
    интеграцию на recovery_timeout.
    """

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


# Per-project circuit breakers — one project's failures don't block others
_migfull_circuits = CircuitBreakerRegistry(
    name_prefix="migfull",
    failure_threshold=5,
    recovery_timeout=120.0,
    exclude_errors=(RateLimitError, MigfullApiError),
)


def _validate_guid(raw: str, what: str = "GUID") -> str:
    """Валидация guid: строго UUID (guard от инъекции в URL-путь).

    Возвращает канонический lowercase-вид. Raises ValueError.
    """
    value = (raw or "").strip()
    if not value:
        raise ValueError(f"Укажите {what} migfull")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ValueError(f"{what} migfull должен быть UUID вида 123e4567-…") from None


def normalize_tenant_guid(raw: str) -> str:
    """Валидация GUID кабинета (выдаёт склад «Натали»). Raises ValueError."""
    return _validate_guid(raw, what="GUID кабинета")


# Типы строк заявок: подставляются в URL-путь — только allowlist
_LINE_TYPES = ("planned", "shipped", "incoming", "received")


def _validate_line_type(line_type: str) -> str:
    if line_type not in _LINE_TYPES:
        raise ValueError(f"migfull: unsupported line_type {line_type!r}")
    return line_type


class MigfullClient:
    """migfull.app API client: circuit breaker, retry, rate limit handling."""

    def __init__(self, tenant_guid: str, token: str, project_id: int | None = None):
        self.tenant_guid = normalize_tenant_guid(tenant_guid)
        self.token = token
        self.project_id = project_id
        self._circuit = _migfull_circuits.get(project_id)

    async def _request(self, path: str, params: dict | None = None) -> dict:
        """GET `{host}/api/customer/{tenant_guid}/{path}`; raise on errors.

        Токен уходит в заголовке — URL секретов не содержит, httpx-ошибки
        безопасны для логов/UI.
        """
        url = f"{API_HOST}/api/customer/{self.tenant_guid}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        async with self._circuit, httpx.AsyncClient(timeout=TIMEOUT) as client:
            logger.info("migfull_api.request", path=path, params=params)
            response = await client.get(url, params=params, headers=headers)

            if response.status_code == 429:
                try:
                    retry_after = int(response.headers.get("Retry-After", "60"))
                except ValueError:  # Retry-After бывает HTTP-датой
                    retry_after = 60
                raise RateLimitError("migfull API rate limited (429)", retry_after=retry_after)
            if 400 <= response.status_code < 500:
                raise MigfullApiError(
                    f"migfull API error ({response.status_code}) at {path}: {response.text[:200]}",
                    status_code=response.status_code,
                )
            if response.status_code != 200:
                # 5xx — деградация сервиса: считается circuit breaker'ом и ретраится
                raise ValueError(f"migfull API error ({response.status_code}) at {path}: {response.text[:200]}")

            data = response.json()
            if not isinstance(data, dict):
                raise ValueError(f"migfull API: unexpected response shape at {path}")
            # Laravel-конверт: 200 + {success: false, message} — ошибка уровня API
            if data.get("success") is False:
                raise MigfullApiError(
                    f"migfull API error at {path}: {str(data.get('message'))[:200]}",
                    status_code=response.status_code,
                )
            return data

    async def test_connection(self) -> bool:
        """Validate token + tenant_guid: products с per_page=1 отвечает без ошибки.

        4xx/{success: false} и сетевые ошибки → False («токен/GUID невалидны»);
        5xx и CircuitOpenError пробрасываются — это не проблема токена.
        """
        try:
            await self._request("products", params={"page": 1, "per_page": 1})
        except (MigfullApiError, httpx.HTTPError):
            return False
        return True

    @staticmethod
    def _rows(data: dict) -> list[dict]:
        """Конверт → список строк data (защита от null/мусора в массиве)."""
        rows = data.get("data")
        if isinstance(rows, dict):  # by-id ответы заворачивают объект в data
            return [rows]
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict)]

    async def _fetch_paginated(self, path: str, params: dict | None = None) -> list[dict]:
        """Fetch ALL pages: останавливаемся по meta.last_page / неполной странице."""
        rows: list[dict] = []
        page = 1
        while True:
            data = await self._request(path, params={**(params or {}), "page": page, "per_page": PAGE_LIMIT})
            batch = self._rows(data)
            rows.extend(batch)
            meta = data.get("meta")
            last_page = meta.get("last_page") if isinstance(meta, dict) else None
            if isinstance(last_page, int) and page >= last_page:
                break
            if not batch or len(batch) < PAGE_LIMIT:
                break
            if page >= MAX_PAGES:
                logger.warning("migfull_api.page_cap", path=path, cap=MAX_PAGES)
                break
            page += 1
        logger.info("migfull_api.fetched", path=path, total=len(rows))
        return rows

    @retry_with_backoff(max_retries=3)
    async def fetch_all_products(self) -> list[dict]:
        """Все товары с остатками: {guid, name, size, color, stock_actual,
        stock_locked, stock_available}. Штрихкодов в списке НЕТ (sku/gtin пустые)."""
        return await self._fetch_paginated("products")

    @retry_with_backoff(max_retries=3)
    async def fetch_product(self, guid: str) -> dict:
        """Карточка товара со штрихкодами: barcodes[] = [{value, is_primary}]."""
        data = await self._request(f"products/{_validate_guid(guid, 'GUID товара')}")
        row = data.get("data")
        return row if isinstance(row, dict) else {}

    @retry_with_backoff(max_retries=3)
    async def fetch_shipments(self) -> list[dict]:
        """Все отгрузки: план/факт, статус, маркетплейс и склад назначения."""
        return await self._fetch_paginated("shipments")

    @retry_with_backoff(max_retries=3)
    async def fetch_submissions(self) -> list[dict]:
        """Все приёмки: reference, статус, даты (состав — отдельными lines)."""
        return await self._fetch_paginated("submissions")

    @retry_with_backoff(max_retries=3)
    async def fetch_shipment_lines(self, guid: str, line_type: str) -> list[dict]:
        """Строки отгрузки: line_type = planned | shipped; product вложен в строку."""
        return await self._fetch_paginated(
            f"shipments/{_validate_guid(guid, 'GUID отгрузки')}/lines/{_validate_line_type(line_type)}"
        )

    @retry_with_backoff(max_retries=3)
    async def fetch_submission_lines(self, guid: str, line_type: str) -> list[dict]:
        """Строки приёмки: line_type = incoming | received; с флагом is_defective."""
        return await self._fetch_paginated(
            f"submissions/{_validate_guid(guid, 'GUID приёмки')}/lines/{_validate_line_type(line_type)}"
        )
