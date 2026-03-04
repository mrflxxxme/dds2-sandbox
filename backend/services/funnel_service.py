"""
Funnel service — business logic for WB sales funnel.

Handles:
- WB API key lookup and decryption
- WB API calls (analytics + advertising)
- Funnel data sync (fetch + upsert per day)
- Data aggregation and filtering
- Cost overrides
- Day analysis with anomaly detection
"""

import logging
import asyncio
import httpx
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models import (
    IntegrationKey, WbFunnelDaily, WbCostOverride,
    CostOrderItem, SyncLog, Project,
)
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
            resp = await client.post(
                "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products",
                headers=headers,
                json=payload,
            )
            if resp.status_code != 200:
                logger.error(f"WB funnel API error {resp.status_code}: {resp.text[:200]}")
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


async def fetch_ad_campaigns(api_key: str) -> list[int]:
    """Get list of active/paused ad campaign IDs."""
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
            if status in ("9", "11"):  # active / paused
                for compa in adv.get("advert_list") or []:
                    cid = compa.get("advertId")
                    if cid and cid not in campaign_ids:
                        campaign_ids.append(cid)
        return campaign_ids


async def fetch_ad_stats(api_key: str, campaign_ids: list[int],
                         begin_date: str, end_date: str) -> dict:
    """Fetch detailed ad stats per nmId per date.
    Returns {date: {nm_id: {sum, clicks, views}}}.
    """
    result = {}
    chunks = [campaign_ids[i:i+50] for i in range(0, len(campaign_ids), 50)]
    skipped_chunks = 0

    async with httpx.AsyncClient(timeout=60) as client:
        for idx, chunk in enumerate(chunks):
            if idx > 0:
                await asyncio.sleep(35)  # WB rate limit: 35s between chunks

            ids_param = ",".join(str(c) for c in chunk)
            url = (f"https://advert-api.wildberries.ru/adv/v3/fullstats"
                   f"?ids={ids_param}&beginDate={begin_date}&endDate={end_date}")

            chunk_ok = False
            for attempt in range(5):  # 5 retries instead of 3
                try:
                    resp = await client.get(url, headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    })
                except Exception as e:
                    logger.warning(f"Ad stats request failed: {e}")
                    break

                if resp.status_code == 429:
                    wait = 45 * (attempt + 1)  # 45, 90, 135, 180, 225 seconds
                    logger.warning(f"WB adv 429 rate limit, waiting {wait}s (attempt {attempt+1}/5)")
                    await asyncio.sleep(wait)
                    continue
                elif resp.status_code != 200:
                    logger.error(f"WB adv stats error {resp.status_code}: {resp.text[:200]}")
                    break

                data = resp.json()
                items = data if isinstance(data, list) else (data.get("data") or data)
                if not isinstance(items, list):
                    break

                for campaign in items:
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

    if skipped_chunks:
        logger.warning(f"WB adv: {skipped_chunks}/{len(chunks)} chunks skipped for {begin_date}→{end_date}")

    return result


# ─── Core sync logic ────────────────────────────────────────────────────────

