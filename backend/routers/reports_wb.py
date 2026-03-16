"""Router: /reports/wb — WB BDR, OPIU, WB finance sync, cost history.

Sub-router extracted from reports.py for maintainability.
"""

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project

router = APIRouter()


@router.get("/wb_bdr")
async def get_wb_bdr(
    date_from: date = Query(...),
    date_to: date = Query(...),
    brand: Optional[str] = Query(None),
    article: Optional[str] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """WB BDR (P&L) report from locally cached finance data."""
    import asyncio
    from backend.services import wb_bdr_service
    try:
        return await asyncio.wait_for(
            wb_bdr_service.get_wb_bdr(
                db, project.id, date_from, date_to, brand=brand, article=article,
            ),
            timeout=60,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Отчёт ВБ БДР: таймаут (>60с). Попробуйте уменьшить период.")


@router.get("/wb_bdr/available_weeks")
async def get_wb_bdr_available_weeks(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Return available date ranges that have wb_finance data."""
    from backend.models.wb_finance import WbFinanceRow
    from sqlalchemy import select, distinct
    result = await db.execute(
        select(distinct(WbFinanceRow.rr_dt)).where(
            WbFinanceRow.project_id == project.id,
        ).order_by(WbFinanceRow.rr_dt.desc())
    )
    dates = [str(r[0]) for r in result if r[0] is not None]
    return {"available_dates": dates}


@router.get("/wb_bdr/sync_status")
async def get_wb_bdr_sync_status(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get WB finance data sync status for the project."""
    from backend.services.wb_finance_sync import get_sync_status
    return await get_sync_status(db, project.id)


@router.get("/opiu")
async def get_opiu(
    date_from: date = Query(...),
    date_to: date = Query(...),
    brand: Optional[str] = Query(None),
    article: Optional[str] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """ОПИУ (P&L) report — monthly breakdown with hierarchical rows."""
    import asyncio
    from backend.services import opiu_service
    try:
        return await asyncio.wait_for(
            opiu_service.get_opiu(
                db, project.id, date_from, date_to, brand=brand, article=article,
            ),
            timeout=60,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Отчёт ОПИУ: таймаут (>60с). Попробуйте уменьшить период.")


@router.post("/wb_bdr/sync")
async def trigger_wb_bdr_sync(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger WB finance data sync (last 2 months)."""
    import asyncio
    from backend.database import AsyncSessionLocal
    from backend.services.wb_finance_sync import sync_wb_finance

    today = date.today()
    date_from = today - timedelta(days=60)
    project_id = project.id

    async def _run_sync():
        try:
            async with AsyncSessionLocal() as bg_db:
                await sync_wb_finance(bg_db, project_id, date_from, today)
        except Exception:
            import logging
            logging.getLogger("dds.reports").error(
                "Background WB finance sync failed for project %s", project_id, exc_info=True
            )

    asyncio.create_task(_run_sync())
    return {"status": "started", "message": "Sync started in background"}


@router.get("/cost_history")
async def get_cost_history(
    article: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Cost price history: articles × orders pivot table."""
    from backend.services import cost_history_service
    return await cost_history_service.get_cost_history(
        db, project.id, article_search=article, brand_filter=brand,
    )
