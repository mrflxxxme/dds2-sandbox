# ruff: noqa: RUF001
"""Router: /reports/stock* — stock analytics, warehouse stocks, restocking, order geography.

Sub-router extracted from reports.py for maintainability.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal, get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.utils.time import utcnow

logger = logging.getLogger("dds.routers.reports_stock")

router = APIRouter()

# Auto-sync threshold: 1 hour
_AUTO_SYNC_STALE_SECONDS = 3600


async def _auto_sync_wb_stocks_if_stale(project_id: int, db: AsyncSession) -> bool:
    """Check if WB stocks are stale (>1h) and trigger background sync. Returns True if sync started."""
    from sqlalchemy import func as sqlfunc

    from backend.models.wb_stocks import WbWarehouseStock

    result = await db.execute(
        select(sqlfunc.max(WbWarehouseStock.updated_at)).where(
            WbWarehouseStock.project_id == project_id,
        )
    )
    last_sync = result.scalar()

    if last_sync and (utcnow() - last_sync).total_seconds() < _AUTO_SYNC_STALE_SECONDS:
        return False

    return True


async def _bg_sync_wb_stocks(project_id: int):
    """Background task: sync WB stocks for a project."""
    try:
        async with AsyncSessionLocal() as db:
            from backend.services.funnel.wb_api_client import fetch_warehouse_stocks, get_wb_key
            from backend.services.stock_analytics_service import sync_warehouse_stocks as do_sync
            from backend.services.warehouse_stock_service import (
                get_warehouse_stocks as _get_wh,
                get_warehouse_stocks_by_article as _get_art,
            )

            api_key = await get_wb_key(db, project_id, "wb")
            if not api_key:
                return

            items = await fetch_warehouse_stocks(api_key)
            await do_sync(db, project_id, items)

            # Prewarm caches
            async with AsyncSessionLocal() as prewarm_db:
                await _get_wh(prewarm_db, project_id)
                await _get_art(prewarm_db, project_id)

            logger.info("Auto-sync WB stocks: project %d done", project_id)
    except Exception as e:
        logger.warning("Auto-sync WB stocks: project %d failed — %s", project_id, e)


@router.get("/stock_analytics")
async def get_stock_analytics(
    background_tasks: BackgroundTasks,
    trend_days: int = Query(7, ge=1, le=90),
    subject: str | None = Query(None),
    brand: str | None = Query(None),
    article: str | None = Query(None),
    mode: str = Query("wb"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Stock depletion forecast based on WB funnel sales data."""
    if await _auto_sync_wb_stocks_if_stale(project.id, db):
        background_tasks.add_task(_bg_sync_wb_stocks, project.id)

    from backend.services import stock_analytics_service

    return await stock_analytics_service.get_stock_analytics(
        db,
        project.id,
        trend_days,
        subject_filter=subject,
        brand_filter=brand,
        article_filter=article,
        mode=mode,
    )


