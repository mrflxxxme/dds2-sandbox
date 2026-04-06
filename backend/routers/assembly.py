"""
Router: /warehouse/assembly — Assembly request CRUD + workflow transitions.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.assembly import (
    AssemblyHistoryResponse,
    AssemblyListResponse,
    AssemblyRequestCreate,
    AssemblyRequestResponse,
    AssemblyRequestUpdate,
    AssignVehicle,
    AssignVehicleBulk,
    LogisticsAnalyticsResponse,
    RefreshFromFboResponse,
    ShipBulk,
)
from backend.services import assembly_service
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/warehouse/assembly", tags=["Assembly"])


# --- List -------------------------------------------------------------------


@router.get("", response_model=AssemblyListResponse)
async def list_assembly_requests(
    warehouse_id: int | None = Query(None),
    status: str | None = Query(None),
    search: str | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    brand: str | None = Query(None),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List assembly requests with filters and pagination."""
    items, total = await assembly_service.list_assembly_requests(
        db,
        project.id,
        warehouse_id=warehouse_id,
        status=status,
        search=search,
        date_from=date_from,
        date_to=date_to,
        brand=brand,
        limit=limit,
        offset=offset,
    )
    response_items = []
    for req in items:
        resp = await assembly_service._build_response(db, req)
        response_items.append(AssemblyRequestResponse.model_validate(resp))
    return AssemblyListResponse(items=response_items, total=total)


# --- WB warehouses ----------------------------------------------------------


@router.get("/wb-warehouses", response_model=list[str])
async def list_wb_warehouses(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get distinct WB warehouse names from assembly history and FBO supplies."""
    return await assembly_service.list_wb_warehouses(db, project.id)


# --- Shipment analytics ----------------------------------------------------


@router.get("/shipments/analytics", response_model=LogisticsAnalyticsResponse)
async def get_logistics_analytics(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    warehouse_ids: str | None = Query(None, description="Comma-separated warehouse IDs"),
    brands: str | None = Query(None, description="Comma-separated brand names"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Logistics cost analytics: summary, by destination, by route."""
    wh_ids = [int(x) for x in warehouse_ids.split(",") if x.strip()] if warehouse_ids else None
    brand_list = [x.strip() for x in brands.split(",") if x.strip()] if brands else None
    return await assembly_service.get_logistics_analytics(
        db,
        project.id,
        date_from=date_from,
        date_to=date_to,
        warehouse_ids=wh_ids,
        brands=brand_list,
    )


# --- Create -----------------------------------------------------------------


@router.post("", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def create_assembly_request(
    payload: AssemblyRequestCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Create a new assembly request."""
    try:
        req = await assembly_service.create_assembly_request(db, project.id, payload)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# --- Get by ID --------------------------------------------------------------


@router.get("/{request_id}", response_model=AssemblyRequestResponse)
async def get_assembly_request(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get a single assembly request by ID."""
    req = await assembly_service.get_assembly_request(db, project.id, request_id)
    if not req:
        raise HTTPException(404, "Assembly request not found")
    return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))


# --- Update -----------------------------------------------------------------


@router.put("/{request_id}", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def update_assembly_request(
    request_id: int,
    payload: AssemblyRequestUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Update an assembly request."""
    try:
        req = await assembly_service.update_assembly_request(db, project.id, request_id, payload)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# --- Status transitions -----------------------------------------------------


@router.post("/{request_id}/start", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def start_assembly(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """PENDING -> IN_PROGRESS."""
    try:
        req = await assembly_service.start_assembly(db, project.id, request_id)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{request_id}/ready", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def mark_ready(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """IN_PROGRESS -> READY."""
    try:
        req = await assembly_service.mark_ready(db, project.id, request_id)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post(
    "/{request_id}/assign-vehicle", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)]
)
async def assign_vehicle(
    request_id: int,
    payload: AssignVehicle,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """READY -> VEHICLE_ASSIGNED."""
    try:
        req = await assembly_service.assign_vehicle(db, project.id, request_id, payload)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post(
    "/{request_id}/unassign-vehicle", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)]
)
async def unassign_vehicle(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """VEHICLE_ASSIGNED -> READY. Cancel vehicle assignment."""
    try:
        req = await assembly_service.unassign_vehicle(db, project.id, request_id)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{request_id}/ship", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def ship_request(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """VEHICLE_ASSIGNED -> SHIPPED."""
    try:
        req = await assembly_service.ship_request(db, project.id, request_id)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{request_id}/cancel", response_model=AssemblyRequestResponse, dependencies=[Depends(rate_limit_write)])
async def cancel_request(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Any status -> CANCELLED."""
    try:
        req = await assembly_service.cancel_request(db, project.id, request_id)
        return AssemblyRequestResponse.model_validate(await assembly_service._build_response(db, req))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# --- Bulk operations --------------------------------------------------------


@router.post(
    "/assign-vehicle-bulk", response_model=list[AssemblyRequestResponse], dependencies=[Depends(rate_limit_write)]
)
async def assign_vehicle_bulk(
    payload: AssignVehicleBulk,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Bulk assign vehicle to multiple requests with per-request parameters."""
    try:
        results = []
        for item in payload.items:
            assign_payload = AssignVehicle(
                vehicle_info=payload.vehicle_info,
                vehicle_brand=payload.vehicle_brand,
                driver_phone=payload.driver_phone,
                pickup_date=item.pickup_date,
                pickup_time_slot=item.pickup_time_slot,
                pickup_cost=item.pickup_cost,
                delivery_date=item.delivery_date,
            )
            req = await assembly_service.assign_vehicle(db, project.id, item.request_id, assign_payload)
            results.append(req)
        response = []
        for req in results:
            resp = await assembly_service._build_response(db, req)
            response.append(AssemblyRequestResponse.model_validate(resp))
        return response
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/ship-bulk", response_model=list[AssemblyRequestResponse], dependencies=[Depends(rate_limit_write)])
async def ship_bulk(
    payload: ShipBulk,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Bulk ship multiple requests."""
    try:
        results = await assembly_service.ship_bulk(db, project.id, payload.ids)
        response = []
        for req in results:
            resp = await assembly_service._build_response(db, req)
            response.append(AssemblyRequestResponse.model_validate(resp))
        return response
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# --- History ----------------------------------------------------------------


@router.get("/{request_id}/history", response_model=list[AssemblyHistoryResponse])
async def get_assembly_history(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get status change history for an assembly request."""
    try:
        history = await assembly_service.get_assembly_history(db, project.id, request_id)
        return [AssemblyHistoryResponse.model_validate(h) for h in history]
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# --- FBO sync ---------------------------------------------------------------


@router.post(
    "/{request_id}/refresh-from-fbo", response_model=RefreshFromFboResponse, dependencies=[Depends(rate_limit_write)]
)
async def refresh_from_fbo(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Re-sync items from linked WbFboSupply."""
    try:
        return await assembly_service.refresh_from_fbo(db, project.id, request_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
