# ruff: noqa: RUF001, RUF002, RUF003
"""
Клиент WB Marketplace API (FBS) — склады продавца, остатки, сборочные задания, поставки.

Хост: https://marketplace-api.wildberries.ru (sandbox — marketplace-api-sandbox...).
Авторизация: заголовок `Authorization: <токен>` БЕЗ префикса `Bearer`.
Категория токена — «Маркетплейс».

Режим контура (`settings.WB_FBS_MODE`, см. backend/config.py):
- `safe` (дефолт) — читаем боевой WB, а любой WRITE-метод немедленно поднимает
  `WbFbsWriteBlocked` ДО похода в сеть. Именно до: 4xx у WB стоит 10 запросов
  бакета, а «запрос ради отказа» ещё и создаёт иллюзию попытки в логах;
- `sandbox` — весь трафик уходит на sandbox-хост (нужен отдельный токен scope
  Test, ключ `wb_marketplace_sandbox`);
- `prod` — боевой контур, запись разрешена.
Разделить «чтение с прода, запись в песочницу» нельзя физически: песочница —
отдельный контур, прод-идентификаторов (складов, chrtId, заданий) там нет.

Устойчивость (отличия от legacy `wb_api.py`, повторять его антипаттерны нельзя):
- ОДИН приватный `_request()` — обработка статусов не размазана по методам;
- СВОЙ реестр circuit breaker'ов (`name_prefix="wb_fbs"`): FBS опрашивается раз в 2 минуты,
  общий с `wb_api` брейкер повалил бы синки остатков/воронки/FBO;
- ретраи только там, где они осмысленны: 429 (пауза по `X-Ratelimit-Retry`) и 5xx (backoff).
  ЛЮБОЙ 4xx — сразу исключение без повтора: у WB каждый 4XX стоит 10 запросов бакета,
  слепая петля выжигает лимит в 30 раз быстрее.

Лимиты WB (token bucket, заголовки `X-Ratelimit-*`):
  задания/поставки/склады/PUT+POST stocks — 300/мин (всплеск 20);
  DELETE /stocks/{warehouseId} — 10/мин; PATCH /orders/{id}/cancel — 100/мин.
"""

import asyncio
import time
from datetime import datetime
from typing import Any, Iterator, Sequence
from urllib.parse import quote

import httpx
import structlog

from backend.config import (
    WB_FBS_MODE_PROD,
    WB_FBS_MODE_SAFE,
    WB_FBS_MODE_SANDBOX,
    WB_FBS_MODES,
    settings,
)
from backend.integrations.resilience import CircuitBreakerRegistry, CircuitOpenError

logger = structlog.get_logger("dds.wb_fbs_api")

# ─── Хосты ──────────────────────────────────────────────────────────────────
WB_FBS_PROD_BASE = "https://marketplace-api.wildberries.ru"
WB_FBS_SANDBOX_BASE = "https://marketplace-api-sandbox.wildberries.ru"

# ─── Текст отказа в режиме safe (уходит пользователю как есть) ──────────────
WRITE_BLOCKED_MESSAGE = (
    "Режим контура WB FBS — «safe»: запись в WB отключена. Остатки не передаются, "
    "поставки и склады продавца не создаются и не меняются. Чтобы разрешить запись, "
    "переключите WB_FBS_MODE в «prod» (боевой кабинет) или «sandbox» (песочница)."
)

