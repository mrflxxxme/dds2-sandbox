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
from datetime import date, datetime, timedelta, timezone

from backend.utils.time import utcnow

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


def _split_into_windows(dates: list[str], max_window: int = 30) -> list[tuple[str, str]]:
    """
    Split a list of date strings into windows of max `max_window` days.
    Dates don't have to be contiguous — the function groups them by proximity.
    Returns list of (from_date, to_date) tuples.
    """
    if not dates:
        return []

    windows = []
    i = 0
    while i < len(dates):
        w_start = date.fromisoformat(dates[i])
        # Find the last date that fits in this window
        j = i
        while j < len(dates):
            d = date.fromisoformat(dates[j])
            if (d - w_start).days >= max_window:
                break
            j += 1
        windows.append((dates[i], dates[j - 1]))
        i = j

    return windows


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
    Find dates that have funnel data but ZERO ad data.
    Only flags days where adv_sum == 0 — if any ad data exists, the day
    is considered complete (WB ad data for past days doesn't change).
    Returns list of date strings, oldest first. Max 30 per call.
    """
    from sqlalchemy import select, func
    today = date.today()
    start = today - timedelta(days=lookback_days)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(
                WbFunnelDaily.date,
                func.sum(WbFunnelDaily.adv_sum).label("total_adv"),
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

    # Only flag days with truly zero ad spend
    zero_days = [r.date.isoformat() for r in rows if float(r.total_adv or 0) == 0]

    if zero_days:
        logger.info(
            f"Ad completeness check: project {project_id}, "
            f"zero ad days: {len(zero_days)}"
        )

    return zero_days[:30]



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
    from backend.services.funnel.sync import run_funnel_sync
    from sqlalchemy import select, update

    import asyncio

    # ad_resync needs completed campaigns to recover historical data
    include_completed = sync_type == "ad_resync"

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
                run_funnel_sync(
                    db, project_id, d_from, d_to,
                    include_completed_campaigns=include_completed,
                ),
                timeout=600,  # 10 min — gives ad stats budget (300s) + headroom
            )
            status = "OK" if not result.get("errors") else "PARTIAL"
    except asyncio.TimeoutError:
        result = {"rows": 0, "errors": [f"Timeout: 10min exceeded ({d_from}→{d_to})"]}
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
                        finished_at=utcnow(),
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

_backfill_locks: dict[int, asyncio.Lock] = {}  # per-project locks

async def fast_backfill_tick():
    """
    Fill missing days ONLY. Runs every 3 min.
    Batches all missing days into one range request (min→max date).
    Stops itself when all projects have full 90-day coverage.
    Does NOT check ad completeness — that's a separate job.
    """
    try:
        project_ids = await _get_sync_project_ids()
        logger.info(f"⏩ Fast backfill tick: {len(project_ids)} projects")

        if not project_ids:
            _stop_fast_backfill()
            return

        all_filled = True
        for pid in project_ids:
            # Per-project lock: skip if this project is already syncing
            if pid not in _backfill_locks:
                _backfill_locks[pid] = asyncio.Lock()
            if _backfill_locks[pid].locked():
                logger.info(f"⏩ Backfill: project {pid} — still running, skipping")
                all_filled = False
                continue

            async with _backfill_locks[pid]:
                missing = await _get_missing_dates(pid)
                if missing:
                    all_filled = False
                    d_from = missing[0]
                    d_to = missing[-1]
                    logger.info(
                        f"⏩ Backfill: project {pid} — syncing range "
                        f"{d_from}→{d_to} ({len(missing)} days remaining)"
                    )
                    res = await _run_and_log(pid, d_from, d_to, "backfill")
                    if res:
                        logger.info(
                            f"⏩ Backfill: project {pid} — {d_from}→{d_to} done, "
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
    Splits incomplete days into 30-day windows (WB API max 31 days).
    Runs every 3 min. Auto-stops when all ads are complete.
    """
    if _ad_check_lock.locked():
        return

    async with _ad_check_lock:
        try:
            project_ids = await _get_sync_project_ids()
            logger.info(f"📊 Ad anomaly check: {len(project_ids)} projects")

            all_ads_ok = True
            for pid in project_ids:
                incomplete_days = await _get_days_with_incomplete_ads(pid)
                if incomplete_days:
                    all_ads_ok = False
                    # Split into 30-day windows (WB API max 31 days per request)
                    windows = _split_into_windows(incomplete_days, max_window=30)
                    logger.info(
                        f"📊 Ad anomaly: project {pid} — "
                        f"{len(incomplete_days)} incomplete days, "
                        f"{len(windows)} windows"
                    )
                    for w_from, w_to in windows:
                        logger.info(
                            f"📊 Ad anomaly: project {pid} — "
                            f"re-syncing window {w_from}→{w_to}"
                        )
                        res = await _run_and_log(pid, w_from, w_to, "ad_resync")
                        if res:
                            logger.info(
                                f"📊 Ad anomaly: project {pid} — "
                                f"{w_from}→{w_to} re-synced, "
                                f"+{res.get('rows', 0)} rows"
                            )
                        await asyncio.sleep(5)
                else:
                    logger.info(f"📊 Ad anomaly: project {pid} — all ads complete ✅")

            if all_ads_ok:
                logger.info("🎉 Ad anomaly check complete — all projects have full ad data! Stopping.")
                _stop_ad_anomaly_check()

        except Exception as e:
            logger.error(f"Ad anomaly check error: {e}\n{traceback.format_exc()}")


def _stop_ad_anomaly_check():
    """Remove the ad anomaly check job from scheduler."""
    global scheduler
    if scheduler:
        try:
            scheduler.remove_job("ad_anomaly_check")
            logger.info("Ad anomaly check job removed from scheduler")
        except Exception:
            pass


def restart_backfill_jobs():
    """Restart backfill + ad anomaly jobs (call when new WB API key is added)."""
    global scheduler
    try:
        if not scheduler or not scheduler.running:
            logger.warning("Scheduler not running, cannot restart jobs")
            return

        # Re-add fast_backfill if not present
        if not scheduler.get_job("fast_backfill"):
            from apscheduler.triggers.interval import IntervalTrigger
            scheduler.add_job(
                fast_backfill_tick,
                IntervalTrigger(minutes=3),
                id="fast_backfill",
                max_instances=1, replace_existing=True,
            )
            logger.info("🔄 Restarted fast_backfill job (new API key detected)")

        # Re-add ad_anomaly_check if not present
        if not scheduler.get_job("ad_anomaly_check"):
            from apscheduler.triggers.interval import IntervalTrigger
            scheduler.add_job(
                ad_anomaly_check,
                IntervalTrigger(minutes=3),
                id="ad_anomaly_check",
                max_instances=1, replace_existing=True,
            )
            logger.info("🔄 Restarted ad_anomaly_check job (new API key detected)")
    except Exception as e:
        logger.error(f"Failed to restart backfill jobs: {e}")


# ─── WB Finance Report sync (with retry) ─────────────────────────────────────

async def sync_all_projects_wb_finance():
    """Smart sync: check max date in DB and fill gap to today.

    Runs multiple times on Mon/Tue. Skips if data is already fresh.
    - No data → initial sync (last 60 days)
    - Has data but stale → sync from max_date+1 to today
    - Data fresh → skip
    """
    logger.info("💰 WB Finance sync: starting for all projects")
    project_ids = await _get_sync_project_ids()

    if not project_ids:
        logger.info("WB Finance sync: no projects with WB keys, skipping")
        return

    from backend.services.wb_finance_sync import sync_wb_finance
    from sqlalchemy import select, func as sa_func
    from backend.models.wb_finance import WbFinanceRow

    today = date.today()

    for pid in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                # Check what data we already have
                result = await db.execute(
                    select(sa_func.max(WbFinanceRow.rr_dt))
                    .where(WbFinanceRow.project_id == pid)
                )
                max_date = result.scalar()

                if max_date is None:
                    # No data at all → initial sync (last 60 days)
                    date_from = today - timedelta(days=60)
                    logger.info(
                        f"💰 WB Finance: project {pid} — initial sync "
                        f"{date_from} → {today}"
                    )
                else:
                    # Data exists — check freshness
                    # Skip if data is less than 5 days old (WB needs time)
                    days_behind = (today - max_date).days
                    if days_behind < 5:
                        logger.info(
                            f"💰 WB Finance: project {pid} — fresh "
                            f"(max_date={max_date}, {days_behind}d behind), skip"
                        )
                        continue

                    # Sync from day after last known date
                    date_from = max_date + timedelta(days=1)
                    logger.info(
                        f"💰 WB Finance: project {pid} — gap-fill "
                        f"{date_from} → {today}"
                    )

                result = await sync_wb_finance(db, pid, date_from, today)
                rows = result.get("rows_synced", 0)
                status = result.get("status", "?")
                logger.info(
                    f"💰 WB Finance: project {pid} — {status}, "
                    f"{rows} rows synced"
                )

        except Exception as e:
            logger.error(f"💰 WB Finance sync failed for project {pid}: {e}")

    logger.info("💰 WB Finance sync: completed for all projects")


# ─── Report cache prewarm ────────────────────────────────────────────────────

async def prewarm_project(project_id: int):
    """Pre-compute OPIU and BDR for a single project (current + previous month).

    Called by scheduler (periodic) and after WB sync / cost mutations.
    Fail-safe: errors are logged but never propagate.
    """
    from datetime import timedelta
    today = date.today()
    # Current month
    month_start = today.replace(day=1)
    # Previous month
    if month_start.month == 1:
        prev_start = month_start.replace(year=month_start.year - 1, month=12)
    else:
        prev_start = month_start.replace(month=month_start.month - 1)

    d_from = prev_start
    d_to = today

    try:
        async with AsyncSessionLocal() as db:
            from backend.services import opiu_service, wb_bdr_service
            await opiu_service.get_opiu(db, project_id, d_from, d_to)
            await wb_bdr_service.get_wb_bdr(db, project_id, d_from, d_to)
        logger.info(f"🔥 Prewarm: project {project_id} — OPIU + BDR cached ({d_from}→{d_to})")
    except Exception as e:
        logger.warning(f"🔥 Prewarm: project {project_id} failed: {e}")


async def prewarm_all_reports():
    """Pre-compute OPIU/BDR for ALL active projects.

    Runs hourly via scheduler + once at startup.
    """
    logger.info("🔥 Prewarm: starting for all projects")
    project_ids = await _get_sync_project_ids()

    if not project_ids:
        logger.info("🔥 Prewarm: no projects with WB keys, skipping")
        return

    for pid in project_ids:
        await prewarm_project(pid)
        await asyncio.sleep(1)  # Small delay between projects

    logger.info(f"🔥 Prewarm: completed for {len(project_ids)} projects")


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

    # Ad anomaly check: every 3 min — re-sync incomplete ad days, auto-stops
    scheduler.add_job(
        ad_anomaly_check,
        trigger=IntervalTrigger(seconds=180),
        id="ad_anomaly_check",
        name="Ad anomaly check (every 3min, auto-stop)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # WB finance report sync: Mon 03/06/09/10/11/12 — retry until data arrives
    for job_hour in [3, 6, 9, 10, 11, 12]:
        scheduler.add_job(
            sync_all_projects_wb_finance,
            trigger=CronTrigger(
                day_of_week="mon", hour=job_hour, minute=0, timezone=MSK,
            ),
            id=f"wb_finance_sync_mon_{job_hour:02d}",
            name=f"WB finance sync (MON {job_hour:02d}:00)",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    # Report cache prewarm: every hour — keeps OPIU/BDR always hot
    scheduler.add_job(
        prewarm_all_reports,
        trigger=IntervalTrigger(hours=1),
        id="prewarm_reports",
        name="Report cache prewarm (every 1h)",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Initial prewarm: 30s after startup — warm cache on deploy/restart
    from apscheduler.triggers.date import DateTrigger
    scheduler.add_job(
        prewarm_all_reports,
        trigger=DateTrigger(run_date=datetime.now(MSK) + timedelta(seconds=30)),
        id="prewarm_startup",
        name="Report cache prewarm (startup)",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "✅ Scheduler started — daily sync 3x/day + backfill + ad check + wb_finance Mon-Tue retry + prewarm 1h"
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

