"""Health monitoring job — disk space, backup freshness, stuck syncs."""

import logging
import os
from datetime import timedelta

from sqlalchemy import func as sa_func, select

from backend.database import AsyncSessionLocal
from backend.models.integrations import SyncLog
from backend.utils.telegram import send_alert
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler")


async def health_monitor():
    """Periodic health check — runs every 6 hours.

    Checks:
    1. Disk space on data volumes
    2. Stuck syncs (RUNNING > 30 min)
    3. Backup freshness (if backup dir exists)
    """
    alerts = []

    # ── 1. Disk space ──────────────────────────────────────────────────
    try:
        stat = os.statvfs("/")
        free_pct = stat.f_bavail / stat.f_blocks * 100
        free_gb = stat.f_bavail * stat.f_frsize / (1024**3)
        if free_pct < 5:
            alerts.append(f"🔴 *DISK CRITICAL*: {free_pct:.1f}% free ({free_gb:.1f} GB)")
        elif free_pct < 15:
            alerts.append(f"🟡 *Disk low*: {free_pct:.1f}% free ({free_gb:.1f} GB)")
    except Exception as e:
        logger.warning(f"Health: disk check failed: {e}")

    # ── 2. Stuck syncs (RUNNING > 30 min) ─────────────────────────────
    try:
        threshold = utcnow() - timedelta(minutes=30)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(sa_func.count())
                .select_from(SyncLog)
                .where(
                    SyncLog.status == "RUNNING",
                    SyncLog.started_at < threshold,
                )
            )
            stuck_count = result.scalar() or 0
            if stuck_count > 0:
                alerts.append(f"🟡 *Stuck syncs*: {stuck_count} job(s) RUNNING > 30 min")
    except Exception as e:
        logger.warning(f"Health: stuck sync check failed: {e}")

    # ── 3. Backup freshness ───────────────────────────────────────────
    backup_dir = "/backups"
    try:
        if os.path.isdir(backup_dir):
            files = sorted(os.listdir(backup_dir), reverse=True)
            sql_files = [f for f in files if f.endswith((".sql", ".sql.gz", ".dump"))]
            if not sql_files:
                alerts.append("🔴 *No backups found* in /backups/")
            else:
                latest = os.path.getmtime(os.path.join(backup_dir, sql_files[0]))
                age_hours = (utcnow().timestamp() - latest) / 3600
                if age_hours > 24:
                    alerts.append(f"🟡 *Backup stale*: latest is {age_hours:.0f}h old " f"({sql_files[0]})")
    except Exception as e:
        logger.warning(f"Health: backup check failed: {e}")

    # ── Send alerts ───────────────────────────────────────────────────
    if alerts:
        await send_alert("🏥 *Health Check*\n\n" + "\n".join(alerts))
        logger.warning(f"Health monitor: {len(alerts)} issue(s) found")
    else:
        logger.info("Health monitor: all checks passed ✅")
