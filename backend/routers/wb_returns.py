# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Router: /wb-returns — WB goods returns (возвраты товаров на ПВЗ).

Thin HTTP layer on top of `backend.services.wb_returns_service`.
"""

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project, SyncLog
from backend.project_context import get_current_project
from backend.schemas.wb_returns import (
    CreateReceiptFromReturnsIn,
    CreateReceiptFromReturnsOut,
    WbGoodsReturnPvzGroup,
    WbGoodsReturnsListResponse,
    WbGoodsReturnsSyncIn,
    WbGoodsReturnsSyncOut,
    WbGoodsReturnSummary,
)
from backend.services import wb_returns_service
from backend.services.integrations_service import _get_wb_key
from backend.utils.rate_limit import rate_limit_write
from backend.utils.time import utcnow

logger = logging.getLogger("dds.routers.wb_returns")

router = APIRouter(prefix="/wb-returns", tags=["WB Returns"])


@router.get("", response_model=WbGoodsReturnsListResponse)
async def list_wb_returns(
    tab: str = Query("all", pattern="^(pvz|in_transit|history|all)$"),
    search: str | None = Query(None, max_length=200),
    pvz_id: int | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List WB goods returns with tab-based filtering + pagination."""
    items, total = await wb_returns_service.list_returns(
        db,
        project.id,
        tab=tab,
        search=search,
        pvz_id=pvz_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return WbGoodsReturnsListResponse(items=items, total=total)


@router.get("/summary", response_model=WbGoodsReturnSummary)
async def get_wb_returns_summary(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """KPI counters for header (ready_for_pickup / in_transit / soon_expires / ...)."""
    return await wb_returns_service.get_summary(db, project.id)


@router.get("/pvz-groups", response_model=list[WbGoodsReturnPvzGroup])
async def get_wb_returns_pvz_groups(
    search: str | None = Query(None, max_length=200),
    limit: int = Query(500, ge=1, le=1000),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Group active-at-PVZ returns by dst_office_id (Tab 1 layout)."""
    return await wb_returns_service.list_pvz_groups(
        db,
        project.id,
        search=search,
        limit=limit,
    )


@router.post(
    "/create-receipt",
    response_model=CreateReceiptFromReturnsOut,
    dependencies=[Depends(rate_limit_write)],
)
async def create_receipt_from_returns(
    payload: CreateReceiptFromReturnsIn,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Create EXPECTED defective InboundReceipt from selected srids."""
    return await wb_returns_service.create_receipt_from_returns(
        db,
        project.id,
        warehouse_id=payload.warehouse_id,
        srids=payload.srids,
        comment=payload.comment,
    )


@router.post(
    "/sync",
    response_model=WbGoodsReturnsSyncOut,
    dependencies=[Depends(rate_limit_write)],
)
async def sync_wb_returns(
    payload: WbGoodsReturnsSyncIn,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Manually sync goods-returns from WB for current project.

    Защищено от параллельного запуска: если в sync_log уже есть RUNNING
    с service=wb/sync_type=goods_returns за последние 10 минут для
    integration_id этого проекта — отказываем (409 Conflict).
    """
    try:
        key, api_key = await _get_wb_key(db, project.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    # Sync-lock: не даём второй параллельный sync.
    cutoff = utcnow() - timedelta(minutes=10)
    running = await db.execute(
        select(SyncLog.id).where(
            SyncLog.integration_id == key.id,
            SyncLog.service == "wb",
            SyncLog.sync_type == "goods_returns",
            SyncLog.status == "RUNNING",
            SyncLog.started_at >= cutoff,
        )
    )
    if running.scalar() is not None:
        raise HTTPException(
            status_code=409,
            detail="Синхронизация уже идёт, попробуйте через минуту",
        )

    # Создаём sync_log=RUNNING до вызова — защита от следующего клика
    # даже если запрос ещё в полёте (он увидит RUNNING).
    sync_log = SyncLog(
        integration_id=key.id,
        service="wb",
        sync_type="goods_returns",
        started_at=utcnow(),
        status="RUNNING",
    )
    db.add(sync_log)
    await db.commit()

    try:
        result = await wb_returns_service.sync_project_returns(
            db,
            project.id,
            api_key,
            date_from=payload.date_from,
            date_to=payload.date_to,
        )
    except ValueError as e:
        sync_log.status = "ERROR"
        sync_log.error_msg = str(e)[:1000]
        sync_log.finished_at = utcnow()
        await db.commit()
        raise HTTPException(status_code=400, detail=str(e)) from None
    except Exception as e:  # pragma: no cover — defensive
        sync_log.status = "ERROR"
        sync_log.error_msg = str(e)[:1000]
        sync_log.finished_at = utcnow()
        await db.commit()
        logger.exception("wb_returns.sync_failed project=%d", project.id)
        raise HTTPException(
            status_code=500,
            detail=f"WB goods returns sync failed: {str(e)[:200]}",
        ) from e

    sync_log.status = "OK"
    sync_log.rows_fetched = int(result.get("rows_fetched", 0))
    sync_log.rows_inserted = int(result.get("rows_upserted", 0))
    sync_log.finished_at = utcnow()
    await db.commit()
    return WbGoodsReturnsSyncOut(**result)