async def run_funnel_sync(
    db: AsyncSession, project_id: int, date_from: str, date_to: str
) -> dict:
    """
    Core funnel sync: fetch WB data for date range and upsert into DB.
    Callable from both the POST /sync endpoint and the background scheduler.
    Returns {status, rows, days, errors}.
    """
    pid = project_id

    # Get API keys
    analytics_key = await get_wb_key(db, pid, "wb_analytics")
    if not analytics_key:
        analytics_key = await get_wb_key(db, pid, "wb")
    if not analytics_key:
        return {"status": "error", "rows": 0, "days": 0, "errors": ["API ключ WB не найден"]}

    adv_key = await get_wb_key(db, pid, "wb_advert")
    if not adv_key:
        adv_key = analytics_key

    # Build date range
    d_from = date.fromisoformat(date_from)
    d_to = date.fromisoformat(date_to)
    dates = []
    d = d_from
    while d <= d_to:
        dates.append(d.isoformat())
        d += timedelta(days=1)

    if not dates:
        return {"status": "error", "rows": 0, "days": 0, "errors": ["Пустой диапазон дат"]}

    # Get cost prices from orders
    cost_map: dict = {}
    try:
        cost_result = await db.execute(
            select(
                CostOrderItem.article_seller,
                CostOrderItem.total_rub,
                CostOrderItem.qty,
            ).where(
                CostOrderItem.article_seller.isnot(None),
                CostOrderItem.total_rub.isnot(None),
            ).order_by(CostOrderItem.id.desc())
        )
        for row in cost_result:
            art = row.article_seller
            if art and art not in cost_map:
                qty = row.qty or 1
                cost_map[art] = float(row.total_rub) / qty
    except Exception as e:
        logger.warning(f"Cost lookup failed: {e}")

    # Get manual overrides
    override_result = await db.execute(
        select(WbCostOverride).where(WbCostOverride.project_id == pid)
    )
    for ov in override_result.scalars():
        cost_map[ov.nm_id] = float(ov.cost_price)

    # Fetch ad campaigns
    campaign_ids = await fetch_ad_campaigns(adv_key)

    # Fetch ad stats for the whole range
    ad_stats = {}
    if campaign_ids:
        ad_stats = await fetch_ad_stats(adv_key, campaign_ids, dates[0], dates[-1])

    # Fetch funnel data per day and upsert — commit per day for partial progress
    total_rows = 0
    errors = []
    for idx, date_str in enumerate(dates):
        if idx > 0:
            await asyncio.sleep(1)
        try:
            funnel_data = await fetch_funnel(analytics_key, date_str)

            rows_to_upsert = []
            for nm_id, fd in funnel_data.items():
                ad = (ad_stats.get(date_str) or {}).get(nm_id, {})
                cost = cost_map.get(nm_id)

                rows_to_upsert.append({
                    "project_id": pid,
                    "date": date.fromisoformat(date_str),
                    "nm_id": nm_id,
                    "vendor_code": fd["vendor_code"],
                    "subject": fd["subject"],
                    "brand": fd["brand"],
                    "open_card": fd["open_card"],
                    "add_to_cart": fd["add_to_cart"],
                    "orders_count": fd["orders_count"],
                    "orders_sum_rub": fd["orders_sum_rub"],
                    "buyout_percent": fd["buyout_percent"],
                    "cart_to_order_pct": fd["cart_to_order_pct"],
                    "add_to_cart_pct": fd["add_to_cart_pct"],
                    "avg_price": fd["avg_price"],
                    "stocks_wb": fd["stocks_wb"],
                    "stocks_mp": fd["stocks_mp"],
                    "adv_views": ad.get("views", 0),
                    "adv_clicks": ad.get("clicks", 0),
                    "adv_sum": ad.get("sum", 0),
                    "cost_price": cost,
                })

            if rows_to_upsert:
                stmt = pg_insert(WbFunnelDaily).values(rows_to_upsert)
                stmt = stmt.on_conflict_do_update(
                    constraint="uq_funnel_daily",
                    set_={
                        "vendor_code": stmt.excluded.vendor_code,
                        "subject": stmt.excluded.subject,
                        "brand": stmt.excluded.brand,
                        "open_card": stmt.excluded.open_card,
                        "add_to_cart": stmt.excluded.add_to_cart,
                        "orders_count": stmt.excluded.orders_count,
                        "orders_sum_rub": stmt.excluded.orders_sum_rub,
                        "buyout_percent": stmt.excluded.buyout_percent,
                        "cart_to_order_pct": stmt.excluded.cart_to_order_pct,
                        "add_to_cart_pct": stmt.excluded.add_to_cart_pct,
                        "avg_price": stmt.excluded.avg_price,
                        "stocks_wb": stmt.excluded.stocks_wb,
                        "stocks_mp": stmt.excluded.stocks_mp,
                        "adv_views": stmt.excluded.adv_views,
                        "adv_clicks": stmt.excluded.adv_clicks,
                        "adv_sum": stmt.excluded.adv_sum,
                        "cost_price": stmt.excluded.cost_price,
                    },
                )
                await db.execute(stmt)
                await db.commit()
                total_rows += len(rows_to_upsert)
                logger.info(f"Funnel synced {date_str}: {len(rows_to_upsert)} rows")
        except Exception as e:
            logger.error(f"Funnel sync error for {date_str}: {e}")
            errors.append(str(e))
            try:
                await db.rollback()
            except Exception:
                pass

    return {"status": "ok", "rows": total_rows, "days": len(dates), "errors": errors[:5]}


