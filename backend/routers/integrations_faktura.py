"""
Router: /integrations/faktura — manual trigger + status for the ВБ Банк statement sync.

Thin HTTP layer; logic in services/faktura_service.py. The statement also syncs
automatically 4×/day via the scheduler — these endpoints are for on-demand runs
and surfacing last-sync status on the import page.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import IntegrationKey, Project, SyncLog
from backend.project_context import get_current_project
from backend.schemas import FakturaStatusSchema, SyncLogSchema
from backend.services.faktura_service import DEFAULT_LOOKBACK_DAYS, sync_faktura_statement
from backend.utils.rate_limit import rate_limit_write

router = APIRouter()


class FakturaSyncRequest(BaseModel):
    lookback_days: int = Field(default=DEFAULT_LOOKBACK_DAYS, ge=1, le=370)


@router.post(
    "/integrations/faktura/sync",
    response_model=SyncLogSchema,
    dependencies=[Depends(rate_limit_write)],
)
async def trigger_faktura_sync(
    payload: FakturaSyncRequest | None = None,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> SyncLog:
    """Run the Faktura statement sync now (rolling window). Returns the resulting SyncLog.

    The run itself reports outcome via SyncLog.status (OK/ERROR), 200 either way —
    same contract as the manual import (ImportLogSchema). Missing/invalid creds → 400.
    """
    lookback = (payload or FakturaSyncRequest()).lookback_days
    try:
        return await sync_faktura_statement(db, project.id, lookback_days=lookback)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


@router.get("/integrations/faktura/status", response_model=FakturaStatusSchema)
async def faktura_status(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> FakturaStatusSchema:
    """Whether Faktura creds exist for the project + the last statement sync result."""
    key = (
        await db.execute(
            select(IntegrationKey).where(
                IntegrationKey.project_id == project.id,
                IntegrationKey.service == "faktura",
                IntegrationKey.is_deleted.is_(False),
            )
        )
    ).scalar_one_or_none()

    last = None
    if key is not None:
        last = (
            await db.execute(
                select(SyncLog)
                .where(SyncLog.integration_id == key.id, SyncLog.service == "faktura")
                .order_by(SyncLog.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    return FakturaStatusSchema(
        configured=bool(key is not None and key.is_active),
        login=(key.config or {}).get("login") if key else None,
        last_sync_at=key.last_sync_at if key else None,
        last_run=SyncLogSchema.model_validate(last) if last is not None else None,
    )
