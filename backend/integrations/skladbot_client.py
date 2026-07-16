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

# Cap страниц при поиске клиента по id (FF-operator токен видит сотни клиентов)
MAX_CUSTOMER_PAGES = 30

# Типы заявок (см. GET /v1/requests/filter/fields)
ASSEMBLY_TYPE_IDS: set[int] = {851}  # «3. Доставка на склад МП»
# Тип заявки для PUSH-создания «Доставка на склад МП» (наша сборка → ФФ)
DELIVERY_REQUEST_TYPE_ID: int = 851
INBOUND_TYPE_IDS: set[int] = {
    852,
    2644,
}  # «2.1 Приемка без согласования маркировки», «2.2 Приемка из номинального остатка»
# Тип заявки для PUSH-создания приёмки (машина/поставка → собственный склад ФФ):
# «2.1 Приемка без согласования маркировки». Форма (живая проба form-data
# 2026-07-14): единственное ОБЯЗАТЕЛЬНОЕ поле — product_arrival_date (Y-m-d);
# comment опционально; полей склада/маркетплейса/дат забора НЕТ (в отличие от 851).
INBOUND_REQUEST_TYPE_ID: int = 852

# Классификация стадий заявки типа 851 «Доставка на склад МП» (живые пробы
# 2026-06-11): стадии 1–2 — сборка ещё идёт (WIP), стадия 3+ — груз готов,
# дальше логистика (виды работ, водитель, погрузка, отгрузка, завершение).
ASSEMBLY_WIP_STAGE_CODES: set[str] = {"cargo_pickup", "delivery_to_the_marketplace_warehouse"}
# Fallback по названию стадии (stage_code бывает пуст): lowercase contains.
# «объема»/«обьема» — обе орфографии провайдера («Указание объема груза» /
# «Указание обьема груза v2»).
ASSEMBLY_WIP_TITLE_MARKERS: tuple[str, ...] = ("забор груза", "объема груза", "обьема груза")

# Стадия отгрузки, на которой skladbot СПИСЫВАЕТ товар из своего остатка
# (stock_actual → наш qty_good), хотя груз ещё физически на складе ФФ, а мы его
# не отгрузили (сборка не SHIPPED; ship_request списывает наш сток только по
# is_completed). Пока заявка на этой стадии и не завершена, её состав нужно
# ДОСЧИТЫВАТЬ к остатку ФФ в отчёте расхождения — иначе ложная недостача.
# Подтверждено данными (2026-07-15, склад Газпром): по таким SKU
# наш_годный − ff_good_зеркало == объём в этой стадии. Если провайдер добавит
# другие пост-списочные пред-отгрузочные стадии (водитель/погрузка) — дополнить.
LOGISTICS_WRITEOFF_STAGE_CODES: set[str] = {"logistics_works"}


