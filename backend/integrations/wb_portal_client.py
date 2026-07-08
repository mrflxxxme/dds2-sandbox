"""
Клиент реплея внутреннего API кабинета Wildberries (seller-supply.wildberries.ru).

НЕ официальный API — воспроизводит те же JSON-RPC-вызовы, что делает браузер на
странице «Поставки» портала. Нужен для операций, которых нет в публичном
suppliers-api: создание черновика FBW-поставки, наполнение, преордер, короба,
пропуск.

Авторизация — два JWT-заголовка (cookie нет):
  - authorizev3 — серверная сессия (без self-exp; живёт, пока WB держит сессию);
  - wb-seller-lk — короткий токен (TTL ~5 мин), обновляется через auth/token.

Финальная бронь даты (plan/add) закрыта антибот-челленджем (x-wb-captcha-token,
fingerprint JS) — здесь НЕ реализована намеренно, остаётся ручным кликом в портале.

Ошибка сессии (401/протух authorizev3) → WbSessionExpired, чтобы вызвавший слой
пометил интеграцию EXPIRED и попросил пользователя обновить токен.
"""

import itertools

import httpx
import structlog

logger = structlog.get_logger("dds.wb_portal")

SUPPLY_BASE = "https://seller-supply.wildberries.ru"
AUTH_BASE = "https://seller.wildberries.ru"
ROOT_VERSION = "v1.100.0"
TIMEOUT = 30

# UA как у реального браузера — часть fingerprint-контекста, WB сверяет с сессией.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


class WbPortalError(Exception):
    """Ошибка реплея портала (не-сессионная): WB вернул JSON-RPC error или не-2xx."""


class WbSessionExpired(WbPortalError):
    """authorizev3/сессия недействительны — нужно обновить токен в настройках."""


class WbPortalClient:
    """
    Реплей-клиент кабинета WB. Инстанс на одну сессию (authorizev3).

    wb-seller-lk кэшируется и лениво обновляется: если не передан или получен 401,
    дёргаем auth/token по authorizev3 и повторяем вызов один раз.
    """

    def __init__(self, authorizev3: str, wb_seller_lk: str | None = None):
        self.authorizev3 = authorizev3
        self.wb_seller_lk = wb_seller_lk
        self._id_counter = itertools.count(1)

    # ─── low-level ────────────────────────────────────────────────────────

    def _rpc_id(self) -> str:
        return f"json-rpc_{next(self._id_counter)}"

    def _headers(self) -> dict[str, str]:
        h = {
            "authorizev3": self.authorizev3,
            "content-type": "application/json",
            "root-version": ROOT_VERSION,
            "origin": AUTH_BASE,
            "referer": AUTH_BASE + "/",
            "user-agent": USER_AGENT,
            "accept": "*/*",
        }
        if self.wb_seller_lk:
            h["wb-seller-lk"] = self.wb_seller_lk
        return h

    async def _refresh_lk(self, client: httpx.AsyncClient) -> None:
        """Обновить короткий wb-seller-lk через authorizev3."""
        resp = await client.post(
            AUTH_BASE + "/ns/suppliers-auth/suppliers-portal-core/auth/token",
            headers=self._headers(),
            json={"params": {}, "jsonrpc": "2.0", "id": self._rpc_id()},
        )
        if resp.status_code == 401:
            raise WbSessionExpired("authorizev3 недействителен при обновлении wb-seller-lk")
        if resp.status_code >= 400:
            raise WbPortalError(f"auth/token: HTTP {resp.status_code}")
        data = resp.json()
        token = (data.get("result") or {}).get("data", {}).get("token")
        if not token:
            # Не дампим сырой ответ портала пользователю (может содержать PII) —
            # детали пусть остаются на стороне WB, наверх — общий текст.
            raise WbSessionExpired("auth/token не вернул токен сессии")
        self.wb_seller_lk = token

    async def _post(self, base: str, path: str, body: dict | list) -> dict | list:
        """
        POST JSON-RPC с ленивым обновлением wb-seller-lk и одним ретраем на 401.
        Возвращает распарсенный `result` (для одиночного RPC) или сырой ответ
        (для батч-массива).
        """
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                if not self.wb_seller_lk:
                    await self._refresh_lk(client)
                for attempt in range(2):
                    resp = await client.post(base + path, headers=self._headers(), json=body)
                    if resp.status_code == 401 and attempt == 0:
                        # Протух короткий токен — обновляем и повторяем один раз.
                        await self._refresh_lk(client)
                        continue
                    if resp.status_code == 401:
                        raise WbSessionExpired(f"401 на {path} после обновления токена")
                    if resp.status_code >= 400:
                        raise WbPortalError(self._http_error(path, resp))
                    return self._unwrap(resp.json(), path)
        except httpx.HTTPError as e:
            # Таймаут/обрыв/DNS — в доменную ошибку, чтобы стадия ушла в ERROR,
            # а не 500. Токена в тексте нет (он в заголовке, не в URL).
            raise WbPortalError(f"WB недоступен ({path}): {type(e).__name__}") from e
        raise WbPortalError(f"Не удалось выполнить {path}")

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
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                await self._refresh_lk(client)
        except httpx.HTTPError as e:
            raise WbPortalError(f"WB недоступен (check_session): {type(e).__name__}") from e
        return True

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

    async def list_supplies(self, status_ids: list[int] | None = None) -> list[dict]:
        """
        Список поставок кабинета — для поиска supply_id после ручной брони даты
        (матч по преордеру).
        """
        res = await self._call(
            {"statusIDs": status_ids or [], "limit": 50, "offset": 0},
            SUPPLY_BASE,
            "/ns/sm-supply/supply-manager/api/v1/supply/listSupplies",
        )
        supplies = res.get("supplies", [])
        return supplies if isinstance(supplies, list) else []

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
        barcode_prefix: str = "WB-GI-",
    ) -> None:
        """Сохранить пропуск (водитель/авто/паллеты)."""
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
                "boxTypeName": "pallets",
                "supplierAssignUUID": None,
                "phone": phone,
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
