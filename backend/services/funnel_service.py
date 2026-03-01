"""
Funnel service — business logic for WB sales funnel.

Handles:
- WB API interaction (analytics + adv)
- Encryption helpers for API keys
- Data aggregation and processing
"""

import logging
import httpx
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from cryptography.fernet import Fernet
from sqlalchemy import select, delete, func, text, or_, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    IntegrationKey, WbFunnelDaily, WbCostOverride, Project,
    CostOrderItem,
)

logger = logging.getLogger("dds.funnel")


# ─── Encryption helpers ──────────────────────────────────────────────────────

def get_fernet():
    """Get Fernet cipher for encrypting/decrypting API keys."""
    return Fernet(settings.SECRET_KEY[:43].encode() + b"=")

def decrypt(value: str) -> str:
    """Decrypt an encrypted API key."""
    f = get_fernet()
    return f.decrypt(value.encode()).decode()


# ─── API Key lookup ──────────────────────────────────────────────────────────

async def get_wb_key(db: AsyncSession, project_id: int, service: str) -> Optional[str]:
    """Get decrypted WB API key by service label. Also checks global keys (project_id IS NULL)."""
    result = await db.execute(
        select(IntegrationKey).where(
            or_(IntegrationKey.project_id == project_id, IntegrationKey.project_id.is_(None)),
            IntegrationKey.service == service,
        ).order_by(IntegrationKey.project_id.desc().nulls_last())
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return decrypt(row.encrypted_key)


# ─── WB API calls ───────────────────────────────────────────────────────────

async def fetch_funnel(api_key: str, date_str: str) -> list[dict]:
    """Fetch sales funnel data from WB Analytics API v3 for a single day."""
    url = "https://seller-analytics-api.wildberries.ru/api/v3/seller-analytics/sales-funnel"
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    body = {"dateFrom": date_str, "dateTo": date_str, "page": 1}

    all_items = []
    max_pages = 50

    async with httpx.AsyncClient(timeout=60) as client:
        for page in range(1, max_pages + 1):
            body["page"] = page
            resp = await client.post(url, json=body, headers=headers)

            if resp.status_code == 429:
                import asyncio
                logger.warning(f"Rate limited on funnel API for {date_str}, waiting 30s...")
                await asyncio.sleep(30)
                resp = await client.post(url, json=body, headers=headers)

            resp.raise_for_status()
            data = resp.json().get("data", {})
            cards = data.get("cards", [])
            if not cards:
                break
            all_items.extend(cards)

            total_pages = data.get("totalPages", 1)
            if page >= total_pages:
                break

    return all_items


async def fetch_ad_campaigns(api_key: str) -> list[int]:
    """Get list of active/paused ad campaign IDs."""
    url = "https://advert-api.wildberries.ru/adv/v1/promotion/count"
    headers = {"Authorization": api_key}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)

        if resp.status_code == 429:
            import asyncio
            await asyncio.sleep(30)
            resp = await client.get(url, headers=headers)

        resp.raise_for_status()
        data = resp.json()

        campaign_ids = []
        for adv_type in data.get("adverts", []):
            for c in adv_type.get("advert_list", []):
                campaign_ids.append(c.get("advertId"))
        return [cid for cid in campaign_ids if cid]


async def fetch_ad_stats(
    api_key: str, campaign_ids: list[int],
    begin_date: str, end_date: str
) -> dict:
    """
    Fetch detailed ad stats per nmId per date.
    Returns {date_str: {nm_id: {sum, clicks, views}}}.
    """
    if not campaign_ids:
        return {}

    url = "https://advert-api.wildberries.ru/adv/v2/fullstats"
    headers = {"Authorization": api_key, "Content-Type": "application/json"}

    result = {}
    chunk_size = 100

    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(campaign_ids), chunk_size):
            chunk = campaign_ids[i:i + chunk_size]
            body = [{"id": cid, "dates": [begin_date, end_date]} for cid in chunk]

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    resp = await client.post(url, json=body, headers=headers)

                    if resp.status_code == 429:
                        import asyncio
                        wait = 30 * (attempt + 1)
                        logger.warning(
                            f"Ad stats rate limited (attempt {attempt + 1}/{max_retries}), "
                            f"waiting {wait}s..."
                        )
                        await asyncio.sleep(wait)
                        continue

                    resp.raise_for_status()
                    campaigns_data = resp.json()
                    break
                except Exception as e:
                    if attempt == max_retries - 1:
                        logger.warning(f"Ad stats chunk failed after {max_retries} attempts: {e}")
                        campaigns_data = []
                    else:
                        import asyncio
                        await asyncio.sleep(10)
                        continue
            else:
                campaigns_data = []

            for camp in campaigns_data:
                for day_data in camp.get("days", []):
                    day_date = day_data.get("date", "")[:10]
                    if day_date not in result:
                        result[day_date] = {}
                    for app_data in day_data.get("apps", []):
                        for nm_data in app_data.get("nm", []):
                            nm_id = nm_data.get("nmId")
                            if nm_id:
                                if nm_id not in result[day_date]:
                                    result[day_date][nm_id] = {"sum": 0, "clicks": 0, "views": 0}
                                result[day_date][nm_id]["sum"] += nm_data.get("sum", 0)
                                result[day_date][nm_id]["clicks"] += nm_data.get("clicks", 0)
                                result[day_date][nm_id]["views"] += nm_data.get("views", 0)

            import asyncio
            await asyncio.sleep(1)

    return result


# ─── Cost price lookup ──────────────────────────────────────────────────────

async def get_cost_price(db: AsyncSession, nm_id: int, project_id: int) -> Optional[Decimal]:
    """
    Get cost price for an nmId.
    Priority: manual override > last CostOrderItem price.
    """
    # Check manual override first
    result = await db.execute(
        select(WbCostOverride.cost_price).where(
            WbCostOverride.nm_id == nm_id,
            WbCostOverride.project_id == project_id,
        )
    )
    override = result.scalar_one_or_none()
    if override is not None:
        return override

    # Fall back to last order item
    result = await db.execute(
        select(CostOrderItem.price_rub).where(
            CostOrderItem.nm_id == nm_id,
        ).order_by(CostOrderItem.id.desc()).limit(1)
    )
    return result.scalar_one_or_none()
