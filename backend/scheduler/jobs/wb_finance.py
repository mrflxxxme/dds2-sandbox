"""WB Finance Report sync job.

Two modes:
- Weekly (Monday): download full weekly reports (period=None, default).
  Deletes any daily rows for the same period before downloading to avoid duplicates
  (daily and weekly reports have different rrd_ids for the same transactions).
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

    Always runs on Monday — ignores freshness check because weekly reports
    are finalized data that must replace preliminary daily reports.
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

    from sqlalchemy import and_, delete, func as sa_func, select

    from backend.models.wb_finance import WbFinanceRow
    from backend.services.wb_finance_sync import sync_wb_finance

    today = date.today()

    for pid in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                # Find max rr_dt already in DB (only from weekly rows to avoid
                # daily rows inflating freshness)
                result = await db.execute(
                    select(sa_func.max(WbFinanceRow.rr_dt)).where(
                        and_(
                            WbFinanceRow.project_id == pid,
                            WbFinanceRow.date_from != WbFinanceRow.date_to,  # weekly only
                        )
                    )
                )
                max_weekly_date = result.scalar()

                if max_weekly_date is None and period != "daily":
                    # No weekly data yet — initial sync (last 60 days)
                    date_from = today - timedelta(days=60)
                    date_to = today
                    logger.info(
                        "💰 WB Finance %s: project %s — initial sync %s → %s",
                        job_label,
                        pid,
                        date_from,
                        date_to,
                    )
                elif period == "daily":
                    # Daily mode: fetch from last Monday (start of current week)
                    # or day after latest daily data
                    last_monday = today - timedelta(days=today.weekday())

                    # Check max daily rr_dt separately
                    daily_result = await db.execute(
                        select(sa_func.max(WbFinanceRow.rr_dt)).where(
                            and_(
                                WbFinanceRow.project_id == pid,
                                WbFinanceRow.date_from == WbFinanceRow.date_to,  # daily only
                            )
                        )
                    )
                    max_daily_date = daily_result.scalar()

                    if max_daily_date and max_daily_date >= last_monday:
                        date_from = max_daily_date + timedelta(days=1)
                    else:
                        date_from = last_monday
                    date_to = today - timedelta(days=1)  # yesterday

                    if date_from > date_to:
                        logger.info(
                            "💰 WB Finance daily: project %s — already up-to-date "
                            "(max_daily=%s, last_monday=%s), skip",
                            pid,
                            max_daily_date,
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
                    # Weekly mode: always fetch previous week on Monday
                    # Compute the previous week range (Mon-Sun)
                    prev_monday = today - timedelta(days=today.weekday() + 7)
                    prev_sunday = prev_monday + timedelta(days=6)

                    # Also gap-fill older missing weeks
                    if max_weekly_date and max_weekly_date >= prev_sunday:
                        logger.info(
                            "💰 WB Finance weekly: project %s — already has data " "through %s, skip",
                            pid,
                            max_weekly_date,
                        )
                        continue

                    date_from = max_weekly_date + timedelta(days=1) if max_weekly_date else prev_monday
                    date_to = today

                    # Delete daily rows that overlap with the weekly period
                    # (daily and weekly have different rrd_ids → would cause duplicates)
                    del_result = await db.execute(
                        delete(WbFinanceRow).where(
                            and_(
                                WbFinanceRow.project_id == pid,
                                WbFinanceRow.date_from == WbFinanceRow.date_to,  # daily rows
                                WbFinanceRow.rr_dt >= date_from,
                                WbFinanceRow.rr_dt <= date_to,
                            )
                        )
                    )
                    deleted = del_result.rowcount
                    if deleted:
                        await db.commit()
                        logger.info(
                            "💰 WB Finance weekly: project %s — deleted %d daily rows "
                            "for period %s → %s before weekly sync",
                            pid,
                            deleted,
                            date_from,
                            date_to,
                        )

                    logger.info(
                        "💰 WB Finance weekly: project %s — fetching %s → %s",
                        pid,
                        date_from,
                        date_to,
                    )

                if period == "daily":
                    sync_result = await sync_wb_finance(
                        db,
                        pid,
                        date_from,
                        date_to,
                        period="daily",
                    )
                else:
                    sync_result = await sync_wb_finance(db, pid, date_from, date_to)

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
