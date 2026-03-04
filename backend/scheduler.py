"""
Background scheduler for periodic WB funnel sync.

Schedule:
- Daily sync: 00:01, 03:00, 05:00 MSK — syncs today + yesterday.
- Fast backfill: every 3 min — fills missing days in 90-day window, auto-stops when done.
- Ad anomaly check: every 6 hours — re-syncs days with incomplete advertising data.

Uses APScheduler (in-process, AsyncIOScheduler).
"""

import asyncio
import logging
import traceback
from datetime import date, timedelta

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.database import AsyncSessionLocal
from backend.models import Project, IntegrationKey, WbFunnelDaily, SyncLog

logger = logging.getLogger("dds.scheduler")

scheduler: AsyncIOScheduler | None = None

MSK = pytz.timezone("Europe/Moscow")

BACKFILL_DAYS = 90       # how far back to check for missing data
MISSING_BATCH_SIZE = 10  # max missing days to sync per scheduler run
MAX_DATE_FAILURES = 3    # skip date after this many TIMEOUT/ERROR in sync_log


# ─── Smart missing-day detection ─────────────────────────────────────────────

async def _get_failed_dates(project_id: int) -> set[str]:
    """
    Find dates that have failed (TIMEOUT/ERROR) >= MAX_DATE_FAILURES times.
    These "poisoned" dates will be skipped to avoid infinite retry loops.
    """
    from sqlalchemy import select, func, literal_column
    async with AsyncSessionLocal() as db:
        # Count failures per date range in sync_log for backfill sync types
        result = await db.execute(
            select(SyncLog.sync_type, SyncLog.error_msg, SyncLog.status)
            .where(
                SyncLog.service == "wb_funnel",
                SyncLog.sync_type.in_(["backfill", "ad_resync"]),
                SyncLog.status.in_(["TIMEOUT", "ERROR"]),
            )
        )
        # Extract dates from error_msg patterns like "Timeout: 5min exceeded (YYYY-MM-DD→YYYY-MM-DD)"
        # and count per date
        from collections import Counter
        import re
        date_failures: Counter = Counter()
        for row in result:
            msg = row.error_msg or ""
            # Try to extract dates from error messages
            dates_found = re.findall(r"\d{4}-\d{2}-\d{2}", msg)
            for d in dates_found:
                date_failures[d] += 1

        return {d for d, count in date_failures.items() if count >= MAX_DATE_FAILURES}


async def _get_missing_dates(project_id: int, lookback_days: int = BACKFILL_DAYS) -> list[str]:
    """
    Find dates in the last `lookback_days` that have NO funnel data for this project.
    Skips "poisoned" dates that have failed >= MAX_DATE_FAILURES times.
    Returns list of date strings (YYYY-MM-DD), oldest first.
    """
    from sqlalchemy import select, func
    today = date.today()
    start = today - timedelta(days=lookback_days)

    # Get all dates that have data
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(WbFunnelDaily.date).where(
                WbFunnelDaily.project_id == project_id,
                WbFunnelDaily.date >= start,
            ).distinct()
        )
        existing_dates = {r[0] for r in result}

    # Get dates to skip (too many failures)
    poisoned = await _get_failed_dates(project_id)
    if poisoned:
        logger.info(f"Skipping {len(poisoned)} poisoned dates: {sorted(poisoned)[:5]}")

    # Build set of all expected dates (exclude today — it updates throughout the day)
    all_dates = set()
    d = start
    while d < today:  # < today, not <=, because today is always re-synced
        all_dates.add(d)
        d += timedelta(days=1)

    missing = sorted(all_dates - existing_dates)
    # Filter out poisoned dates
    missing = [d.isoformat() for d in missing if d.isoformat() not in poisoned]
    return missing


