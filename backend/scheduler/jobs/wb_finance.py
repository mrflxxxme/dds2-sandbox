"""WB Finance Report sync job.

Two modes:
- Weekly (Monday): download full weekly reports (period=None, default)
- Daily (Tue–Sun): download daily reports to fill current incomplete week (period="daily")
"""

import logging
from datetime import date, timedelta

from backend.database import AsyncSessionLocal
from backend.scheduler.helpers import get_sync_project_ids
from backend.utils.telegram import send_alert

logger = logging.getLogger("dds.scheduler")


async def sync_all_projects_wb_finance():
    """Weekly sync: download weekly finance reports (Mon schedule).

    Smart gap-fill: check max date in DB and fill gap to today.
    Skips if data is already fresh (<5 days behind).
    """
    await _sync_finance_for_all(period=None, job_label="weekly")


async def sync_all_projects_wb_finance_daily():
    """Daily sync: download daily finance reports for current incomplete week.

    Runs Tue–Sun. Fetches from last Monday (or max_date+1) to yesterday
    using period=daily so we get fresh data while the week is incomplete.
    """
    await _sync_finance_for_all(period="daily", job_label="daily")


async def _sync_finance_for_all(period: str | None, job_label: str):
    """Core sync logic shared by weekly and daily jobs."""
    logger.info("💰 WB Finance %s sync: starting for all projects", job_label)
    project_ids = await get_sync_project_ids()

    if not project_ids:
        logger.info("WB Finance %s sync: no projects with WB keys, skipping", job_label)
        return

    from sqlalchemy import func as sa_func, select

    from backend.models.wb_finance import WbFinanceRow
    from backend.services.wb_finance_sync import sync_wb_finance

    today = date.today()

    for pid in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                # Find max rr_dt already in DB
                result = await db.execute(select(sa_func.max(WbFinanceRow.rr_dt)).where(WbFinanceRow.project_id == pid))
                max_date = result.scalar()

                if max_date is None:
                    # No data yet — initial sync (last 60 days, weekly)
                    date_from = today - timedelta(days=60)
                    logger.info(
                        "💰 WB Finance %s: project %s — initial sync %s → %s",
                        job_label,
                        pid,
                        date_from,
                        today,
                    )
                elif period == "daily":
                    # Daily mode: fetch from last Monday (start of current week)
                    # or max_date+1, whichever is later
                    last_monday = today - timedelta(days=today.weekday())
                    date_from = max(last_monday, max_date + timedelta(days=1))
                    date_to = today - timedelta(days=1)  # yesterday

                    if date_from > date_to:
                        logger.info(
                            "💰 WB Finance daily: project %s — already up-to-date "
                            "(max_date=%s, last_monday=%s), skip",
                            pid,
                            max_date,
                            last_monday,
                        )
                        continue
                    logger.info(
                        "💰 WB Finance daily: project %s — fetching %s → %s (period=daily)",
                        pid,
                        date_from,
                        date_to,
                    )
                else:
                    # Weekly mode: gap-fill from max_date+1 to today
                    days_behind = (today - max_date).days
                    if days_behind < 5:
                        logger.info(
                            "💰 WB Finance weekly: project %s — fresh " "(max_date=%s, %sd behind), skip",
                            pid,
                            max_date,
                            days_behind,
                        )
                        continue
                    date_from = max_date + timedelta(days=1)
                    date_to = today
                    logger.info(
                        "💰 WB Finance weekly: project %s — gap-fill %s → %s",
                        pid,
                        date_from,
                        date_to,
                    )

                if period == "daily" and max_date is not None:
                    sync_result = await sync_wb_finance(
                        db,
                        pid,
                        date_from,
                        date_to,
                        period="daily",
                    )
                else:
                    sync_result = await sync_wb_finance(db, pid, date_from, today)

                rows = sync_result.get("rows_synced", 0)
                status = sync_result.get("status", "?")
                logger.info(
                    "💰 WB Finance %s: project %s — %s, %s rows synced",
                    job_label,
                    pid,
                    status,
                    rows,
                )

        except Exception as e:
            logger.error(
                "💰 WB Finance %s sync failed for project %s: %s",
                job_label,
                pid,
                e,
            )
            await send_alert(
                f"WB Finance Sync ({job_label}) *ERROR*\n" f"Project: {pid}\n" f"Error: {str(e)[:300]}",
                exc=e,
            )

    logger.info("💰 WB Finance %s sync: completed for all projects", job_label)
