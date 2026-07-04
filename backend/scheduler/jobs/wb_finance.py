"""WB Finance Report sync job.

Two modes:
- Weekly (Monday): download full weekly reports (period=None, default).
  Deletes any daily rows for the same period AFTER successful download to avoid
  data loss if sync fails (daily and weekly reports have different rrd_ids).
- Daily (Tue–Sun): download daily reports to fill current incomplete week (period="daily")
"""

import asyncio
import logging
from datetime import date, timedelta

from backend.database import AsyncSessionLocal
from backend.models import SyncLog
from backend.scheduler.helpers import get_sync_project_ids
from backend.utils.telegram import send_alert
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler")


def _compute_weekly_range(
    today: date,
    max_weekly_date: date | None,
    initial_lookback_days: int = 7,
) -> tuple[date, date] | None:
    """Compute date range for weekly finance sync (runs Mon).

    Returns (date_from, date_to) or None when already up-to-date.

    CRITICAL: date_to never includes today (incomplete Monday) — WB has no
    final data for the current day and returns 204, which earlier treated
    as a successful empty sync and caused data loss.

    initial_lookback_days applies only when max_weekly_date is None
    (first sync for this project).
    """
    prev_monday = today - timedelta(days=today.weekday() + 7)
    prev_sunday = prev_monday + timedelta(days=6)

    if max_weekly_date is not None and max_weekly_date >= prev_sunday:
        return None

    if max_weekly_date is None:
        date_from = today - timedelta(days=initial_lookback_days)
    else:
        date_from = max_weekly_date + timedelta(days=1)
    return date_from, prev_sunday