async def _get_days_with_incomplete_ads(project_id: int, lookback_days: int = BACKFILL_DAYS) -> list[str]:
    """
    Find dates that have funnel data but INCOMPLETE ad data.
    Uses the median ad total across recent days as a baseline —
    any day with < 50% of median is flagged for re-sync.
    Also flags days with zero ad data.
    Returns list of date strings, oldest first. Max 5 per call.
    """
    from sqlalchemy import select, func
    today = date.today()
    start = today - timedelta(days=lookback_days)

    async with AsyncSessionLocal() as db:
        # Get ad totals per day
        result = await db.execute(
            select(
                WbFunnelDaily.date,
                func.sum(WbFunnelDaily.adv_sum).label("total_adv"),
                func.count(func.nullif(WbFunnelDaily.adv_sum, 0)).label("items_with_ads"),
            ).where(
                WbFunnelDaily.project_id == project_id,
                WbFunnelDaily.date >= start,
                WbFunnelDaily.date < today,
            ).group_by(WbFunnelDaily.date)
            .order_by(WbFunnelDaily.date)
        )
        rows = list(result)

    if not rows:
        return []

    # Calculate median ad total across all days
    ad_totals = sorted([float(r.total_adv or 0) for r in rows])
    mid = len(ad_totals) // 2
    if len(ad_totals) % 2 == 0:
        median_adv = (ad_totals[mid - 1] + ad_totals[mid]) / 2
    else:
        median_adv = ad_totals[mid]

    if median_adv <= 0:
        # No baseline — fall back to just finding zero days
        return [r.date.isoformat() for r in rows if float(r.total_adv or 0) == 0][:5]

    # Flag days with < 50% of median ad total
    threshold = median_adv * 0.5
    incomplete = []
    for r in rows:
        day_adv = float(r.total_adv or 0)
        if day_adv < threshold:
            incomplete.append(r.date.isoformat())

    if incomplete:
        logger.info(
            f"Ad completeness check: project {project_id}, "
            f"median={median_adv:.0f}, threshold={threshold:.0f}, "
            f"incomplete days: {len(incomplete)}"
        )

    return incomplete[:5]



async def _get_sync_project_ids() -> list[int]:
    """
    Get project IDs that should be synced.
    If a global WB key exists (project_id IS NULL), return ALL projects.
    Otherwise, return only projects with their own WB keys.
    """
    from sqlalchemy import select, or_
    async with AsyncSessionLocal() as db:
        # Check if there's a global (project_id=NULL) WB key
        global_key = await db.execute(
            select(IntegrationKey.id).where(
                IntegrationKey.service.in_(["wb", "wb_analytics"]),
                IntegrationKey.is_active == True,
                IntegrationKey.project_id.is_(None),
            ).limit(1)
        )
        has_global = global_key.scalar() is not None

        if has_global:
            # Global key: sync ALL projects
            result = await db.execute(
                select(Project.id)
            )
            return [r[0] for r in result if r[0]]
        else:
            # Only projects with their own keys
            result = await db.execute(
                select(IntegrationKey.project_id).where(
                    IntegrationKey.service.in_(["wb", "wb_analytics"]),
                    IntegrationKey.is_active == True,
                    IntegrationKey.project_id.isnot(None),
                ).distinct()
            )
            return [r[0] for r in result if r[0]]


# ─── Main sync task ──────────────────────────────────────────────────────────

async def sync_all_projects_funnel():
    """
    Daily sync: today + yesterday for all projects.
    Runs at 00:01, 03:00, 05:00 MSK.
    """
    logger.info("⏰ Scheduler: starting daily funnel sync for all projects")

    project_ids = await _get_sync_project_ids()

    if not project_ids:
        logger.info("Scheduler: no projects with WB keys found, skipping")
        return

    for pid in project_ids:
        try:
            d_from = (date.today() - timedelta(days=1)).isoformat()
            d_to = date.today().isoformat()
            logger.info(f"Scheduler: project {pid} — sync today+yesterday {d_from} → {d_to}")
            await _run_and_log(pid, d_from, d_to, "funnel_auto")
        except Exception as e:
            logger.error(f"Scheduler: project {pid} sync failed: {e}")


