"""
Router: /funnel — WB Sales funnel analytics (воронка продаж).
Sync data from WB API (analytics + advertising), show daily stats.
"""

import logging
import asyncio
import httpx
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, delete, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from pydantic import BaseModel

from backend.database import get_db
from backend.project_context import get_current_project
from backend.models import (
    Project, IntegrationKey, WbFunnelDaily, WbCostOverride,
    CostOrderItem, SyncLog,
)
from backend.config import settings

logger = logging.getLogger("dds.funnel")

router = APIRouter(prefix="/funnel")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_fernet():
    import base64
    from cryptography.fernet import Fernet
    key = settings.SECRET_KEY.encode()[:32].ljust(32, b"=")
    return Fernet(base64.urlsafe_b64encode(key))


def _decrypt(value: str) -> str:
    from cryptography.fernet import InvalidToken
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        raise ValueError("Cannot decrypt API key")


async def _get_wb_key(db: AsyncSession, project_id: int, service: str) -> Optional[str]:
    """Get decrypted WB API key by service label. Also checks global keys (project_id IS NULL)."""
    from sqlalchemy import or_
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
    return _decrypt(key.encrypted_key)


# ─── WB API calls ───────────────────────────────────────────────────────────

async def _fetch_funnel(api_key: str, date_str: str) -> dict:
    """Fetch sales funnel data from WB Analytics API v3 for a single day."""
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


async def _fetch_ad_campaigns(api_key: str) -> list[int]:
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


async def _fetch_ad_stats(api_key: str, campaign_ids: list[int],
                          begin_date: str, end_date: str) -> dict:
    """Fetch detailed ad stats per nmId per date. Returns {date: {nm_id: {sum, clicks, views}}}."""
    import asyncio
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    result = {}

    # Split campaigns into chunks of 50
    chunks = [campaign_ids[i:i+50] for i in range(0, len(campaign_ids), 50)]

    async with httpx.AsyncClient(timeout=60) as client:
        for idx, chunk in enumerate(chunks):
            if idx > 0:
                await asyncio.sleep(21)  # WB rate limit

            ids_param = ",".join(str(c) for c in chunk)
            url = (f"https://advert-api.wildberries.ru/adv/v3/fullstats"
                   f"?ids={ids_param}&beginDate={begin_date}&endDate={end_date}")

            # Retry on 429 with backoff
            for attempt in range(3):
                try:
                    resp = await client.get(url, headers=headers)
                except Exception as e:
                    logger.warning(f"Ad stats request failed: {e}")
                    break

                if resp.status_code == 429:
                    wait = 30 * (attempt + 1)
                    logger.warning(f"WB adv 429 rate limit, waiting {wait}s (attempt {attempt+1}/3)")
                    await asyncio.sleep(wait)
                    continue
                elif resp.status_code != 200:
                    logger.error(f"WB adv stats error {resp.status_code}: {resp.text[:200]}")
                    break

                # Success — parse
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
                break  # Success, move to next chunk

    return result


# ─── Schemas ─────────────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    date_from: str  # YYYY-MM-DD
    date_to: str    # YYYY-MM-DD


class CostOverrideRequest(BaseModel):
    nm_id: int
    cost_price: float


