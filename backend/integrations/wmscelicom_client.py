# ruff: noqa: RUF002, RUF003
"""
WMS Celicom («Целиком») fulfillment API client.

Base: клиентский инстанс `https://{client}.wmscelicom.ru` (wildcard-домена нет,
адрес вводится при подключении). Аутентификация: персональный токен ПРЯМО
В URL-ПУТИ — `/api/{token}/...`. Токен = секрет в path: НИКОГДА не логируем
полный URL, только суффикс пути после токена.

Endpoints used (https://api-doc.wmscelicom.ru/):
- GET /api/{t}/items/get/           — товары + остатки (bare JSON array)
- GET /api/{t}/dispatchorders/list/ — заявки на отгрузку («ФФ сборка», зОГ): склад
  отгрузки (город МП) + состав (items[]); data — dict {orderid: row}. Это РОДИТЕЛЬ
  отгрузки FBO; shipmentsfbo (где shipped_target — лишь площадка) больше не тянем.
- GET /api/{t}/unloadingorders/list/ — заявки на приёмку; data — dict {id: row}
- POST /api/{t}/dispatchorders/add/  — создать заявку на отгрузку (PUSH из сборки):
  delivery=2 (самовывоз) + fbo=1 + items[{barcode,count}]; → {status:OK, id}
- POST /api/{t}/dispatchorders/send/ — подтвердить заявку (перевод в сборку, Shipment)

Ограничения API: 150 req/min на всё; max 30 элементов на страницу. Список заявок
на отгрузку тяжёлый при широкой выборке (сервер материализует весь набор до
пагинации) — тянем двумя ограниченными запросами (активные / терминальные+окно).

Resilience:
- Per-project circuit breaker (5 failures → 120s cooldown), 4xx исключены
- Exponential backoff retry на fetch-методах (429/5xx)
"""

import json
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

