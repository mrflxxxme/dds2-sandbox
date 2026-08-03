"""
Router: /warehouse/fbo-supplies — FBO supply sync, list, items, link/unlink.
"""

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import AsyncSessionLocal, get_db
from backend.integrations.wb_api import WBApiClient
from backend.models import Project, User
from backend.project_context import get_current_project
from backend.rbac import require_page
from backend.schemas.wb_fbo import (
    FboArchiveRequest,
    FboAuditListResponse,
    FboAuditResponse,
    FboAuditRevertResponse,
    FboExcessRequest,
    FboExcessResponse,
    FboReassignCandidatesResponse,
    FboReassignRequest,
    FboReassignResponse,
    FboReturnRequest,
    FboReturnResponse,
    FboSyncResultSchema,
    WbFboSupplyItemSchema,
    WbFboSupplyListResponse,
    WbFboSupplySchema,
)
from backend.services import fbo_supply_service
from backend.services.integrations_service import _get_wb_key
from backend.utils.rate_limit import rate_limit_write

logger = logging.getLogger("dds.routers.fbo_supplies")
router = APIRouter(
    prefix="/warehouse/fbo-supplies",
    tags=["FBO Supplies"],
    dependencies=[Depends(require_page("fbo"))],
)

# Store background task references to prevent GC
_background_tasks: set = set()


# ─── List supplies ──────────────────────────────────────────────────────────


@router.get("", response_model=WbFboSupplyListResponse)
async def list_fbo_supplies(
    search: str | None = Query(None, description="Search by ID or name"),
    status: str | None = Query(None, description="Filter by WB status"),
    warehouse: str | None = Query(None, description="Filter by warehouse name"),
    source_warehouse_id: int | None = Query(None, description="Filter by our pickup warehouse id"),
    date_from: date | None = Query(None, description="Date from"),
    date_to: date | None = Query(None, description="Date to"),
    sort_by: str = Query("created_at_wb", description="Sort field"),
    sort_order: str = Query("desc", description="Sort order: asc/desc"),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    exclude_with_assembly: bool = Query(False, description="Exclude supplies with active assembly requests"),
    exclude_assembly_warehouse_id: int | None = Query(
        None,
        description="С exclude_with_assembly: исключать только поставки, занятые сборкой ЭТОГО склада-источника "
        "(совместная поставка — оставляем доступной для другого ФФ)",
    ),
    without_assembly: bool = Query(False, description="Only supplies without any assembly request"),
    partial_only: bool = Query(False, description="Only supplies with partial acceptance (accepted_qty < total_qty)"),
    excess_only: bool = Query(
        False, description="Only supplies with excess (accepted_qty > total_qty), not yet written off"
    ),
    archived_view: bool = Query(
        False, description="Show ONLY archived supplies (separate page); default — only active"
    ),
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
        source_warehouse_id=source_warehouse_id,
        date_from=date_from,
        date_to=date_to,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
        exclude_with_assembly=exclude_with_assembly,
        exclude_assembly_warehouse_id=exclude_assembly_warehouse_id,
        without_assembly=without_assembly,
        partial_only=partial_only,
        excess_only=excess_only,
        accounting_started_at=project.accounting_started_at,
        archived_view=archived_view,
    )
    return WbFboSupplyListResponse(
        items=[WbFboSupplySchema(**s) for s in supplies],
        total=total,
    )


# ─── Summary (counts for dashboard cards) ──────────────────────────────────