# ─── Тайминги ───────────────────────────────────────────────────────────────
TIMEOUT = 30.0  # на один HTTP-запрос
MAX_RETRIES = 2  # максимум 2 ПОВТОРА (итого ≤3 попытки)
RETRY_BASE_DELAY = 1.0  # backoff для 5xx: 1 c, 2 c
RETRY_MAX_DELAY = 8.0
RETRY_AFTER_DEFAULT = 5.0  # если WB не прислал X-Ratelimit-Retry
RETRY_AFTER_MAX = 30.0  # верхняя отсечка паузы по 429
#: Полный бюджет ОДНОГО `_request` вместе с ретраями и паузами по 429.
#:
#: Без него счёт попыток — единственное ограничение, и под 429 один запрос
#: занимал до `3×TIMEOUT + 2×RETRY_AFTER_MAX` ≈ 210 c. Внешний `asyncio.wait_for`
#: в джобах короче (`NEW_ORDERS_TIMEOUT_SEC` = 120 c, а у хвоста проектов —
#: `MIN_PROJECT_TIMEOUT_SEC` = 30 c), поэтому корутину убивали раньше, чем
#: клиент успевал отдать осмысленную ошибку: в SyncLog вместо «лимит WB
#: исчерпан» ложился безликий «Timeout», а `finished_at − started_at` РОВНО
#: совпадал с таймаутом — тот же почерк, что у прод-бага синков рекламы.
#: Инвариант REQUEST_TOTAL_BUDGET_SEC < NEW_ORDERS_TIMEOUT_SEC держит тест
#: tests/test_wb_fbs_jobs.py::TestTimeBudgets.
REQUEST_TOTAL_BUDGET_SEC = 75.0

# ─── Размеры чанков (жёсткие лимиты WB) ─────────────────────────────────────
STOCKS_CHUNK = 1000  # PUT/POST/DELETE /api/v3/stocks/{warehouseId}
ORDERS_STATUS_CHUNK = 1000  # POST /api/v3/orders/status
STICKERS_CHUNK = 100  # POST /api/v3/orders/stickers
SUPPLY_ORDERS_CHUNK = 100  # PATCH /api/marketplace/v3/supplies/{id}/orders
ORDERS_PAGE_LIMIT = 1000  # GET /api/v3/orders?limit=
SUPPLIES_PAGE_LIMIT = 1000  # GET /api/v3/supplies?limit=


# ─── Ошибки ─────────────────────────────────────────────────────────────────


class WbFbsApiError(Exception):
    """Ошибка WB Marketplace API. `status=0` — сетевая ошибка/таймаут."""

    def __init__(self, status: int, code: str | None, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"WB FBS API {status}{f' [{code}]' if code else ''}: {message}")


class WbFbsClientError(WbFbsApiError):
    """4xx кроме 429 — не ретраится и НЕ считается отказом сервиса (брейкер её игнорирует).

    Это доменные ответы WB (409 `StoreIsProcessing`, 406 `WarehouseStocksUpdateBlock`,
    401 «нет скоупа», 404 …), а не признак того, что API лежит.
    """


class WbFbsRateLimited(WbFbsApiError):
    """429 — исчерпан бакет. `retry_after` — секунды из `X-Ratelimit-Retry`."""

    def __init__(self, message: str, retry_after: float, code: str | None = None):
        self.retry_after = retry_after
        super().__init__(429, code, message)


class WbFbsWriteBlocked(WbFbsClientError):
    """Режим `safe`: WRITE-метод не выпущен наружу. Наверх — 409, а не 500.

    Наследуется от `WbFbsClientError` намеренно: это доменный отказ НАШЕГО
    контура, а не сбой WB — брейкер такие ошибки игнорирует.
    """

    def __init__(self, message: str | None = None, code: str = "WriteBlocked") -> None:
        super().__init__(409, code, message or WRITE_BLOCKED_MESSAGE)


# ─── Режим контура ──────────────────────────────────────────────────────────


def current_mode() -> str:
    """Активный режим FBS: `safe` | `sandbox` | `prod`.

    Зеркалит нормализацию `Settings._resolve_wb_fbs_mode`, потому что значение
    в рантайме могут подменить (тесты, ручная правка `settings`), а падать или
    молча писать в боевой WB из-за мусора в поле нельзя: неизвестное значение
    и пустая строка → самый безопасный режим (с учётом legacy `WB_FBS_SANDBOX`).
    """
    raw = str(getattr(settings, "WB_FBS_MODE", "") or "").strip().lower()
    if raw in WB_FBS_MODES:
        return raw
    if not raw and getattr(settings, "WB_FBS_SANDBOX", False):
        return WB_FBS_MODE_SANDBOX
    return WB_FBS_MODE_SAFE


