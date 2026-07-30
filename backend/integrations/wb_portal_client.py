"""
Клиент реплея внутреннего API кабинета Wildberries (seller-supply.wildberries.ru).

НЕ официальный API — воспроизводит те же JSON-RPC-вызовы, что делает браузер на
странице «Поставки» портала. Нужен для операций, которых нет в публичном
suppliers-api: создание черновика FBW-поставки, наполнение, преордер, короба,
пропуск.

Авторизация (доказано probe'ом 2026-07-08): токенов-заголовков НЕДОСТАТОЧНО —
supply-manager закрыт анти-ботом за HttpOnly-cookie `.wildberries.ru`
(`wbx-validation-key` + `__zzatw-wb` + `cfidsw-wb`). Именно их не было в HAR
(HttpOnly не виден ни JS, ни экспортёру HAR) — оттого любой серверный клиент
получал 401. С cookie обычный httpx (h1/h2) отдаёт 200 БЕЗ браузера в рантайме.

Поэтому сессия теперь — не голый authorizev3, а «бандл»:
    {"authorizev3": "...", "wb_seller_lk": "...", "root_version": "v1.100.0",
     "cookies": [{"name","value","domain","path"}...]}
Бандл собирается один раз браузером (scripts harvest, ~раз/неделю по SMS) и
кладётся в ту же зашифрованную ячейку сессии. Конструктор принимает и бандл
(JSON-строку/dict), и голый токен (легаси — без cookie, оставлен для совместимости).

Заголовки:
  - authorizev3 — серверная сессия (без self-exp; живёт, пока WB держит сессию);
  - wb-seller-lk — короткий токен (TTL ~5 мин), обновляется через auth/token
    (тоже требует cookie);
  - cookie — анти-бот/сессия `.wildberries.ru`, отдаются httpx cookie-jar'ом;
    Set-Cookie ответов подмешиваются обратно в jar (переживают ротацию в рамках
    жизни клиента).

Финальная бронь даты (plan/add) дополнительно закрыта одноразовым
x-wb-captcha-token (антибот-челлендж ADD_OR_UPDATE_SUPPLY, fingerprint JS) —
здесь НЕ реализована: токен требует браузерного тика, см. wb_supply_service.

Ошибка сессии (401/протух authorizev3/cookie) → WbSessionExpired, чтобы вызвавший
слой пометил интеграцию EXPIRED и попросил пользователя обновить доступ.
"""

import asyncio
import itertools
import json

import httpx
import structlog

from backend.utils.phone import normalize_ru_phone

logger = structlog.get_logger("dds.wb_portal")

SUPPLY_BASE = "https://seller-supply.wildberries.ru"
AUTH_BASE = "https://seller.wildberries.ru"
CMP_BASE = "https://cmp.wildberries.ru"  # кабинет рекламы (стата кампаний) — GET, authorizev3
# Биржа карточек товаров (card-exchange showcase) — REST (НЕ JSON-RPC), тот же host,
# та же авторизация authorizev3+cookie. Проксируем витрину/справочник/корзину в DDS2.
EXC_PREFIX = "/ns/exc-api/monetization/api/v1"
ROOT_VERSION = "v1.100.0"
TIMEOUT = 30

# Ретрай на транзиентный «черновик не найден»: реплика черновика между узлами WB
# догоняет за <1-2с (см. _ensure_client про connection-affinity).
_DRAFT_RETRIES = 5
_DRAFT_BACKOFF = 0.6  # сек, линейный рост

# UA/sec-ch-ua как у реального Chrome (совпадает с харвест-сессией). Не является
# дискриминатором доступа (гейт — cookie), но держим консистентным с браузером.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
SEC_CH_UA = '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"'


class WbPortalError(Exception):
    """Ошибка реплея портала (не-сессионная): WB вернул JSON-RPC error или не-2xx."""


class WbSessionExpired(WbPortalError):
    """authorizev3/cookie недействительны — нужно обновить доступ в настройках."""


