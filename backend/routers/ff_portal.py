# ruff: noqa: RUF002, RUF003
"""
FF-портал — HTTP-эндпоинты для внешнего оператора фулфилмента (Хамза).

Кросс-проектные, скоуп по складам оператора (см. backend/ff_context.py). Доступ
внешних аккаунтов ТОЛЬКО к /api/v1/ff/* — гарантируется middleware (block_external_users).
Бизнес-логика переиспользует существующие сервисы сборки/приёмки.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.ff_context import FfContext, get_ff_context
from backend.models.assembly import AssemblyStatus
from backend.schemas.ff_portal import (
    FfAcceptanceListResponse,
    FfAcceptanceRow,
    FfAcceptPayload,
    FfAssemblyListResponse,
    FfAssemblyRow,
    FfMeResponse,
    FfProjectInfo,
    FfReadyPayload,
    FfStockListResponse,
    FfWarehouseInfo,
)
from backend.services import ff_portal_service as svc
from backend.services.assembly.status import mark_ready, ship_request, start_assembly
from backend.services.warehouse_inbound import accept_receipt_ff, claim_receipt
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/ff", tags=["FF Portal"])


# ─── Профиль оператора (шапка + гейт доступа) ────────────────────────────────


@router.get("/me", response_model=FfMeResponse)
async def ff_me(ctx: FfContext = Depends(get_ff_context)):
    projects = [FfProjectInfo(slug=p.slug, name=p.name) for p in ctx.projects.values()]
    warehouses: list[FfWarehouseInfo] = []
    for pid, wh_ids in ctx.warehouses.items():
        proj = ctx.projects.get(pid)
        for wid in wh_ids:
            wh = ctx.warehouse_by_id.get(wid)
            warehouses.append(
                FfWarehouseInfo(
                    project_slug=proj.slug if proj else "",
                    warehouse_id=wid,
                    warehouse_name=wh.name if wh else "",
                )
            )
    return FfMeResponse(username=ctx.user.username, projects=projects, warehouses=warehouses)


# ─── Заявки на сборку ────────────────────────────────────────────────────────


@router.get("/assemblies", response_model=FfAssemblyListResponse)
async def ff_list_assemblies(
    status: str | None = Query(None),
    warehouse_id: int | None = Query(None),
    limit: int = Query(svc.DEFAULT_LIST_LIMIT, ge=1, le=svc.MAX_LIST_LIMIT),
    offset: int = Query(0, ge=0),
    ctx: FfContext = Depends(get_ff_context),
    db: AsyncSession = Depends(get_db),
):
    items, total = await svc.list_assemblies(
        db, ctx, status=status, warehouse_id=warehouse_id, limit=limit, offset=offset
    )
    return FfAssemblyListResponse(items=items, total=total)


@router.post("/assemblies/{assembly_id}/start", response_model=FfAssemblyRow, dependencies=[Depends(rate_limit_write)])
async def ff_start_assembly(
    assembly_id: int,
    ctx: FfContext = Depends(get_ff_context),
    db: AsyncSession = Depends(get_db),
):
    req = await svc.get_scoped_assembly(db, ctx, assembly_id)
    try:
        await start_assembly(db, req.project_id, req.id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return await svc.assembly_row(db, ctx, await svc.get_scoped_assembly(db, ctx, assembly_id))


@router.post("/assemblies/{assembly_id}/ready", response_model=FfAssemblyRow, dependencies=[Depends(rate_limit_write)])
async def ff_ready_assembly(
    assembly_id: int,
    payload: FfReadyPayload,
    ctx: FfContext = Depends(get_ff_context),
    db: AsyncSession = Depends(get_db),
):
    req = await svc.get_scoped_assembly(db, ctx, assembly_id)
    # Проставляем палеты/вес перед mark_ready (он требует их > 0).
    req.pallets_count = payload.pallets_count
    req.pallet_weight_kg = payload.pallet_weight_kg
    await db.flush()
    try:
        await mark_ready(db, req.project_id, req.id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return await svc.assembly_row(db, ctx, await svc.get_scoped_assembly(db, ctx, assembly_id))


@router.post("/assemblies/{assembly_id}/ship", response_model=FfAssemblyRow, dependencies=[Depends(rate_limit_write)])
async def ff_ship_assembly(
    assembly_id: int,
    ctx: FfContext = Depends(get_ff_context),
    db: AsyncSession = Depends(get_db),
):
    req = await svc.get_scoped_assembly(db, ctx, assembly_id)
    if AssemblyStatus(req.status) != AssemblyStatus.VEHICLE_ASSIGNED:
        raise HTTPException(400, "Машина ещё не назначена логистикой — отгрузка недоступна")
    try:
        await ship_request(db, req.project_id, req.id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return await svc.assembly_row(db, ctx, await svc.get_scoped_assembly(db, ctx, assembly_id))


# ─── Приёмки (вкл. возвраты WB) ──────────────────────────────────────────────


@router.get("/acceptances", response_model=FfAcceptanceListResponse)
async def ff_list_acceptances(
    status: str | None = Query(None),
    warehouse_id: int | None = Query(None),
    limit: int = Query(svc.DEFAULT_LIST_LIMIT, ge=1, le=svc.MAX_LIST_LIMIT),
    offset: int = Query(0, ge=0),
    ctx: FfContext = Depends(get_ff_context),
    db: AsyncSession = Depends(get_db),
):
    items, total = await svc.list_acceptances(
        db, ctx, status=status, warehouse_id=warehouse_id, limit=limit, offset=offset
    )
    return FfAcceptanceListResponse(items=items, total=total)


@router.post(
    "/acceptances/{receipt_id}/start", response_model=FfAcceptanceRow, dependencies=[Depends(rate_limit_write)]
)
async def ff_start_acceptance(
    receipt_id: int,
    ctx: FfContext = Depends(get_ff_context),
    db: AsyncSession = Depends(get_db),
):
    receipt = await svc.get_scoped_receipt(db, ctx, receipt_id)
    try:
        await claim_receipt(db, receipt.project_id, receipt.id, ctx.user.id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return await svc.acceptance_row(db, ctx, await svc.get_scoped_receipt(db, ctx, receipt_id))


@router.post(
    "/acceptances/{receipt_id}/accept", response_model=FfAcceptanceRow, dependencies=[Depends(rate_limit_write)]
)
async def ff_accept_acceptance(
    receipt_id: int,
    payload: FfAcceptPayload,
    ctx: FfContext = Depends(get_ff_context),
    db: AsyncSession = Depends(get_db),
):
    receipt = await svc.get_scoped_receipt(db, ctx, receipt_id)
    items = [it.model_dump() for it in payload.items]
    try:
        await accept_receipt_ff(db, receipt.project_id, receipt.id, items)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return await svc.acceptance_row(db, ctx, await svc.get_scoped_receipt(db, ctx, receipt_id))


# ─── Остатки ─────────────────────────────────────────────────────────────────


@router.get("/stock", response_model=FfStockListResponse)
async def ff_list_stock(
    barcode: str | None = Query(None),
    warehouse_id: int | None = Query(None),
    limit: int = Query(100, ge=1, le=svc.MAX_LIST_LIMIT),
    offset: int = Query(0, ge=0),
    ctx: FfContext = Depends(get_ff_context),
    db: AsyncSession = Depends(get_db),
):
    items, total, total_qty = await svc.list_stock(
        db, ctx, barcode=barcode, warehouse_id=warehouse_id, limit=limit, offset=offset
    )
    return FfStockListResponse(items=items, total=total, total_quantity=total_qty)