async def _run_and_log(project_id: int, d_from: str, d_to: str, sync_type: str):
    """Run funnel sync and log result to sync_log table.

    GUARANTEED: sync_log status will be updated to OK/PARTIAL/TIMEOUT/ERROR,
    never left as RUNNING.
    """
    from backend.services.funnel_service import run_funnel_sync
    from sqlalchemy import select, update
    from datetime import datetime
    import asyncio

    log_id = None

    # Step 1: Create sync_log entry (RUNNING)
    async with AsyncSessionLocal() as db:
        int_key = await db.execute(
            select(IntegrationKey.id).where(
                IntegrationKey.project_id == project_id,
                IntegrationKey.service.in_(["wb", "wb_analytics"]),
                IntegrationKey.is_active == True,
            ).limit(1)
        )
        key_id = int_key.scalar() or None

        sync_log = SyncLog(
            integration_id=key_id,
            service="wb_funnel",
            sync_type=sync_type,
            status="RUNNING",
        )
        db.add(sync_log)
        await db.commit()
        await db.refresh(sync_log)
        log_id = sync_log.id

    # Step 2: Run sync with timeout and guaranteed status update
    result = {"rows": 0, "errors": []}
    status = "ERROR"

    try:
        async with AsyncSessionLocal() as db:
            result = await asyncio.wait_for(
                run_funnel_sync(db, project_id, d_from, d_to),
                timeout=600,  # 10 min — gives ad stats budget (180s) + headroom
            )
            status = "OK" if not result.get("errors") else "PARTIAL"
    except asyncio.TimeoutError:
        result = {"rows": 0, "errors": [f"Timeout: 5min exceeded ({d_from}→{d_to})"]}
        status = "TIMEOUT"
        logger.error(f"Scheduler: project {project_id} [{sync_type}] TIMEOUT {d_from}→{d_to}")
    except Exception as e:
        result = {"rows": 0, "errors": [str(e)[:500]]}
        status = "ERROR"
        logger.error(f"Scheduler: project {project_id} [{sync_type}] ERROR: {e}")
    finally:
        # ALWAYS update sync_log — never leave as RUNNING
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(SyncLog).where(SyncLog.id == log_id).values(
                        status=status,
                        rows_inserted=result.get("rows", 0),
                        finished_at=datetime.utcnow(),
                        error_msg="; ".join(result.get("errors", [])[:3]) or None,
                    )
                )
                await db.commit()
        except Exception as log_err:
            logger.error(f"Failed to update sync_log {log_id}: {log_err}")

    logger.info(
        f"Scheduler: project {project_id} [{sync_type}] {d_from}→{d_to} — "
        f"{result.get('rows', 0)} rows, status: {status}"
    )
    return result


# ─── Fast backfill (every 3min until all missing days filled) ────────────────

_backfill_lock = asyncio.Lock()

async def fast_backfill_tick():
    """
    Fill missing days ONLY. Runs every 3 min.
    Stops itself when all projects have full 90-day coverage.
    Does NOT check ad completeness — that's a separate job.
    """
    if _backfill_lock.locked():
        return  # previous tick still running

    async with _backfill_lock:
        try:
            project_ids = await _get_sync_project_ids()
            logger.info(f"⏩ Fast backfill tick: {len(project_ids)} projects")

            if not project_ids:
                _stop_fast_backfill()
                return

            all_filled = True
            for pid in project_ids:
                missing = await _get_missing_dates(pid)
                if missing:
                    all_filled = False
                    day = missing[0]
                    logger.info(
                        f"⏩ Backfill: project {pid} — syncing {day} "
                        f"({len(missing)} days remaining)"
                    )
                    res = await _run_and_log(pid, day, day, "backfill")
                    if res:
                        logger.info(
                            f"⏩ Backfill: project {pid} — {day} done, "
                            f"+{res.get('rows', 0)} rows"
                        )
                    await asyncio.sleep(1)
                else:
                    logger.info(f"⏩ Backfill: project {pid} — all {BACKFILL_DAYS} days covered ✅")

            if all_filled:
                logger.info("🎉 Fast backfill complete — all projects fully covered! Stopping.")
                _stop_fast_backfill()

        except Exception as e:
            logger.error(f"Fast backfill error: {e}\n{traceback.format_exc()}")