def _parse_session(session: "str | dict") -> tuple[str, str | None, list[dict], str]:
    """
    Разобрать сессию: JSON-бандл {authorizev3, wb_seller_lk, cookies, root_version}
    ИЛИ голый authorizev3 (легаси). Возвращает (authorizev3, lk, cookies, root_version).
    """
    data: dict | None = None
    if isinstance(session, dict):
        data = session
    else:
        s = (session or "").strip()
        if s.startswith("{"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, dict):
                    data = parsed
            except (ValueError, TypeError):
                data = None
        if data is None:
            return s, None, [], ROOT_VERSION  # голый токен
    cookies = data.get("cookies") or []
    cookies = [c for c in cookies if isinstance(c, dict) and c.get("name")]
    return (
        (data.get("authorizev3") or "").strip(),
        data.get("wb_seller_lk") or None,
        cookies,
        data.get("root_version") or ROOT_VERSION,
    )


def _stat_val(field: object) -> float:
    """campaigns-stats оборачивает каждую метрику в {value, percent} — берём value."""
    if isinstance(field, dict):
        field = field.get("value")
    try:
        return float(field or 0)
    except (TypeError, ValueError):
        return 0.0


class WbPortalClient:
    """
    Реплей-клиент кабинета WB. Инстанс на одну сессию (бандл authorizev3+cookie).

    wb-seller-lk кэшируется и лениво обновляется: если не передан или получен 401,
    дёргаем auth/token по authorizev3+cookie и повторяем вызов один раз.
    """

    def __init__(self, session: "str | dict", wb_seller_lk: str | None = None):
        authorizev3, lk, cookies, root_version = _parse_session(session)
        self.authorizev3 = authorizev3
        self.wb_seller_lk = wb_seller_lk or lk
        self._root_version = root_version
        self._id_counter = itertools.count(1)
        self._client: httpx.AsyncClient | None = None
        # Персистентный cookie-jar инстанса: сидируется бандлом, обновляется
        # Set-Cookie'ами (ротация анти-бот-cookie переживает вызовы одного клиента).
        self._jar = httpx.Cookies()
        for c in cookies:
            self._jar.set(
                c["name"], c.get("value", ""),
                domain=c.get("domain", ".wildberries.ru"), path=c.get("path", "/"),
            )

    # ─── low-level ────────────────────────────────────────────────────────

    def _rpc_id(self) -> str:
        return f"json-rpc_{next(self._id_counter)}"

    def _headers(self) -> dict[str, str]:
        h = {
            "authorizev3": self.authorizev3,
            "content-type": "application/json",
            "root-version": self._root_version,
            "origin": AUTH_BASE,
            "referer": AUTH_BASE + "/",
            "user-agent": USER_AGENT,
            "accept": "*/*",
            "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "sec-ch-ua": SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
        }
        if self.wb_seller_lk:
            h["wb-seller-lk"] = self.wb_seller_lk
        return h

    def _ensure_client(self) -> httpx.AsyncClient:
        """
        Один shared keep-alive httpx.AsyncClient на инстанс. КРИТИЧНО: WB роутит
        черновик по connection-affinity — на НОВОМ TCP-соединении следующий вызов
        может попасть на другой узел, где черновик ещё не реплицировался
        (`draft not found`, воспроизведено). Одно живое соединение = read-after-write
        консистентность. Свой cookie-jar клиента переживает ротацию Set-Cookie между
        вызовами. Закрытие — по желанию (`aclose`/`async with`); иначе освободится
        при GC инстанса (сервис создаёт клиента на одну операцию).
        """
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=TIMEOUT,
                cookies=self._jar,
                limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=60.0),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def __aenter__(self) -> "WbPortalClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _refresh_lk(self) -> None:
        """Обновить короткий wb-seller-lk через authorizev3 + cookie."""
        resp = await self._ensure_client().post(
            AUTH_BASE + "/ns/suppliers-auth/suppliers-portal-core/auth/token",
            headers=self._headers(),
            json={"params": {}, "jsonrpc": "2.0", "id": self._rpc_id()},
        )
        if resp.status_code == 401:
            raise WbSessionExpired("authorizev3/cookie недействительны при обновлении wb-seller-lk")
        if resp.status_code >= 400:
            raise WbPortalError(f"auth/token: HTTP {resp.status_code}")
        data = resp.json()
        token = (data.get("result") or {}).get("data", {}).get("token")
        if not token:
            # Не дампим сырой ответ портала (может содержать PII) — общий текст.
            raise WbSessionExpired("auth/token не вернул токен сессии")
        self.wb_seller_lk = token

    async def _raw_post(self, url: str, body: dict | list) -> dict | list:
        """Один POST через shared-клиент с ретраем на 401 (обновление wb-seller-lk)."""
        client = self._ensure_client()
        try:
            for attempt in range(2):
                resp = await client.post(url, headers=self._headers(), json=body)
                if resp.status_code == 401 and attempt == 0:
                    # Протух короткий токен — обновляем и повторяем один раз.
                    await self._refresh_lk()
                    continue
                if resp.status_code == 401:
                    raise WbSessionExpired(f"401 на {url} после обновления токена")
                if resp.status_code >= 400:
                    raise WbPortalError(self._http_error(url, resp))
                return resp.json()
        except httpx.HTTPError as e:
            # Таймаут/обрыв/DNS — в доменную ошибку, чтобы стадия ушла в ERROR, а не 500.
            raise WbPortalError(f"WB недоступен ({url}): {type(e).__name__}") from e
        raise WbPortalError(f"Не удалось выполнить {url}")

    async def _raw_get(self, base: str, path: str, params: dict) -> dict | list:
        """GET на кабинетный хост (cmp) через shared-клиент с authorizev3+cookie.

        В отличие от supply-POST, cmp-стата НЕ использует wb-seller-lk, поэтому 401 здесь =
        недействительный authorizev3/cookie → сразу WbSessionExpired (без refresh_lk,
        который ходит в supply-auth и к рекламному кабинету отношения не имеет).
        origin/referer подменяем на cmp — зеркало реального вызова браузера.
        """
        client = self._ensure_client()
        headers = {**self._headers(), "origin": base, "referer": base + "/"}
        headers.pop("wb-seller-lk", None)  # supply-токен на cmp не нужен
        try:
            resp = await client.get(base + path, headers=headers, params=params)
        except httpx.HTTPError as e:
            raise WbPortalError(f"WB недоступен ({base + path}): {type(e).__name__}") from e
        if resp.status_code == 401:
            raise WbSessionExpired(
                f"401 на {path}: сессия рекламного кабинета недействительна (нужен свежий доступ WB)"
            )
        if resp.status_code >= 400:
            raise WbPortalError(self._http_error(path, resp))
        return resp.json()

    async def _post(self, base: str, path: str, body: dict | list) -> dict | list:
        """
        POST JSON-RPC. Ленивое обновление wb-seller-lk + ретрай на транзиентный
        `draft not found` (реплика черновика догоняет). Возвращает распарсенный
        `result` (одиночный RPC) или сырой ответ (батч-массив).
        """
        if not self.wb_seller_lk:
            await self._refresh_lk()
        url = base + path
        data: dict | list = {}
        for i in range(_DRAFT_RETRIES):
            data = await self._raw_post(url, body)
            if not self._is_draft_not_found(data):
                return self._unwrap(data, path)
            await asyncio.sleep(_DRAFT_BACKOFF * (i + 1))
        return self._unwrap(data, path)  # ретраи исчерпаны → поднимет WbPortalError

    @staticmethod
    def _unwrap(data: dict | list, path: str) -> dict | list:
        """Достать result из JSON-RPC-ответа, подняв WbPortalError на error."""
        # Батч-эндпоинты (sm-box) отвечают массивом — отдаём как есть.
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("error"):
                    raise WbPortalError(f"{path}: {item['error']}")
            return data
        if data.get("error"):
            raise WbPortalError(f"{path}: {data['error']}")
        return data.get("result", {})

    @staticmethod
    def _http_error(path: str, resp: httpx.Response) -> str:
        """Текст ошибки не-2xx: доменный error из тела, иначе — код статуса."""
        try:
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                return f"{path}: {data['error']}"
        except Exception:
            pass
        return f"{path}: HTTP {resp.status_code}"

    @staticmethod
    def _is_draft_not_found(data: dict | list) -> bool:
        """Транзиентная «черновик не найден» (реплика догоняет) — стоит ретрая."""
        if not isinstance(data, dict):
            return False
        err = data.get("error")
        if not isinstance(err, dict):
            return False
        edata = err.get("data") or {}
        if edata.get("trKey") == "error.draft_not_found":
            return True
        blob = f"{err.get('message', '')} {edata.get('innerMsg', '')} {edata.get('msg', '')}".lower()
        return "draft" in blob and "not found" in blob

    async def _call(self, params: dict, base: str, path: str) -> dict:
        """Одиночный JSON-RPC; возвращает result-объект (dict)."""
        res = await self._post(
            base, path, {"params": params, "jsonrpc": "2.0", "id": self._rpc_id()}
        )
        return res if isinstance(res, dict) else {}

    # ─── auth check ───────────────────────────────────────────────────────

    async def check_session(self) -> bool:
        """Проверить, жива ли сессия (обновлением wb-seller-lk). True/exception."""
        try:
            await self._refresh_lk()
        except httpx.HTTPError as e:
            raise WbPortalError(f"WB недоступен (check_session): {type(e).__name__}") from e
        finally:
            await self.aclose()
        return True

    # ─── реклама: кабинетная стата кампаний (cmp) ─────────────────────────

    async def fetch_campaigns_stats(
        self, campaign_ids: list[int], date_from: str, date_to: str
    ) -> list[dict]:
        """Стата кампаний за период из кабинета рекламы (cmp/api/v1/campaigns-stats).

        Для внутридневных снимков зовётся с date_from == date_to == сегодня (МСК): WB
        отдаёт НАКОПИТЕЛЬНЫЙ счётчик за день (обнуляется в полночь МСК). Дельта между
        соседними снимками = показы/клики/расход за интервал (см. WbAdCampaignSnapshot).

        Возвращает [{campaign_id, views, clicks, spend}]. clicks WB напрямую не отдаёт —
        восстанавливаем из views·ctr% (как считает сам кабинет). Даты — 'YYYY-MM-DD'.
        """
        if not campaign_ids:
            return []
        ids_csv = ",".join(str(i) for i in campaign_ids)
        data = await self._raw_get(
            CMP_BASE,
            "/api/v1/campaigns-stats",
            {"campaigns_ids": ids_csv, "from": date_from, "to": date_to},
        )
        rows = data.get("data") if isinstance(data, dict) else None
        out: list[dict] = []
        for r in rows or []:
            if not isinstance(r, dict) or r.get("id") is None:
                continue
            views = int(_stat_val(r.get("views")))
            ctr = _stat_val(r.get("ctr"))
            out.append(
                {
                    "campaign_id": int(r["id"]),
                    "views": views,
                    "clicks": round(views * ctr / 100),
                    "spend": _stat_val(r.get("sum")),
                }
            )
        return out

    # ─── биржа карточек товаров (card-exchange showcase) ──────────────────
    # REST на seller.wildberries.ru/ns/exc-api/... (НЕ JSON-RPC): тело POST —
    # это сам объект фильтра, ответ обёрнут в {"data":..., "error":bool, "errorText"}.
    # Авторизация — authorizev3 + cookie (как supply); wb-seller-lk exc-api не требует,
    # но _raw_post его подмешивает безвредно и ретраит 401 (см. _raw_post).

    async def showcase_subjects(self) -> list[dict]:
        """Справочник предметов биржи: [{"id": int, "name": str, "category": str}].

        WB отдаёт ПЛОСКИЙ список без группировки (`category` пустой) — корневые
        категории даёт наш справочник card_exchange_categories, матчим по `name`.
        """
        data = await self._raw_get(AUTH_BASE, EXC_PREFIX + "/showcase/subjects", {})
        subjects = (data.get("data") or {}).get("subjects") if isinstance(data, dict) else None
        return subjects if isinstance(subjects, list) else []

    async def showcase_ads(
        self,
        *,
        search: str | None = None,
        filter: dict | None = None,
        sort: dict | None = None,
        cursor: dict | None = None,
    ) -> dict:
        """Витрина объявлений биржи (одна страница, курсорная пагинация).

        filter: {"subjectIDs": [int]|None, "brands": [str]|None, "supplierIDs": [int]|None,
                 "rating": float|None, "hasStocks": bool|None}
        sort:   {"field": "feedbacksCount"|..., "order": "asc"|"desc"}
        cursor: None (первая страница) | {"lastAdID": int, "lastValue": <знач. поля сортировки>}
        Возвращает объект `data`: {"ads": [{adID, nmID, imtID, meta{title,brand,supplierName,
        imtCount,stockQty,photo,contactCountries,isKiz}, totalPrice, feedbacks{rating,count},
        hasInCart, isCardOwner}]}.
        """
        body = {
            "search": search,
            "filter": filter
            or {"subjectIDs": None, "brands": None, "supplierIDs": None, "rating": None, "hasStocks": None},
            "sort": sort or {"field": "feedbacksCount", "order": "desc"},
            "cursor": cursor,
        }
        data = await self._raw_post(AUTH_BASE + EXC_PREFIX + "/showcase/ads", body)
        return (data.get("data") or {}) if isinstance(data, dict) else {}

    async def exc_cart_get(self) -> dict:
        """Корзина биржи (группировка по продавцам): data{suppliers:[...], flags{...}}."""
        data = await self._raw_get(AUTH_BASE, EXC_PREFIX + "/cart", {})
        return (data.get("data") or {}) if isinstance(data, dict) else {}

    async def exc_cart_add(self, ad_id: int) -> bool:
        """Добавить объявление в корзину биржи. POST /cart/add {"adID": int} → data:true."""
        data = await self._raw_post(AUTH_BASE + EXC_PREFIX + "/cart/add", {"adID": ad_id})
        return bool(data.get("data")) if isinstance(data, dict) else False

    async def exc_cart_delete(self, ad_ids: list[int]) -> bool:
        """Убрать объявления из корзины. POST /cart/delete {"adIDs": [int]} → data:true."""
        data = await self._raw_post(AUTH_BASE + EXC_PREFIX + "/cart/delete", {"adIDs": ad_ids})
        return bool(data.get("data")) if isinstance(data, dict) else False

    # ─── шаг 1-4: черновик → преордер ─────────────────────────────────────

    async def draft_create(self) -> str:
        """Создать пустой черновик. Возвращает draftID (uuid)."""
        res = await self._call({}, SUPPLY_BASE, "/ns/sm-draft/supply-manager/api/v1/draft/create")
        return str(res["draftID"])

    async def draft_update_goods(self, draft_id: str, barcodes: list[dict]) -> None:
        """
        Набить товары в черновик.
        barcodes: [{"barcode": str, "quantity": int}, ...]
        """
        await self._call(
            {"draftID": draft_id, "barcodes": barcodes},
            SUPPLY_BASE,
            "/ns/sm-draft/supply-manager/api/v1/draft/UpdateDraftGoods",
        )

    async def edit_preorder_goods(self, preorder_id: int, barcodes: list[dict]) -> list[dict]:
        """Правка товаров готового преордера (upsert). barcodes: [{"barcode","quantity"}].

        Возвращает поштучный результат валидации WB:
        [{"barcode","quantity","errors","hasError",...}].
        """
        details = [
            {"barcode": b["barcode"], "quantity": b["quantity"], "preorderID": preorder_id}
            for b in barcodes
        ]
        res = await self._call(
            {"details": details, "supplyID": None},
            SUPPLY_BASE,
            "/ns/sm-preorder/supply-manager/api/v1/preorder/detailEdit",
        )
        inner = res.get("result")
        return inner if isinstance(inner, list) else []

    async def get_warehouse_filter_items(self) -> list[dict]:
        """Список складов назначения портала: [{"warehouseID": int, "warehouseName": str}]."""
        res = await self._call(
            {},
            SUPPLY_BASE,
            "/ns/sm-supply/supply-manager/api/v1/warehouse/getWarehouseFilterItems",
        )
        items = res.get("items", [])
        return items if isinstance(items, list) else []

    async def validate_warehouse_goods(self, draft_id: str, warehouse_id: int) -> dict:
        """Валидировать наполнение под конкретный склад. Возвращает items+metaInfo."""
        return await self._call(
            {"draftID": draft_id, "warehouseId": warehouse_id, "transitWarehouseId": None},
            SUPPLY_BASE,
            "/ns/sm/supply-manager/api/v1/plan/validateWarehouseGoodsV2",
        )

    async def supply_create(
        self,
        draft_id: str,
        warehouse_id: int,
        box_type_id: int,
        is_box_on_pallet: bool = False,
        is_container: bool = False,
    ) -> int:
        """Создать преордер (склад+тип). Возвращает preOrderId."""
        res = await self._call(
            {
                "draftID": draft_id,
                "warehouseId": warehouse_id,
                "transitWarehouseId": None,
                "boxTypeID": box_type_id,
                "isBoxOnPallet": is_box_on_pallet,
                "isContainer": is_container,
            },
            SUPPLY_BASE,
            "/ns/sm-supply/supply-manager/api/v1/supply/create",
        )
        ids = res.get("ids") or []
        if not ids:
            raise WbPortalError(f"supply/create не вернул ids: {res}")
        return int(ids[0]["Id"])

    async def list_supplies(
        self, status_ids: list[int] | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict]:
        """
        Список поставок кабинета — для поиска supply_id после ручной брони даты
        (матч по преордеру) и bulk-синка статусов (F1). limit/offset — пагинация
        (bulk-синк тянет страницами до исчерпания).
        """
        # statusIDs=[] кабинет трактует как «ничего» (пустой список). [-2] =
        # «Все статусы» (из supply/listStatuses) → возвращаются реальные поставки.
        # Явный фильтр статусов передаём как есть.
        res = await self._call(
            {"statusIDs": status_ids if status_ids else [-2], "limit": limit, "offset": offset},
            SUPPLY_BASE,
            "/ns/sm-supply/supply-manager/api/v1/supply/listSupplies",
        )
        # WB отдаёт список под ключом `data` (+ `totalCount`), НЕ `supplies`.
        # Каждый элемент: {supplyId, preorderId, statusId, statusName, boxTypeName,
        # warehouseName, boxQuantity, ...}. Страница максимум ~20.
        supplies = res.get("data")
        if not isinstance(supplies, list):
            supplies = res.get("supplies", [])  # запас на случай смены схемы
        return supplies if isinstance(supplies, list) else []

    async def supply_details(self, supply_id: int) -> dict:
        """
        Карточка поставки из кабинета (supplyDetails) — АВТОРИТЕТНЫЙ статус.

        Возвращает объект `supply` с `statusId`/`statusName` («Запланировано» /
        «Отгрузка разрешена» / «Идёт приёмка» / «Принято» …), а также
        `boxQuantity`, `supplyDate`, `hasBoxes`, `warehouseName` и пр. Это тот
        статус, что видит пользователь в кабинете; `statusId` — та же шкала, что
        `statusID` Suppliers API (см. `FBW_STATUS_MAP`).
        """
        res = await self._call(
            {"supplyID": supply_id, "preorderID": None, "pageNumber": 1, "pageSize": 1, "search": ""},
            SUPPLY_BASE,
            "/ns/sm-supply/supply-manager/api/v1/supply/supplyDetails",
        )
        supply = res.get("supply")
        return supply if isinstance(supply, dict) else {}

    async def list_statuses(self) -> list[dict]:
        """
        Справочник статусов поставок кабинета: [{"id"/"statusID": int,
        "name"/"statusName": str}]. Нужен для резолва числового statusID из
        listSupplies в человекочитаемое имя («В сборке» / «В пути» …).
        """
        res = await self._call(
            {},
            SUPPLY_BASE,
            "/ns/sm-supply/supply-manager/api/v1/supply/listStatuses",
        )
        # WB отдаёт список под разными ключами в зависимости от версии портала.
        for key in ("statuses", "items", "data"):
            val = res.get(key)
            if isinstance(val, list):
                return val
        return []

    # ─── шаг 6: короба ────────────────────────────────────────────────────

    async def create_box_barcodes(self, supply_id: int, count: int) -> list[str]:
        """Создать N коробов. Возвращает список boxcode (WB_...)."""
        res = await self._call(
            {"supplyId": supply_id, "barcodeNumber": count},
            SUPPLY_BASE,
            "/ns/sm-box/supply-manager/api/v1/box/createBoxBarcodes",
        )
        return list(res["barcodes"])

    async def bind_barcodes(self, supply_id: int, bind: list[dict]) -> None:
        """
        Разложить товары по коробам.
        bind: [{"boxcode": str, "barcodes": [{"barcode": str,
                "expirationDate": None, "quantity": int}], "quantity": 1}]
        """
        await self._call(
            {"bind": bind, "incomeID": supply_id},
            SUPPLY_BASE,
            "/ns/sm-box/supply-manager/api/v1/box/bindBarcodes",
        )

    async def list_boxes(self, supply_id: int) -> list[dict]:
        """
        Короба поставки с содержимым (как в кабинете «Упаковка»).

        Batch-метод `ListBarcodesBoxes` на /ns/sm-box/.../box. Возвращает
        [{"boxcode": "WB_...", "quantity": int,
          "barcodes": [{"barcode","quantity","imtName","imgSrc","brand","saNm",
                        "nmID","colorName","volume", ...}]}].
        """
        body = [
            {
                "method": "ListBarcodesBoxes",
                "params": {"incomeID": supply_id},
                "id": self._rpc_id(),
                "jsonrpc": "2.0",
            }
        ]
        res = await self._post(SUPPLY_BASE, "/ns/sm-box/supply-manager/api/v1/box", body)
        # Batch-ответ — массив RPC-результатов; берём первый.
        if isinstance(res, list) and res:
            inner = res[0].get("result") if isinstance(res[0], dict) else None
            boxes = (inner or {}).get("boxes") if isinstance(inner, dict) else None
            return boxes if isinstance(boxes, list) else []
        return []

    # ─── шаг 7: пропуск ───────────────────────────────────────────────────

    async def trn_details(self, supply_id: int) -> dict:
        """Прочитать деталь пропуска (в т.ч. barcodeId для setTRNDetails)."""
        return await self._call(
            {"supplyId": supply_id, "isPassPreCreate": True},
            SUPPLY_BASE,
            "/ns/sm/supply-manager/api/v1/barcode/tRNDetails",
        )

    async def set_trn(
        self,
        *,
        barcode_id: int,
        supply_id: int,
        first_name: str,
        last_name: str,
        car_model: str,
        car_number: str,
        phone: str,
        pallets: int,
        as_boxes: bool = False,
        barcode_prefix: str = "WB-GI-",
    ) -> None:
        """Сохранить пропуск (водитель/авто/кол-во единиц).

        as_boxes — способ отгрузки в кабинете WB: True → «отдельные короба»
        (boxTypeName="box"), False → «на паллете» (boxTypeName="pallets"). `pallets`
        в обоих случаях = число единиц (коробов или паллет).
        """
        await self._call(
            {
                "barcodeId": barcode_id,
                "barcodePrefix": barcode_prefix,
                "firstName": first_name,
                "lastName": last_name,
                "carModel": car_model,
                "carNumber": car_number,
                "supplyId": supply_id,
                "number": pallets,
                "boxTypeName": "box" if as_boxes else "pallets",
                "supplierAssignUUID": None,
                "phone": normalize_ru_phone(phone),
            },
            SUPPLY_BASE,
            "/ns/sm/supply-manager/api/v1/barcode/setTRNDetails",
        )

    async def pass_history(self) -> list[dict]:
        """История водителей для автозаполнения пропуска."""
        res = await self._post(
            SUPPLY_BASE,
            "/ns/sm/supply-manager/api/v1/pass",
            {"method": "history", "jsonrpc": "2.0", "id": self._rpc_id()},
        )
        if not isinstance(res, dict):
            return []
        drivers = (res.get("passes") or {}).get("drivers", [])
        return drivers if isinstance(drivers, list) else []
