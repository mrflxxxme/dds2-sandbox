"""Funnel sync jobs — daily sync, fast backfill, ad anomaly check."""

import asyncio
import logging
import traceback
from datetime import date, timedelta

from sqlalchemy import select, update

from backend.database import AsyncSessionLocal
from backend.models import IntegrationKey, SyncLog
from backend.scheduler.helpers import (
    BACKFILL_DAYS,
    get_days_with_incomplete_ads,
    get_missing_dates,
    get_sync_project_ids,
    split_into_windows,
)
from backend.utils.telegram import send_alert
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler")

_backfill_locks: dict[int, asyncio.Lock] = {}
_ad_check_lock = asyncio.Lock()


async def _run_and_log(project_id: int, d_from: str, d_to: str, sync_type: str):
    """Run funnel sync and log result. GUARANTEED: never leaves sync_log as RUNNING."""
    from backend.services.funnel.sync import run_funnel_sync

    include_completed = sync_type == "ad_resync"
    log_id = None

    async with AsyncSessionLocal() as db:
        int_key = await db.execute(
            select(IntegrationKey.id)
            .where(
                IntegrationKey.project_id == project_id,
                IntegrationKey.service.in_(["wb", "wb_analytics"]),
                IntegrationKey.is_active.is_(True),
            )
            .limit(1)
        )
        key_id = int_key.scalar() or None
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

    result = {"rows": 0, "errors": []}
    status = "ERROR"

    try:
        async with AsyncSessionLocal() as db:
            result = await asyncio.wait_for(
                run_funnel_sync(
                    db,
                    project_id,
                    d_from,
                    d_to,
                    include_completed_campaigns=include_completed,
                ),
                timeout=600,
            )
            status = "OK" if not result.get("errors") else "PARTIAL"
    except TimeoutError:
        result = {"rows": 0, "errors": [f"Timeout: 10min exceeded ({d_from}→{d_to})"]}
        status = "TIMEOUT"
        logger.error(f"Scheduler: project {project_id} [{sync_type}] TIMEOUT {d_from}→{d_to}")
    except Exception as e:
        result = {"rows": 0, "errors": [str(e)[:500]]}
        status = "ERROR"
        logger.error(f"Scheduler: project {project_id} [{sync_type}] ERROR: {e}")
    finally:
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(SyncLog)
                    .where(SyncLog.id == log_id)
                    .values(
                        status=status,
                        rows_inserted=result.get("rows", 0),
                        finished_at=utcnow(),
                        error_msg="; ".join(result.get("errors", [])[:3]) or None,
                    )
                )
                await db.commit()
        except Exception as log_err:
            logger.error(f"Failed to update sync_log {log_id}: {log_err}")

    logger.info(
        f"Scheduler: project {project_id} [{sync_type}] {d_from}→{d_to} — "
        f"{result.get('rows', 0)} rows, status: {status}"
    )

    if status in ("ERROR", "TIMEOUT"):
        errors_text = "; ".join(result.get("errors", [])[:3])
        await send_alert(
            f"WB Sync *{status}*\n"
            f"Project: {project_id}\n"
            f"Type: {sync_type}\n"
            f"Period: {d_from} → {d_to}\n"
            f"Error: {errors_text[:300]}",
        )

    return result


# ─── Daily sync ──────────────────────────────────────────────────────────────


async def sync_all_projects_funnel():
    """Daily sync: today + yesterday for all projects."""
    logger.info("⏰ Scheduler: starting daily funnel sync for all projects")
    project_ids = await get_sync_project_ids()
    if not project_ids:
        logger.info("Scheduler: no projects with WB keys found, skipping")
        return
    for pid in project_ids:
        try:
            d_from = (date.today() - timedelta(days=1)).isoformat()
            d_to = date.today().isoformat()
            logger.info(f"Scheduler: project {pid} — sync today+yesterday {d_from} → {d_to}")
            await _run_and_log(pid, d_from, d_to, "funnel_auto")
        except Exception as e:
            logger.error(f"Scheduler: project {pid} sync failed: {e}")


# ─── Fast backfill ───────────────────────────────────────────────────────────


