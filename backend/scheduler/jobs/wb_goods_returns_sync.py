"""
Scheduler job: sync WB goods-returns (возвраты на ПВЗ) for all projects.

Runs at 08:00 and 20:00 MSK (see backend/scheduler/__init__.py). Uses WB
Seller Analytics API (rate limit: 1/min). Default window = last 7 days
(overlap with prior runs for safety).

On success updates Prometheus gauge dds_wb_goods_returns_last_success_timestamp —
Grafana/Alertmanager use it to detect stale sync (WbReturnsSyncStale alert).
"""

import asyncio
import logging
import time

from sqlalchemy import update

from backend.database import AsyncSessionLocal
from backend.metrics import wb_goods_returns_last_success
from backend.scheduler.helpers import get_sync_project_ids
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler")


async def sync_all_projects_wb_returns():
    """
    Iterate all projects with active WB keys and sync goods-returns report.
    Called by APScheduler every 30 minutes.
    """
    logger.info("WB goods returns sync: starting for all projects")
    project_ids = await get_sync_project_ids()

    if not project_ids:
        logger.info("WB goods returns sync: no projects with WB keys, skipping")
        return

    from backend.models import SyncLog
    from backend.services.integrations_service import _get_wb_key
    from backend.services.wb_returns_service import sync_project_returns

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
                    logger.debug(
                        "WB goods returns sync: project %d has no WB key, skipping",
                        project_id,
                    )
                    continue

                sync_log = SyncLog(
                    integration_id=key.id,
                    service="wb",
                    sync_type="goods_returns",
                    started_at=utcnow(),
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.flush()
                log_id = sync_log.id
                await db.commit()

            # Синк — в ОТДЕЛЬНОЙ сессии (сервис коммитит успех сам): её падение
            # не закоммитит частичные данные в сессию sync_log и не замаскирует ошибку.
            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(
                    sync_project_returns(
                        db,
                        project_id,
                        api_key,
                        date_from=None,
                        date_to=None,
                    ),
                    timeout=600,
                )

            log_status = "OK"
            rows_fetched = int(result.get("rows_fetched", 0))
            rows_inserted = int(result.get("rows_upserted", 0))

            logger.info(
                "WB goods returns sync: project %d — fetched=%d, upserted=%d",
                project_id,
                rows_fetched,
                rows_inserted,
            )
            ok += 1

        except asyncio.CancelledError:
            log_error = "Task cancelled (worker shutdown or restart)"
            raise
        except TimeoutError:
            log_error = "Timeout 600s exceeded"
            logger.error("WB goods returns sync: project %d — TIMEOUT (600s)", project_id)
            errors += 1
        except Exception as e:
            log_error = str(e)[:1000]
            logger.error(
                "WB goods returns sync: project %d failed — %s",
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
                    logger.error(
                        "WB goods returns sync: failed to update sync_log %s — %s",
                        log_id,
                        log_err,
                    )

    # Update last-success gauge only when at least one project synced OK and
    # no project failed — matches the semantics of «sync is healthy» used by
    # the Prometheus alert WbReturnsSyncStale.
    if ok > 0 and errors == 0:
        wb_goods_returns_last_success.set(time.time())

    logger.info("WB goods returns sync: done — %d ok, %d errors", ok, errors)
