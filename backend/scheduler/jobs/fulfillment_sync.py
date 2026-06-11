"""
Scheduler job: sync fulfillment stocks/requests for all warehouses with
active skladbot integrations. Runs every 15 minutes (worker container only).
"""

import asyncio
import logging

from sqlalchemy import select, update

from backend.database import AsyncSessionLocal
from backend.models import IntegrationKey, SyncLog
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler")

SYNC_TIMEOUT = 600  # seconds per warehouse


async def sync_all_fulfillment_warehouses():
    """Iterate all active skladbot keys bound to warehouses and sync each.

    Called by APScheduler every 15 minutes.
    SyncLog обновляется отдельной сессией в finally — никогда не остаётся
    RUNNING, даже если сессия синка умерла в rolled-back состоянии.
    """
    logger.info("Fulfillment sync: starting for all warehouses")

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(IntegrationKey.id, IntegrationKey.project_id, IntegrationKey.warehouse_id).where(
                IntegrationKey.service == "skladbot",
                IntegrationKey.warehouse_id.isnot(None),
                IntegrationKey.is_active.is_(True),
                IntegrationKey.is_deleted == False,  # noqa: E712
            )
        )
        targets = result.all()

    if not targets:
        logger.info("Fulfillment sync: no active skladbot keys, skipping")
        return

    from backend.services.fulfillment_service import sync_warehouse

    ok = 0
    errors = 0

    for key_id, project_id, warehouse_id in targets:
        log_id = None
        log_status = "ERROR"
        log_error: str | None = None
        rows_fetched = 0
        rows_inserted = 0
        try:
            # SyncLog — в собственной сессии: фиксируется независимо от судьбы синка
            async with AsyncSessionLocal() as db:
                sync_log = SyncLog(
                    integration_id=key_id,
                    service="skladbot",
                    sync_type="fulfillment",
                    started_at=utcnow(),
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.flush()
                log_id = sync_log.id
                await db.commit()

            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(
                    sync_warehouse(db, project_id, warehouse_id),
                    timeout=SYNC_TIMEOUT,
                )

            log_status = "OK"
            rows_fetched = result["stocks_synced"] + result["requests_synced"]
            rows_inserted = result["stocks_synced"]
            logger.info(
                "Fulfillment sync: project %d warehouse %d — %d stocks, %d requests",
                project_id,
                warehouse_id,
                result["stocks_synced"],
                result["requests_synced"],
            )
            ok += 1

        except asyncio.CancelledError:
            log_error = "Task cancelled (worker shutdown or restart)"
            raise
        except TimeoutError:
            log_error = f"Timeout {SYNC_TIMEOUT}s exceeded"
            logger.error("Fulfillment sync: project %d warehouse %d — TIMEOUT", project_id, warehouse_id)
            errors += 1
        except Exception as e:
            log_error = str(e)[:1000]
            logger.error(
                "Fulfillment sync: project %d warehouse %d failed — %s",
                project_id,
                warehouse_id,
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
                    logger.error("Fulfillment sync: failed to update sync_log %s — %s", log_id, log_err)

    logger.info("Fulfillment sync: done — %d ok, %d errors", ok, errors)