@router.post("/stock_warehouses/sync")
async def sync_warehouse_stocks(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Sync warehouse stock levels from WB API supplier/stocks."""
    from backend.services.funnel.wb_api_client import fetch_warehouse_stocks, get_wb_key
    from backend.services.stock_analytics_service import sync_warehouse_stocks as do_sync

    api_key = await get_wb_key(db, project.id, "wb")
    if not api_key:
        raise HTTPException(status_code=400, detail="WB API key not configured")

    items = await fetch_warehouse_stocks(api_key)
    count = await do_sync(db, project.id, items)

    # Prewarm caches after manual sync
    try:
        from backend.services.warehouse_stock_service import (
            get_warehouse_stocks as _get_wh,
            get_warehouse_stocks_by_article as _get_art,
        )

        await _get_wh(db, project.id)
        await _get_art(db, project.id)
    except Exception:
        logger.warning("Cache prewarm after manual sync failed", exc_info=True)

    return {"synced": count}


@router.get("/stock_warehouses")
async def get_warehouse_stocks(
    background_tasks: BackgroundTasks,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get warehouse stock levels grouped by warehouse with yesterday comparison."""
    from backend.services.warehouse_stock_service import get_warehouse_stocks as get_wh

    if await _auto_sync_wb_stocks_if_stale(project.id, db):
        background_tasks.add_task(_bg_sync_wb_stocks, project.id)

    return await get_wh(db, project.id)


@router.get("/stock_warehouses/articles")
async def get_warehouse_stocks_by_article(
    search: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get stock levels grouped by article with per-warehouse breakdown."""
    from backend.services.warehouse_stock_service import get_warehouse_stocks_by_article as get_art

    return await get_art(db, project.id, search=search)


@router.get("/stock_warehouses/history")
async def get_stock_history(
    date_from: str = Query(...),
    date_to: str = Query(...),
    warehouse: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get daily stock history from snapshots for chart and table."""
    from backend.services.warehouse_stock_service import get_stock_history as get_hist

    return await get_hist(db, project.id, date_from, date_to, warehouse=warehouse)


@router.get("/stock_need")
async def get_stock_need(
    background_tasks: BackgroundTasks,
    supply_days: int = Query(14, ge=1, le=90, description="Target stock level in days"),
    analysis_days: int = Query(14, ge=1, le=90, description="Lookback period for avg daily orders"),
    mode: str = Query("actual", pattern="^(actual|hypothetical)$"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Compute restocking need per warehouse per article."""
    if await _auto_sync_wb_stocks_if_stale(project.id, db):
        background_tasks.add_task(_bg_sync_wb_stocks, project.id)

    from backend.services.stock_analytics_service import get_warehouse_need

    return await get_warehouse_need(db, project.id, supply_days, analysis_days, mode)


@router.post("/stock_analytics/upload_order_cities")
async def upload_order_cities(
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Upload WB order feed Excel to extract city-level delivery mapping."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from backend.etl.parsers.order_city_parser import parse_order_city_excel
    from backend.models.order_city import OrderCityMap

    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Файл должен быть в формате .xlsx")

    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Файл слишком большой (макс 50 МБ)")

    mappings = parse_order_city_excel(data)
    if not mappings:
        raise HTTPException(status_code=400, detail="Не найдены данные городов в файле")

    inserted = 0
    batch_size = 500
    for i in range(0, len(mappings), batch_size):
        batch = mappings[i : i + batch_size]
        stmt = pg_insert(OrderCityMap).values(
            [
                {
                    "project_id": project.id,
                    "srid": m["srid"],
                    "city": m["city"],
                    "okrug": m.get("okrug"),
                    "order_date": m.get("order_date"),
                }
                for m in batch
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["project_id", "srid"],
            set_={"city": stmt.excluded.city, "okrug": stmt.excluded.okrug, "order_date": stmt.excluded.order_date},
        )
        result = await db.execute(stmt)
        affected = result.rowcount or 0
        inserted += affected

    await db.commit()
    return {"ok": True, "total_mappings": len(mappings), "affected_rows": inserted}


@router.get("/stock_analytics/order_cities_status")
async def order_cities_status(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get status of uploaded order city mappings for hypothetical forecast."""
    from sqlalchemy import func as sqlfunc

    from backend.models.order_city import OrderCityMap

    result = await db.execute(
        select(
            sqlfunc.count(OrderCityMap.id).label("total"),
            sqlfunc.min(OrderCityMap.order_date).label("date_from"),
            sqlfunc.max(OrderCityMap.order_date).label("date_to"),
            sqlfunc.max(OrderCityMap.updated_at).label("last_updated"),
        ).where(OrderCityMap.project_id == project.id)
    )
    row = result.one()
    return {
        "has_data": (row.total or 0) > 0,
        "total_mappings": row.total or 0,
        "date_from": row.date_from.isoformat() if row.date_from else None,
        "date_to": row.date_to.isoformat() if row.date_to else None,
        "last_updated": row.last_updated.isoformat() if row.last_updated else None,
    }


@router.get("/order_geography")
async def order_geography(
    date_from: str,
    date_to: str,
    brand: str | None = None,
    category: str | None = None,
    article: str | None = None,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get order geography — cities with order counts, daily chart, filters."""
    from backend.services.order_geography_service import get_order_geography

    return await get_order_geography(
        db,
        project.id,
        date_from,
        date_to,
        brand=brand,
        category=category,
        article=article,
    )
