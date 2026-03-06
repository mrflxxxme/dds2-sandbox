"""
Router: /integrations — manage external API keys and sync data.
Thin HTTP layer — all business logic is in services/integrations_service.py.
"""

from datetime import date
from typing import Optional

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.project_context import get_current_project
from backend.models import Project
from backend.schemas import (
    IntegrationKeySchema, SyncLogSchema, DeleteResponse,
)
from backend.services import integrations_service

router = APIRouter(prefix="/integrations")


# ─── Integration Keys CRUD ───────────────────────────────────────────────────


class AddKeyRequest(BaseModel):
    service: str
    api_key: str
    label: Optional[str] = None


@router.get("/keys", response_model=list[IntegrationKeySchema])
async def list_keys(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List all integration keys for the current project (masked)."""
    return await integrations_service.list_keys(db, project.id)


@router.post("/keys", response_model=IntegrationKeySchema)
async def add_key(
    body: AddKeyRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Add a new integration API key (encrypted) for the current project."""
    try:
        return await integrations_service.add_key(
            db, project.id, body.service, body.api_key, body.label,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/keys/{key_id}", response_model=DeleteResponse)
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


@router.post("/wb/sync", response_model=SyncLogSchema)
async def sync_wb_sales(
    sync_type: str = Query("sales", enum=["sales", "orders", "finance"]),
    date_from: Optional[date] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Sync data from Wildberries API."""
    try:
        return await integrations_service.sync_wb_sales(
            db, project.id, sync_type, date_from,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/wb/sync_nomenclature", response_model=SyncLogSchema)
async def sync_wb_nomenclature(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Sync nomenclature from Wildberries Content API."""
    try:
        return await integrations_service.sync_wb_nomenclature(db, project.id)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ─── Sync Log ────────────────────────────────────────────────────────────────


@router.get("/sync_log", response_model=list[SyncLogSchema])
async def get_sync_log(
    service: Optional[str] = Query(None),
    limit: int = Query(20),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get sync history for the current project."""
    return await integrations_service.get_sync_log(db, project.id, service, limit)
