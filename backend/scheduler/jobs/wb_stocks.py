"""
Scheduler job: sync WB warehouse stocks for all projects with active WB integrations.
Runs every 1 hour. Saves snapshots for historical analysis.
"""

import asyncio
import logging

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
        sync_log = None
        try:
            async with AsyncSessionLocal() as db:
                try:
                    key, api_key = await _get_wb_key(db, project_id)
                except ValueError:
                    logger.debug("WB stocks sync: project %d has no WB key, skipping", project_id)
                    continue

                # Create sync log
                sync_log = SyncLog(
                    integration_id=key.id,
                    service="wb",
                    sync_type="warehouse_stocks",
                    started_at=utcnow(),
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.flush()

                try:
                    items = await asyncio.wait_for(
                        fetch_warehouse_stocks(api_key),
                        timeout=600,
                    )
                    count = await sync_warehouse_stocks(db, project_id, items)

                    sync_log.status = "OK"
                    sync_log.rows_fetched = len(items)
                    sync_log.rows_inserted = count
                    sync_log.finished_at = utcnow()
                    await db.commit()

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

                except Exception as e:
                    sync_log.status = "ERROR"
                    sync_log.error_msg = str(e)[:1000]
                    raise
                finally:
                    # GUARANTEED: update sync_log — never leave RUNNING
                    if sync_log.status != "OK":
                        sync_log.finished_at = utcnow()
                    await db.commit()

        except Exception as e:
            logger.error(
                "WB stocks sync: project %d failed — %s",
                project_id,
                str(e),
                exc_info=True,
            )
            errors += 1

    logger.info("WB stocks sync: done — %d ok, %d errors", ok, errors)
