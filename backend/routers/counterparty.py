"""
Router: /counterparties — Counterparty CRUD + documents.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.config import settings
from backend.database import get_db
from backend.models import Project, User
from backend.project_context import get_current_project
from backend.schemas.counterparty import (
    ALLOWED_DOC_TYPES,
    CounterpartyCreate,
    CounterpartyDetail,
    CounterpartyDocumentResponse,
    CounterpartyFilter,
    CounterpartyListItem,
    CounterpartyListResponse,
    CounterpartyUpdate,
)
from backend.services.counterparty_service import (
    CounterpartyConflictError,
    CounterpartyService,
)
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/counterparties", tags=["Counterparties"])


# Upload: whitelist of MIME prefixes considered safe for counterparty docs.
_ALLOWED_MIME_PREFIXES = (
    "application/pdf",
    "image/",
    "application/msword",
    "application/vnd.openxmlformats-officedocument",
    "application/vnd.ms-excel",
    "text/plain",
    "text/csv",
    "application/zip",
)

# File size cap for counterparty documents (separate from global MAX_UPLOAD_SIZE_MB).
_COUNTERPARTY_DOC_MAX_MB = 20


# ─── List / Detail ───────────────────────────────────────────────────────────


@router.get("", response_model=CounterpartyListResponse)
async def list_counterparties(
    type: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
    active_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List counterparties with filters."""
    filters = CounterpartyFilter(
        type=type,
        q=q,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )
    service = CounterpartyService(db)
    items, total = await service.list(project_id=project.id, filters=filters)
    return CounterpartyListResponse(
        items=[CounterpartyListItem.model_validate(cp) for cp in items],
        total=total,
    )


@router.get("/{counterparty_id}", response_model=CounterpartyDetail)
async def get_counterparty(
    counterparty_id: int,
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get counterparty detail with stats over a period (defaults to last 90 days)."""
    if date_to is None:
        date_to = date.today()
    if date_from is None:
        date_from = date_to - timedelta(days=90)

    service = CounterpartyService(db)
    detail = await service.get(
        counterparty_id=counterparty_id,
        project_id=project.id,
        date_from=date_from,
        date_to=date_to,
    )
    return detail


# ─── Create / Update / Delete ────────────────────────────────────────────────


@router.post(
    "",
    response_model=CounterpartyListItem,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def create_counterparty(
    body: CounterpartyCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Create a new counterparty."""
    service = CounterpartyService(db)
    try:
        cp = await service.create(body, project_id=project.id)
    except CounterpartyConflictError as e:
        raise HTTPException(
            status_code=409,
            detail={"code": "inn_conflict", "message": str(e)},
        ) from None
    return CounterpartyListItem.model_validate(cp)


@router.patch(
    "/{counterparty_id}",
    response_model=CounterpartyListItem,
    dependencies=[Depends(rate_limit_write)],
)
async def update_counterparty(
    counterparty_id: int,
    body: CounterpartyUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Partial update of a counterparty."""
    service = CounterpartyService(db)
    try:
        cp = await service.update(
            counterparty_id=counterparty_id,
            data=body,
            project_id=project.id,
        )
    except CounterpartyConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return CounterpartyListItem.model_validate(cp)


@router.delete(
    "/{counterparty_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_write)],
)
async def delete_counterparty(
    counterparty_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Archive (soft-delete) a counterparty."""
    service = CounterpartyService(db)
    deleted = await service.soft_delete(counterparty_id, project_id=project.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Counterparty not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─── Documents ───────────────────────────────────────────────────────────────


@router.post(
    "/{counterparty_id}/documents",
    response_model=CounterpartyDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def upload_counterparty_document(
    counterparty_id: int,
    file: UploadFile = File(...),
    doc_type: str = Form("OTHER"),
    user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Upload a document (MinIO) attached to a counterparty."""
    if doc_type not in ALLOWED_DOC_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"doc_type must be one of: {ALLOWED_DOC_TYPES}",
        )

    # MIME whitelist — reject executables etc. upfront.
    content_type = (file.content_type or "").lower()
    filename_lower = (file.filename or "").lower()
    # Reject obvious executables by extension (belt-and-braces vs forged MIME)
    BAD_EXT = (".exe", ".bat", ".cmd", ".dll", ".sh", ".msi", ".ps1", ".com")
    if filename_lower.endswith(BAD_EXT) or not any(content_type.startswith(p) for p in _ALLOWED_MIME_PREFIXES):
        raise HTTPException(
            status_code=415,
            detail="Тип файла не поддерживается. Разрешены PDF / Office / изображения.",
        )

    # Size enforcement — per-counterparty cap (20MB) stricter than global MAX_UPLOAD_SIZE_MB
    data = await file.read()
    max_bytes = (
        min(
            _COUNTERPARTY_DOC_MAX_MB,
            settings.MAX_UPLOAD_SIZE_MB,
        )
        * 1024
        * 1024
    )
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Файл слишком большой. Максимум: {_COUNTERPARTY_DOC_MAX_MB} МБ",
        )

    service = CounterpartyService(db)
    doc = await service.upload_document(
        counterparty_id=counterparty_id,
        project_id=project.id,
        file_data=data,
        filename=file.filename or "unnamed",
        doc_type=doc_type,
        mime_type=file.content_type,
        uploaded_by_user_id=user.id,
    )
    return CounterpartyDocumentResponse.model_validate(doc)


@router.get(
    "/{counterparty_id}/documents",
    response_model=list[CounterpartyDocumentResponse],
)
async def list_counterparty_documents(
    counterparty_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List all documents attached to a counterparty."""
    service = CounterpartyService(db)
    docs = await service.list_documents(counterparty_id=counterparty_id, project_id=project.id)
    return [CounterpartyDocumentResponse.model_validate(d) for d in docs]


@router.delete(
    "/{counterparty_id}/documents/{doc_id}",
    dependencies=[Depends(rate_limit_write)],
)
async def delete_counterparty_document(
    counterparty_id: int,
    doc_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a document and best-effort remove from MinIO."""
    service = CounterpartyService(db)
    ok = await service.delete_document(
        doc_id=doc_id,
        counterparty_id=counterparty_id,
        project_id=project.id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted": True, "id": doc_id}
