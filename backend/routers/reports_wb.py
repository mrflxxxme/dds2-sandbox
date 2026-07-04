"""Router: /reports/wb — WB BDR, OPIU, WB finance sync, cost history.

Sub-router extracted from reports.py for maintainability.
"""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.utils.rate_limit import rate_limit_write

router = APIRouter()


@router.get("/wb_bdr")
async def get_wb_bdr(
    date_from: date = Query(...),
    date_to: date = Query(...),
    brand: str | None = Query(None),
    article: str | None = Query(None),
    group_by: str = Query("article"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """WB BDR (P&L) report from locally cached finance data."""
    import asyncio

    from backend.services import wb_bdr_service

    if group_by not in ("article", "brand", "subject", "abc", "tag", "imt"):
        group_by = "article"
    try:
        return await asyncio.wait_for(
            wb_bdr_service.get_wb_bdr(
                db,
                project.id,
                date_from,
                date_to,
                brand=brand,
                article=article,
                group_by=group_by,
            ),
            timeout=60,
        )
    except TimeoutError as err:
        raise HTTPException(
            status_code=504, detail="Отчет ВБ БДР: таймаут (>60с). Попробуйте уменьшить период."
        ) from err


@router.get("/wb_bdr/available_weeks")
async def get_wb_bdr_available_weeks(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Return available date ranges that have wb_finance data."""
    from sqlalchemy import distinct, select

    from backend.models.wb_finance import WbFinanceRow

    result = await db.execute(
        select(distinct(WbFinanceRow.rr_dt))
        .where(
            WbFinanceRow.project_id == project.id,
        )
        .order_by(WbFinanceRow.rr_dt.desc())
    )
    dates = [str(r[0]) for r in result if r[0] is not None]
    return {"available_dates": dates}


@router.get("/wb_bdr/sync_status")
async def get_wb_bdr_sync_status(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get WB finance data sync status for the project.

    If date_from/date_to provided, also returns period_rows and is_period_covered
    so the UI can show real coverage for the selected period instead of just
    "any data exists".
    """
    from backend.services.wb_finance_sync import get_sync_status

    return await get_sync_status(db, project.id, date_from=date_from, date_to=date_to)


@router.get("/opiu")
async def get_opiu(
    date_from: date = Query(...),
    date_to: date = Query(...),
    brand: str | None = Query(None),
    article: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """ОПИУ (P&L) report — returns cached data instantly or triggers background computation."""
    import asyncio
    import json
    import logging

    from backend.cache import get_redis

    _log = logging.getLogger("dds.reports")

    # Check cache first — instant response if available
    r = await get_redis()
    if r:
        cache_key = f"reports:opiu:project_id={project.id}:date_from={date_from}:date_to={date_to}"
        if brand:
            cache_key += f":brand={brand}"
        if article:
            cache_key += f":article={article}"
        try:
            cached_value = await r.get(cache_key)
            if cached_value is not None:
                return json.loads(cached_value)
        except Exception:
            pass

    # Cache miss — trigger background computation and return "computing"
    project_id = project.id

    # Check if a previous background task already failed (error flag in Redis)
    error_key = f"reports:opiu:error:{project_id}:{date_from}:{date_to}"
    lock_key = f"reports:opiu:lock:{project_id}:{date_from}:{date_to}"
    prev_error = None
    if r:
        try:
            prev_error = await r.get(error_key)
        except Exception:
            pass

    # Distributed lock: prevent multiple background tasks for the same report
    already_computing = False
    if r:
        try:
            already_computing = not await r.set(lock_key, "1", ex=120, nx=True)
        except Exception:
            pass

    if not already_computing:

        async def _compute_background():
            try:
                from backend.database import AsyncSessionLocal
                from backend.services import opiu_service

                async with AsyncSessionLocal() as bg_db:
                    await opiu_service.get_opiu(
                        bg_db,
                        project_id,
                        date_from,
                        date_to,
                        brand=brand,
                        article=article,
                    )
                _log.info("OPIU background compute done for project %s", project_id)
                # Clear error flag on success
                if r:
                    try:
                        await r.delete(error_key)
                    except Exception:
                        pass
            except Exception as e:
                _log.error("OPIU background compute failed for project %s: %s", project_id, e)
                # Set error flag so next request knows computation failed
                if r:
                    try:
                        await r.setex(error_key, 120, str(e))
                    except Exception:
                        pass
            finally:
                # Release lock
                if r:
                    try:
                        await r.delete(lock_key)
                    except Exception:
                        pass

        asyncio.create_task(_compute_background())

    if prev_error:
        return {
            "computing": True,
            "error": True,
            "message": f"Ошибка при расчёте отчёта: {prev_error}. Повторная попытка запущена.",
        }

    return {"computing": True, "message": "Отчёт рассчитывается, обновите через несколько секунд"}


@router.post("/wb_bdr/sync", dependencies=[Depends(rate_limit_write)])
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
    article: str | None = Query(None),
    brand: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Cost price history: articles × orders pivot table."""
    from backend.services import cost_history_service

    return await cost_history_service.get_cost_history(
        db,
        project.id,
        article_search=article,
        brand_filter=brand,
    )


@router.get("/cost_dna")
async def get_cost_dna(
    period_days: int = Query(
        30, description="Rolling period in days (used when date_from/date_to are absent). Default 30."
    ),
    date_from: date | None = Query(None, description="Custom period start (inclusive). Requires date_to."),
    date_to: date | None = Query(None, description="Custom period end (inclusive). Requires date_from."),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Cost-DNA report: per-category (subject) decomposition of revenue
    into cost components, marketplace fees, taxes, and margin.

    Used as forecasting tool to compare prognosis cost vs realization,
    and as fallback for new SKUs without cost data.

    Period selection:
      - If neither date_from nor date_to is provided → rolling ``period_days``
        back from yesterday (legacy behaviour; only 30 / 60 accepted).
      - If both date_from and date_to are provided → use the custom range
        (up to 365 days). This lets the user align Cost-DNA period with
        BDR for direct comparison.
    """
    from backend.services import cost_dna_service
    from backend.utils.time import utcnow

    if (date_from is None) != (date_to is None):
        raise HTTPException(status_code=422, detail="date_from and date_to must be provided together")

    if date_from is not None and date_to is not None:
        if date_from > date_to:
            raise HTTPException(status_code=422, detail="date_from must be <= date_to")
        span = (date_to - date_from).days + 1
        if span > 365:
            raise HTTPException(status_code=422, detail=f"period too long: {span} days (max 365)")
    else:
        if period_days not in (30, 60):
            period_days = 30

    # Pin snapshot_date so it enters the cache key — prevents silent drift
    # across day boundaries when the rolling default is used (bug 2026-04-15).
    snapshot_date = utcnow().date()
    return await cost_dna_service.get_cost_dna(
        db,
        project.id,
        period_days=period_days,
        date_from=date_from,
        date_to=date_to,
        snapshot_date=snapshot_date,
    )
