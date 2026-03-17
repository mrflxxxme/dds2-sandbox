"""WB Finance Report sync job."""

import logging
from datetime import date, timedelta

from backend.database import AsyncSessionLocal
from backend.scheduler.helpers import get_sync_project_ids
from backend.utils.telegram import send_alert

logger = logging.getLogger("dds.scheduler")


async def sync_all_projects_wb_finance():
    """Smart sync: check max date in DB and fill gap to today.

    Runs multiple times on Mon/Tue. Skips if data is already fresh.
    """
    logger.info("💰 WB Finance sync: starting for all projects")
    project_ids = await get_sync_project_ids()

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
                result = await db.execute(
                    select(sa_func.max(WbFinanceRow.rr_dt))
                    .where(WbFinanceRow.project_id == pid)
                )
                max_date = result.scalar()

                if max_date is None:
                    date_from = today - timedelta(days=60)
                    logger.info(f"💰 WB Finance: project {pid} — initial sync {date_from} → {today}")
                else:
                    days_behind = (today - max_date).days
                    if days_behind < 5:
                        logger.info(f"💰 WB Finance: project {pid} — fresh (max_date={max_date}, {days_behind}d behind), skip")
                        continue
                    date_from = max_date + timedelta(days=1)
                    logger.info(f"💰 WB Finance: project {pid} — gap-fill {date_from} → {today}")

                result = await sync_wb_finance(db, pid, date_from, today)
                rows = result.get("rows_synced", 0)
                status = result.get("status", "?")
                logger.info(f"💰 WB Finance: project {pid} — {status}, {rows} rows synced")

        except Exception as e:
            logger.error(f"💰 WB Finance sync failed for project {pid}: {e}")
            await send_alert(
                f"WB Finance Sync *ERROR*\n"
                f"Project: {pid}\n"
                f"Error: {str(e)[:300]}",
                exc=e,
            )

    logger.info("💰 WB Finance sync: completed for all projects")
