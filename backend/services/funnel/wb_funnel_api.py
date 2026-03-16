"""WB Analytics API — sales funnel data fetching.

Handles:
- fetch_funnel: per-day product analytics
- fetch_funnel_history: multi-day history (requires WB Джем)
"""

import logging
import asyncio
import httpx

logger = logging.getLogger("dds.funnel")


async def fetch_funnel(api_key: str, date_str: str) -> dict:
    """Fetch sales funnel data from WB Analytics API v3 for a single day.
    Returns {nm_id: {vendor_code, subject, brand, open_card, ...}}.
    """
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    all_items = {}
    offset = 0
    limit = 1000

    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            payload = {
                "selectedPeriod": {"start": date_str, "end": date_str},
                "nmIds": [],
                "skipDeletedNm": True,
                "limit": limit,
                "offset": offset,
            }
            resp = None
            for attempt in range(3):
                resp = await client.post(
                    "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code == 429:
                    wait = 10 * (2 ** attempt)
                    logger.warning(
                        f"WB funnel API 429 rate limited, waiting {wait}s "
                        f"(attempt {attempt+1}/3, offset={offset})"
                    )
                    await asyncio.sleep(wait)
                    continue
                break

            if resp is None or resp.status_code != 200:
                status = resp.status_code if resp else "no response"
                logger.error(f"WB funnel API error {status}: {resp.text[:200] if resp else 'N/A'}")
                break

            data = resp.json()
            products = (data.get("data") or {}).get("products") or []
            if not products:
                break

            for item in products:
                p = item.get("product") or {}
                s = (item.get("statistic") or {}).get("selected") or {}
                conv = s.get("conversions") or {}
                stocks = p.get("stocks") or {}

                nm_id = p.get("nmId")
                if not nm_id:
                    continue

                all_items[nm_id] = {
                    "vendor_code": p.get("vendorCode", ""),
                    "subject": p.get("subjectName", ""),
                    "brand": p.get("brandName", ""),
                    "open_card": s.get("openCount", 0),
                    "add_to_cart": s.get("cartCount", 0),
                    "orders_count": s.get("orderCount", 0),
                    "orders_sum_rub": s.get("orderSum", 0),
                    "buyout_percent": conv.get("buyoutPercent", 0),
                    "cart_to_order_pct": conv.get("cartToOrderPercent", 0),
                    "add_to_cart_pct": conv.get("addToCartPercent", 0),
                    "avg_price": s.get("avgPrice", 0),
                    "stocks_wb": stocks.get("wb", 0),
                    "stocks_mp": stocks.get("mp", 0),
                }
            offset += len(products)

    return all_items


async def fetch_funnel_history(api_key: str, date_from: str, date_to: str) -> dict | None:
    """Fetch sales funnel data with per-day breakdown using /history endpoint.

    Requires WB Джем subscription. Returns None if not available (402).
    Returns {date_str: {nm_id: {vendor_code, subject, brand, open_card, ...}}}.
    """
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    result: dict = {}
    offset = 0
    limit = 1000

    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            payload = {
                "selectedPeriod": {"start": date_from, "end": date_to},
                "nmIds": [],
                "skipDeletedNm": True,
                "aggregationLevel": "day",
                "limit": limit,
                "offset": offset,
            }

            resp = None
            for attempt in range(3):
                resp = await client.post(
                    "https://seller-analytics-api.wildberries.ru"
                    "/api/analytics/v3/sales-funnel/products/history",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code == 429:
                    wait = 10 * (2 ** attempt)
                    logger.warning(
                        f"WB history API 429, waiting {wait}s "
                        f"(attempt {attempt+1}/3, offset={offset})"
                    )
                    await asyncio.sleep(wait)
                    continue
                break

            if resp is None:
                break

            if resp.status_code == 402:
                logger.info("WB history API: 402 — Джем not available, falling back to per-day")
                return None

            if resp.status_code == 400:
                logger.info(f"WB history API: 400 — {resp.text[:150]}, falling back to per-day")
                return None

            if resp.status_code != 200:
                logger.error(f"WB history API error {resp.status_code}: {resp.text[:200]}")
                break

            data = resp.json()
            products = data if isinstance(data, list) else ((data.get("data") or {}).get("products") if isinstance(data, dict) else None) or data
            if not isinstance(products, list) or not products:
                break

            for item in products:
                p = item.get("product") or item.get("p") or {}
                nm_id = p.get("nmId")
                if not nm_id:
                    continue

                vendor_code = p.get("vendorCode", "")
                subject = p.get("subjectName", "")
                brand = p.get("brandName", "")

                for day in item.get("history") or []:
                    day_date = (day.get("date") or "")[:10]
                    if not day_date:
                        continue

                    if day_date not in result:
                        result[day_date] = {}

                    result[day_date][nm_id] = {
                        "vendor_code": vendor_code,
                        "subject": subject,
                        "brand": brand,
                        "open_card": day.get("openCount", 0),
                        "add_to_cart": day.get("cartCount", 0),
                        "orders_count": day.get("orderCount", 0),
                        "orders_sum_rub": day.get("orderSum", 0),
                        "buyout_percent": day.get("buyoutPercent", 0),
                        "cart_to_order_pct": day.get("cartToOrderConversion", 0),
                        "add_to_cart_pct": day.get("addToCartConversion", 0),
                        "avg_price": 0,
                        "stocks_wb": 0,
                        "stocks_mp": 0,
                    }

            offset += len(products)

    if result:
        total_items = sum(len(v) for v in result.values())
        logger.info(
            f"WB history: {date_from}→{date_to} — "
            f"{len(result)} days, {total_items} product-days"
        )
    return result