# ─── Core sync logic (reusable by endpoint + scheduler) ─────────────────────

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
    analytics_key = await _get_wb_key(db, pid, "wb_analytics")
    if not analytics_key:
        analytics_key = await _get_wb_key(db, pid, "wb")
    if not analytics_key:
        return {"status": "error", "rows": 0, "days": 0, "errors": ["API ключ WB не найден"]}

    adv_key = await _get_wb_key(db, pid, "wb_advert")
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
    campaign_ids = await _fetch_ad_campaigns(adv_key)

    # Fetch ad stats for the whole range
    ad_stats = {}
    if campaign_ids:
        ad_stats = await _fetch_ad_stats(adv_key, campaign_ids, dates[0], dates[-1])

    # Fetch funnel data per day and upsert — commit per day for partial progress
    total_rows = 0
    errors = []
    for idx, date_str in enumerate(dates):
        # Rate limit: pause between days to avoid WB 429
        if idx > 0:
            await asyncio.sleep(1)
        try:
            funnel_data = await _fetch_funnel(analytics_key, date_str)

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


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/sync")
async def sync_funnel(
    body: SyncRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Sync WB sales funnel + advertising data for a date range (manual trigger)."""
    result = await run_funnel_sync(db, project.id, body.date_from, body.date_to)
    if result["status"] == "error" and not result["rows"]:
        raise HTTPException(400, result["errors"][0] if result["errors"] else "Sync failed")
    return result


@router.get("/sync_status")
async def get_sync_status(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get scheduler status and last sync info."""
    from backend.scheduler import get_scheduler_info

    # Last sync log for this project's funnel
    last_sync = await db.execute(
        select(SyncLog).where(
            SyncLog.service == "wb_funnel",
        ).order_by(SyncLog.id.desc()).limit(5)
    )
    logs = [
        {
            "id": s.id,
            "sync_type": s.sync_type,
            "status": s.status,
            "rows_inserted": s.rows_inserted,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            "error_msg": s.error_msg,
        }
        for s in last_sync.scalars()
    ]

    return {
        "scheduler": get_scheduler_info(),
        "last_syncs": logs,
    }


@router.post("/backfill")
async def backfill_funnel(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger full backfill: find all missing days in last 90 and sync them.
    Runs in BACKGROUND — returns immediately, sync continues async.
    Check progress via GET /funnel/sync_status or /funnel/backfill_status.
    """
    from backend.scheduler import _get_missing_dates

    pid = project.id
    missing = await _get_missing_dates(pid)

    if not missing:
        return {"status": "ok", "message": "Нет пропущенных дней", "missing_count": 0}

    logger.info(f"Backfill: project {pid} — {len(missing)} missing days, starting background task")

    # Launch background task
    asyncio.create_task(_run_backfill_bg(pid, missing))

    return {
        "status": "started",
        "message": f"Запущен фоновый backfill: {len(missing)} пропущенных дней",
        "total_missing": len(missing),
    }


async def _run_backfill_bg(project_id: int, missing_dates: list[str]):
    """Background coroutine: sync missing days one-by-one with rate limit pauses."""
    from backend.database import AsyncSessionLocal

    total_rows = 0
    total_errors = 0

    for idx, day_str in enumerate(missing_dates):
        try:
            if idx > 0:
                await asyncio.sleep(2)  # rate limit protection between days

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


@router.get("/data")
async def get_funnel_data(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    vendor_code: Optional[str] = Query(None),
    subject: Optional[str] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get funnel data. Aggregated by day if no brand/article filter, detailed otherwise."""
    pid = project.id
    tax_rate = float(project.tax_rate or 6)
    detailed = bool(brand or vendor_code)

    if not detailed:
        # Aggregated view: SUM by date
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

        q = q.group_by(WbFunnelDaily.date).order_by(WbFunnelDaily.date.desc())
        result = await db.execute(q)
        rows = result.all()

        data = []
        for r in rows:
            orders_sum = float(r.orders_sum_rub or 0)
            buyout = float(r.buyout_percent or 0)
            revenue = orders_sum * buyout / 100 if buyout else orders_sum
            adv = float(r.adv_sum or 0)
            tax = revenue * tax_rate / 100
            profit = revenue - adv - tax
            margin = (profit / revenue * 100) if revenue else 0
            views = int(r.adv_views or 0)
            clicks = int(r.adv_clicks or 0)
            ctr = (clicks / views * 100) if views else 0
            cpc = (adv / clicks) if clicks else 0
            cpm = (adv / views * 1000) if views else 0
            orders_count = int(r.orders_count or 0)
            cr = (orders_count / clicks * 100) if clicks else 0
            drr = (adv / orders_sum * 100) if orders_sum else 0

            data.append({
                "date": r.date.isoformat(),
                "open_card": int(r.open_card or 0),
                "add_to_cart": int(r.add_to_cart or 0),
                "orders_count": orders_count,
                "orders_sum_rub": orders_sum,
                "buyout_percent": round(buyout, 2),
                "revenue": round(revenue, 2),
                "adv_sum": adv,
                "adv_views": views,
                "adv_clicks": clicks,
                "ctr": round(ctr, 2),
                "cpc": round(cpc, 2),
                "cpm": round(cpm, 2),
                "cr": round(cr, 2),
                "drr": round(drr, 2),
                "tax": round(tax, 2),
                "profit": round(profit, 2),
                "margin": round(margin, 2),
                "avg_price": round(float(r.avg_price or 0), 2),
                "add_to_cart_pct": round(float(r.add_to_cart_pct or 0), 2),
                "cart_to_order_pct": round(float(r.cart_to_order_pct or 0), 2),
            })

        return {"data": data, "tax_rate": tax_rate, "detailed": False}

    else:
        # Detailed view: per product
        q = select(WbFunnelDaily).where(WbFunnelDaily.project_id == pid)

        if date_from:
            q = q.where(WbFunnelDaily.date >= date.fromisoformat(date_from))
        if date_to:
            q = q.where(WbFunnelDaily.date <= date.fromisoformat(date_to))
        if brand:
            q = q.where(WbFunnelDaily.brand == brand)
        if vendor_code:
            q = q.where(WbFunnelDaily.vendor_code.ilike(f"%{vendor_code}%"))
        if subject:
            q = q.where(WbFunnelDaily.subject == subject)

        q = q.order_by(WbFunnelDaily.date.desc(), WbFunnelDaily.orders_sum_rub.desc())
        result = await db.execute(q)
        rows = result.scalars().all()

        data = []
        for r in rows:
            orders_sum = float(r.orders_sum_rub or 0)
            buyout = float(r.buyout_percent or 0)
            revenue = orders_sum * buyout / 100 if buyout else orders_sum
            adv = float(r.adv_sum or 0)
            cost_per_unit = float(r.cost_price or 0)
            cost_total = cost_per_unit * (r.orders_count or 0)
            tax = revenue * tax_rate / 100
            profit = revenue - cost_total - adv - tax
            margin = (profit / revenue * 100) if revenue else 0
            views = r.adv_views or 0
            clicks = r.adv_clicks or 0
            ctr = (clicks / views * 100) if views else 0
            cpc = (adv / clicks) if clicks else 0
            cpm = (adv / views * 1000) if views else 0
            cr = (r.orders_count / clicks * 100) if clicks else 0
            drr = (adv / orders_sum * 100) if orders_sum else 0

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
                "revenue": round(revenue, 2),
                "cost_price": cost_per_unit,
                "cost_total": round(cost_total, 2),
                "tax": round(tax, 2),
                "profit": round(profit, 2),
                "margin": round(margin, 2),
                "adv_sum": adv,
                "adv_views": views,
                "adv_clicks": clicks,
                "ctr": round(ctr, 2),
                "cpc": round(cpc, 2),
                "cpm": round(cpm, 2),
                "cr": round(cr, 2),
                "drr": round(drr, 2),
                "add_to_cart_pct": float(r.add_to_cart_pct or 0),
                "cart_to_order_pct": float(r.cart_to_order_pct or 0),
                "avg_price": float(r.avg_price or 0),
                "stocks_wb": r.stocks_wb,
                "stocks_mp": r.stocks_mp,
            })

        return {"data": data, "tax_rate": tax_rate, "detailed": True}