async def run_backfill_bg(project_id: int, missing_dates: list[str]):
    """Background coroutine: sync missing days one-by-one with rate limit pauses."""
    from backend.database import AsyncSessionLocal

    total_rows = 0
    total_errors = 0

    for idx, day_str in enumerate(missing_dates):
        try:
            if idx > 0:
                await asyncio.sleep(2)

            async with AsyncSessionLocal() as db:
                result = await run_funnel_sync(db, project_id, day_str, day_str)
                rows = result.get("rows", 0)
                total_rows += rows
                if result.get("errors"):
                    total_errors += len(result["errors"])

            logger.info(
                f"Backfill bg: project {project_id} — "
                f"day {idx+1}/{len(missing_dates)} ({day_str}), "
                f"+{rows} rows, total: {total_rows}"
            )
        except Exception as e:
            logger.error(f"Backfill bg error: project {project_id}, {day_str}: {e}")
            total_errors += 1

    logger.info(
        f"✅ Backfill complete: project {project_id} — "
        f"{len(missing_dates)} days, {total_rows} rows, {total_errors} errors"
    )


# ─── Data queries ────────────────────────────────────────────────────────────

def _compute_metrics(orders_sum: float, buyout: float, adv: float,
                     tax_rate: float, views: int, clicks: int,
                     orders_count: int, cost_total: float = 0) -> dict:
    """Compute derived funnel metrics from raw aggregates."""
    revenue = orders_sum * buyout / 100 if buyout else orders_sum
    tax = revenue * tax_rate / 100
    profit = revenue - cost_total - adv - tax
    margin = (profit / revenue * 100) if revenue else 0
    ctr = (clicks / views * 100) if views else 0
    cpc = (adv / clicks) if clicks else 0
    cpm = (adv / views * 1000) if views else 0
    cr = (orders_count / clicks * 100) if clicks else 0
    drr = (adv / orders_sum * 100) if orders_sum else 0
    return {
        "revenue": round(revenue, 2),
        "tax": round(tax, 2),
        "profit": round(profit, 2),
        "margin": round(margin, 2),
        "ctr": round(ctr, 2),
        "cpc": round(cpc, 2),
        "cpm": round(cpm, 2),
        "cr": round(cr, 2),
        "drr": round(drr, 2),
    }


