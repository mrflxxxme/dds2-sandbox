"""Funnel sync — background backfill and batch ad re-sync.

Extracted from sync.py for maintainability.
Contains:
- run_backfill_bg: background coroutine for missing days
- batch_resync_ads: batch re-sync ALL ad data for a project
"""

import asyncio
import logging
from datetime import date

from sqlalchemy import func, select, update as sa_update

from backend.models import WbFunnelDaily
from backend.services.funnel.wb_api_client import (
    fetch_ad_campaigns,
    fetch_ad_stats,
    get_wb_key,
)

logger = logging.getLogger("dds.funnel")


async def run_backfill_bg(project_id: int, missing_dates: list[str]) -> None:
    """Background coroutine: sync missing days with concurrent semaphore."""
    from backend.database import AsyncSessionLocal
    from backend.services.funnel.sync import run_funnel_sync

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
                        timeout=600,
                    )
                    return {"day": day_str, "rows": result.get("rows", 0), "errors": result.get("errors", [])}
            except TimeoutError:
                return {"day": day_str, "rows": 0, "errors": [f"Timeout 5min: {day_str}"]}
            except Exception as e:
                return {"day": day_str, "rows": 0, "errors": [str(e)[:200]]}

    batch_size = 5
    for i in range(0, len(missing_dates), batch_size):
        batch = missing_dates[i : i + batch_size]
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
                logger.info(f"Backfill bg: project {project_id} — " f"day {r['day']}, +{r.get('rows', 0)} rows")

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
    from datetime import timedelta as td

    from backend.database import AsyncSessionLocal

    try:
        from backend.scheduler import get_scheduler_instance

        sched = get_scheduler_instance()
        if sched:
            try:
                sched.pause_job("ad_anomaly_check")
                logger.info("🔄 Paused ad_anomaly_check scheduler job")
            except Exception:  # noqa: S110
                pass
    except Exception:
        sched = None

    errors = []
    total_updated = 0
    days_updated = 0

    try:
        async with AsyncSessionLocal() as db:
            adv_key = await get_wb_key(db, project_id, "wb")
            if not adv_key:
                return {"status": "error", "days_updated": 0, "total_updated": 0, "errors": ["API key not found"]}

            result = await db.execute(
                select(
                    func.min(WbFunnelDaily.date),
                    func.max(WbFunnelDaily.date),
                ).where(WbFunnelDaily.project_id == project_id)
            )
            row = result.one_or_none()
            if not row or not row[0]:
                return {"status": "error", "days_updated": 0, "total_updated": 0, "errors": ["No existing data"]}

            start_date = row[0]
            end_date = row[1]
            total_days = (end_date - start_date).days + 1
            logger.info(
                f"🔄 Batch ad resync: project {project_id}, " f"range {start_date} → {end_date} ({total_days} days)"
            )

            campaign_ids = await fetch_ad_campaigns(adv_key)
            if not campaign_ids:
                return {"status": "error", "days_updated": 0, "total_updated": 0, "errors": ["No campaigns found"]}

            logger.info(f"🔄 Batch ad resync: {len(campaign_ids)} campaigns")

            WINDOW = 30
            window_start = start_date
            window_num = 0

            while window_start <= end_date:
                window_end = min(window_start + td(days=WINDOW - 1), end_date)
                window_num += 1
                w_from = window_start.isoformat()
                w_to = window_end.isoformat()

                logger.info(f"🔄 Batch ad resync: window {window_num} — " f"{w_from} → {w_to}")

                ad_stats = await fetch_ad_stats(adv_key, campaign_ids, w_from, w_to)

                if ad_stats:
                    for date_str, nm_data in ad_stats.items():
                        day_count = 0
                        for nm_id, ad in nm_data.items():
                            res = await db.execute(
                                sa_update(WbFunnelDaily)
                                .where(
                                    WbFunnelDaily.project_id == project_id,
                                    WbFunnelDaily.date == date.fromisoformat(date_str),
                                    WbFunnelDaily.nm_id == nm_id,
                                )
                                .values(
                                    adv_sum=ad["sum"],
                                    adv_views=ad["views"],
                                    adv_clicks=ad["clicks"],
                                )
                            )
                            day_count += res.rowcount

                        await db.commit()
                        total_updated += day_count
                        days_updated += 1

                    logger.info(f"🔄 Window {window_num} done: " f"{len(ad_stats)} days updated")
                else:
                    errors.append(f"No data for window {w_from}→{w_to}")
                    logger.warning(f"🔄 Window {window_num}: no ad data returned")

                window_start = window_end + td(days=1)

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
        return {"status": "error", "days_updated": 0, "total_updated": 0, "errors": [str(e)[:500]]}
