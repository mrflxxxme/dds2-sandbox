"""WB Supplier/Warehouse API — orders, stocks, acceptance.

Handles:
- fetch_supplier_orders: order history per warehouse
- fetch_acceptance_coefficients: warehouse open/closed status
- fetch_wb_warehouses: ID → name mapping
- fetch_acceptance_options: per-barcode warehouse availability
- fetch_warehouse_stocks: per-warehouse stock levels
"""

import logging
import asyncio
import httpx

logger = logging.getLogger("dds.funnel")


# WB /supplier/orders отдаёт максимум ~80k записей за запрос, отсортированных по
# lastChangeDate ↑. Один запрос за широкое окно молча терял НОВЕЙШИЕ заказы
# (обрезался на старейших 80k) → «не догружает свежие дни». Пагинируем по курсору.
_ORDERS_PAGE_CAP = 80_000
_ORDERS_MAX_PAGES = 60  # предохранитель от бесконечного цикла


async def _fetch_orders_page(client: httpx.AsyncClient, api_key: str, date_from: str) -> list[dict] | None:
    """Одна страница /supplier/orders. None — неустранимая ошибка (стоп пагинации)."""
    url = "https://statistics-api.wildberries.ru/api/v1/supplier/orders"
    headers = {"Authorization": api_key}
    params = {"dateFrom": date_from}

    for attempt in range(3):
        try:
            resp = await client.get(url, headers=headers, params=params)
        except httpx.RequestError as e:
            logger.error(f"WB supplier/orders request error: {e}")
            if attempt < 2:
                await asyncio.sleep(5)
                continue
            return None

        if resp.status_code == 429:
            wait = min(int(resp.headers.get("Retry-After", "60")), 120)
            logger.warning(f"WB supplier/orders 429, waiting {wait}s (attempt {attempt+1})")
            await asyncio.sleep(wait)
            continue

        if resp.status_code != 200:
            logger.error(f"WB supplier/orders API error {resp.status_code}: {resp.text[:200]}")
            return None

        data = resp.json()
        if not isinstance(data, list):
            logger.error(f"WB supplier/orders: unexpected response type {type(data)}")
            return None
        return data

    return None


async def fetch_supplier_orders(api_key: str, date_from: str) -> list[dict]:
    """Все заказы с lastChangeDate >= date_from (Statistics API, с пагинацией).

    WB отдаёт максимум ~80k записей за запрос (сортировка по lastChangeDate ↑),
    поэтому широкое окно в ОДИН запрос теряло новейшие заказы. Идём страницами:
    следующий dateFrom = максимальный lastChangeDate предыдущей страницы, пока
    страница не станет неполной. Соседние страницы повторяют записи на границе
    lastChangeDate — дедуп по srid делает вызывающий (sync_wb_orders).
    """
    all_orders: list[dict] = []
    cursor = date_from
    pages = 0

    async with httpx.AsyncClient(timeout=120) as client:
        for _ in range(_ORDERS_MAX_PAGES):
            batch = await _fetch_orders_page(client, api_key, cursor)
            pages += 1
            if batch is None:
                if not all_orders:
                    return []  # ошибка первой страницы — как раньше
                break  # ошибка последующей — отдаём собранное
            all_orders.extend(batch)
            if len(batch) < _ORDERS_PAGE_CAP:
                break  # последняя (неполная) страница
            candidates: list[str] = [
                str(o.get("lastChangeDate")) for o in batch if o.get("lastChangeDate")
            ]
            next_cursor = max(candidates, default=None)
            if not next_cursor or next_cursor == cursor:
                # вся полная страница в одну метку — иначе бесконечный цикл
                logger.warning("WB supplier/orders: курсор не сдвинулся на %s — стоп пагинации", cursor)
                break
            cursor = next_cursor

    logger.info("WB supplier/orders: собрано %d заказов от %s (страниц: %d)", len(all_orders), date_from, pages)
    return all_orders