async def get_funnel_aggregated(db: AsyncSession, pid: int, tax_rate: float,
                                date_from: Optional[str], date_to: Optional[str],
                                brand: Optional[str], subject: Optional[str]) -> list[dict]:
    """Get funnel data aggregated by day (no article filter)."""
    q = select(
        WbFunnelDaily.date,
        func.sum(WbFunnelDaily.open_card).label("open_card"),
        func.sum(WbFunnelDaily.add_to_cart).label("add_to_cart"),
        func.sum(WbFunnelDaily.orders_count).label("orders_count"),
        func.sum(WbFunnelDaily.orders_sum_rub).label("orders_sum_rub"),
        func.avg(WbFunnelDaily.buyout_percent).label("buyout_percent"),
        func.avg(WbFunnelDaily.cart_to_order_pct).label("cart_to_order_pct"),
        func.avg(WbFunnelDaily.add_to_cart_pct).label("add_to_cart_pct"),
        func.avg(WbFunnelDaily.avg_price).label("avg_price"),
        func.sum(WbFunnelDaily.adv_views).label("adv_views"),
        func.sum(WbFunnelDaily.adv_clicks).label("adv_clicks"),
        func.sum(WbFunnelDaily.adv_sum).label("adv_sum"),
    ).where(WbFunnelDaily.project_id == pid)

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)

    q = q.group_by(WbFunnelDaily.date).order_by(WbFunnelDaily.date.desc())
    result = await db.execute(q)
    rows = result.all()

    data = []
    for r in rows:
        orders_sum = float(r.orders_sum_rub or 0)
        buyout = float(r.buyout_percent or 0)
        adv = float(r.adv_sum or 0)
        views = int(r.adv_views or 0)
        clicks = int(r.adv_clicks or 0)
        orders_count = int(r.orders_count or 0)
        m = _compute_metrics(orders_sum, buyout, adv, tax_rate, views, clicks, orders_count)

        data.append({
            "date": r.date.isoformat(),
            "open_card": int(r.open_card or 0),
            "add_to_cart": int(r.add_to_cart or 0),
            "orders_count": orders_count,
            "orders_sum_rub": orders_sum,
            "buyout_percent": round(buyout, 2),
            "adv_sum": adv,
            "adv_views": views,
            "adv_clicks": clicks,
            "avg_price": round(float(r.avg_price or 0), 2),
            "add_to_cart_pct": round(float(r.add_to_cart_pct or 0), 2),
            "cart_to_order_pct": round(float(r.cart_to_order_pct or 0), 2),
            **m,
        })

    return data


async def get_funnel_detailed(db: AsyncSession, pid: int, tax_rate: float,
                              date_from: Optional[str], date_to: Optional[str],
                              brand: Optional[str], vendor_code: Optional[str],
                              subject: Optional[str]) -> list[dict]:
    """Get detailed funnel data per product."""
    q = select(WbFunnelDaily).where(WbFunnelDaily.project_id == pid)

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)
    if vendor_code:
        vc_filter = WbFunnelDaily.vendor_code.ilike(f"%{vendor_code}%")
        if vendor_code.isdigit():
            vc_filter = or_(vc_filter, WbFunnelDaily.nm_id == int(vendor_code))
        q = q.where(vc_filter)
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)

    q = q.order_by(WbFunnelDaily.date.desc(), WbFunnelDaily.orders_sum_rub.desc())
    result = await db.execute(q)
    rows = result.scalars().all()

    data = []
    for r in rows:
        orders_sum = float(r.orders_sum_rub or 0)
        buyout = float(r.buyout_percent or 0)
        adv = float(r.adv_sum or 0)
        cost_per_unit = float(r.cost_price or 0)
        cost_total = cost_per_unit * (r.orders_count or 0)
        views = r.adv_views or 0
        clicks = r.adv_clicks or 0
        m = _compute_metrics(orders_sum, buyout, adv, tax_rate, views, clicks,
                             r.orders_count or 0, cost_total)

        data.append({
            "id": r.id,
            "date": r.date.isoformat(),
            "nm_id": r.nm_id,
            "vendor_code": r.vendor_code,
            "subject": r.subject,
            "brand": r.brand,
            "open_card": r.open_card,
            "add_to_cart": r.add_to_cart,
            "orders_count": r.orders_count,
            "orders_sum_rub": orders_sum,
            "buyout_percent": buyout,
            "cost_price": cost_per_unit,
            "cost_total": round(cost_total, 2),
            "adv_sum": adv,
            "adv_views": views,
            "adv_clicks": clicks,
            "add_to_cart_pct": float(r.add_to_cart_pct or 0),
            "cart_to_order_pct": float(r.cart_to_order_pct or 0),
            "avg_price": float(r.avg_price or 0),
            "stocks_wb": r.stocks_wb,
            "stocks_mp": r.stocks_mp,
            **m,
        })

    return data


