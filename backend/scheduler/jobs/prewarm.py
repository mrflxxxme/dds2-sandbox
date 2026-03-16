"""Report cache prewarm job."""

import asyncio
import logging
from datetime import date

from backend.database import AsyncSessionLocal
from backend.scheduler.helpers import get_sync_project_ids

logger = logging.getLogger("dds.scheduler")


async def prewarm_project(project_id: int):
    """Pre-compute OPIU and BDR for a single project (current + previous month)."""
    today = date.today()
    month_start = today.replace(day=1)
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
    """Pre-compute OPIU/BDR for ALL active projects. Runs hourly + at startup."""
    logger.info("🔥 Prewarm: starting for all projects")
    project_ids = await get_sync_project_ids()

    if not project_ids:
        logger.info("🔥 Prewarm: no projects with WB keys, skipping")
        return

    for pid in project_ids:
        await prewarm_project(pid)
        await asyncio.sleep(1)

    logger.info(f"🔥 Prewarm: completed for {len(project_ids)} projects")
