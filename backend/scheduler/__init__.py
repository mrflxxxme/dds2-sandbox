"""
Background scheduler for periodic WB funnel sync.

Refactored: scheduler package with job modules.
- helpers.py: project detection, missing dates, windowing
- jobs/funnel.py: daily sync, backfill, ad anomaly
- jobs/wb_finance.py: WB Finance report sync
- jobs/prewarm.py: OPIU/BDR cache prewarm

This module: scheduler lifecycle (start, stop, status, restart).
"""

import contextlib
import logging

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.scheduler.jobs.ai_digest import send_daily_digests
from backend.scheduler.jobs.fbo_supplies import enrich_all_projects_fbo_supplies, sync_all_projects_fbo_supplies
from backend.scheduler.jobs.funnel import (
    ad_anomaly_check,
    fast_backfill_tick,
    sync_ad_campaigns_all_projects,
    sync_all_projects_funnel,
    sync_budgets_all_projects,
    sync_funnel_hourly,
    sync_nomenclature_all_projects,
)
from backend.scheduler.jobs.health_check import health_monitor
from backend.scheduler.jobs.heartbeat import heartbeat_ping
from backend.scheduler.jobs.prewarm import prewarm_all_reports, prewarm_project  # noqa: F401
from backend.scheduler.jobs.wb_finance import (
    sync_all_projects_wb_finance,
    sync_all_projects_wb_finance_daily,
)
from backend.scheduler.jobs.wb_stocks import sync_all_projects_wb_stocks

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

    # Ad campaigns sync: every 30 min at :02 and :32 — names, types, statuses, budgets
    _scheduler.add_job(
        sync_ad_campaigns_all_projects,
        trigger=CronTrigger(minute="2,32", timezone=MSK),
        id="sync_ad_campaigns",
        name="WB Ad Campaigns Sync (every 30min)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # Budget-only sync: every 10 min — lightweight, active campaigns only
    _scheduler.add_job(
        sync_budgets_all_projects,
        trigger=IntervalTrigger(minutes=10),
        id="sync_budgets",
        name="WB Ad Budgets Sync (every 10min)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=120,
    )

    # Funnel hourly sync: every hour at :45 — last 2 days
    _scheduler.add_job(
        sync_funnel_hourly,
        trigger=CronTrigger(minute=45, timezone=MSK),
        id="sync_funnel_hourly",
        name="WB Funnel Sync (hourly, last 2 days)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # Nomenclature sync: 2x/day at 08:30 and 20:30 MSK
    for nom_hour in [8, 20]:
        _scheduler.add_job(
            sync_nomenclature_all_projects,
            trigger=CronTrigger(hour=nom_hour, minute=30, timezone=MSK),
            id=f"nomenclature_sync_{nom_hour:02d}",
            name=f"WB Nomenclature Sync ({nom_hour:02d}:30 MSK)",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=600,
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

    # Heartbeat: every 2 min — prevents false BackendNoUserTraffic alerts at night
    _scheduler.add_job(
        heartbeat_ping,
        trigger=IntervalTrigger(minutes=2),
        id="heartbeat_ping",
        name="Backend heartbeat ping (every 2min)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # FBO supplies sync + statuses: every 30 min
    _scheduler.add_job(
        sync_all_projects_fbo_supplies,
        trigger=IntervalTrigger(minutes=30),
        id="fbo_supplies_sync",
        name="FBO supplies sync (every 30min)",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # FBO supplies enrich: every 3 hours (warehouse_name via detail API)
    _scheduler.add_job(
        enrich_all_projects_fbo_supplies,
        trigger=IntervalTrigger(hours=3),
        id="fbo_supplies_enrich",
        name="FBO supplies enrich (every 3h)",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # WB warehouse stocks snapshot: daily at 00:00 MSK
    _scheduler.add_job(
        sync_all_projects_wb_stocks,
        trigger=CronTrigger(hour=0, minute=0, timezone=MSK),
        id="wb_stocks_sync",
        name="WB warehouse stocks snapshot (daily 00:00 MSK)",
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

    # Cleanup stale sync_log records left by previous crashes
    import asyncio

    async def _cleanup_stale_syncs():
        try:
            from sqlalchemy import text

            from backend.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    text("""
                        UPDATE sync_log
                        SET status = 'ERROR',
                            error_msg = COALESCE(error_msg, '') || ' [auto-fix: stale after worker restart]',
                            finished_at = COALESCE(finished_at, NOW())
                        WHERE status IN ('STALE', 'RUNNING')
                          AND started_at < NOW() - interval '30 minutes'
                    """)
                )
                await db.commit()
                if result.rowcount:
                    logger.info("🧹 Cleaned up %d stale/stuck sync_log records", result.rowcount)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Stale sync cleanup failed: %s", e)

    asyncio.get_running_loop().create_task(_cleanup_stale_syncs())

    # Startup catch-up: run finance daily sync 30s after start
    # to recover from missed jobs (e.g. worker restart during scheduled time)
    from datetime import datetime, timedelta

    from apscheduler.triggers.date import DateTrigger

    _scheduler.add_job(
        sync_all_projects_wb_finance_daily,
        trigger=DateTrigger(run_date=datetime.now(MSK) + timedelta(seconds=30)),
        id="wb_finance_daily_catchup",
        name="WB finance daily catch-up (startup)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Startup catch-up: run weekly finance sync 60s after start.
    # Recovers from missed Mon weekly job (e.g. backend hang incident 2026-04-14).
    # Job itself skips projects whose max_weekly_date already covers prev_sunday,
    # so it's a safe no-op when nothing is missing.
    _scheduler.add_job(
        sync_all_projects_wb_finance,
        trigger=DateTrigger(run_date=datetime.now(MSK) + timedelta(seconds=60)),
        id="wb_finance_weekly_catchup",
        name="WB finance weekly catch-up (startup)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    _scheduler.start()
    logger.info(
        "✅ Scheduler started — daily sync 3x/day + backfill + ad check + wb_finance weekly Mon + wb_finance daily Tue-Sun + prewarm 1h + AI digest 07:00 + finance catch-up"
    )


def stop_scheduler():
    """Stop the background scheduler gracefully.

    wait=True gives running jobs time to finish (up to stop_grace_period).
    This prevents SIGKILL from docker when jobs are mid-flight.
    """
    global _scheduler
    if _scheduler:
        try:
            _scheduler.shutdown(wait=True)
            logger.info("Scheduler stopped gracefully (all jobs finished)")
        except Exception as e:
            logger.warning("Scheduler shutdown error (forcing): %s", e)
            with contextlib.suppress(Exception):
                _scheduler.shutdown(wait=False)
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
