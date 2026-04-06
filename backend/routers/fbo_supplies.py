"""
Router: /warehouse/fbo-supplies — FBO supply sync, list, items, link/unlink.
"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal, get_db
from backend.integrations.wb_api import WBApiClient
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.wb_fbo import (
    FboSupplyLinkRequest,
    FboSyncResultSchema,
    WbFboSupplyItemSchema,
    WbFboSupplyListResponse,
    WbFboSupplySchema,
)
from backend.services import fbo_supply_service
from backend.services.integrations_service import _get_wb_key

logger = logging.getLogger("dds.routers.fbo_supplies")
router = APIRouter(prefix="/warehouse/fbo-supplies", tags=["FBO Supplies"])

# Store background task references to prevent GC
_background_tasks: set = set()


# ─── List supplies ──────────────────────────────────────────────────────────


@router.get("", response_model=WbFboSupplyListResponse)
async def list_fbo_supplies(
    search: str | None = Query(None, description="Search by ID or name"),
    status: str | None = Query(None, description="Filter by WB status"),
    warehouse: str | None = Query(None, description="Filter by warehouse name"),
    date_from: date | None = Query(None, description="Date from"),
    date_to: date | None = Query(None, description="Date to"),
    sort_by: str = Query("created_at_wb", description="Sort field"),
    sort_order: str = Query("desc", description="Sort order: asc/desc"),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    exclude_with_assembly: bool = Query(False, description="Exclude supplies with active assembly requests"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List FBO supplies with filtering, search, sorting, and pagination."""
    supplies, total = await fbo_supply_service.list_fbo_supplies(
        db,
        project.id,
        search=search,
        status=status,
        warehouse=warehouse,
        date_from=date_from,
        date_to=date_to,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
        exclude_with_assembly=exclude_with_assembly,
    )
    return WbFboSupplyListResponse(
        items=[WbFboSupplySchema(**s) for s in supplies],
        total=total,
    )


# ─── Warehouse names (for filter dropdown) ─────────────────────────────────


@router.get("/warehouses", response_model=list[str])
async def list_fbo_warehouses(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get unique warehouse names for filter dropdown."""
    return await fbo_supply_service.list_warehouses(db, project.id)


# ─── Supply items ──────────────────────────────────────────────────────────


@router.get("/{supply_id}/items")
async def get_fbo_supply_items(
    supply_id: int,
    refresh: bool = Query(False),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get items (orders) for a specific FBO supply. Lazy-loads from WB API if needed."""
    try:
        # Build API client for lazy-load
        try:
            key, api_key = await _get_wb_key(db, project.id)
            api_client = WBApiClient(api_key, project_id=project.id)
        except ValueError:
            api_client = None

        items = await fbo_supply_service.get_fbo_supply_items(
            db,
            project.id,
            supply_id,
            api_client=api_client,
            force_refresh=refresh,
        )
        return [WbFboSupplyItemSchema.model_validate(i) for i in items]
    except ValueError as e:
        raise HTTPException(404, str(e)) from None


# ─── Sync: full ────────────────────────────────────────────────────────────


@router.post("/sync", response_model=FboSyncResultSchema)
async def sync_fbo_supplies(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Fast sync: load supply list (1 API call), then enrich details in background."""
    try:
        key, api_key = await _get_wb_key(db, project.id)
        api_client = WBApiClient(api_key, project_id=project.id)

        # Fast: list + upsert (1-2 sec)
        result = await fbo_supply_service.sync_fbo_supplies(
            db,
            project.id,
            api_client,
            key.id,
        )

        # Background: enrich with warehouse/qty details (rate-limited)
        # Use asyncio.create_task to avoid cancellation when HTTP connection closes
        import asyncio

        task = asyncio.create_task(_enrich_in_background(project.id, api_key))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        return FboSyncResultSchema(**result)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        raise HTTPException(500, f"Sync failed: {str(e)[:200]}") from e


async def _enrich_in_background(project_id: int, api_key: str):
    """Run enrichment in a separate DB session (detached from HTTP request)."""
    try:
        api_client = WBApiClient(api_key, project_id=project_id)
        async with AsyncSessionLocal() as db:
            result = await fbo_supply_service.enrich_fbo_supplies(
                db,
                project_id,
                api_client,
                max_calls=300,
            )
            logger.info(
                "FBO enrich background: project %d — %d enriched, %d errors",
                project_id,
                result["enriched"],
                result["errors"],
            )
    except Exception as e:
        logger.error("FBO enrich background failed: project %d — %s", project_id, e)


# ─── Sync: statuses only ──────────────────────────────────────────────────


@router.post("/sync-statuses", response_model=FboSyncResultSchema)
async def sync_fbo_statuses(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Sync only statuses of active (non-final) supplies."""
    try:
        key, api_key = await _get_wb_key(db, project.id)
        api_client = WBApiClient(api_key, project_id=project.id)
        result = await fbo_supply_service.sync_fbo_statuses(
            db,
            project.id,
            api_client,
            key.id,
        )
        return FboSyncResultSchema(**result)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except Exception as e:
        raise HTTPException(500, f"Status sync failed: {str(e)[:200]}") from e


# ─── Link supply ↔ shipment ───────────────────────────────────────────────


@router.post("/{supply_id}/link")
async def link_supply(
    supply_id: int,
    payload: FboSupplyLinkRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Link FBO supply to an OutboundShipment."""
    try:
        supply = await fbo_supply_service.link_supply_to_shipment(
            db,
            project.id,
            supply_id,
            payload.outbound_shipment_id,
        )
        return WbFboSupplySchema.model_validate(supply)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.delete("/{supply_id}/link")
async def unlink_supply(
    supply_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Unlink FBO supply from OutboundShipment."""
    try:
        supply = await fbo_supply_service.unlink_supply_from_shipment(
            db,
            project.id,
            supply_id,
        )
        return WbFboSupplySchema.model_validate(supply)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