@router.get("/summary")
async def get_funnel_summary(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get summary totals for the period header."""
    pid = project.id

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
    }


@router.get("/filters")
async def get_funnel_filters(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get unique brands, subjects, dates for filter dropdowns."""
    pid = project.id

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

@router.get("/costs")
async def get_cost_overrides(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get all manual cost overrides + items without cost."""
    pid = project.id

    # Get overrides
    overrides = await db.execute(
        select(WbCostOverride).where(WbCostOverride.project_id == pid)
    )
    override_list = [
        {"nm_id": o.nm_id, "cost_price": float(o.cost_price)}
        for o in overrides.scalars()
    ]

    # Get all unique nm_ids without cost
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
        {
            "nm_id": r.nm_id,
            "vendor_code": r.vendor_code,
            "subject": r.subject,
            "brand": r.brand,
        }
        for r in no_cost
    ]

    return {"overrides": override_list, "missing": missing}


@router.post("/cost")
async def set_cost_override(
    body: CostOverrideRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Set or update manual cost price for an nmId."""
    pid = project.id

    stmt = pg_insert(WbCostOverride).values(
        project_id=pid,
        nm_id=body.nm_id,
        cost_price=body.cost_price,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_cost_override_nm",
        set_={"cost_price": body.cost_price},
    )
    await db.execute(stmt)

    # Also update existing funnel rows
    await db.execute(
        WbFunnelDaily.__table__.update()
        .where(
            WbFunnelDaily.project_id == pid,
            WbFunnelDaily.nm_id == body.nm_id,
        )
        .values(cost_price=body.cost_price)
    )

    await db.commit()
    return {"status": "ok"}


@router.get("/tax")
async def get_tax_rate(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get project tax rate."""
    return {"tax_rate": float(project.tax_rate or 6)}


class TaxRateRequest(BaseModel):
    tax_rate: float


@router.post("/tax")
async def set_tax_rate(
    body: TaxRateRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Set project tax rate."""
    project.tax_rate = Decimal(str(body.tax_rate))
    await db.commit()
    return {"status": "ok", "tax_rate": body.tax_rate}