async def fast_backfill_tick():
    """Fill missing days ONLY. Runs every 3 min. Auto-stops when complete."""
    try:
        project_ids = await get_sync_project_ids()
        logger.info(f"⏩ Fast backfill tick: {len(project_ids)} projects")
        if not project_ids:
            _stop_fast_backfill()
            return

        all_filled = True
        for pid in project_ids:
            if pid not in _backfill_locks:
                _backfill_locks[pid] = asyncio.Lock()
            if _backfill_locks[pid].locked():
                logger.info(f"⏩ Backfill: project {pid} — still running, skipping")
                all_filled = False
                continue

            async with _backfill_locks[pid]:
                missing = await get_missing_dates(pid)
                if missing:
                    all_filled = False
                    d_from, d_to = missing[0], missing[-1]
                    logger.info(
                        f"⏩ Backfill: project {pid} — syncing range {d_from}→{d_to} ({len(missing)} days remaining)"
                    )
                    res = await _run_and_log(pid, d_from, d_to, "backfill")
                    if res:
                        logger.info(f"⏩ Backfill: project {pid} — {d_from}→{d_to} done, +{res.get('rows', 0)} rows")
                    await asyncio.sleep(1)
                else:
                    logger.info(f"⏩ Backfill: project {pid} — all {BACKFILL_DAYS} days covered ✅")

        if all_filled:
            logger.info("🎉 Fast backfill complete — all projects fully covered! Stopping.")
            _stop_fast_backfill()
    except Exception as e:
        logger.error(f"Fast backfill error: {e}\n{traceback.format_exc()}")


def _stop_fast_backfill():
    from backend.scheduler import get_scheduler_instance

    sched = get_scheduler_instance()
    if sched:
        try:
            sched.remove_job("fast_backfill")
            logger.info("Fast backfill job removed from scheduler")
        except Exception as e:
            logger.debug(f"fast_backfill removal: {e}")


# ─── Ad anomaly check ────────────────────────────────────────────────────────


async def ad_anomaly_check():
    """Check for days with incomplete ad data and re-sync them."""
    if _ad_check_lock.locked():
        return
    async with _ad_check_lock:
        try:
            project_ids = await get_sync_project_ids()
            logger.info(f"📊 Ad anomaly check: {len(project_ids)} projects")
            all_ads_ok = True
            for pid in project_ids:
                incomplete_days = await get_days_with_incomplete_ads(pid)
                if incomplete_days:
                    all_ads_ok = False
                    windows = split_into_windows(incomplete_days, max_window=30)
                    logger.info(
                        f"📊 Ad anomaly: project {pid} — {len(incomplete_days)} incomplete days, {len(windows)} windows"
                    )
                    for w_from, w_to in windows:
                        logger.info(f"📊 Ad anomaly: project {pid} — re-syncing window {w_from}→{w_to}")
                        res = await _run_and_log(pid, w_from, w_to, "ad_resync")
                        if res:
                            logger.info(
                                f"📊 Ad anomaly: project {pid} — {w_from}→{w_to} re-synced, +{res.get('rows', 0)} rows"
                            )
                        await asyncio.sleep(5)
                else:
                    logger.info(f"📊 Ad anomaly: project {pid} — all ads complete ✅")
            if all_ads_ok:
                logger.info("🎉 Ad anomaly check complete — all projects have full ad data! Stopping.")
                _stop_ad_anomaly_check()
        except Exception as e:
            logger.error(f"Ad anomaly check error: {e}\n{traceback.format_exc()}")


def _stop_ad_anomaly_check():
    from backend.scheduler import get_scheduler_instance

    sched = get_scheduler_instance()
    if sched:
        try:
            sched.remove_job("ad_anomaly_check")
            logger.info("Ad anomaly check job removed from scheduler")
        except Exception as e:
            logger.debug(f"ad_anomaly_check removal: {e}")


# ─── Ad campaigns sync (hourly) ─────────────────────────────────────────────


async def sync_ad_campaigns_all_projects():
    """Sync ad campaigns (names, types, budgets) for all projects with WB keys."""
    from backend.services.funnel.ad_campaigns_service import sync_ad_campaigns

    logger.info("⏰ Scheduler: starting hourly ad campaigns sync")
    project_ids = await get_sync_project_ids()
    if not project_ids:
        logger.info("Scheduler: no projects with WB keys for ad campaigns sync")
        return

    for pid in project_ids:
        log_id = None
        status = "ERROR"
        synced = 0
        error_msg = None

        try:
            # Create sync_log entry
            async with AsyncSessionLocal() as db:
                int_key = await db.execute(
                    select(IntegrationKey.id)
                    .where(
                        IntegrationKey.project_id == pid,
                        IntegrationKey.service.in_(["wb", "wb_advert", "wb_analytics"]),
                        IntegrationKey.is_active.is_(True),
                    )
                    .limit(1)
                )
                key_id = int_key.scalar() or None
                sync_log = SyncLog(
                    integration_id=key_id,
                    service="wb_funnel",
                    sync_type="ad_campaigns",
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.commit()
                await db.refresh(sync_log)
                log_id = sync_log.id

            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(
                    sync_ad_campaigns(db, pid),
                    timeout=600,
                )
                synced = result.get("synced", 0)
                status = "OK"
                logger.info(f"Ad campaigns sync: project {pid} — {synced} campaigns")
        except TimeoutError:
            status = "TIMEOUT"
            error_msg = "Timeout 10min exceeded"
            logger.error(f"Ad campaigns sync TIMEOUT for project {pid}")
        except Exception as e:
            error_msg = str(e)[:500]
            logger.error(f"Ad campaigns sync failed for project {pid}: {e}")
        finally:
            if log_id:
                try:
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(SyncLog)
                            .where(SyncLog.id == log_id)
                            .values(
                                status=status,
                                rows_inserted=synced,
                                finished_at=utcnow(),
                                error_msg=error_msg,
                            )
                        )
                        await db.commit()
                except Exception as log_err:
                    logger.error(f"Failed to update sync_log {log_id}: {log_err}")


