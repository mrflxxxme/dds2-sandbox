"""
Router: /assembly/drafts — Assembly draft CRUD + commit to N AssemblyRequests.

Drafts are persistent NxM distribution plans (RF source warehouses x WB
target warehouses) used by the «Создать сборку» flow on the warehouse
analytics page. Commit turns a balanced draft into one AssemblyRequest per
unique (source_ff, target_wb) pair with non-zero qty, then soft-deletes
the draft.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.assembly_draft import (
    AssemblyDraftCommitResponse,
    AssemblyDraftCreate,
    AssemblyDraftRead,
    AssemblyDraftUpdate,
)
from backend.services import assembly_draft_service
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/assembly/drafts", tags=["Assembly Drafts"])


@router.get("", response_model=list[AssemblyDraftRead])
async def list_drafts(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> list[AssemblyDraftRead]:
    """List non-deleted drafts of current project, newest-updated first."""
    drafts = await assembly_draft_service.list_drafts(db, project.id)
    return [await assembly_draft_service.to_read_model(db, project.id, d) for d in drafts]


@router.post("", response_model=AssemblyDraftRead, dependencies=[Depends(rate_limit_write)])
async def create_draft(
    payload: AssemblyDraftCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """Create a new draft."""
    draft = await assembly_draft_service.create_draft(db, project.id, payload)
    return await assembly_draft_service.to_read_model(db, project.id, draft)


@router.get("/{draft_id}", response_model=AssemblyDraftRead)
async def get_draft(
    draft_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """Get a single draft by id (404 if missing or deleted)."""
    draft = await assembly_draft_service.get_draft(db, project.id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return await assembly_draft_service.to_read_model(db, project.id, draft)


@router.put("/{draft_id}", response_model=AssemblyDraftRead, dependencies=[Depends(rate_limit_write)])
async def update_draft(
    draft_id: int,
    payload: AssemblyDraftUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftRead:
    """Update mutable fields of a draft."""
    draft = await assembly_draft_service.update_draft(db, project.id, draft_id, payload)
    return await assembly_draft_service.to_read_model(db, project.id, draft)


@router.delete("/{draft_id}", status_code=204, dependencies=[Depends(rate_limit_write)])
async def delete_draft(
    draft_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a draft."""
    await assembly_draft_service.delete_draft(db, project.id, draft_id)


@router.post(
    "/{draft_id}/commit",
    response_model=AssemblyDraftCommitResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def commit_draft(
    draft_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> AssemblyDraftCommitResponse:
    """Turn a draft into N AssemblyRequests (one per non-zero pair) and soft-delete it."""
    return await assembly_draft_service.commit_draft(db, project.id, draft_id)