async def fetch_acceptance_coefficients(api_key: str) -> list[dict]:
    """Fetch acceptance coefficients from WB Tariffs API."""
    url = "https://common-api.wildberries.ru/api/tariffs/v1/acceptance/coefficients"
    headers = {"Authorization": api_key}

    async with httpx.AsyncClient(timeout=60) as client:
        for attempt in range(3):
            try:
                resp = await client.get(url, headers=headers)
            except httpx.RequestError as e:
                logger.error(f"WB acceptance/coefficients request error: {e}")
                if attempt < 2:
                    await asyncio.sleep(5)
                    continue
                return []

            if resp.status_code == 429:
                wait = min(int(resp.headers.get("Retry-After", "60")), 120)
                logger.warning(f"WB acceptance/coefficients 429, waiting {wait}s")
                await asyncio.sleep(wait)
                continue

            if resp.status_code != 200:
                logger.error(
                    f"WB acceptance/coefficients error {resp.status_code}: "
                    f"{resp.text[:200]}"
                )
                return []

            data = resp.json()
            if not isinstance(data, list):
                logger.error(
                    f"WB acceptance/coefficients: unexpected type {type(data)}"
                )
                return []

            logger.info(f"WB acceptance/coefficients: got {len(data)} entries")
            return data

    return []


async def fetch_wb_warehouses(api_key: str) -> dict[int, str]:
    """Fetch WB warehouse ID → name mapping."""
    url = "https://supplies-api.wildberries.ru/api/v1/warehouses"
    headers = {"Authorization": api_key}

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url, headers=headers)
        except httpx.RequestError as e:
            logger.error(f"WB warehouses request error: {e}")
            return {}

        if resp.status_code != 200:
            logger.error(
                f"WB warehouses API error {resp.status_code}: {resp.text[:200]}"
            )
            return {}

        data = resp.json()
        if not isinstance(data, list):
            logger.error(f"WB warehouses: unexpected type {type(data)}")
            return {}

        result = {}
        for w in data:
            wid = w.get("ID", w.get("id", 0))
            wname = w.get("name", w.get("Name", ""))
            if wid and wname:
                result[wid] = wname
        logger.info(f"WB warehouses: got {len(result)} entries")
        return result


async def fetch_acceptance_options(
    api_key: str, barcodes: list[str]
) -> dict[str, list[dict]]:
    """Fetch warehouse acceptance options for specific barcodes."""
    url = "https://supplies-api.wildberries.ru/api/v1/acceptance/options"
    headers = {"Authorization": api_key}
    payload = [{"quantity": 1, "barcode": bc} for bc in barcodes[:5000]]

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(url, headers=headers, json=payload)
        except httpx.RequestError as e:
            logger.error(f"WB acceptance/options request error: {e}")
            return {}

        if resp.status_code == 429:
            wait = min(int(resp.headers.get("Retry-After", "60")), 120)
            logger.warning(f"WB acceptance/options 429, waiting {wait}s")
            await asyncio.sleep(wait)
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.RequestError:
                return {}

        if resp.status_code != 200:
            logger.error(
                f"WB acceptance/options error {resp.status_code}: "
                f"{resp.text[:200]}"
            )
            return {}

        data = resp.json()

        result: dict[str, list[dict]] = {}
        if isinstance(data, dict) and "result" in data:
            result_list = data["result"]
            if isinstance(result_list, list):
                for entry in result_list:
                    if isinstance(entry, dict):
                        bc = entry.get("barcode", "")
                        wh_list = entry.get("warehouses", [])
                        if bc and isinstance(wh_list, list):
                            result[bc] = wh_list

        if not result:
            logger.warning("WB acceptance/options: no data found in response")
            return {}

        total_wh = sum(len(v) for v in result.values())
        logger.info(
            f"WB acceptance/options: {len(result)} barcodes, "
            f"{total_wh} total warehouse entries"
        )
        return result


async def fetch_warehouse_stocks(api_key: str) -> list[dict]:
    """Fetch per-warehouse stock levels from WB Statistics API."""
    url = "https://statistics-api.wildberries.ru/api/v1/supplier/stocks"
    headers = {"Authorization": api_key}
    params = {"dateFrom": "2019-01-01"}

    async with httpx.AsyncClient(timeout=60) as client:
        for attempt in range(3):
            try:
                resp = await client.get(url, headers=headers, params=params)
            except httpx.RequestError as e:
                logger.error(f"WB warehouse stocks request error: {e}")
                if attempt < 2:
                    await asyncio.sleep(5)
                    continue
                return []

            if resp.status_code == 429:
                wait = min(int(resp.headers.get("Retry-After", "60")), 120)
                logger.warning(f"WB warehouse stocks 429, waiting {wait}s (attempt {attempt+1})")
                await asyncio.sleep(wait)
                continue

            if resp.status_code != 200:
                logger.error(f"WB warehouse stocks API error {resp.status_code}: {resp.text[:200]}")
                return []

            data = resp.json()
            if not isinstance(data, list):
                logger.error(f"WB warehouse stocks: unexpected response type {type(data)}")
                return []

            logger.info(f"WB warehouse stocks: got {len(data)} items")
            return data

    return []