def is_write_enabled(mode: str | None = None) -> bool:
    """Разрешена ли запись в WB. `safe` → нет, остальные два → да."""
    return (mode or current_mode()) in (WB_FBS_MODE_SANDBOX, WB_FBS_MODE_PROD)


def resolve_base_url(mode: str | None = None) -> str:
    """Хост контура: песочница только в режиме `sandbox`, иначе прод/WB_FBS_API_BASE."""
    if (mode or current_mode()) == WB_FBS_MODE_SANDBOX:
        return WB_FBS_SANDBOX_BASE
    return str(getattr(settings, "WB_FBS_API_BASE", "") or WB_FBS_PROD_BASE).rstrip("/")


# Свой реестр брейкеров: один на проект, изолирован от `wb_api._wb_circuits`.
# 429 и доменные 4xx отказом не считаются.
_fbs_circuits = CircuitBreakerRegistry(
    name_prefix="wb_fbs",
    failure_threshold=5,
    recovery_timeout=120.0,
    exclude_errors=(WbFbsRateLimited, WbFbsClientError),
)


# ─── Хелперы ────────────────────────────────────────────────────────────────


async def _sleep(seconds: float) -> None:
    """Пауза между попытками. Отдельная функция — чтобы тесты её подменяли."""
    await asyncio.sleep(seconds)


def _chunks(items: Sequence[Any], size: int) -> Iterator[list[Any]]:
    """Нарезка последовательности на куски ≤size."""
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _redact(text: str, secret: str) -> str:
    """Вырезать токен из текста (страховка: WB иногда эхом возвращает заголовки)."""
    if secret and secret in text:
        text = text.replace(secret, "***")
    return text


def _backoff(attempt: int) -> float:
    return min(RETRY_BASE_DELAY * (2**attempt), RETRY_MAX_DELAY)


def _retry_after(resp: httpx.Response) -> float:
    """Пауза по 429: `X-Ratelimit-Retry`, фолбэк `Retry-After`, затем дефолт."""
    for header in ("X-Ratelimit-Retry", "Retry-After"):
        raw = resp.headers.get(header)
        if raw:
            try:
                return max(0.0, min(float(raw), RETRY_AFTER_MAX))
            except ValueError:
                continue
    return RETRY_AFTER_DEFAULT


def _parse_error(resp: httpx.Response, secret: str) -> tuple[str | None, str]:
    """Достать (code, message) из тела ошибки WB. Тело бывает не-JSON."""
    code: str | None = None
    message = ""
    try:
        body = resp.json()
    except Exception:
        body = None
    if isinstance(body, dict):
        raw_code = body.get("code")
        code = str(raw_code) if raw_code else None
        for key in ("message", "detail", "title", "errorText"):
            value = body.get(key)
            if isinstance(value, str) and value:
                message = value
                break
    if not message:
        message = (resp.text or "")[:300]
    return code, _redact(message, secret)


