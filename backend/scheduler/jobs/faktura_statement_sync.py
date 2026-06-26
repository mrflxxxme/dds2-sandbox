"""
Scheduler job: auto-sync the ВБ Банк (Faktura.ru) statement for all projects.

Runs 4×/day — 06:00 / 12:00 / 18:00 / 23:00 MSK (see backend/scheduler/__init__.py).
For each project with active Faktura credentials, pulls a rolling 14-day window of
operations for every account and feeds them into the shared ETL pipeline (dedup by
txn_id → idempotent overlap). Statement-only; payments are signed by a human in the
bank.
"""

import asyncio
import logging

from backend.database import AsyncSessionLocal

logger = logging.getLogger("dds.scheduler")


async def _get_faktura_project_ids() -> list[int]:
    """Project IDs that have an active, per-project Faktura key."""
    from sqlalchemy import select

    from backend.models import IntegrationKey

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(IntegrationKey.project_id)
            .where(
                IntegrationKey.service == "faktura",
                IntegrationKey.is_active.is_(True),
                IntegrationKey.is_deleted.is_(False),
                IntegrationKey.project_id.isnot(None),
            )
            .distinct()
        )
        return [r[0] for r in result if r[0]]


async def sync_all_projects_faktura_statements():
    """Iterate all projects with Faktura creds and sync the bank statement."""
    logger.info("Faktura statement sync: starting for all projects")
    project_ids = await _get_faktura_project_ids()

    if not project_ids:
        logger.info("Faktura statement sync: no projects with Faktura keys, skipping")
        return

    from backend.services.faktura_service import sync_faktura_statement

    ok = 0
    errors = 0
    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                log = await asyncio.wait_for(
                    sync_faktura_statement(db, project_id),
                    timeout=600,
                )
            if log.status == "OK":
                ok += 1
            else:
                errors += 1
                logger.warning(
                    "Faktura statement sync: project %d returned %s — %s",
                    project_id,
                    log.status,
                    (log.error_msg or "")[:200],
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            errors += 1
            logger.error("Faktura statement sync: project %d failed — %s", project_id, e, exc_info=True)

    logger.info("Faktura statement sync: done — %d ok, %d errors", ok, errors)