async def get_summary(db: AsyncSession, pid: int,
                      date_from: Optional[str], date_to: Optional[str],
                      brand: Optional[str], subject: Optional[str]) -> dict:
    """Get summary totals for the period header."""
    q = select(
        func.sum(WbFunnelDaily.open_card).label("open_card"),
        func.sum(WbFunnelDaily.add_to_cart).label("add_to_cart"),
        func.sum(WbFunnelDaily.orders_count).label("orders_count"),
        func.sum(WbFunnelDaily.orders_sum_rub).label("orders_sum_rub"),
        func.sum(WbFunnelDaily.adv_sum).label("adv_sum"),
        func.sum(WbFunnelDaily.adv_views).label("adv_views"),
        func.sum(WbFunnelDaily.adv_clicks).label("adv_clicks"),
    ).where(WbFunnelDaily.project_id == pid)

    if date_from:
        q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
    if date_to:
        q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
    if brand:
        q = q.where(WbFunnelDaily.brand == brand)
    if subject:
        q = q.where(WbFunnelDaily.subject == subject)

    result = await db.execute(q)
    row = result.one()

    return {
        "open_card": int(row.open_card or 0),
        "add_to_cart": int(row.add_to_cart or 0),
        "orders_count": int(row.orders_count or 0),
        "orders_sum_rub": float(row.orders_sum_rub or 0),
        "adv_sum": float(row.adv_sum or 0),
        "adv_views": int(row.adv_views or 0),
        "adv_clicks": int(row.adv_clicks or 0),
        "drr": round(
            float(row.adv_sum or 0) / float(row.orders_sum_rub or 1) * 100, 2
        ) if float(row.orders_sum_rub or 0) > 0 else 0,
    }


async def get_filters(db: AsyncSession, pid: int) -> dict:
    """Get unique brands, subjects, date range for filter dropdowns."""
    brands = await db.execute(
        select(WbFunnelDaily.brand).where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.brand.isnot(None),
        ).distinct()
    )
    subjects = await db.execute(
        select(WbFunnelDaily.subject).where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.subject.isnot(None),
        ).distinct()
    )
    dates = await db.execute(
        select(
            func.min(WbFunnelDaily.date).label("min_date"),
            func.max(WbFunnelDaily.date).label("max_date"),
        ).where(WbFunnelDaily.project_id == pid)
    )
    d = dates.one()

    return {
        "brands": sorted([r[0] for r in brands if r[0]]),
        "subjects": sorted([r[0] for r in subjects if r[0]]),
        "min_date": d.min_date.isoformat() if d.min_date else None,
        "max_date": d.max_date.isoformat() if d.max_date else None,
    }


# ─── Cost overrides ─────────────────────────────────────────────────────────

async def get_cost_overrides(db: AsyncSession, pid: int) -> dict:
    """Get all manual cost overrides + items without cost."""
    overrides = await db.execute(
        select(WbCostOverride).where(WbCostOverride.project_id == pid)
    )
    override_list = [
        {"nm_id": o.nm_id, "cost_price": float(o.cost_price)}
        for o in overrides.scalars()
    ]

    no_cost = await db.execute(
        select(
            WbFunnelDaily.nm_id,
            WbFunnelDaily.vendor_code,
            WbFunnelDaily.subject,
            WbFunnelDaily.brand,
        ).where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.cost_price.is_(None),
        ).distinct()
    )
    missing = [
        {"nm_id": r.nm_id, "vendor_code": r.vendor_code,
         "subject": r.subject, "brand": r.brand}
        for r in no_cost
    ]

    return {"overrides": override_list, "missing": missing}