def _as_list(payload: Any, key: str) -> list[dict]:
    """WB отдаёт то `{"orders": [...]}`, то голый список — принимаем оба варианта."""
    if isinstance(payload, dict):
        value = payload.get(key)
        return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def _listify(payload: Any) -> list[dict]:
    """Ответ-массив верхнего уровня (`/offices`, `/warehouses`)."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    return []


def _unix(dt: datetime | None) -> int | None:
    return int(dt.timestamp()) if dt else None


def _seg(value: str | int) -> str:
    """Сегмент пути (id поставки — строка вида `WB-GI-…`)."""
    return quote(str(value), safe="")


# ─── Клиент ─────────────────────────────────────────────────────────────────


class WbFbsClient:
    """Клиент WB Marketplace API для FBS-контура.

    Один инстанс = один проект (свой circuit breaker). Живёт в рамках одной операции,
    HTTP-соединение открывается на каждый запрос (как во всех клиентах проекта).

    `transport` — только для тестов (`httpx.MockTransport`), в проде не задаётся.
    """

    def __init__(
        self,
        api_key: str,
        project_id: int,
        base_url: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.project_id = project_id
        self.base_url = (base_url or self._default_base_url()).rstrip("/")
        self._headers = {"Authorization": api_key}
        self._transport = transport
        self._circuit = _fbs_circuits.get(project_id)

    @staticmethod
    def _default_base_url() -> str:
        """Хост по режиму контура. Явный `base_url` конструктора перебивает его."""
        return resolve_base_url()

    # ─── Единственная точка выхода в сеть ───────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
        expect_json: bool = True,
        write: bool = False,
    ) -> Any:
        """HTTP к WB с брейкером и ограниченными ретраями.

        429 → пауза по `X-Ratelimit-Retry`; 5xx и сетевые сбои → backoff;
        прочие 4xx → `WbFbsClientError` немедленно (4XX стоит 10 запросов бакета).
        Возвращает распарсенный JSON или None (204 / `expect_json=False`).

        `write=True` — метод МЕНЯЕТ состояние в кабинете WB. Это единственная
        точка гейта режима: в `safe` такой вызов падает `WbFbsWriteBlocked` ДО
        любого HTTP. Классификация по эффекту, а не по HTTP-глаголу: стикеры и
        QR поставки — тоже POST/GET, но состояния не меняют, значит READ.
        """
        if write and not is_write_enabled():
            logger.info("wb_fbs.write_blocked", method=method, path=path, mode=current_mode())
            raise WbFbsWriteBlocked()
        url = f"{self.base_url}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            return await self._send(method, path, url, json, clean_params, expect_json)
        except CircuitOpenError as e:
            # Наружу — единый тип ошибки домена: вызывающим не нужно знать про брейкер.
            raise WbFbsApiError(503, "CircuitOpen", str(e)) from None

    async def _send(
        self,
        method: str,
        path: str,
        url: str,
        json: Any,
        clean_params: dict,
        expect_json: bool,
    ) -> Any:
        """Тело `_request`: брейкер + цикл попыток. Отдельно только ради разворота
        `CircuitOpenError` — вся обработка статусов живёт здесь и больше нигде."""
        started = time.monotonic()

        def budget_left(pause: float) -> bool:
            """Успеет ли ещё одна попытка уложиться в бюджет запроса.

            Считаем ВМЕСТЕ с паузой: уснуть на 30 c, чтобы тут же быть убитым
            внешним таймаутом, — худший вариант из всех. Лучше отдать честную
            ошибку сейчас и дать вызывающему записать её в журнал.
            """
            return (time.monotonic() - started) + pause < REQUEST_TOTAL_BUDGET_SEC

        async with self._circuit:
            async with httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport) as client:
                retries = 0
                while True:
                    try:
                        resp = await client.request(
                            method,
                            url,
                            headers=self._headers,
                            json=json,
                            params=clean_params or None,
                        )
                    except httpx.HTTPError as e:
                        detail = _redact(f"{type(e).__name__}: {e}", self.api_key)
                        if retries >= MAX_RETRIES or not budget_left(_backoff(retries)):
                            logger.warning("wb_fbs.network_error", path=path, error=detail[:200])
                            raise WbFbsApiError(0, None, f"сетевая ошибка: {detail[:200]}") from None
                        await _sleep(_backoff(retries))
                        retries += 1
                        continue

                    status = resp.status_code

                    if status == 429:
                        pause = _retry_after(resp)
                        code, _ = _parse_error(resp, self.api_key)
                        if retries >= MAX_RETRIES or not budget_left(pause):
                            logger.warning("wb_fbs.rate_limited", path=path, retry_after=pause)
                            raise WbFbsRateLimited(
                                f"лимит запросов WB исчерпан ({method} {path})",
                                retry_after=pause,
                                code=code,
                            )
                        logger.info("wb_fbs.retry", path=path, reason="429", pause=pause)
                        await _sleep(pause)
                        retries += 1
                        continue

                    if status >= 500:
                        code, message = _parse_error(resp, self.api_key)
                        if retries >= MAX_RETRIES or not budget_left(_backoff(retries)):
                            raise WbFbsApiError(status, code, message or f"{method} {path}")
                        logger.info("wb_fbs.retry", path=path, reason=str(status))
                        await _sleep(_backoff(retries))
                        retries += 1
                        continue

                    if status >= 400:
                        # НЕ ретраим: каждый 4XX стоит 10 запросов бакета.
                        code, message = _parse_error(resp, self.api_key)
                        logger.warning("wb_fbs.client_error", path=path, status=status, code=code)
                        raise WbFbsClientError(status, code, message or f"{method} {path}")

                    logger.info("wb_fbs.request", method=method, path=path, status=status)
                    if not expect_json or status == 204 or not resp.content:
                        return None
                    try:
                        return resp.json()
                    except Exception:
                        raise WbFbsApiError(status, None, "WB вернул не-JSON ответ") from None

    # ─── Проба токена ───────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """`GET /ping` — жив ли API и есть ли у токена категория «Маркетплейс»."""
        try:
            await self._request("GET", "/ping")
        except WbFbsApiError as e:
            logger.info("wb_fbs.ping_failed", status=e.status, code=e.code)
            return False
        return True

    # ─── Склады ─────────────────────────────────────────────────────────────

    async def list_offices(self) -> list[dict]:
        """Список складов WB (пунктов приёмки) — `id, name, address, city, cargoType, …`."""
        return _listify(await self._request("GET", "/api/v3/offices"))

    async def list_warehouses(self) -> list[dict]:
        """Склады продавца — `id, name, officeId, cargoType, deliveryType, isDeleting, isProcessing`."""
        return _listify(await self._request("GET", "/api/v3/warehouses"))

    async def create_warehouse(self, name: str, office_id: int) -> int:
        """WRITE. Создать склад продавца → id. WB отвечает 201."""
        data = await self._request(
            "POST",
            "/api/v3/warehouses",
            json={"name": name, "officeId": office_id},
            write=True,
        )
        if not isinstance(data, dict) or data.get("id") is None:
            raise WbFbsApiError(201, None, "WB не вернул id созданного склада")
        return int(data["id"])

    async def update_warehouse(self, wb_warehouse_id: int, name: str, office_id: int | None = None) -> None:
        """WRITE. Переименовать склад / сменить офис. Смена офиса — раз в сутки."""
        payload: dict[str, Any] = {"name": name}
        if office_id is not None:
            payload["officeId"] = office_id
        await self._request(
            "PUT",
            f"/api/v3/warehouses/{_seg(wb_warehouse_id)}",
            json=payload,
            expect_json=False,
            write=True,
        )

    async def delete_warehouse(self, wb_warehouse_id: int) -> None:
        """WRITE. Удалить склад продавца в кабинете WB."""
        await self._request(
            "DELETE",
            f"/api/v3/warehouses/{_seg(wb_warehouse_id)}",
            expect_json=False,
            write=True,
        )

    # ─── Остатки (ключ — chrtId, не баркод: sku выпилен 09.02.2026) ──────────

    async def put_stocks(self, wb_warehouse_id: int, items: list[tuple[int, int]]) -> None:
        """WRITE. Загрузить остатки чанками по 1000.

        ГРАБЛЯ WB: имена полей не валидируются — 204 приходит даже когда остаток
        не обновился. После каждого PUT обязательна верификация через `get_stocks`.
        """
        if not items:
            return
        for chunk in _chunks(items, STOCKS_CHUNK):
            await self._request(
                "PUT",
                f"/api/v3/stocks/{_seg(wb_warehouse_id)}",
                json={"stocks": [{"chrtId": int(chrt_id), "amount": int(amount)} for chrt_id, amount in chunk]},
                expect_json=False,
                write=True,
            )

    async def get_stocks(self, wb_warehouse_id: int, chrt_ids: list[int]) -> dict[int, int]:
        """READ (POST по HTTP, состояние WB не меняет): `{chrtId: amount}`. Чанки по 1000."""
        if not chrt_ids:
            return {}
        result: dict[int, int] = {}
        for chunk in _chunks(chrt_ids, STOCKS_CHUNK):
            data = await self._request(
                "POST",
                f"/api/v3/stocks/{_seg(wb_warehouse_id)}",
                json={"chrtIds": [int(x) for x in chunk]},
            )
            for row in _as_list(data, "stocks"):
                chrt_id = row.get("chrtId")
                if chrt_id is None:
                    continue
                result[int(chrt_id)] = int(row.get("amount") or 0)
        return result

    async def delete_stocks(self, wb_warehouse_id: int, chrt_ids: list[int]) -> None:
        """WRITE. НЕОБРАТИМО и всего 10 запросов/мин — в фоновых джобах не использовать."""
        if not chrt_ids:
            return
        for chunk in _chunks(chrt_ids, STOCKS_CHUNK):
            await self._request(
                "DELETE",
                f"/api/v3/stocks/{_seg(wb_warehouse_id)}",
                json={"chrtIds": [int(x) for x in chunk]},
                expect_json=False,
                write=True,
            )

    # ─── Сборочные задания ──────────────────────────────────────────────────

    async def get_new_orders(self) -> list[dict]:
        """Новые сборочные задания (без пагинации). Цены приходят ×100 (в копейках)."""
        return _as_list(await self._request("GET", "/api/v3/orders/new"), "orders")

    async def get_orders(
        self,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        max_pages: int = 50,
    ) -> list[dict]:
        """Задания за период (окно ≤30 дней, глубина 3 месяца). Пагинация через `next`."""
        orders: list[dict] = []
        cursor: int = 0
        for _ in range(max(1, max_pages)):
            data = await self._request(
                "GET",
                "/api/v3/orders",
                params={
                    "limit": ORDERS_PAGE_LIMIT,
                    "next": cursor,
                    "dateFrom": _unix(date_from),
                    "dateTo": _unix(date_to),
                },
            )
            page = _as_list(data, "orders")
            orders.extend(page)
            next_cursor = data.get("next") if isinstance(data, dict) else None
            if not page or not next_cursor or int(next_cursor) == cursor:
                break
            cursor = int(next_cursor)
        return orders

    async def get_orders_status(self, order_ids: list[int]) -> list[dict]:
        """Статусы заданий: `[{id, isCancellable, supplierStatus, wbStatus}]`. Чанки по 1000."""
        if not order_ids:
            return []
        result: list[dict] = []
        for chunk in _chunks(order_ids, ORDERS_STATUS_CHUNK):
            data = await self._request(
                "POST",
                "/api/v3/orders/status",
                json={"orders": [int(x) for x in chunk]},
            )
            result.extend(_as_list(data, "orders"))
        return result

    async def cancel_order(self, order_id: int) -> None:
        """WRITE. Отменить задание. Работает, только пока `isCancellable`. Лимит 100/мин."""
        await self._request(
            "PATCH",
            f"/api/v3/orders/{_seg(order_id)}/cancel",
            expect_json=False,
            write=True,
        )

    async def get_stickers(
        self,
        order_ids: list[int],
        sticker_type: str = "png",
        width: int = 58,
        height: int = 40,
    ) -> list[dict]:
        """READ (POST по HTTP, но состояния WB не меняет): стикеры заданий (`file` — base64).

        Чанки по 100; WB отдаёт стикеры только для `confirm`/`complete`.
        """
        if not order_ids:
            return []
        result: list[dict] = []
        for chunk in _chunks(order_ids, STICKERS_CHUNK):
            data = await self._request(
                "POST",
                "/api/v3/orders/stickers",
                json={"orders": [int(x) for x in chunk]},
                params={"type": sticker_type, "width": width, "height": height},
            )
            result.extend(_as_list(data, "stickers"))
        return result

    # ─── Поставки ───────────────────────────────────────────────────────────

    async def create_supply(self, name: str) -> str:
        """WRITE. Создать поставку → id вида `WB-GI-…`. WB отвечает 201."""
        data = await self._request("POST", "/api/v3/supplies", json={"name": name}, write=True)
        supply_id = data.get("id") if isinstance(data, dict) else None
        if not supply_id:
            raise WbFbsApiError(201, None, "WB не вернул id созданной поставки")
        return str(supply_id)

    async def list_supplies(self, max_pages: int = 20) -> list[dict]:
        """Список поставок. Пагинация через `next`."""
        supplies: list[dict] = []
        cursor: int = 0
        for _ in range(max(1, max_pages)):
            data = await self._request(
                "GET",
                "/api/v3/supplies",
                params={"limit": SUPPLIES_PAGE_LIMIT, "next": cursor},
            )
            page = _as_list(data, "supplies")
            supplies.extend(page)
            next_cursor = data.get("next") if isinstance(data, dict) else None
            if not page or not next_cursor or int(next_cursor) == cursor:
                break
            cursor = int(next_cursor)
        return supplies

    async def get_supply(self, supply_id: str) -> dict:
        """Детали поставки: `done, cargoType, crossBorderType, destinationOfficeId, …`."""
        data = await self._request("GET", f"/api/v3/supplies/{_seg(supply_id)}")
        return data if isinstance(data, dict) else {}

    async def get_supply_order_ids(self, supply_id: str) -> list[int]:
        """id заданий, входящих в поставку."""
        data = await self._request("GET", f"/api/marketplace/v3/supplies/{_seg(supply_id)}/order-ids")
        raw: Any = data
        if isinstance(data, dict):
            for key in ("orders", "orderIds", "ids"):
                if isinstance(data.get(key), list):
                    raw = data[key]
                    break
            else:
                raw = []
        if not isinstance(raw, list):
            return []
        ids: list[int] = []
        for item in raw:
            value = item.get("id") if isinstance(item, dict) else item
            if value is not None:
                ids.append(int(value))
        return ids

    async def add_orders_to_supply(self, supply_id: str, order_ids: list[int]) -> None:
        """WRITE. Добавить задания в поставку (чанки по 100) — переводит их в `confirm`.

        ГАБАРИТНЫЙ ЗАЛИПОН: первое добавленное задание фиксирует `cargoType`/
        `crossBorderType` поставки; дальше принимаются только такие же, и задания
        с разных складов в одну поставку нельзя. Проверять ДО вызова (полоса 3).
        """
        if not order_ids:
            return
        for chunk in _chunks(order_ids, SUPPLY_ORDERS_CHUNK):
            await self._request(
                "PATCH",
                f"/api/marketplace/v3/supplies/{_seg(supply_id)}/orders",
                json={"orders": [int(x) for x in chunk]},
                expect_json=False,
                write=True,
            )

    async def deliver_supply(self, supply_id: str) -> None:
        """WRITE. Передать поставку в доставку. 409: `SupplyHasZeroOrders`, `MetaValidationFail`."""
        await self._request(
            "PATCH",
            f"/api/v3/supplies/{_seg(supply_id)}/deliver",
            expect_json=False,
            write=True,
        )

    async def delete_supply(self, supply_id: str) -> None:
        """WRITE. Удалить поставку — только активную и пустую."""
        await self._request(
            "DELETE",
            f"/api/v3/supplies/{_seg(supply_id)}",
            expect_json=False,
            write=True,
        )

    async def get_supply_barcode(self, supply_id: str, sticker_type: str = "png") -> dict:
        """READ (GET, состояния не меняет): QR поставки (`{barcode, file}`).

        Доступен только ПОСЛЕ deliver, иначе WB отвечает 409 `SupplyNotClosed`.
        """
        data = await self._request(
            "GET",
            f"/api/v3/supplies/{_seg(supply_id)}/barcode",
            params={"type": sticker_type},
        )
        return data if isinstance(data, dict) else {}


__all__ = [
    "WbFbsApiError",
    "WbFbsClientError",
    "WbFbsRateLimited",
    "WbFbsWriteBlocked",
    "WbFbsClient",
    "WB_FBS_PROD_BASE",
    "WB_FBS_SANDBOX_BASE",
    "WRITE_BLOCKED_MESSAGE",
    "current_mode",
    "is_write_enabled",
    "resolve_base_url",
]
