"""
Background scheduler for periodic WB funnel sync.

Refactored: scheduler package with job modules.
- helpers.py: project detection, missing dates, windowing
- jobs/funnel.py: daily sync, backfill, ad anomaly
- jobs/wb_finance.py: WB Finance report sync
- jobs/prewarm.py: OPIU/BDR cache prewarm

This module: scheduler lifecycle (start, stop, status, restart).
"""

import logging

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.scheduler.jobs.ai_digest import send_daily_digests
from backend.scheduler.jobs.fbo_supplies import sync_all_projects_fbo_supplies
from backend.scheduler.jobs.funnel import (
    ad_anomaly_check,
    fast_backfill_tick,
    sync_all_projects_funnel,
)
from backend.scheduler.jobs.health_check import health_monitor
from backend.scheduler.jobs.prewarm import prewarm_all_reports, prewarm_project  # noqa: F401
from backend.scheduler.jobs.wb_finance import (
    sync_all_projects_wb_finance,
    sync_all_projects_wb_finance_daily,
)

logger = logging.getLogger("dds.scheduler")

MSK = pytz.timezone("Europe/Moscow")

_scheduler: AsyncIOScheduler | None = None


def get_scheduler_instance() -> AsyncIOScheduler | None:
    """Get the current scheduler instance (used by jobs to self-remove)."""
    return _scheduler


def start_scheduler():
    """Start the background scheduler with cron jobs + fast backfill.

    Runs ONLY in the worker container (DDS_ROLE=worker).
    Single instance is guaranteed by Docker (1 worker container).
    """
    from backend.config import settings

    if not settings.SCHEDULER_ENABLED:
        logger.info("⏭️ Scheduler disabled (SCHEDULER_ENABLED=false)")
        return

    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=MSK)

    # Daily sync: 00:01, 03:00, 05:00 MSK — today + yesterday
    for hour, minute in [(0, 1), (3, 0), (5, 0)]:
        _scheduler.add_job(
            sync_all_projects_funnel,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=MSK),
            id=f"funnel_sync_{hour:02d}{minute:02d}",
            name=f"WB daily sync at {hour:02d}:{minute:02d} MSK",
            replace_existing=True,
            misfire_grace_time=600,
        )

    # Fast backfill: every 3 min — missing days only, auto-stops
    _scheduler.add_job(
        fast_backfill_tick,
        trigger=IntervalTrigger(seconds=180),
        id="fast_backfill",
        name="Fast backfill (every 3min, auto-stop)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Ad anomaly check: every 3 min — re-sync incomplete ad days, auto-stops
    _scheduler.add_job(
        ad_anomaly_check,
        trigger=IntervalTrigger(seconds=180),
        id="ad_anomaly_check",
        name="Ad anomaly check (every 3min, auto-stop)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # WB finance report sync — WEEKLY: Mon 03/06/09 (full week reports)
    for job_hour in [3, 6, 9]:
        _scheduler.add_job(
            sync_all_projects_wb_finance,
            trigger=CronTrigger(day_of_week="mon", hour=job_hour, minute=0, timezone=MSK),
            id=f"wb_finance_sync_mon_{job_hour:02d}",
            name=f"WB finance weekly sync (MON {job_hour:02d}:00)",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    # WB finance report sync — DAILY: Tue-Sun at 08:00, 14:00 MSK
    # Fetches daily reports (period=daily) for current incomplete week
    for job_hour in [8, 14]:
        _scheduler.add_job(
            sync_all_projects_wb_finance_daily,
            trigger=CronTrigger(day_of_week="tue-sun", hour=job_hour, minute=0, timezone=MSK),
            id=f"wb_finance_daily_{job_hour:02d}",
            name=f"WB finance daily sync ({job_hour:02d}:00)",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    # Report cache prewarm: every 1h (re-enabled after BDR SQL migration)
    _scheduler.add_job(
        prewarm_all_reports,
        trigger=IntervalTrigger(hours=1),
        id="prewarm_reports",
        name="Report cache prewarm (every 1h)",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Health monitor: every 6 hours
    _scheduler.add_job(
        health_monitor,
        trigger=IntervalTrigger(hours=6),
        id="health_monitor",
        name="Health monitor (disk, backups, stuck syncs)",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # FBO supplies sync: every 1 hour
    _scheduler.add_job(
        sync_all_projects_fbo_supplies,
        trigger=IntervalTrigger(hours=1),
        id="fbo_supplies_sync",
        name="FBO supplies sync (every 1h)",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # AI morning digest: daily at 7:00 MSK
    _scheduler.add_job(
        send_daily_digests,
        trigger=CronTrigger(hour=7, minute=0, timezone=MSK),
        id="ai_daily_digest",
        name="AI daily digest (07:00 MSK)",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    _scheduler.start()
    logger.info(
        "✅ Scheduler started — daily sync 3x/day + backfill + ad check + wb_finance weekly Mon + wb_finance daily Tue-Sun + prewarm 1h + AI digest 07:00"
    )


def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
        _scheduler = None


def get_scheduler_info() -> dict:
    """Get scheduler status and next run times."""
    if not _scheduler:
        return {"running": False, "jobs": []}

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append(
            {
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            }
        )

    return {"running": _scheduler.running, "jobs": jobs}


def restart_backfill_jobs():
    """Restart backfill + ad anomaly jobs (call when new WB API key is added)."""
    global _scheduler
    try:
        if not _scheduler or not _scheduler.running:
            logger.warning("Scheduler not running, cannot restart jobs")
            return

        if not _scheduler.get_job("fast_backfill"):
            _scheduler.add_job(
                fast_backfill_tick,
                IntervalTrigger(minutes=3),
                id="fast_backfill",
                max_instances=1,
                replace_existing=True,
            )
            logger.info("🔄 Restarted fast_backfill job (new API key detected)")

        if not _scheduler.get_job("ad_anomaly_check"):
            _scheduler.add_job(
                ad_anomaly_check,
                IntervalTrigger(minutes=3),
                id="ad_anomaly_check",
                max_instances=1,
                replace_existing=True,
            )
            logger.info("🔄 Restarted ad_anomaly_check job (new API key detected)")
    except Exception as e:
        logger.error(f"Failed to restart backfill jobs: {e}")
