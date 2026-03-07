"""
WB API client — Wildberries API integration layer.

Handles:
- API key lookup and decryption
- Analytics API (sales funnel data)
- Advertising API (campaigns, stats)
"""

import logging
import asyncio
import time
import httpx
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import IntegrationKey
from backend.utils.crypto import decrypt

logger = logging.getLogger("dds.funnel")


# ─── API Key lookup ──────────────────────────────────────────────────────────

async def get_wb_key(db: AsyncSession, project_id: int, service: str) -> Optional[str]:
    """Get decrypted WB API key by service label. Also checks global keys (project_id IS NULL)."""
    result = await db.execute(
        select(IntegrationKey).where(
            or_(
                IntegrationKey.project_id == project_id,
                IntegrationKey.project_id.is_(None),
            ),
            IntegrationKey.service == service,
            IntegrationKey.is_active == True,
        ).order_by(IntegrationKey.project_id.desc().nullslast()).limit(1)
    )
    key = result.scalar_one_or_none()
    if not key:
        return None
    return decrypt(key.encrypted_key)


# ─── WB API calls ───────────────────────────────────────────────────────────

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
            # Retry loop for 429 rate limiting
            resp = None
            for attempt in range(3):
                resp = await client.post(
                    "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code == 429:
                    wait = 10 * (2 ** attempt)  # 10s, 20s, 40s
                    logger.warning(
                        f"WB funnel API 429 rate limited, waiting {wait}s "
                        f"(attempt {attempt+1}/3, offset={offset})"
                    )
                    await asyncio.sleep(wait)
                    continue
                break  # Success or non-retryable error

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
    Max 7 days per request.
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
                return None  # Signal: use fallback

            if resp.status_code == 400:
                logger.info(f"WB history API: 400 — {resp.text[:150]}, falling back to per-day")
                return None  # Signal: use fallback (dates too old or invalid params)

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
                        "avg_price": 0,  # not in history response
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


async def fetch_ad_campaigns(api_key: str) -> list[int]:
    """Get list of active/paused ad campaign IDs.
    Only status 9 (active) and 11 (paused) — matching Google Script logic.
    Completed campaigns (7) are excluded to keep the list small.
    """
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://advert-api.wildberries.ru/adv/v1/promotion/count",
            headers=headers,
        )
        if resp.status_code != 200:
            logger.error(f"WB adv count error {resp.status_code}: {resp.text[:200]}")
            return []

        data = resp.json()
        campaign_ids = []
        for adv in data.get("adverts") or []:
            status = str(adv.get("status", ""))
            if status in ("9", "11"):  # active / paused only
                for compa in adv.get("advert_list") or []:
                    cid = compa.get("advertId")
                    if cid and cid not in campaign_ids:
                        campaign_ids.append(cid)
        return campaign_ids


async def fetch_ad_stats(api_key: str, campaign_ids: list[int],
                         begin_date: str, end_date: str) -> dict:
    """Fetch detailed ad stats per nmId per date.
    Returns {date: {nm_id: {sum, clicks, views}}}.

    Uses a 180-second time budget: returns partial data if budget is
    exceeded instead of hanging until the outer wait_for kills us.
    """
    result = {}
    chunks = [campaign_ids[i:i+50] for i in range(0, len(campaign_ids), 50)]
    skipped_chunks = 0
    budget_exceeded = False
    TIME_BUDGET = 300  # seconds — enough for 9 chunks with 12s delays + retries
    t_start = time.monotonic()

    async with httpx.AsyncClient(timeout=60) as client:
        for idx, chunk in enumerate(chunks):
            # ── Time budget check ────────────────────────────────────
            elapsed = time.monotonic() - t_start
            if elapsed >= TIME_BUDGET:
                remaining = len(chunks) - idx
                logger.warning(
                    f"WB adv: time budget {TIME_BUDGET}s exceeded after "
                    f"{elapsed:.0f}s — skipping {remaining} remaining chunks "
                    f"({begin_date}→{end_date})"
                )
                skipped_chunks += remaining
                budget_exceeded = True
                break

            if idx > 0:
                await asyncio.sleep(20)  # 20s — matches proven Google Script timing

            ids_param = ",".join(str(c) for c in chunk)
            url = (f"https://advert-api.wildberries.ru/adv/v3/fullstats"
                   f"?ids={ids_param}&beginDate={begin_date}&endDate={end_date}")

            chunk_ok = False
            for attempt in range(2):  # 2 attempts max (Google Script: 0 retries)
                try:
                    resp = await client.get(url, headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    })
                except Exception as e:
                    logger.warning(f"Ad stats request failed: {e}")
                    break

                if resp.status_code == 429:
                    wait = [20, 40][attempt]
                    logger.warning(
                        f"WB adv 429 rate limit, waiting {wait}s "
                        f"(attempt {attempt+1}/2, elapsed {time.monotonic()-t_start:.0f}s)"
                    )
                    if time.monotonic() - t_start + wait >= TIME_BUDGET:
                        logger.warning(
                            f"WB adv: sleep {wait}s would exceed budget, "
                            f"skipping chunk {idx+1}/{len(chunks)}"
                        )
                        break
                    await asyncio.sleep(wait)
                    continue
                elif resp.status_code != 200:
                    logger.error(f"WB adv stats error {resp.status_code}: {resp.text[:200]}")
                    break

                data = resp.json()
                if data is None:
                    logger.warning(
                        f"WB adv: empty JSON response for chunk {idx+1}, skipping"
                    )
                    break  # Don't retry empty responses — move on like Google Script
                items = data if isinstance(data, list) else (data.get("data") or data)
                if not isinstance(items, list):
                    break

                for campaign in items:
                    if not campaign or not isinstance(campaign, dict):
                        continue
                    for day in campaign.get("days") or []:
                        res_date = (day.get("date") or "")[:10]
                        if not res_date:
                            continue
                        if res_date not in result:
                            result[res_date] = {}

                        for app in day.get("apps") or []:
                            for nm in app.get("nms") or []:
                                nm_id = nm.get("nmId")
                                if not nm_id:
                                    continue
                                if nm_id not in result[res_date]:
                                    result[res_date][nm_id] = {"sum": 0, "clicks": 0, "views": 0}
                                result[res_date][nm_id]["sum"] += nm.get("sum", 0)
                                result[res_date][nm_id]["clicks"] += nm.get("clicks", 0)
                                result[res_date][nm_id]["views"] += nm.get("views", 0)
                chunk_ok = True
                break  # Success, move to next chunk

            if not chunk_ok:
                skipped_chunks += 1
                logger.warning(
                    f"WB adv: chunk {idx+1}/{len(chunks)} SKIPPED after retries "
                    f"({len(chunk)} campaigns, {begin_date}→{end_date})"
                )

    elapsed_total = time.monotonic() - t_start
    if skipped_chunks:
        logger.warning(
            f"WB adv: {skipped_chunks}/{len(chunks)} chunks skipped "
            f"for {begin_date}→{end_date} (elapsed {elapsed_total:.0f}s, "
            f"budget_exceeded={budget_exceeded})"
        )
    else:
        logger.info(
            f"WB adv: all {len(chunks)} chunks OK "
            f"for {begin_date}→{end_date} in {elapsed_total:.0f}s"
        )

    return result