class SkladbotApiError(ValueError):
    """4xx от skladbot (кроме 429): проблема запроса/токена, НЕ деградация сервиса.

    Исключён из circuit breaker — пять connect'ов с плохим токеном не должны
    блокировать интеграцию на recovery_timeout.

    `reason` — чистый текст причины от провайдера (message/detail из JSON-тела,
    иначе срез тела) — для человеческих сообщений в UI; str(e) — для логов.
    """

    def __init__(self, message: str, status_code: int, reason: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


def _reason_from_body(text: str) -> str | None:
    """Достать человекочитаемую причину из тела ошибки (JSON message/detail/error)."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text[:200].strip() or None
    if isinstance(data, dict):
        for key in ("message", "detail", "error"):
            val = data.get(key)
            if val:
                return str(val)[:200]
    return text[:200].strip() or None


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
                    reason=_reason_from_body(response.text),
                )
            if not (200 <= response.status_code < 300):
                # 3xx (редирект) / 5xx (деградация) — циркуит-брейкер и ретрай.
                # POST /v1/requests отвечает 201 Created — любой 2xx считаем успехом.
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
    async def count_customers(self) -> int:
        """Сколько клиентов видит токен (meta.total первой страницы /v1/customers).

        Селлер-токен видит 1 клиента; FF-operator токен — весь tenant (десятки/сотни).
        По этому числу connect решает, нужен ли явный выбор customer_id.
        """
        data = await self._request("GET", "/v1/customers", params={"page": 1})
        meta = data.get("meta") or {}
        total = meta.get("total")
        if total is not None:
            return int(total)
        return len(data.get("data") or [])

    async def find_customer(self, customer_id: int) -> dict | None:
        """Найти клиента по id среди видимых токену (пролистывая /v1/customers).

        Прямого lookup по id у API нет (search фильтрует по имени), поэтому
        листаем страницы до совпадения; cap MAX_CUSTOMER_PAGES страниц.
        None — не найден (id не принадлежит этому токену) или превышен cap.
        """
        page = 1
        while page <= MAX_CUSTOMER_PAGES:
            data = await self._request("GET", "/v1/customers", params={"page": page})
            rows = data.get("data") or []
            for c in rows:
                if isinstance(c, dict) and c.get("id") == customer_id:
                    return c
            last_page = int((data.get("meta") or {}).get("last_page") or 1)
            if page >= last_page or not rows:
                break
            page += 1
        return None

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
    async def fetch_form_data(self) -> dict:
        """Справочники формы создания заявки: GET /v1/requests/form-data.

        Возвращает `requestTypes[]` (типы с активными полями), `customers[]` и
        `utils{}` — справочники select-полей: `marketplaces`, `marketplaceWarehouses`
        (text/value/marketplace/customer/is_active), `marketplaceDeliveryTypes` и др.
        Нужно, потому что поля `marketplace` / `marketplace_warehouse` заявки типа
        851 принимают НЕ имя, а integer `value` (id) из этих справочников.
        """
        data = await self._request("GET", "/v1/requests/form-data")
        # Laravel иногда заворачивает в {data}; справочники могут лежать и на верхнем уровне
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            return data["data"]
        return data if isinstance(data, dict) else {}

    @retry_with_backoff(max_retries=3)
    async def resolve_products(
        self,
        customer_id: int,
        request_type_id: int,
        barcodes: list[str],
    ) -> dict[str, dict]:
        """Резолв ШК → product_data_id для формы создания заявки (read-only).

        GET /v1/requests/products?customer=&request_type_id=&value=<ШК[,ШК...]>
        отдаёт ≤25 товаров на запрос (строгий whereIn по ШК) с остатком,
        доступным под заявку этого типа. ШК бьём на чанки по 20. Возвращает
        {barcode: {product_data_id, amount, counts, name, vendor_code}} — только
        для разрезолвленных ШК (товар есть в кабинете и доступен под тип заявки);
        нерезолвленные просто отсутствуют в словаре. При нескольких карточках на
        один ШК предпочитаем is_main_barcode.
        """
        resolved: dict[str, dict] = {}
        unique = [bc for bc in dict.fromkeys(b.strip() for b in barcodes) if bc]
        for start in range(0, len(unique), 20):
            chunk = unique[start : start + 20]
            data = await self._request(
                "GET",
                "/v1/requests/products",
                params={
                    "customer": customer_id,
                    "request_type_id": request_type_id,
                    "value": ",".join(chunk),
                },
            )
            rows = data if isinstance(data, list) else (data.get("data") or [])
            for row in rows:
                if not isinstance(row, dict):
                    continue
                barcode = str(row.get("barcode") or "").strip()
                pdid = row.get("product_data_id")
                if not barcode or pdid is None:
                    continue
                # is_main_barcode=1 побеждает; первый встреченный — иначе
                if barcode in resolved and not row.get("is_main_barcode"):
                    continue
                resolved[barcode] = {
                    "product_data_id": pdid,
                    "amount": int(row.get("amount") or 0),
                    "counts": int(row.get("counts") or 0),  # доступный остаток под заявку
                    "name": row.get("name"),
                    "vendor_code": row.get("vendor_code"),
                }
        logger.info(
            "skladbot_api.products_resolved",
            requested=len(unique),
            resolved=len(resolved),
            customer_id=customer_id,
        )
        return resolved

    async def create_request(self, payload: dict) -> dict:
        """Создать заявку у ФФ: POST /v1/requests (БЕЗ retry — это реальный заказ).

        Идемпотентность не на стороне клиента: повторный POST создаст вторую
        заявку, поэтому ретраев нет — ошибку (4xx/5xx/сеть) пробрасываем выше,
        вызывающий решает. Тело: {customer_id, request_type_id, products[],
        fields{...}, comment, notify}. Возвращает созданную заявку (Laravel
        иногда заворачивает в {data}: разворачиваем).
        """
        data = await self._request("POST", "/v1/requests", json_body=payload)
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            return data["data"]
        return data if isinstance(data, dict) else {}

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
