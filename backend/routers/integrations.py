"""
Router: /integrations — manage external API keys and sync data.
Thin HTTP layer — all business logic is in services/integrations_service.py.
"""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas import (
    DeleteResponse,
    IntegrationKeySchema,
    SyncLogSchema,
)
from backend.services import integrations_service
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/integrations")


# ─── Integration Keys CRUD ───────────────────────────────────────────────────


class AddKeyRequest(BaseModel):
    service: Literal["wb", "wb_advert", "wb_content", "ozon"]
    api_key: str
    label: str | None = None


@router.get("/keys", response_model=list[IntegrationKeySchema])
async def list_keys(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List all integration keys for the current project (masked)."""
    return await integrations_service.list_keys(db, project.id)


@router.post("/keys", response_model=IntegrationKeySchema, dependencies=[Depends(rate_limit_write)])
async def add_key(
    body: AddKeyRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Add a new integration API key (encrypted) for the current project."""
    try:
        return await integrations_service.add_key(
            db,
            project.id,
            body.service,
            body.api_key,
            body.label,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.delete("/keys/{key_id}", response_model=DeleteResponse, dependencies=[Depends(rate_limit_write)])
async def delete_key(
    key_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Delete an integration key (only within the current project)."""
    deleted = await integrations_service.delete_key(db, project.id, key_id)
    if not deleted:
        raise HTTPException(404, "Ключ не найден")
    return {"deleted": True, "id": key_id}


# ─── WB Sync ─────────────────────────────────────────────────────────────────


@router.post("/wb/sync", response_model=SyncLogSchema, dependencies=[Depends(rate_limit_write)])
async def sync_wb_sales(
    sync_type: str = Query("sales", enum=["sales", "orders", "finance"]),
    date_from: date | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Sync data from Wildberries API."""
    try:
        return await integrations_service.sync_wb_sales(
            db,
            project.id,
            sync_type,
            date_from,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/wb/sync_nomenclature", response_model=SyncLogSchema, dependencies=[Depends(rate_limit_write)])
async def sync_wb_nomenclature(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Sync nomenclature from Wildberries Content API."""
    try:
        return await integrations_service.sync_wb_nomenclature(db, project.id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# ─── Sync Log ────────────────────────────────────────────────────────────────


@router.get("/sync_log", response_model=list[SyncLogSchema])
async def get_sync_log(
    service: str | None = Query(None),
    limit: int = Query(20),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get sync history for the current project."""
    return await integrations_service.get_sync_log(db, project.id, service, limit)
