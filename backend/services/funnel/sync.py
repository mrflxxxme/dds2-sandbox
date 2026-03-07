"""
Funnel sync — core data synchronization logic.

Handles:
- Daily funnel sync (fetch from WB + upsert into DB)
- Background backfill for missing days
- Batch ad data re-sync
"""

import logging
import asyncio
import time
from datetime import date, timedelta

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily, WbCostOverride, CostOrderItem
from backend.services.funnel.wb_api_client import (
    get_wb_key, fetch_funnel, fetch_funnel_history,
    fetch_ad_campaigns, fetch_ad_stats,
)

logger = logging.getLogger("dds.funnel")


async def run_funnel_sync(
    db: AsyncSession, project_id: int, date_from: str, date_to: str,
    include_completed_campaigns: bool = False,
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
    campaign_ids = await fetch_ad_campaigns(adv_key, include_completed=include_completed_campaigns)

    # Fetch ad stats for the whole range
    ad_stats = {}
    if campaign_ids:
        ad_stats = await fetch_ad_stats(adv_key, campaign_ids, dates[0], dates[-1])

    # ── Try bulk history endpoint first (7 days/request, requires Джем) ────
    # Split into 7-day windows and fetch all funnel data upfront
    HISTORY_WINDOW = 7
    funnel_by_day: dict = {}  # {date_str: {nm_id: {...}}}
    use_history = True

    for i in range(0, len(dates), HISTORY_WINDOW):
        window = dates[i:i + HISTORY_WINDOW]
        w_from, w_to = window[0], window[-1]

        if use_history:
            history_data = await fetch_funnel_history(analytics_key, w_from, w_to)
            if history_data is None:
                # 402 — Джем not available, fall back to per-day for ALL remaining
                use_history = False
                logger.info("Falling back to per-day funnel fetch (no Джем)")
            else:
                funnel_by_day.update(history_data)
                logger.info(f"History batch {w_from}→{w_to}: {len(history_data)} days")
                if i + HISTORY_WINDOW < len(dates):
                    await asyncio.sleep(1)
                continue

        # Fallback: fetch per day
        for idx, ds in enumerate(window):
            if idx > 0:
                await asyncio.sleep(1)
            try:
                day_data = await fetch_funnel(analytics_key, ds)
                if day_data:
                    funnel_by_day[ds] = day_data
            except Exception as e:
                logger.error(f"Funnel fetch error {ds}: {e}")

    if use_history and funnel_by_day:
        logger.info(
            f"Funnel history mode: {len(funnel_by_day)} days fetched "
            f"in {(len(dates) + HISTORY_WINDOW - 1) // HISTORY_WINDOW} requests "
            f"(vs {len(dates)} requests per-day)"
        )

    # ── Upsert per day ────────────────────────────────────────────────────
    total_rows = 0
    errors = []
    for date_str in dates:
        funnel_data = funnel_by_day.get(date_str)
        if not funnel_data:
            continue

        try:
            # Check if we have real ad data for this day
            day_ads = ad_stats.get(date_str) or {}
            has_ad_data = bool(day_ads)

            rows_to_upsert = []
            for nm_id, fd in funnel_data.items():
                ad = day_ads.get(nm_id, {})
                cost = cost_map.get(nm_id) or cost_map.get(fd.get("vendor_code"))

                row = {
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
                    "avg_price": fd.get("avg_price", 0),
                    "stocks_wb": fd.get("stocks_wb", 0),
                    "stocks_mp": fd.get("stocks_mp", 0),
                    "adv_views": ad.get("views", 0),
                    "adv_clicks": ad.get("clicks", 0),
                    "adv_sum": ad.get("sum", 0),
                    "cost_price": cost,
                }
                rows_to_upsert.append(row)

            if rows_to_upsert:
                stmt = pg_insert(WbFunnelDaily).values(rows_to_upsert)

                # Base fields to always update
                update_fields = {
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
                    "cost_price": stmt.excluded.cost_price,
                }

                # Only overwrite ad fields if we have REAL ad data for this day
                if has_ad_data:
                    update_fields["adv_views"] = stmt.excluded.adv_views
                    update_fields["adv_clicks"] = stmt.excluded.adv_clicks
                    update_fields["adv_sum"] = stmt.excluded.adv_sum

                stmt = stmt.on_conflict_do_update(
                    constraint="uq_funnel_daily",
                    set_=update_fields,
                )
                await db.execute(stmt)
                await db.commit()
                total_rows += len(rows_to_upsert)
                logger.info(
                    f"Funnel synced {date_str}: {len(rows_to_upsert)} rows"
                    f"{' (with ads)' if has_ad_data else ' (no ad data, preserved existing)'}"
                )
        except Exception as e:
            logger.error(f"Funnel sync error for {date_str}: {e}")
            errors.append(str(e))
            try:
                await db.rollback()
            except Exception:
                pass

    return {"status": "ok", "rows": total_rows, "days": len(dates), "errors": errors[:5]}


async def run_backfill_bg(project_id: int, missing_dates: list[str]):
    """Background coroutine: sync missing days with concurrent semaphore."""
    from backend.database import AsyncSessionLocal

    # Semaphore: max 3 concurrent WB API calls
    sem = asyncio.Semaphore(3)
    total_rows = 0
    total_errors = 0

    async def _sync_one(day_str: str) -> dict:
        """Sync a single day, guarded by semaphore."""
        async with sem:
            try:
                async with AsyncSessionLocal() as db:
                    result = await asyncio.wait_for(
                        run_funnel_sync(db, project_id, day_str, day_str),
                        timeout=600,  # 10 min — allows ad stats budget (180s) + funnel fetch + DB ops
                    )
                    return {"day": day_str, "rows": result.get("rows", 0), "errors": result.get("errors", [])}
            except asyncio.TimeoutError:
                return {"day": day_str, "rows": 0, "errors": [f"Timeout 5min: {day_str}"]}
            except Exception as e:
                return {"day": day_str, "rows": 0, "errors": [str(e)[:200]]}

    # Process in batches of 5 days at a time
    batch_size = 5
    for i in range(0, len(missing_dates), batch_size):
        batch = missing_dates[i:i + batch_size]
        results = await asyncio.gather(
            *[_sync_one(d) for d in batch],
            return_exceptions=True,
        )

        for r in results:
            if isinstance(r, Exception):
                total_errors += 1
                logger.error(f"Backfill bg exception: project {project_id}: {r}")
            elif isinstance(r, dict):
                total_rows += r.get("rows", 0)
                if r.get("errors"):
                    total_errors += len(r["errors"])
                logger.info(
                    f"Backfill bg: project {project_id} — "
                    f"day {r['day']}, +{r.get('rows', 0)} rows"
                )

        # Pause between batches to respect rate limits
        if i + batch_size < len(missing_dates):
            await asyncio.sleep(3)

    logger.info(
        f"✅ Backfill complete: project {project_id} — "
        f"{len(missing_dates)} days, {total_rows} rows, {total_errors} errors"
    )


async def batch_resync_ads(project_id: int) -> dict:
    """
    Batch re-sync ALL ad data for a project.
    Splits into 30-day windows (WB API max 31 days), fetches each window
    with all campaign chunks, then updates adv columns in DB.
    Pauses scheduler ad jobs to avoid 429 conflicts.
    Returns {status, days_updated, total_updated, errors}.
    """
    from backend.database import AsyncSessionLocal
    from datetime import timedelta as td

    # Pause scheduler ad jobs to avoid 429 conflicts
    try:
        from backend.scheduler import scheduler as sched
        if sched:
            try:
                sched.pause_job("ad_anomaly_check")
                logger.info("🔄 Paused ad_anomaly_check scheduler job")
            except Exception:
                pass
    except Exception:
        sched = None

    errors = []
    total_updated = 0
    days_updated = 0

    try:
        async with AsyncSessionLocal() as db:
            # 1. Get API key
            adv_key = await get_wb_key(db, project_id, "wb")
            if not adv_key:
                return {"status": "error", "days_updated": 0,
                        "total_updated": 0, "errors": ["API key not found"]}

            # 2. Get date range from existing data
            result = await db.execute(
                select(
                    func.min(WbFunnelDaily.date),
                    func.max(WbFunnelDaily.date),
                ).where(WbFunnelDaily.project_id == project_id)
            )
            row = result.one_or_none()
            if not row or not row[0]:
                return {"status": "error", "days_updated": 0,
                        "total_updated": 0, "errors": ["No existing data"]}

            start_date = row[0]
            end_date = row[1]
            total_days = (end_date - start_date).days + 1
            logger.info(
                f"🔄 Batch ad resync: project {project_id}, "
                f"range {start_date} → {end_date} ({total_days} days)"
            )

            # 3. Get active campaign IDs
            campaign_ids = await fetch_ad_campaigns(adv_key)
            if not campaign_ids:
                return {"status": "error", "days_updated": 0,
                        "total_updated": 0, "errors": ["No campaigns found"]}

            logger.info(
                f"🔄 Batch ad resync: {len(campaign_ids)} campaigns"
            )

            # 4. Split into 30-day windows (WB API max 31 days)
            WINDOW = 30
            window_start = start_date
            window_num = 0

            while window_start <= end_date:
                window_end = min(window_start + td(days=WINDOW - 1), end_date)
                window_num += 1
                w_from = window_start.isoformat()
                w_to = window_end.isoformat()

                logger.info(
                    f"🔄 Batch ad resync: window {window_num} — "
                    f"{w_from} → {w_to}"
                )

                # Fetch ad stats for this window
                ad_stats = await fetch_ad_stats(
                    adv_key, campaign_ids, w_from, w_to
                )

                if ad_stats:
                    # Update ad columns in DB
                    from sqlalchemy import update as sa_update
                    for date_str, nm_data in ad_stats.items():
                        day_count = 0
                        for nm_id, ad in nm_data.items():
                            res = await db.execute(
                                sa_update(WbFunnelDaily).where(
                                    WbFunnelDaily.project_id == project_id,
                                    WbFunnelDaily.date == date.fromisoformat(date_str),
                                    WbFunnelDaily.nm_id == nm_id,
                                ).values(
                                    adv_sum=ad["sum"],
                                    adv_views=ad["views"],
                                    adv_clicks=ad["clicks"],
                                )
                            )
                            day_count += res.rowcount

                        await db.commit()
                        total_updated += day_count
                        days_updated += 1

                    logger.info(
                        f"🔄 Window {window_num} done: "
                        f"{len(ad_stats)} days updated"
                    )
                else:
                    errors.append(f"No data for window {w_from}→{w_to}")
                    logger.warning(
                        f"🔄 Window {window_num}: no ad data returned"
                    )

                window_start = window_end + td(days=1)

                # Delay between windows to avoid rate limiting
                if window_start <= end_date:
                    await asyncio.sleep(10)

            logger.info(
                f"✅ Batch ad resync complete: project {project_id} — "
                f"{days_updated} days, {total_updated} rows, "
                f"{window_num} windows"
            )
            return {
                "status": "ok",
                "days_updated": days_updated,
                "total_updated": total_updated,
                "windows": window_num,
                "errors": errors,
            }

    except Exception as e:
        import traceback
        logger.error(f"Batch ad resync error: {e}\n{traceback.format_exc()}")
        return {"status": "error", "days_updated": 0,
                "total_updated": 0, "errors": [str(e)[:500]]}