async def set_cost_override(db: AsyncSession, pid: int, nm_id: int,
                            cost_price: float) -> dict:
    """Set or update manual cost price for an nmId."""
    stmt = pg_insert(WbCostOverride).values(
        project_id=pid, nm_id=nm_id, cost_price=cost_price,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_cost_override_nm",
        set_={"cost_price": cost_price},
    )
    await db.execute(stmt)

    # Also update existing funnel rows
    await db.execute(
        WbFunnelDaily.__table__.update()
        .where(WbFunnelDaily.project_id == pid, WbFunnelDaily.nm_id == nm_id)
        .values(cost_price=cost_price)
    )

    await db.commit()
    return {"status": "ok"}


# ─── Day analysis ────────────────────────────────────────────────────────────

async def get_day_analysis(db: AsyncSession, pid: int, tax_rate: float,
                           target_date: str, brand: Optional[str],
                           subject: Optional[str], trend_days: int = 14) -> dict:
    """Day analysis: summary, comparison, top products, trend, anomalies."""
    td = date.fromisoformat(target_date)
    prev_d = td - timedelta(days=1)
    trend_start = td - timedelta(days=trend_days - 1)

    def _base_filter(q):
        q = q.where(WbFunnelDaily.project_id == pid)
        if brand:
            q = q.where(WbFunnelDaily.brand == brand)
        if subject:
            q = q.where(WbFunnelDaily.subject == subject)
        return q

    async def _day_agg(d: date) -> dict:
        q = select(
            func.coalesce(func.sum(WbFunnelDaily.open_card), 0).label("open_card"),
            func.coalesce(func.sum(WbFunnelDaily.add_to_cart), 0).label("add_to_cart"),
            func.coalesce(func.sum(WbFunnelDaily.orders_count), 0).label("orders_count"),
            func.coalesce(func.sum(WbFunnelDaily.orders_sum_rub), 0).label("orders_sum"),
            func.coalesce(func.sum(WbFunnelDaily.adv_sum), 0).label("adv_sum"),
            func.coalesce(func.sum(WbFunnelDaily.adv_views), 0).label("adv_views"),
            func.coalesce(func.sum(WbFunnelDaily.adv_clicks), 0).label("adv_clicks"),
        ).where(WbFunnelDaily.date == d)
        q = _base_filter(q)
        row = (await db.execute(q)).one()
        os_ = float(row.orders_sum)
        adv = float(row.adv_sum)
        revenue = os_
        tax = revenue * tax_rate / 100
        profit = revenue - adv - tax
        drr = (adv / os_ * 100) if os_ else 0
        views = int(row.adv_views)
        clicks = int(row.adv_clicks)
        ctr = (clicks / views * 100) if views else 0
        cpc = (adv / clicks) if clicks else 0
        return {
            "date": d.isoformat(),
            "open_card": int(row.open_card),
            "add_to_cart": int(row.add_to_cart),
            "orders_count": int(row.orders_count),
            "orders_sum": round(os_, 2),
            "revenue": round(revenue, 2),
            "adv_sum": round(adv, 2),
            "adv_views": views,
            "adv_clicks": clicks,
            "ctr": round(ctr, 2),
            "cpc": round(cpc, 2),
            "drr": round(drr, 2),
            "tax": round(tax, 2),
            "profit": round(profit, 2),
            "margin": round((profit / revenue * 100) if revenue else 0, 2),
        }

    today_agg = await _day_agg(td)
    prev_agg = await _day_agg(prev_d)

    # Comparison
    def _pct(cur, prev):
        if prev == 0:
            return 100.0 if cur > 0 else 0.0
        return round((cur - prev) / abs(prev) * 100, 1)

    comparison = {}
    for key in ["orders_sum", "revenue", "adv_sum", "orders_count", "profit", "drr", "open_card", "add_to_cart"]:
        comparison[key] = {
            "current": today_agg[key],
            "previous": prev_agg[key],
            "change_pct": _pct(today_agg[key], prev_agg[key]),
        }

    # Top products
    q_top = select(WbFunnelDaily).where(WbFunnelDaily.date == td)
    q_top = _base_filter(q_top)
    q_top = q_top.order_by(WbFunnelDaily.orders_sum_rub.desc()).limit(50)
    top_rows = (await db.execute(q_top)).scalars().all()

    top_products = []
    for r in top_rows:
        os_ = float(r.orders_sum_rub or 0)
        adv = float(r.adv_sum or 0)
        drr = (adv / os_ * 100) if os_ else 0
        top_products.append({
            "nm_id": r.nm_id, "vendor_code": r.vendor_code,
            "brand": r.brand, "subject": r.subject,
            "orders_count": r.orders_count or 0,
            "orders_sum": round(os_, 2), "adv_sum": round(adv, 2),
            "drr": round(drr, 2),
            "open_card": r.open_card or 0, "add_to_cart": r.add_to_cart or 0,
        })

    # Trend
    q_trend = select(
        WbFunnelDaily.date,
        func.sum(WbFunnelDaily.orders_sum_rub).label("orders_sum"),
        func.sum(WbFunnelDaily.adv_sum).label("adv_sum"),
        func.sum(WbFunnelDaily.orders_count).label("orders_count"),
        func.sum(WbFunnelDaily.open_card).label("open_card"),
    ).where(WbFunnelDaily.date >= trend_start, WbFunnelDaily.date <= td)
    q_trend = _base_filter(q_trend)
    q_trend = q_trend.group_by(WbFunnelDaily.date).order_by(WbFunnelDaily.date)
    trend_rows = (await db.execute(q_trend)).all()

    trend = []
    for r in trend_rows:
        os_ = float(r.orders_sum or 0)
        adv = float(r.adv_sum or 0)
        trend.append({
            "date": r.date.isoformat(),
            "orders_sum": round(os_, 2), "adv_sum": round(adv, 2),
            "orders_count": int(r.orders_count or 0),
            "open_card": int(r.open_card or 0),
            "drr": round((adv / os_ * 100) if os_ else 0, 2),
        })

    # Anomalies
    q_avg = select(
        WbFunnelDaily.nm_id,
        func.avg(WbFunnelDaily.orders_sum_rub).label("avg_orders_sum"),
        func.avg(WbFunnelDaily.orders_count).label("avg_orders_count"),
    ).where(WbFunnelDaily.date >= trend_start, WbFunnelDaily.date < td)
    q_avg = _base_filter(q_avg)
    q_avg = q_avg.group_by(WbFunnelDaily.nm_id)
    avg_rows = (await db.execute(q_avg)).all()
    avg_map = {
        r.nm_id: {"avg_sum": float(r.avg_orders_sum or 0), "avg_cnt": float(r.avg_orders_count or 0)}
        for r in avg_rows
    }

    anomalies = []
    for p in top_products:
        avg = avg_map.get(p["nm_id"])
        if not avg:
            continue
        flags = []
        if avg["avg_sum"] > 0:
            pct_change = (p["orders_sum"] - avg["avg_sum"]) / avg["avg_sum"] * 100
            if abs(pct_change) > 30:
                flags.append(
                    f"{'📈' if pct_change > 0 else '📉'} Выручка "
                    f"{'+' if pct_change > 0 else ''}{round(pct_change)}% vs средней"
                )
        if p["drr"] > 20:
            flags.append(f"⚠️ ДРР {p['drr']}% (>20%)")
        if p["adv_sum"] > 0 and p["orders_count"] == 0:
            flags.append("🚫 Реклама без заказов")
        if flags:
            anomalies.append({**p, "flags": flags})

    return {
        "target_date": target_date,
        "summary": today_agg,
        "comparison": comparison,
        "top_products": top_products,
        "trend": trend,
        "anomalies": anomalies,
    }