def _compute_daily_range(today: date, max_daily_date: date | None) -> tuple[date, date] | None:
    """Compute date range for daily finance sync (runs Tue-Sun).

    Fetches current incomplete week from last Monday to yesterday.
    Returns None if already up-to-date.
    """
    last_monday = today - timedelta(days=today.weekday())
    yesterday = today - timedelta(days=1)

    date_from = max_daily_date + timedelta(days=1) if max_daily_date and max_daily_date >= last_monday else last_monday
    if date_from > yesterday:
        return None
    return date_from, yesterday


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
    from backend.services.wb_cancel_sync import sync_cancel_stats
    from backend.services.wb_finance_sync import sync_wb_finance

    today = date.today()

    for pid in project_ids:
        sync_log = None
        log_status = "ERROR"
        log_error_msg = None
        rows_synced = 0

        try:
            async with AsyncSessionLocal() as db:
                # Create sync_log entry
                from backend.services.integrations_service import _get_wb_key

                try:
                    key, _api_key = await _get_wb_key(db, pid)
                    key_id = key.id
                except ValueError:
                    logger.debug("WB Finance %s: project %d has no WB key, skipping", job_label, pid)
                    continue

                sync_log = SyncLog(
                    integration_id=key_id,
                    service="wb",
                    sync_type=f"wb_finance_{job_label}",
                    started_at=utcnow(),
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.commit()
                log_id = sync_log.id

            async with AsyncSessionLocal() as db:
                if period == "daily":
                    # Check max daily rr_dt
                    daily_result = await db.execute(
                        select(sa_func.max(WbFinanceRow.rr_dt)).where(
                            and_(
                                WbFinanceRow.project_id == pid,
                                WbFinanceRow.date_from == WbFinanceRow.date_to,  # daily only
                            )
                        )
                    )
                    max_daily_date = daily_result.scalar()

                    daily_range = _compute_daily_range(today, max_daily_date)
                    if daily_range is None:
                        logger.info(
                            "💰 WB Finance daily: project %s — already up-to-date " "(max_daily=%s), skip",
                            pid,
                            max_daily_date,
                        )
                        log_status = "OK"
                        continue
                    date_from, date_to = daily_range
                    logger.info(
                        "💰 WB Finance daily: project %s — fetching %s → %s (period=daily)",
                        pid,
                        date_from,
                        date_to,
                    )
                else:
                    # Weekly: check max rr_dt from weekly rows only
                    result = await db.execute(
                        select(sa_func.max(WbFinanceRow.rr_dt)).where(
                            and_(
                                WbFinanceRow.project_id == pid,
                                WbFinanceRow.date_from != WbFinanceRow.date_to,  # weekly only
                            )
                        )
                    )
                    max_weekly_date = result.scalar()

                    # 60-day lookback for initial sync, 7-day for incremental
                    lookback = 60 if max_weekly_date is None else 7
                    weekly_range = _compute_weekly_range(today, max_weekly_date, initial_lookback_days=lookback)
                    if weekly_range is None:
                        logger.info(
                            "💰 WB Finance weekly: project %s — already has data through %s, skip",
                            pid,
                            max_weekly_date,
                        )
                        log_status = "OK"
                        continue
                    date_from, date_to = weekly_range
                    logger.info(
                        "💰 WB Finance weekly: project %s — fetching %s → %s%s",
                        pid,
                        date_from,
                        date_to,
                        " (initial 60-day sync)" if max_weekly_date is None else "",
                    )

                if period == "daily":
                    sync_result = await asyncio.wait_for(
                        sync_wb_finance(db, pid, date_from, date_to, period="daily"),
                        timeout=600,
                    )
                else:
                    sync_result = await asyncio.wait_for(
                        sync_wb_finance(db, pid, date_from, date_to),
                        timeout=600,
                    )

                rows_synced = sync_result.get("rows_synced", 0)
                log_status = sync_result.get("status", "OK")

                # Delete daily rows AFTER successful weekly sync with non-empty result
                # Guard (rows_synced > 0): WB returns 204 for not-yet-ready periods,
                # which arrives as {"status":"ok","rows_synced":0}. Without this guard,
                # daily rows get deleted and nothing replaces them → data loss.
                if period != "daily" and rows_synced > 0:
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
                            "for period %s → %s after weekly sync",
                            pid,
                            deleted,
                            date_from,
                            date_to,
                        )
                elif period != "daily" and rows_synced == 0:
                    logger.warning(
                        "💰 WB Finance weekly: project %s — 0 rows synced for %s → %s, "
                        "SKIPPING daily cleanup to prevent data loss",
                        pid,
                        date_from,
                        date_to,
                    )
                    log_status = "EMPTY"
                logger.info(
                    "💰 WB Finance %s: project %s — %s, %s rows synced",
                    job_label,
                    pid,
                    log_status,
                    rows_synced,
                )

            # Sync order cancel stats (for accurate buyout_pct)
            try:
                async with AsyncSessionLocal() as db2:
                    cancel_from = today - timedelta(days=30)
                    cancel_result = await sync_cancel_stats(db2, pid, cancel_from)
                    logger.info(
                        "💰 WB Cancel sync: project %s — %s, %d orders, %d cancelled",
                        pid,
                        cancel_result.get("status", "?"),
                        cancel_result.get("total_orders", 0),
                        cancel_result.get("total_cancelled", 0),
                    )
            except Exception as cancel_err:
                logger.error(
                    "💰 WB Cancel sync failed for project %s: %s",
                    pid,
                    cancel_err,
                )

        except TimeoutError:
            log_status = "TIMEOUT"
            log_error_msg = "Timeout 600s exceeded"
            logger.error("💰 WB Finance %s: project %s — TIMEOUT (600s)", job_label, pid)
            await send_alert(
                f"WB Finance Sync ({job_label}) *TIMEOUT*\nProject: {pid}",
            )
        except asyncio.CancelledError:
            log_status = "ERROR"
            log_error_msg = "Task cancelled (worker shutdown or restart)"
            logger.warning("💰 WB Finance %s: project %s — CANCELLED", job_label, pid)
            raise  # propagate cancellation — never swallow it to keep looping
        except Exception as e:
            log_status = "ERROR"
            log_error_msg = str(e)[:500]
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
        finally:
            # GUARANTEED: update sync_log — never leave RUNNING
            if sync_log is not None:
                try:
                    async with AsyncSessionLocal() as db:
                        from sqlalchemy import update

                        await db.execute(
                            update(SyncLog)
                            .where(SyncLog.id == log_id)
                            .values(
                                status=log_status,
                                rows_inserted=rows_synced,
                                finished_at=utcnow(),
                                error_msg=log_error_msg,
                            )
                        )
                        await db.commit()
                except Exception as log_err:
                    logger.error("Failed to update sync_log for project %s: %s", pid, log_err)

    logger.info("💰 WB Finance %s sync: completed for all projects", job_label)
