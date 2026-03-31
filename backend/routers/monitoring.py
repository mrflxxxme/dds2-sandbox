"""
Router: /monitoring — sync health dashboard, scheduler status.
Thin HTTP layer — logic in services/monitoring_service.py.
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.monitoring import (
    MonitoringOverview,
    SchedulerJobInfo,
    SchedulerStatus,
    SyncLogEntry,
    SyncTypeStatus,
)
from backend.services import monitoring_service

logger = logging.getLogger("dds.monitoring")

router = APIRouter(prefix="/monitoring")


@router.get("/overview", response_model=MonitoringOverview)
async def get_overview(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get monitoring overview: sync health + scheduler status."""
    overview = await monitoring_service.get_sync_overview(db, project.id)

    # Get scheduler info — try in-memory first (works in worker),
    # fall back to DB-based detection (works in api container)
    from backend.scheduler import get_scheduler_info

    sched_info = get_scheduler_info()

    # If in-memory scheduler is not available (api container),
    # infer status from recent sync_log activity
    if not sched_info["running"] and overview["total_syncs_24h"] > 0:
        sched_info = {"running": True, "jobs": []}
        logger.debug("Scheduler detected as running via sync_log activity (api container)")

    return MonitoringOverview(
        sync_types=[SyncTypeStatus(**st) for st in overview["sync_types"]],
        scheduler=SchedulerStatus(
            running=sched_info["running"],
            jobs=[SchedulerJobInfo(**j, is_active=True) for j in sched_info["jobs"]],
        ),
        total_syncs_24h=overview["total_syncs_24h"],
        total_errors_24h=overview["total_errors_24h"],
    )


@router.get("/sync_log", response_model=list[SyncLogEntry])
async def get_sync_log(
    service: str | None = Query(None),
    sync_type: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get filtered sync log with duration calculation."""
    return await monitoring_service.get_sync_log_filtered(
        db,
        project.id,
        service,
        sync_type,
        status,
        limit,
        offset,
    )
