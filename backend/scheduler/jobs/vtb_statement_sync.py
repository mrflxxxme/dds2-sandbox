"""
Scheduler job: auto-sync the VTB «Интеграционный Банк-Клиент» statement for all projects.

Runs a few×/day (see backend/scheduler/__init__.py). For each project with active VTB
ИБК credentials, pulls a rolling window for every account and feeds operations into the
shared ETL pipeline (dedup by txn_id → idempotent overlap). Statement-only; payments
(PayDocRu) are out of scope. Zeркало faktura_statement_sync.
"""

import asyncio
import logging

from backend.database import AsyncSessionLocal

logger = logging.getLogger("dds.scheduler")


async def _get_vtb_project_ids() -> list[int]:
    """Project IDs that have an active, per-project VTB ИБК key."""
    from sqlalchemy import select

    from backend.models import IntegrationKey

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(IntegrationKey.project_id)
            .where(
                IntegrationKey.service == "vtb_ibk",
                IntegrationKey.is_active.is_(True),
                IntegrationKey.is_deleted.is_(False),
                IntegrationKey.project_id.isnot(None),
            )
            .distinct()
        )
        return [r[0] for r in result if r[0]]


async def sync_all_projects_vtb_statements():
    """Iterate all projects with VTB ИБК creds and sync the bank statement."""
    logger.info("VTB statement sync: starting for all projects")
    project_ids = await _get_vtb_project_ids()

    if not project_ids:
        logger.info("VTB statement sync: no projects with VTB ИБК keys, skipping")
        return

    from backend.services.vtb_ibk_service import sync_vtb_statement

    ok = 0
    errors = 0
    for project_id in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                log = await asyncio.wait_for(sync_vtb_statement(db, project_id), timeout=900)
            if log.status == "OK":
                ok += 1
            else:
                errors += 1
                logger.warning(
                    "VTB statement sync: project %d returned %s — %s",
                    project_id,
                    log.status,
                    (log.error_msg or "")[:200],
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            errors += 1
            logger.error("VTB statement sync: project %d failed — %s", project_id, e, exc_info=True)

    logger.info("VTB statement sync: done — %d ok, %d errors", ok, errors)