def _stop_fast_backfill():
    """Remove the fast backfill job from scheduler."""
    global scheduler
    if scheduler:
        try:
            scheduler.remove_job("fast_backfill")
            logger.info("Fast backfill job removed from scheduler")
        except Exception:
            pass


# ─── Ad anomaly check (every 6h — re-sync days with incomplete ads) ──────────

_ad_check_lock = asyncio.Lock()

async def ad_anomaly_check():
    """
    Check for days with incomplete ad data and re-sync them.
    Runs every 6 hours. Uses median-based detection.
    """
    if _ad_check_lock.locked():
        return

    async with _ad_check_lock:
        try:
            project_ids = await _get_sync_project_ids()
            logger.info(f"📊 Ad anomaly check: {len(project_ids)} projects")

            for pid in project_ids:
                incomplete_days = await _get_days_with_incomplete_ads(pid)
                if incomplete_days:
                    day = incomplete_days[0]  # one day per project per check
                    logger.info(
                        f"📊 Ad anomaly: project {pid} — re-syncing {day} "
                        f"({len(incomplete_days)} incomplete days total)"
                    )
                    res = await _run_and_log(pid, day, day, "ad_resync")
                    if res:
                        logger.info(
                            f"📊 Ad anomaly: project {pid} — {day} re-synced, "
                            f"+{res.get('rows', 0)} rows"
                        )
                    await asyncio.sleep(3)
                else:
                    logger.info(f"📊 Ad anomaly: project {pid} — all ads complete ✅")

        except Exception as e:
            logger.error(f"Ad anomaly check error: {e}\n{traceback.format_exc()}")


# ─── Scheduler lifecycle ─────────────────────────────────────────────────────

def start_scheduler():
    """Start the background scheduler with cron jobs + fast backfill.

    Uses a file lock to prevent duplicate schedulers when running with
    multiple uvicorn workers (--workers 2).
    Disabled when SCHEDULER_ENABLED=false (for local dev when server is syncing).
    """
    from backend.config import settings
    if not settings.SCHEDULER_ENABLED:
        logger.info("⏭️ Scheduler disabled (SCHEDULER_ENABLED=false)")
        return

    global scheduler

    # Guard: only ONE worker should run the scheduler
    import fcntl
    lock_file = "/tmp/.dds_scheduler.lock"
    try:
        _lock_fd = open(lock_file, "w")
        fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Keep the fd alive so the lock is held for the process lifetime
        start_scheduler._lock_fd = _lock_fd
    except (IOError, OSError):
        logger.info("⏭️ Scheduler already running in another worker, skipping")
        return

    scheduler = AsyncIOScheduler(timezone=MSK)

    # Daily sync: 00:01, 03:00, 05:00 MSK — today + yesterday
    for hour, minute in [(0, 1), (3, 0), (5, 0)]:
        scheduler.add_job(
            sync_all_projects_funnel,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=MSK),
            id=f"funnel_sync_{hour:02d}{minute:02d}",
            name=f"WB daily sync at {hour:02d}:{minute:02d} MSK",
            replace_existing=True,
            misfire_grace_time=600,
        )

    # Fast backfill: every 3 min — missing days only, auto-stops when done
    from apscheduler.triggers.interval import IntervalTrigger
    scheduler.add_job(
        fast_backfill_tick,
        trigger=IntervalTrigger(seconds=180),
        id="fast_backfill",
        name="Fast backfill (every 3min, auto-stop)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Ad anomaly check: every 6 hours — re-sync incomplete ad days
    scheduler.add_job(
        ad_anomaly_check,
        trigger=IntervalTrigger(hours=6),
        id="ad_anomaly_check",
        name="Ad anomaly check (every 6h)",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.start()
    logger.info(
        "✅ Scheduler started — daily sync 3x/day + backfill (auto-stop) + ad check every 6h"
    )


def stop_scheduler():
    """Stop the background scheduler."""
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
        scheduler = None


def get_scheduler_info() -> dict:
    """Get scheduler status and next run times."""
    if not scheduler:
        return {"running": False, "jobs": []}

    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })

    return {"running": scheduler.running, "jobs": jobs}

