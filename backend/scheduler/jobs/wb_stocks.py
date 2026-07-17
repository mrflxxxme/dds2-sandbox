"""
Scheduler job: sync WB warehouse stocks for all projects with active WB integrations.
Runs every 1 hour. Saves snapshots for historical analysis.
"""

import asyncio
import logging

from sqlalchemy import update

from backend.database import AsyncSessionLocal
from backend.scheduler.helpers import get_sync_project_ids
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler")


async def sync_all_projects_wb_stocks():
    """
    Iterate all projects with active WB API keys and sync warehouse stocks.
    Called by APScheduler every 1 hour.
    """
    logger.info("WB stocks sync: starting for all projects")
    project_ids = await get_sync_project_ids()

    if not project_ids:
        logger.info("WB stocks sync: no projects with WB keys, skipping")
        return

    from backend.models import SyncLog
    from backend.services.funnel.wb_api_client import fetch_warehouse_stocks
    from backend.services.integrations_service import _get_wb_key
    from backend.services.warehouse_stock_service import sync_warehouse_stocks

    ok = 0
    errors = 0

    for project_id in project_ids:
        log_id = None
        log_status = "ERROR"
        log_error: str | None = None
        rows_fetched = 0
        rows_inserted = 0
        try:
            # SyncLog — в собственной короткой сессии: фиксируется независимо от судьбы синка
            async with AsyncSessionLocal() as db:
                try:
                    key, api_key = await _get_wb_key(db, project_id)
                except ValueError:
                    logger.debug("WB stocks sync: project %d has no WB key, skipping", project_id)
                    continue

                sync_log = SyncLog(
                    integration_id=key.id,
                    service="wb",
                    sync_type="warehouse_stocks",
                    started_at=utcnow(),
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.flush()
                log_id = sync_log.id
                await db.commit()

            # Синк — в ОТДЕЛЬНОЙ сессии: её падение не закоммитит частичные данные
            # в сессию sync_log и не замаскирует ошибку через finally-commit.
            async with AsyncSessionLocal() as db:
                items = await asyncio.wait_for(
                    fetch_warehouse_stocks(api_key),
                    timeout=600,
                )
                count = await sync_warehouse_stocks(db, project_id, items)

            log_status = "OK"
            rows_fetched = len(items)
            rows_inserted = count

            # Prewarm stock caches after sync
            try:
                from backend.services.warehouse_stock_service import (
                    get_warehouse_stocks as _get_wh,
                    get_warehouse_stocks_by_article as _get_art,
                )

                async with AsyncSessionLocal() as prewarm_db:
                    await asyncio.wait_for(_get_wh(prewarm_db, project_id), timeout=60)
                    await asyncio.wait_for(_get_art(prewarm_db, project_id), timeout=60)
                logger.info("WB stocks prewarm: project %d — cached", project_id)
            except Exception as e:
                logger.warning("WB stocks prewarm failed for project %d: %s", project_id, e)

            logger.info(
                "WB stocks sync: project %d — %d items synced",
                project_id,
                count,
            )
            ok += 1

        except asyncio.CancelledError:
            log_error = "Task cancelled (worker shutdown or restart)"
            raise
        except TimeoutError:
            log_error = "Timeout 600s exceeded"
            logger.error("WB stocks sync: project %d — TIMEOUT (600s)", project_id)
            errors += 1
        except Exception as e:
            log_error = str(e)[:1000]
            logger.error(
                "WB stocks sync: project %d failed — %s",
                project_id,
                str(e),
                exc_info=True,
            )
            errors += 1
        finally:
            # GUARANTEED: update sync_log в ОТДЕЛЬНОЙ сессии — never leave RUNNING
            if log_id is not None:
                try:
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(SyncLog)
                            .where(SyncLog.id == log_id)
                            .values(
                                status=log_status,
                                rows_fetched=rows_fetched,
                                rows_inserted=rows_inserted,
                                finished_at=utcnow(),
                                error_msg=log_error,
                            )
                        )
                        await db.commit()
                except Exception as log_err:
                    logger.error("WB stocks sync: failed to update sync_log %s — %s", log_id, log_err)

    logger.info("WB stocks sync: done — %d ok, %d errors", ok, errors)


async def sync_all_projects_wb_remains():
    """
    Sync the WB analytics report «Остатки на складах» (warehouse_remains) for
    all projects with active WB keys. Cabinet-accurate stock (incl. acceptance
    and inter-warehouse transit) for the unified stock «WB склады» column.
    Called by APScheduler every 1 hour. Create/download limits are 1 req/min
    per seller account — projects are processed sequentially.
    """
    logger.info("WB remains sync: starting for all projects")
    project_ids = await get_sync_project_ids()

    if not project_ids:
        logger.info("WB remains sync: no projects with WB keys, skipping")
        return

    from backend.models import SyncLog
    from backend.services.funnel.wb_api_client import fetch_warehouse_remains
    from backend.services.integrations_service import _get_wb_key
    from backend.services.warehouse_stock_service import sync_warehouse_remains

    ok = 0
    errors = 0

    for project_id in project_ids:
        log_id = None
        log_status = "ERROR"
        log_error: str | None = None
        rows_fetched = 0
        rows_inserted = 0
        try:
            # SyncLog — в собственной короткой сессии (см. sync_all_projects_wb_stocks)
            async with AsyncSessionLocal() as db:
                try:
                    key, api_key = await _get_wb_key(db, project_id)
                except ValueError:
                    logger.debug("WB remains sync: project %d has no WB key, skipping", project_id)
                    continue

                sync_log = SyncLog(
                    integration_id=key.id,
                    service="wb",
                    sync_type="warehouse_remains",
                    started_at=utcnow(),
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.flush()
                log_id = sync_log.id
                await db.commit()

            # Синк — в отдельной сессии: падение не маскирует ошибку sync_log
            async with AsyncSessionLocal() as db:
                items = await asyncio.wait_for(
                    fetch_warehouse_remains(api_key),
                    timeout=600,
                )
                count = await sync_warehouse_remains(db, project_id, items)

            log_status = "OK"
            rows_fetched = len(items)
            rows_inserted = count
            logger.info("WB remains sync: project %d — %d rows synced", project_id, count)
            ok += 1

        except asyncio.CancelledError:
            log_error = "Task cancelled (worker shutdown or restart)"
            raise
        except TimeoutError:
            log_error = "Timeout 600s exceeded"
            logger.error("WB remains sync: project %d — TIMEOUT (600s)", project_id)
            errors += 1
        except Exception as e:
            log_error = str(e)[:1000]
            logger.error("WB remains sync: project %d failed — %s", project_id, str(e), exc_info=True)
            errors += 1
        finally:
            # GUARANTEED: update sync_log в отдельной сессии — never leave RUNNING
            if log_id is not None:
                try:
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(SyncLog)
                            .where(SyncLog.id == log_id)
                            .values(
                                status=log_status,
                                rows_fetched=rows_fetched,
                                rows_inserted=rows_inserted,
                                finished_at=utcnow(),
                                error_msg=log_error,
                            )
                        )
                        await db.commit()
                except Exception as log_err:
                    logger.error("WB remains sync: failed to update sync_log %s — %s", log_id, log_err)

    logger.info("WB remains sync: done — %d ok, %d errors", ok, errors)
