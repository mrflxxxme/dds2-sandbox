"""
Scheduler job: sync WB goods-returns (возвраты на ПВЗ) for all projects.

Runs every 30 minutes. Uses WB Seller Analytics API (rate limit: 1/min).
Default window = last 7 days (for overlap/safety).
"""

import asyncio
import logging

from backend.database import AsyncSessionLocal
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
        sync_log: SyncLog | None = None
        try:
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

                try:
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

                    sync_log.status = "OK"
                    sync_log.rows_fetched = int(result.get("rows_fetched", 0))
                    sync_log.rows_inserted = int(result.get("rows_upserted", 0))
                    sync_log.finished_at = utcnow()
                    await db.commit()

                    logger.info(
                        "WB goods returns sync: project %d — fetched=%d, upserted=%d",
                        project_id,
                        sync_log.rows_fetched,
                        sync_log.rows_inserted,
                    )
                    ok += 1

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    sync_log.status = "ERROR"
                    sync_log.error_msg = str(e)[:1000]
                    raise
                finally:
                    # GUARANTEED: close sync_log — never leave RUNNING
                    if sync_log is not None and sync_log.status != "OK":
                        sync_log.finished_at = utcnow()
                    await db.commit()

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "WB goods returns sync: project %d failed — %s",
                project_id,
                str(e),
                exc_info=True,
            )
            errors += 1

    logger.info("WB goods returns sync: done — %d ok, %d errors", ok, errors)
