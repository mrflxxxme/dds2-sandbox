"""
Scheduler job: sync FBO supplies for all projects with active WB integrations.
Runs every 1 hour.
"""

import logging

from backend.database import AsyncSessionLocal
from backend.scheduler.helpers import get_sync_project_ids
from backend.utils.telegram import send_alert

logger = logging.getLogger("dds.scheduler")


async def sync_all_projects_fbo_supplies():
    """
    Iterate all projects with active WB API keys and sync FBO supplies.
    Called by APScheduler every 1 hour.
    """
    logger.info("FBO supplies sync: starting for all projects")
    project_ids = await get_sync_project_ids()

    if not project_ids:
        logger.info("FBO supplies sync: no projects with WB keys, skipping")
        return

    from backend.integrations.wb_api import WBApiClient
    from backend.services.fbo_supply_service import sync_fbo_supplies
    from backend.services.integrations_service import _get_wb_key

    ok = 0
    errors = 0

    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                try:
                    key, api_key = await _get_wb_key(db, project_id)
                except ValueError:
                    logger.debug("FBO supplies sync: project %d has no WB key, skipping", project_id)
                    continue

                api_client = WBApiClient(api_key)
                result = await sync_fbo_supplies(db, project_id, api_client, key.id)

                logger.info(
                    "FBO supplies sync: project %d — %d synced (%d new, %d updated)",
                    project_id,
                    result["synced"],
                    result["created"],
                    result["updated"],
                )
                ok += 1

        except Exception as e:
            logger.error(
                "FBO supplies sync: project %d failed — %s",
                project_id,
                str(e),
                exc_info=True,
            )
            errors += 1
            try:
                await send_alert(f"FBO sync failed for project {project_id}: {str(e)[:200]}")
            except Exception:
                logger.warning("FBO supplies sync: failed to send alert for project %d", project_id)

    logger.info("FBO supplies sync: done — %d ok, %d errors", ok, errors)


async def enrich_all_projects_fbo_supplies():
    """
    Enrich FBO supplies with warehouse_name via detail API for all projects.
    Called by APScheduler every 3 hours. Rate-limited: 6 req/min per project.
    """
    logger.info("FBO enrich: starting for all projects")
    project_ids = await get_sync_project_ids()

    if not project_ids:
        logger.info("FBO enrich: no projects with WB keys, skipping")
        return

    from backend.integrations.wb_api import WBApiClient
    from backend.services.fbo_supply_service import enrich_fbo_supplies
    from backend.services.integrations_service import _get_wb_key

    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                try:
                    _key, api_key = await _get_wb_key(db, project_id)
                except ValueError:
                    logger.debug("FBO enrich: project %d has no WB key, skipping", project_id)
                    continue

                api_client = WBApiClient(api_key)
                result = await enrich_fbo_supplies(db, project_id, api_client, max_calls=300)

                logger.info(
                    "FBO enrich: project %d — %d enriched, %d errors",
                    project_id,
                    result["enriched"],
                    result["errors"],
                )

        except Exception as e:
            logger.error(
                "FBO enrich: project %d failed — %s",
                project_id,
                str(e),
                exc_info=True,
            )