# ─── Nomenclature sync (2x/day) ──────────────────────────────────────────────


async def sync_nomenclature_all_projects():
    """Sync nomenclature (barcodes, brands, categories) for all projects with WB keys."""
    from backend.services.integrations_service import sync_wb_nomenclature

    logger.info("⏰ Scheduler: starting nomenclature sync")
    project_ids = await get_sync_project_ids()
    if not project_ids:
        logger.info("Scheduler: no projects with WB keys for nomenclature sync")
        return

    for pid in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(
                    sync_wb_nomenclature(db, pid),
                    timeout=300,
                )
                logger.info(
                    f"Nomenclature sync: project {pid} — " f"inserted={result.rows_inserted}, status={result.status}"
                )
        except TimeoutError:
            logger.error(f"Nomenclature sync TIMEOUT for project {pid}")
        except Exception as e:
            logger.error(f"Nomenclature sync failed for project {pid}: {e}")


# ─── Budget-only sync (every 10 min) ─────────────────────────────────────────


async def sync_budgets_all_projects():
    """Sync ONLY budgets of active campaigns for all projects. Lightweight, every 10 min."""
    from backend.services.funnel.ad_campaigns_service import sync_ad_budgets_only

    logger.info("⏰ Scheduler: starting budget-only sync for all projects")
    project_ids = await get_sync_project_ids()
    if not project_ids:
        logger.info("Scheduler: no projects with WB keys for budget sync")
        return

    for pid in project_ids:
        log_id = None
        status = "ERROR"
        updated = 0
        events = 0
        error_msg = None

        try:
            async with AsyncSessionLocal() as db:
                int_key = await db.execute(
                    select(IntegrationKey.id)
                    .where(
                        IntegrationKey.project_id == pid,
                        IntegrationKey.service.in_(["wb", "wb_advert"]),
                        IntegrationKey.is_active.is_(True),
                    )
                    .limit(1)
                )
                key_id = int_key.scalar() or None
                sync_log = SyncLog(
                    integration_id=key_id,
                    service="wb_funnel",
                    sync_type="ad_budgets",
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.commit()
                await db.refresh(sync_log)
                log_id = sync_log.id

            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(
                    sync_ad_budgets_only(db, pid),
                    timeout=300,
                )
                updated = result.get("updated", 0)
                events = result.get("events", 0)
                status = "OK"
                logger.info(f"Budget sync: project {pid} — updated={updated}, events={events}")
        except TimeoutError:
            status = "TIMEOUT"
            error_msg = "Timeout 5min exceeded"
            logger.error(f"Budget sync TIMEOUT for project {pid}")
        except Exception as e:
            error_msg = str(e)[:500]
            logger.error(f"Budget sync failed for project {pid}: {e}")
        finally:
            if log_id:
                try:
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(SyncLog)
                            .where(SyncLog.id == log_id)
                            .values(
                                status=status,
                                rows_inserted=updated,
                                finished_at=utcnow(),
                                error_msg=error_msg,
                            )
                        )
                        await db.commit()
                except Exception as log_err:
                    logger.error(f"Failed to update sync_log {log_id}: {log_err}")


# ─── Funnel hourly sync (last 2 days) ────────────────────────────────────────


async def sync_funnel_hourly():
    """Hourly funnel sync: last 2 days for all projects."""
    logger.info("⏰ Scheduler: starting hourly funnel sync (last 2 days)")
    project_ids = await get_sync_project_ids()
    if not project_ids:
        logger.info("Scheduler: no projects with WB keys for hourly funnel sync")
        return

    for pid in project_ids:
        try:
            d_from = (date.today() - timedelta(days=1)).isoformat()
            d_to = date.today().isoformat()
            logger.info(f"Hourly funnel: project {pid} — {d_from} → {d_to}")
            await _run_and_log(pid, d_from, d_to, "funnel_hourly")
        except Exception as e:
            logger.error(f"Hourly funnel sync failed for project {pid}: {e}")
