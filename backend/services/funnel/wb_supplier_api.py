"""WB Supplier/Warehouse API — orders, stocks, acceptance.

Handles:
- fetch_supplier_orders: order history per warehouse
- fetch_acceptance_coefficients: warehouse open/closed status
- fetch_wb_warehouses: ID → name mapping
- fetch_acceptance_options: per-barcode warehouse availability
- fetch_warehouse_stocks: per-warehouse stock levels
- fetch_warehouse_remains: analytics report «Остатки на складах» (cabinet-accurate)
"""

import logging
import asyncio
import time

import httpx

logger = logging.getLogger("dds.funnel")


async def fetch_supplier_orders(api_key: str, date_from: str) -> list[dict]:
    """Fetch supplier orders from WB Statistics API."""
    url = "https://statistics-api.wildberries.ru/api/v1/supplier/orders"
    headers = {"Authorization": api_key}
    params = {"dateFrom": date_from}

    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(3):
            try:
                resp = await client.get(url, headers=headers, params=params)
            except httpx.RequestError as e:
                logger.error(f"WB supplier/orders request error: {e}")
                if attempt < 2:
                    await asyncio.sleep(5)
                    continue
                return []

            if resp.status_code == 429:
                wait = min(int(resp.headers.get("Retry-After", "60")), 120)
                logger.warning(f"WB supplier/orders 429, waiting {wait}s (attempt {attempt+1})")
                await asyncio.sleep(wait)
                continue

            if resp.status_code != 200:
                logger.error(f"WB supplier/orders API error {resp.status_code}: {resp.text[:200]}")
                return []

            data = resp.json()
            if not isinstance(data, list):
                logger.error(f"WB supplier/orders: unexpected response type {type(data)}")
                return []

            logger.info(f"WB supplier/orders: got {len(data)} orders from {date_from}")
            return data

    return []


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


_REMAINS_BASE = "https://seller-analytics-api.wildberries.ru/api/v1/warehouse_remains"


async def _remains_get(
    client: httpx.AsyncClient, url: str, api_key: str, params: dict | None = None
) -> dict | list | None:
    """One GET to the warehouse_remains API with 429/error handling. Returns parsed JSON or None."""
    for attempt in range(3):
        try:
            resp = await client.get(url, headers={"Authorization": api_key}, params=params)
        except httpx.RequestError as e:
            logger.error(f"WB warehouse_remains request error: {e}")
            if attempt < 2:
                await asyncio.sleep(5)
                continue
            return None

        if resp.status_code == 429:
            wait = min(int(resp.headers.get("Retry-After", "60")), 120)
            logger.warning(f"WB warehouse_remains 429, waiting {wait}s (attempt {attempt + 1})")
            await asyncio.sleep(wait)
            continue

        if resp.status_code == 204:  # download: report exists but has no data
            return []

        if resp.status_code != 200:
            logger.error(f"WB warehouse_remains error {resp.status_code}: {resp.text[:200]}")
            return None

        parsed = resp.json()
        return parsed if isinstance(parsed, (dict, list)) else None

    return None


async def fetch_warehouse_remains(api_key: str, max_wait_seconds: int = 300) -> list[dict]:
    """Fetch the WB analytics report «Остатки на складах» (warehouse_remains).

    Task-based flow: create task → poll status → download. Matches the seller
    cabinet numbers 1:1 — includes goods being accepted and in transit between
    WB warehouses, which statistics supplier/stocks does not see.

    Returns rows like {brand, subjectName, vendorCode, nmId, barcode, techSize,
    volume, warehouses: [{warehouseName, quantity}]} where warehouses includes
    pseudo-rows «В пути до получателей», «В пути возвраты на склад WB»,
    «Всего находится на складах» (total = sum of real warehouses).

    Rate limits: create/download 1 req/min, status 1 req/5s — one call per
    project per sync fits; polling every 10s stays well under the status limit.
    """
    params = {
        "locale": "ru",
        "groupByBrand": "true",
        "groupBySubject": "true",
        "groupBySa": "true",
        "groupByNm": "true",
        "groupByBarcode": "true",
    }

    async with httpx.AsyncClient(timeout=120) as client:
        created = await _remains_get(client, _REMAINS_BASE, api_key, params=params)
        # `.get("data") or {}` — не `.get("data", {})`: WB может вернуть data=null (ключ
        # есть, значение null) → .get с дефолтом отдаст None, а None.get() = AttributeError.
        task_id = ((created or {}).get("data") or {}).get("taskId") if isinstance(created, dict) else None
        if not task_id:
            logger.error(f"WB warehouse_remains: no taskId in create response: {str(created)[:200]}")
            return []

        # Poll status until "done" (report generation is usually seconds)
        deadline = time.monotonic() + max_wait_seconds
        status = ""
        while time.monotonic() < deadline:
            await asyncio.sleep(10)
            st = await _remains_get(client, f"{_REMAINS_BASE}/tasks/{task_id}/status", api_key)
            status = ((st or {}).get("data") or {}).get("status", "") if isinstance(st, dict) else ""
            if status == "done":
                break
            if status in ("error", "canceled"):
                logger.error(f"WB warehouse_remains: task {task_id} failed with status={status}")
                return []
        if status != "done":
            logger.error(f"WB warehouse_remains: task {task_id} not ready in {max_wait_seconds}s")
            return []

        data = await _remains_get(client, f"{_REMAINS_BASE}/tasks/{task_id}/download", api_key)
        if not isinstance(data, list):
            logger.error(f"WB warehouse_remains: unexpected download type {type(data)}")
            return []

        logger.info(f"WB warehouse_remains: got {len(data)} rows")
        return data
