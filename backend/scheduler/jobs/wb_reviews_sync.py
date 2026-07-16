# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Scheduler job: sync WB customer feedbacks (отзывы) для всех проектов.

Ночной прогон. Первый проход для проекта (пустое зеркало) делает full_backfill —
тянет и архив WB, чтобы собрать историю. Дальше — только активные отзывы.
Каждый проект — в своей сессии; sync_log финализируется гарантированно.
"""

import asyncio
import logging

from sqlalchemy import func, select, update

from backend.database import AsyncSessionLocal
from backend.models import WBFeedback
from backend.scheduler.helpers import get_sync_project_ids
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler")


async def sync_all_projects_wb_feedbacks():
    """Пройти все проекты с активным WB-ключом и синхронизировать отзывы."""
    logger.info("WB feedbacks sync: starting for all projects")
    project_ids = await get_sync_project_ids()

    if not project_ids:
        logger.info("WB feedbacks sync: no projects with WB keys, skipping")
        return

    from backend.models import SyncLog
    from backend.services.integrations_service import _get_wb_key
    from backend.services.wb_reviews_sync import sync_project_feedbacks

    ok = 0
    errors = 0

    for project_id in project_ids:
        log_id = None
        log_status = "ERROR"
        log_error: str | None = None
        rows_fetched = 0
        rows_inserted = 0
        try:
            async with AsyncSessionLocal() as db:
                try:
                    key, api_key = await _get_wb_key(db, project_id)
                except ValueError:
                    logger.debug("WB feedbacks sync: project %d has no WB key, skipping", project_id)
                    continue

                sync_log = SyncLog(
                    integration_id=key.id,
                    service="wb",
                    sync_type="feedbacks",
                    started_at=utcnow(),
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.flush()
                log_id = sync_log.id
                await db.commit()

                # Первый прогон для проекта (пустое зеркало) → full backfill (+архив)
                existing = await db.scalar(
                    select(func.count(WBFeedback.id)).where(WBFeedback.project_id == project_id)
                )
                full_backfill = not existing

            # Синк — в отдельной сессии (сервис коммитит успех сам)
            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(
                    sync_project_feedbacks(db, project_id, api_key, full_backfill=full_backfill),
                    timeout=600,
                )

            log_status = "OK"
            rows_fetched = int(result.get("rows_fetched", 0))
            rows_inserted = int(result.get("rows_upserted", 0))
            # per-project INFO пишет сам сервис (sync_project_feedbacks) — здесь не дублируем
            ok += 1

        except asyncio.CancelledError:
            log_error = "Task cancelled (worker shutdown or restart)"
            raise
        except TimeoutError:
            log_error = "Timeout 600s exceeded"
            logger.error("WB feedbacks sync: project %d — TIMEOUT (600s)", project_id)
            errors += 1
        except Exception as e:
            log_error = str(e)[:1000]
            logger.error("WB feedbacks sync: project %d failed — %s", project_id, str(e), exc_info=True)
            errors += 1
        finally:
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
                    logger.error("WB feedbacks sync: failed to update sync_log %s — %s", log_id, log_err)

    logger.info("WB feedbacks sync: done — %d ok, %d errors", ok, errors)
