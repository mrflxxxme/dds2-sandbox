"""
Background scheduler for periodic WB funnel sync.

Schedule: 00:01, 07:00, 13:00, 19:00 MSK daily.
- Finds missing days in the last 90 days and syncs them (up to 10 days per run).
- Always syncs today + yesterday.

Uses APScheduler (in-process, AsyncIOScheduler).
"""

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


# ─── Smart missing-day detection ─────────────────────────────────────────────

async def _get_missing_dates(project_id: int, lookback_days: int = BACKFILL_DAYS) -> list[str]:
    """
    Find dates in the last `lookback_days` that have NO funnel data for this project.
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

    # Build set of all expected dates (exclude today — it updates throughout the day)
    all_dates = set()
    d = start
    while d < today:  # < today, not <=, because today is always re-synced
        all_dates.add(d)
        d += timedelta(days=1)

    missing = sorted(all_dates - existing_dates)
    return [d.isoformat() for d in missing]


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
    Iterate over all projects with WB API keys and sync funnel data.
    - Always sync today + yesterday (data updates throughout the day).
    - Also fill in missing days (up to MISSING_BATCH_SIZE per run).
    """
    import asyncio
    from sqlalchemy import select
    from backend.services.funnel_service import run_funnel_sync

    logger.info("⏰ Scheduler: starting funnel sync for all projects")

    project_ids = await _get_sync_project_ids()

    if not project_ids:
        logger.info("Scheduler: no projects with WB keys found, skipping")
        return

    for pid in project_ids:
        try:
            # 1. Always sync today + yesterday
            d_from = (date.today() - timedelta(days=1)).isoformat()
            d_to = date.today().isoformat()
            sync_type = "funnel_auto"
            logger.info(f"Scheduler: project {pid} — sync today+yesterday {d_from} → {d_to}")

            await _run_and_log(pid, d_from, d_to, sync_type)

            # 2. Fill missing days (batch)
            missing = await _get_missing_dates(pid)
            if missing:
                batch = missing[:MISSING_BATCH_SIZE]
                logger.info(
                    f"Scheduler: project {pid} — {len(missing)} missing days total, "
                    f"syncing batch of {len(batch)}: {batch[0]} → {batch[-1]}"
                )
                # Sync missing days one-by-one with delay to avoid WB rate limits
                for day_str in batch:
                    await asyncio.sleep(2)  # rate limit protection
                    await _run_and_log(pid, day_str, day_str, "funnel_backfill")

                remaining = len(missing) - len(batch)
                if remaining > 0:
                    logger.info(
                        f"Scheduler: project {pid} — {remaining} missing days remaining, "
                        f"will continue on next run"
                    )
            else:
                logger.info(f"Scheduler: project {pid} — no missing days, all {BACKFILL_DAYS} days covered ✅")

        except Exception as e:
            logger.error(f"Scheduler: project {pid} sync failed: {e}")


async def _run_and_log(project_id: int, d_from: str, d_to: str, sync_type: str):
    """Run funnel sync and log result to sync_log table."""
    from backend.services.funnel_service import run_funnel_sync
    from sqlalchemy import select
    from datetime import datetime

    async with AsyncSessionLocal() as db:
        # Find integration key for logging
        int_key = await db.execute(
            select(IntegrationKey.id).where(
                IntegrationKey.project_id == project_id,
                IntegrationKey.service.in_(["wb", "wb_analytics"]),
                IntegrationKey.is_active == True,
            ).limit(1)
        )
        key_id = int_key.scalar() or None

        # Create sync log entry
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

    # Run the actual sync
    async with AsyncSessionLocal() as db:
        result = await run_funnel_sync(db, project_id, d_from, d_to)

    # Update sync log with result
    async with AsyncSessionLocal() as db:
        from sqlalchemy import update
        await db.execute(
            update(SyncLog).where(SyncLog.id == log_id).values(
                status="OK" if not result.get("errors") else "PARTIAL",
                rows_inserted=result.get("rows", 0),
                finished_at=datetime.utcnow(),
                error_msg="; ".join(result.get("errors", [])[:3]) or None,
            )
        )
        await db.commit()

    logger.info(
        f"Scheduler: project {project_id} [{sync_type}] {d_from}→{d_to} — "
        f"{result.get('rows', 0)} rows, errors: {len(result.get('errors', []))}"
    )
    return result


# ─── Fast backfill (every 30s until all days filled) ─────────────────────────

_backfill_lock = False  # simple lock to prevent overlapping runs

async def fast_backfill_tick():
    """
    Scheduler tick: sync ONE missing day for EACH project.
    Runs every 30 seconds. Removes itself when all projects are fully covered.
    """
    global _backfill_lock
    if _backfill_lock:
        return  # previous tick still running
    _backfill_lock = True

    try:
        import asyncio
        from backend.services.funnel_service import run_funnel_sync

        project_ids = await _get_sync_project_ids()

        if not project_ids:
            _stop_fast_backfill()
            return

        all_filled = True
        for pid in project_ids:
            missing = await _get_missing_dates(pid)
            if not missing:
                logger.info(f"⏩ Fast backfill: project {pid} — all {BACKFILL_DAYS} days filled ✅")
                continue

            all_filled = False
            day = missing[0]  # sync oldest missing day first
            logger.info(
                f"⏩ Fast backfill: project {pid} — syncing {day} "
                f"({len(missing)} days remaining)"
            )

            async with AsyncSessionLocal() as db:
                res = await run_funnel_sync(db, pid, day, day)
                logger.info(
                    f"⏩ Fast backfill: project {pid} — {day} done, "
                    f"+{res.get('rows', 0)} rows"
                )

            # Small pause between projects
            await asyncio.sleep(1)

        if all_filled:
            logger.info("🎉 Fast backfill complete — all projects fully covered!")
            _stop_fast_backfill()

    except Exception as e:
        logger.error(f"Fast backfill error: {e}\n{traceback.format_exc()}")
    finally:
        _backfill_lock = False


def _stop_fast_backfill():
    """Remove the fast backfill job from scheduler."""
    global scheduler
    if scheduler:
        try:
            scheduler.remove_job("fast_backfill")
            logger.info("Fast backfill job removed from scheduler")
        except Exception:
            pass


# ─── Scheduler lifecycle ─────────────────────────────────────────────────────

def start_scheduler():
    """Start the background scheduler with cron jobs + fast backfill."""
    global scheduler

    scheduler = AsyncIOScheduler(timezone=MSK)

    # 4 syncs per day: 00:01, 07:00, 13:00, 19:00 MSK
    for hour, minute in [(0, 1), (7, 0), (13, 0), (19, 0)]:
        scheduler.add_job(
            sync_all_projects_funnel,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=MSK),
            id=f"funnel_sync_{hour:02d}{minute:02d}",
            name=f"WB Funnel sync at {hour:02d}:{minute:02d} MSK",
            replace_existing=True,
            misfire_grace_time=600,  # 10 min grace
        )

    # Fast backfill: every 30s until all days are filled
    from apscheduler.triggers.interval import IntervalTrigger
    scheduler.add_job(
        fast_backfill_tick,
        trigger=IntervalTrigger(seconds=60),
        id="fast_backfill",
        name="Fast backfill (every 30s)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    scheduler.start()
    logger.info(
        "✅ Scheduler started — funnel sync 4x/day + fast backfill every 30s"
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