# Английские статусы заявки на отгрузку (dispatchorders), см. openapi.
# Активные ещё не отгружены (FBO-отгрузки нет) → date-фильтр (он по дате отгрузки)
# их бы отсёк, поэтому тянем без даты — их немного. Терминальные тянем за окно.
DISPATCH_ACTIVE_STATUSES = ("new", "combinig", "waitforcombine", "waitingdelivery", "combined", "waitclientapproval")
DISPATCH_TERMINAL_STATUSES = ("ondelivery", "delivered", "annuled")


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

    def _redact(self, text: str) -> str:
        """Убрать токен из произвольного текста (URL в httpx-ошибках, echo в body)."""
        return text.replace(self.token, "***")

    def _check(self, response: httpx.Response, path: str) -> dict | list:
        """Единая обработка ответа GET/POST: 429/4xx/5xx/{status:ERROR} → исключения.

        В тексты ошибок попадает только `path` (после токена) и _redact: сам URL
        содержит секрет. 200 + {status: ERROR, message} (спека oneOf) — тоже ошибка.
        """
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "60"))
            raise RateLimitError("WMS Celicom API rate limited (429)", retry_after=retry_after)
        if 400 <= response.status_code < 500:
            raise WmsCelicomApiError(
                self._redact(f"WMS Celicom API error ({response.status_code}) at {path}: {response.text[:200]}"),
                status_code=response.status_code,
            )
        if response.status_code != 200:
            # 5xx — деградация сервиса: считается circuit breaker'ом и ретраится
            raise ValueError(
                self._redact(f"WMS Celicom API error ({response.status_code}) at {path}: {response.text[:200]}")
            )

        data = response.json()
        if isinstance(data, dict) and str(data.get("status", "")).upper() == "ERROR":
            raise WmsCelicomApiError(
                self._redact(f"WMS Celicom API error at {path}: {str(data.get('message'))[:200]}"),
                status_code=response.status_code,
            )
        return data

    async def _request(self, path: str, params: dict | None = None) -> dict | list:
        """GET `{base}/api/{token}/{path}`; raise on errors (общий _check).

        В логи попадает только `path` (после токена); httpx-ошибки редактируются.
        """
        url = f"{self.base_url}/api/{self.token}/{path.lstrip('/')}"
        async with self._circuit, httpx.AsyncClient(timeout=TIMEOUT) as client:
            logger.info("wmscelicom_api.request", path=path, params=params)
            try:
                response = await client.get(url, params=params, headers={"Accept": "application/json"})
            except httpx.HTTPError as e:
                # str(httpx-ошибки) может содержать полный URL — а в нём токен
                raise httpx.ConnectError(self._redact(f"{type(e).__name__}: {e}")) from None
            return self._check(response, path)

    async def _post(self, path: str, payload: dict) -> dict | list:
        """POST JSON `{base}/api/{token}/{path}`; обработка как у _request.

        Тело НЕ логируем (состав/доп.инфо). Вызывающие НЕ оборачивают в retry:
        повторный POST на создание = дубликат реального заказа у ФФ.
        """
        url = f"{self.base_url}/api/{self.token}/{path.lstrip('/')}"
        async with self._circuit, httpx.AsyncClient(timeout=TIMEOUT) as client:
            logger.info("wmscelicom_api.post", path=path)
            try:
                response = await client.post(url, json=payload, headers={"Accept": "application/json"})
            except httpx.HTTPError as e:
                raise httpx.ConnectError(self._redact(f"{type(e).__name__}: {e}")) from None
            return self._check(response, path)

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
    async def fetch_dispatch_orders(self, terminal_from: str, terminal_to: str) -> list[dict]:
        """Заявки на отгрузку (DispatchOrders) со складом отгрузки и составом.

        Состав уже внутри (items[]), отдельной деталки нет. Чтобы не материализовать
        весь архив (медленно/таймаут), тянем двумя ограниченными выборками:
        активные статусы целиком (их немного, без date-фильтра — он по дате FBO,
        которой у них ещё нет) + терминальные за окно [terminal_from, terminal_to]
        (YYYY-MM-DD). Статус-фильтр — JSON-массив английских статусов.
        """
        rows = await self._fetch_paginated(
            "dispatchorders/list/", params={"status": json.dumps(DISPATCH_ACTIVE_STATUSES)}
        )
        rows += await self._fetch_paginated(
            "dispatchorders/list/",
            params={
                "status": json.dumps(DISPATCH_TERMINAL_STATUSES),
                "date": json.dumps([terminal_from, terminal_to]),
            },
        )
        return rows

    @retry_with_backoff(max_retries=3)
    async def fetch_unloading_orders(self) -> list[dict]:
        """Все заявки на приёмку (items включены в ответ списка)."""
        return await self._fetch_paginated("unloadingorders/list/")

    async def create_dispatch(
        self,
        items: list[dict],
        *,
        delivery: int = 2,
        fbo: int = 1,
        comment: str | None = None,
    ) -> dict:
        """Создать заявку на отгрузку (POST dispatchorders/add/) в статусе «Новая».

        Контракт FBO-отгрузки на склад МП у «Целиком»: `delivery`=2 (самовывоз —
        WB/транспорт забирает со склада ФФ; склад WB определяется привязкой к
        WB-поставке на стороне «Целиком», в payload НЕ задаётся), `fbo`=1 (списание
        FBO), состав — `items` = [{barcode, count}]. БЕЗ retry: повторный POST =
        дубликат реального заказа. Ответ — {status: OK, id: <orderId>, ...}.
        """
        payload: dict = {"delivery": delivery, "fbo": fbo, "items": items}
        if comment:
            payload["comment"] = comment
        data = await self._post("dispatchorders/add/", payload)
        if not isinstance(data, dict):
            raise WmsCelicomApiError("WMS Celicom вернул неожиданный ответ на dispatchorders/add", status_code=200)
        return data

    async def send_dispatch(self, order_id: int | str) -> dict:
        """Подтвердить заявку (POST dispatchorders/send/): перевод в сборку + Shipment.

        После add/ заявка в статусе «Новая»; send/ создаёт запись об отгрузке и
        переводит в сборку. БЕЗ retry. Ответ — {status: OK, order_id, shipment_id}.
        """
        data = await self._post("dispatchorders/send/", {"id": order_id})
        if not isinstance(data, dict):
            raise WmsCelicomApiError("WMS Celicom вернул неожиданный ответ на dispatchorders/send", status_code=200)
        return data
