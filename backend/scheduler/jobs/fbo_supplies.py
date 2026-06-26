"""
Scheduler job: sync FBO supplies for all projects with active WB integrations.
Runs every 1 hour.
"""

import asyncio
import logging

from backend.database import AsyncSessionLocal
from backend.models import SyncLog
from backend.scheduler.helpers import get_sync_project_ids
from backend.utils.telegram import send_alert
from backend.utils.time import utcnow

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
    from backend.services.fbo_supply_service import sync_fbo_statuses, sync_fbo_supplies
    from backend.services.integrations_service import _get_wb_key

    ok = 0
    errors = 0

    for project_id in project_ids:
        sync_log = None
        log_id = None
        log_status = "ERROR"
        log_error_msg = None
        rows_synced = 0

        try:
            async with AsyncSessionLocal() as db:
                try:
                    key, api_key = await _get_wb_key(db, project_id)
                except ValueError:
                    logger.debug("FBO supplies sync: project %d has no WB key, skipping", project_id)
                    continue

                # Create sync_log entry
                sync_log = SyncLog(
                    integration_id=key.id,
                    service="wb",
                    sync_type="fbo_supplies",
                    started_at=utcnow(),
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.flush()
                log_id = sync_log.id

                api_client = WBApiClient(api_key, project_id=project_id)
                result = await asyncio.wait_for(
                    sync_fbo_supplies(db, project_id, api_client, key.id),
                    timeout=600,
                )

                rows_synced = result.get("synced", 0)

                # Also sync statuses for active supplies
                status_result = await asyncio.wait_for(
                    sync_fbo_statuses(db, project_id, api_client, key.id),
                    timeout=120,
                )
                logger.info(
                    "FBO statuses sync: project %d — %d updated",
                    project_id,
                    status_result.get("updated", 0),
                )

                log_status = "OK"
                logger.info(
                    "FBO supplies sync: project %d — %d synced (%d new, %d updated)",
                    project_id,
                    result["synced"],
                    result["created"],
                    result["updated"],
                )
                ok += 1

        except TimeoutError:
            log_status = "TIMEOUT"
            log_error_msg = "Timeout 600s exceeded"
            logger.error("FBO supplies sync: project %d — TIMEOUT (600s)", project_id)
            errors += 1
            try:
                await send_alert(f"FBO sync TIMEOUT for project {project_id}")
            except Exception:
                logger.warning("FBO supplies sync: failed to send alert for project %d", project_id)
        except asyncio.CancelledError:
            log_status = "ERROR"
            log_error_msg = "Task cancelled (worker shutdown or restart)"
            logger.warning("FBO supplies sync: project %d — CANCELLED", project_id)
            errors += 1
        except Exception as e:
            log_status = "ERROR"
            log_error_msg = str(e)[:500]
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
        finally:
            # GUARANTEED: update sync_log — never leave RUNNING
            if log_id is not None:
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
                    logger.error("Failed to update sync_log for project %d: %s", project_id, log_err)

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

                api_client = WBApiClient(api_key, project_id=project_id)
                result = await enrich_fbo_supplies(db, project_id, api_client, max_calls=300)

                logger.info(
                    "FBO enrich: project %d — %d enriched, %d errors",
                    project_id,
                    result["enriched"],
                    result["errors"],
                )

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "FBO enrich: project %d failed — %s",
                project_id,
                str(e),
                exc_info=True,
            )


async def refresh_all_projects_assemblies_from_fbo():
    """Авто-рефреш состава активных сборок из привязанных FBO-поставок (все проекты).

    Зеркалит ручную кнопку «Из FBO» в фоне: без неё «Расхождение наполнения» с
    заявками ФФ обновлялось только при ручном клике в заявку. Один WB-клиент на
    проект. Called by APScheduler every 1 hour.
    """
    logger.info("Assembly FBO auto-refresh: starting for all projects")
    project_ids = await get_sync_project_ids()

    if not project_ids:
        logger.info("Assembly FBO auto-refresh: no projects with WB keys, skipping")
        return

    from backend.integrations.wb_api import WBApiClient
    from backend.services.assembly.analytics import refresh_active_assemblies_from_fbo
    from backend.services.integrations_service import _get_wb_key

    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                try:
                    _key, api_key = await _get_wb_key(db, project_id)
                except ValueError:
                    logger.debug("Assembly FBO auto-refresh: project %d has no WB key, skipping", project_id)
                    continue

                api_client = WBApiClient(api_key, project_id=project_id)
                # 1800s: троттлинг WB (~22 c/поставку) × кап 60 поставок ≈ 22 мин.
                result = await asyncio.wait_for(
                    refresh_active_assemblies_from_fbo(db, project_id, api_client),
                    timeout=1800,
                )
                logger.info(
                    "Assembly FBO auto-refresh: project %d — %d supplies, %d processed, %d refreshed, %d errors",
                    project_id,
                    result["supplies"],
                    result["processed"],
                    result["refreshed"],
                    result["errors"],
                )

        except TimeoutError:
            logger.error("Assembly FBO auto-refresh: project %d — TIMEOUT (600s)", project_id)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                "Assembly FBO auto-refresh: project %d failed — %s",
                project_id,
                str(e),
                exc_info=True,
            )