@router.get("/summary")
async def get_fbo_supplies_summary(
    date_from: date | None = Query(None, description="Date from"),
    date_to: date | None = Query(None, description="Date to"),
    warehouse: str | None = Query(None, description="Filter by warehouse name"),
    status: str | None = Query(None, description="Filter by WB status (comma-separated)"),
    archived_view: bool = Query(False, description="Show counters for archived bucket"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> dict[str, int]:
    """
    Summary counts: total, accepted, accepted_without_assembly,
    accepted_partial. Mirrors the same global filters as the list view
    (period, archive, warehouse, status) so cards stay consistent.
    """
    return await fbo_supply_service.get_fbo_summary(
        db,
        project.id,
        accounting_started_at=project.accounting_started_at,
        date_from=date_from,
        date_to=date_to,
        warehouse=warehouse,
        status=status,
        archived_view=archived_view,
    )


@router.get("/partial-summary")
async def get_partial_summary(
    date_from: date | None = Query(None, description="Date from"),
    date_to: date | None = Query(None, description="Date to"),
    warehouse: str | None = Query(None, description="Filter by warehouse name"),
    status: str | None = Query(None, description="Filter by WB status (comma-separated)"),
    archived_view: bool = Query(False, description="Count supplies in archive bucket instead of active"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Недоприёмка dashboard: aggregate qty buckets + per-barcode breakdown.
    Shown as a mini-panel when user enables the 'С недоприёмкой' filter.
    Mirrors the list-view filters (period/warehouse/status) so the cards
    don't show всю историю когда видимый список — окно за 30 дней.
    """  # noqa: RUF002 — mixed cyrillic/latin is intentional (product terms)
    return await fbo_supply_service.get_partial_acceptance_summary(
        db,
        project.id,
        accounting_started_at=project.accounting_started_at,
        date_from=date_from,
        date_to=date_to,
        warehouse=warehouse,
        status=status,
        archived_view=archived_view,
    )


# ─── Warehouse names (for filter dropdown) ─────────────────────────────────


@router.get("/warehouses", response_model=list[str])
async def list_fbo_warehouses(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get unique warehouse names for filter dropdown."""
    return await fbo_supply_service.list_warehouses(db, project.id)


# ─── Archive override ─────────────────────────────────────────────────────


@router.post(
    "/archive/restore-linked",
    dependencies=[Depends(rate_limit_write)],
)
async def restore_linked_from_archive(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, int]:
    """
    Bulk-restore archived supplies that are linked to our AssemblyRequest
    or OutboundShipment. Useful on the archive page when a user wants to
    recover all «our» supplies at once.
    """
    restored = await fbo_supply_service.restore_linked_from_archive(
        db,
        project.id,
        project.accounting_started_at,
        user_id=user.id,
    )
    return {"restored": restored}


@router.patch(
    "/{supply_id}/archive",
    response_model=WbFboSupplySchema,
    dependencies=[Depends(rate_limit_write)],
)
async def patch_fbo_archive(
    supply_id: int,
    payload: FboArchiveRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Override the auto archive rule for a supply.
    is_archived=True → move to archive; False → restore; null → reset to auto.
    """
    try:
        supply = await fbo_supply_service.set_archive_flag(
            db,
            project.id,
            supply_id,
            payload.is_archived,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
    return WbFboSupplySchema.model_validate(supply)


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


@router.post("/sync", response_model=FboSyncResultSchema, dependencies=[Depends(rate_limit_write)])
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


@router.post("/sync-statuses", response_model=FboSyncResultSchema, dependencies=[Depends(rate_limit_write)])
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


# ─── Return: handle unaccepted qty ─────────────────────────────────────────


@router.post(
    "/{supply_id}/return",
    response_model=FboReturnResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def create_fbo_return(
    supply_id: int,
    payload: FboReturnRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Оформить возврат/утилизацию по непринятому qty. Каждая строка items несёт
    свой return_type (GOODS / UTILIZED / DEFECT), что позволяет в одном
    вызове одну часть вернуть на склад, а другую — списать.
    """  # noqa: RUF002 — cyrillic product terms
    try:
        result = await fbo_supply_service.process_fbo_return(
            db,
            project.id,
            supply_id,
            items=[item.model_dump() for item in payload.items],
            warehouse_id=payload.warehouse_id,
            comment=payload.comment,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None

    return FboReturnResponse(**result)


# ─── Excess: списать излишек, который WB принял сверх отправленного ────────


@router.post(
    "/{supply_id}/excess",
    response_model=FboExcessResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def create_fbo_excess(
    supply_id: int,
    payload: FboExcessRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Списать со склада излишек, который WB принял сверх отправленного.
    Создаёт OutboundShipment на указанный склад на сумму per-barcode delta
    (accepted - quantity). Помечает supply.excess_processed_at.
    """  # noqa: RUF002 — cyrillic product terms
    try:
        result = await fbo_supply_service.process_fbo_excess(
            db,
            project.id,
            supply_id,
            items=[item.model_dump() for item in payload.items],
            warehouse_id=payload.warehouse_id,
            comment=payload.comment,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return FboExcessResponse(**result)


# ─── Reassignment (link under-delivery micro-supply to parent) ────────────


@router.get(
    "/{supply_id}/reassign/candidates",
    response_model=FboReassignCandidatesResponse,
)
async def get_reassign_candidates(
    supply_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Список поставок с недоприёмкой по тем же артикулам, что и в текущей поставке.
    Используется на UI для привязки «микро-доприёмки» к родительской поставке,
    где WB частично принял товар.
    """  # noqa: RUF002 — cyrillic product terms
    try:
        result = await fbo_supply_service.get_reassignment_candidates(db, project.id, supply_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
    return FboReassignCandidatesResponse(**result)


@router.post(
    "/{supply_id}/reassign",
    response_model=FboReassignResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def reassign_fbo_supply(
    supply_id: int,
    payload: FboReassignRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Привязать текущую поставку (доприёмку) к поставке с недоприёмкой:
    qty добавляется к target.accepted_qty по совпадающим штрихкодам,
    source уходит в архив с reassigned_to_supply_id = target.id.
    """  # noqa: RUF002 — cyrillic product terms
    try:
        result = await fbo_supply_service.reassign_to_supply(
            db,
            project.id,
            supply_id,
            payload.target_supply_id,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return FboReassignResponse(**result)


# ─── Audit trail (history + undo) ─────────────────────────────────────────


@router.get("/audit", response_model=FboAuditListResponse)
async def list_fbo_audit(
    action: str | None = Query(None, description="Filter by action"),
    supply_wb_id: str | None = Query(None, description="Filter by WB supply id"),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Global audit feed across all supplies — newest first."""
    entries, total = await fbo_supply_service.get_audit_list(
        db,
        project.id,
        action=action,
        supply_wb_id=supply_wb_id,
        limit=limit,
        offset=offset,
    )
    return FboAuditListResponse(entries=entries, total=total)


@router.get("/{supply_id}/audit", response_model=FboAuditResponse)
async def get_fbo_audit(
    supply_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """History of user actions for this supply, newest first."""
    entries = await fbo_supply_service.get_audit_trail(db, project.id, supply_id)
    return FboAuditResponse(supply_id=supply_id, entries=entries)


@router.post(
    "/audit/{audit_id}/revert",
    response_model=FboAuditRevertResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def revert_fbo_audit(
    audit_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Undo a prior reversible action (ARCHIVE/UNARCHIVE/REASSIGN)."""
    try:
        result = await fbo_supply_service.revert_audit_entry(
            db,
            project.id,
            audit_id,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return FboAuditRevertResponse(**result)
