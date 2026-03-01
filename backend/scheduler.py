"""
Background scheduler for periodic WB funnel sync.

Schedule: 00:01, 07:00, 13:00, 19:00 MSK daily.
- First run (no data): backfill last 90 days.
- Regular runs: sync only today + yesterday.

Uses APScheduler (in-process, AsyncIOScheduler).
"""

import logging
import asyncio
from datetime import date, timedelta

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.database import AsyncSessionLocal
from backend.models import Project, IntegrationKey, WbFunnelDaily, SyncLog

logger = logging.getLogger("dds.scheduler")

scheduler: AsyncIOScheduler | None = None

MSK = pytz.timezone("Europe/Moscow")

# ─── Backfill detection ──────────────────────────────────────────────────────

async def _needs_backfill(project_id: int) -> bool:
    """Check if project has any funnel data. If not — need 90-day backfill."""
    from sqlalchemy import select, func
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(func.count(WbFunnelDaily.id)).where(
                WbFunnelDaily.project_id == project_id
            )
        )
        count = result.scalar() or 0
    return count == 0


# ─── Main sync task ──────────────────────────────────────────────────────────

async def sync_all_projects_funnel():
    """
    Iterate over all projects with WB API keys and sync funnel data.
    - If project has no funnel data yet: backfill 90 days.
    - Otherwise: sync only today + yesterday.
    """
    from sqlalchemy import select, or_
    from backend.routers.funnel import run_funnel_sync

    logger.info("⏰ Scheduler: starting funnel sync for all projects")

    async with AsyncSessionLocal() as db:
        # Find all projects that have WB API keys
        result = await db.execute(
            select(IntegrationKey.project_id).where(
                IntegrationKey.service.in_(["wb", "wb_analytics"]),
                IntegrationKey.is_active == True,
                IntegrationKey.project_id.isnot(None),
            ).distinct()
        )
        project_ids = [r[0] for r in result if r[0]]

    if not project_ids:
        logger.info("Scheduler: no projects with WB keys found, skipping")
        return

    for pid in project_ids:
        try:
            needs_backfill = await _needs_backfill(pid)

            if needs_backfill:
                # First run: backfill 90 days
                d_from = (date.today() - timedelta(days=90)).isoformat()
                d_to = date.today().isoformat()
                sync_type = "funnel_backfill"
                logger.info(f"Scheduler: project {pid} — backfill {d_from} → {d_to}")
            else:
                # Regular run: today + yesterday only
                d_from = (date.today() - timedelta(days=1)).isoformat()
                d_to = date.today().isoformat()
                sync_type = "funnel_auto"
                logger.info(f"Scheduler: project {pid} — sync {d_from} → {d_to}")

            async with AsyncSessionLocal() as db:
                # Find integration key for logging
                from sqlalchemy import select
                int_key = await db.execute(
                    select(IntegrationKey.id).where(
                        IntegrationKey.project_id == pid,
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
                result = await run_funnel_sync(db, pid, d_from, d_to)

            # Update sync log with result
            async with AsyncSessionLocal() as db:
                from sqlalchemy import update
                from datetime import datetime
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
                f"Scheduler: project {pid} done — "
                f"{result.get('rows', 0)} rows, {result.get('days', 0)} days"
            )

        except Exception as e:
            logger.error(f"Scheduler: project {pid} sync failed: {e}")
            # Update sync log with error
            try:
                async with AsyncSessionLocal() as db:
                    from sqlalchemy import update
                    from datetime import datetime
                    await db.execute(
                        update(SyncLog).where(SyncLog.id == log_id).values(
                            status="ERROR",
                            finished_at=datetime.utcnow(),
                            error_msg=str(e)[:500],
                        )
                    )
                    await db.commit()
            except Exception:
                pass


# ─── Scheduler lifecycle ─────────────────────────────────────────────────────

def start_scheduler():
    """Start the background scheduler with cron jobs."""
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

    scheduler.start()
    logger.info(
        "✅ Scheduler started — funnel sync at 00:01, 07:00, 13:00, 19:00 MSK"
    )

    # Run initial sync in background (after 30s delay for startup)
    async def _initial_sync():
        await asyncio.sleep(30)
        logger.info("Scheduler: running initial sync check...")
        await sync_all_projects_funnel()

    asyncio.ensure_future(_initial_sync())


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
