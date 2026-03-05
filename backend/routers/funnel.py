"""
Router: /funnel — WB Sales funnel analytics (воронка продаж).
Thin HTTP layer — all business logic is in services/funnel_service.py.
"""

import asyncio
import logging
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.project_context import get_current_project
from backend.models import Project, SyncLog
from backend.services import funnel_service

logger = logging.getLogger("dds.funnel")

router = APIRouter(prefix="/funnel")


# ─── Schemas ─────────────────────────────────────────────────────────────────

class SyncRequest(BaseModel):
    date_from: str  # YYYY-MM-DD
    date_to: str    # YYYY-MM-DD


class CostOverrideRequest(BaseModel):
    nm_id: int
    cost_price: float


class TaxRateRequest(BaseModel):
    tax_rate: float


# ─── Sync endpoints ─────────────────────────────────────────────────────────

@router.post("/sync")
async def sync_funnel(
    body: SyncRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Sync WB sales funnel + advertising data for a date range (manual trigger)."""
    result = await funnel_service.run_funnel_sync(db, project.id, body.date_from, body.date_to)
    if result["status"] == "error" and not result["rows"]:
        raise HTTPException(400, result["errors"][0] if result["errors"] else "Sync failed")
    return result


@router.post("/resync_ads")
async def resync_ads(
    project: Project = Depends(get_current_project),
):
    """Batch re-sync ALL ad data for the project (entire date range at once).
    Runs in background — returns immediately.
    """
    asyncio.create_task(funnel_service.batch_resync_ads(project.id))
    return {"status": "started", "message": f"Batch ad resync started for project {project.id}"}


@router.get("/sync_status")
async def get_sync_status(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get scheduler status, last sync info, and missing days count."""
    from backend.scheduler import get_scheduler_info, _get_missing_dates
    from sqlalchemy import select

    last_sync = await db.execute(
        select(SyncLog).where(
            SyncLog.service == "wb_funnel",
        ).order_by(SyncLog.id.desc()).limit(10)
    )
    logs = [
        {
            "id": s.id,
            "sync_type": s.sync_type,
            "status": s.status,
            "rows_inserted": s.rows_inserted,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            "error_msg": s.error_msg,
        }
        for s in last_sync.scalars()
    ]

    # Count missing days for this project
    try:
        missing = await _get_missing_dates(project.id)
        missing_days = len(missing)
    except Exception:
        missing_days = None

    return {
        "scheduler": get_scheduler_info(),
        "last_syncs": logs,
        "missing_days": missing_days,
    }


@router.post("/backfill")
async def backfill_funnel(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger full backfill: find all missing days in last 90 and sync them.
    Runs in BACKGROUND — returns immediately, sync continues async.
    """
    from backend.scheduler import _get_missing_dates

    pid = project.id
    missing = await _get_missing_dates(pid)

    if not missing:
        return {"status": "ok", "message": "Нет пропущенных дней", "missing_count": 0}

    logger.info(f"Backfill: project {pid} — {len(missing)} missing days, starting background task")
    asyncio.create_task(funnel_service.run_backfill_bg(pid, missing))

    return {
        "status": "started",
        "message": f"Запущен фоновый backfill: {len(missing)} пропущенных дней",
        "total_missing": len(missing),
    }


# ─── Data endpoints ─────────────────────────────────────────────────────────

@router.get("/data")
async def get_funnel_data(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    vendor_code: Optional[str] = Query(None),
    subject: Optional[str] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get funnel data. Aggregated by day if no article filter, detailed otherwise."""
    tax_rate = float(project.tax_rate or 6)
    detailed = bool(vendor_code)

    if not detailed:
        data = await funnel_service.get_funnel_aggregated(
            db, project.id, tax_rate, date_from, date_to, brand, subject
        )
        return {"data": data, "tax_rate": tax_rate, "detailed": False}
    else:
        data = await funnel_service.get_funnel_detailed(
            db, project.id, tax_rate, date_from, date_to, brand, vendor_code, subject
        )
        return {"data": data, "tax_rate": tax_rate, "detailed": True}


@router.get("/summary")
async def get_funnel_summary(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    subject: Optional[str] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get summary totals for the period header."""
    return await funnel_service.get_summary(db, project.id, date_from, date_to, brand, subject)


@router.get("/filters")
async def get_funnel_filters(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get unique brands, subjects, dates for filter dropdowns."""
    return await funnel_service.get_filters(db, project.id)


# ─── Cost overrides ─────────────────────────────────────────────────────────

@router.get("/costs")
async def get_cost_overrides(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get all manual cost overrides + items without cost."""
    return await funnel_service.get_cost_overrides(db, project.id)


@router.post("/cost")
async def set_cost_override(
    body: CostOverrideRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Set or update manual cost price for an nmId."""
    return await funnel_service.set_cost_override(db, project.id, body.nm_id, body.cost_price)


@router.get("/tax")
async def get_tax_rate(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get project tax rate."""
    return {"tax_rate": float(project.tax_rate or 6)}


@router.post("/tax")
async def set_tax_rate(
    body: TaxRateRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Set project tax rate."""
    project.tax_rate = Decimal(str(body.tax_rate))
    await db.commit()
    return {"status": "ok", "tax_rate": body.tax_rate}


# ─── Day Analysis ────────────────────────────────────────────────────────────

@router.get("/day-analysis")
async def get_day_analysis(
    target_date: str = Query(..., description="YYYY-MM-DD"),
    brand: Optional[str] = Query(None),
    subject: Optional[str] = Query(None),
    trend_days: int = Query(14, description="Days for trend window"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Day analysis: summary, comparison, top products, trend, anomalies."""
    tax_rate = float(project.tax_rate or 6)
    return await funnel_service.get_day_analysis(
        db, project.id, tax_rate, target_date, brand, subject, trend_days
    )


@router.get("/trends")
async def get_product_trends(
    trend_days: int = Query(7, description="Trend window: 7, 14, or 30"),
    brand: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Filter by vendor_code or nmId"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Per-product metrics with linear regression trends."""
    tax_rate = float(project.tax_rate or 6)
    return await funnel_service.get_product_trends(
        db, project.id, tax_rate, trend_days, brand, search
    )
