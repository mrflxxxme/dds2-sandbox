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
from backend.schemas.cold_start import (
    ColdStartTableResponse,
    DistributeRequest,
    DistributeResponse,
)
from backend.utils.time import utcnow

logger = logging.getLogger("dds.routers.reports_stock")

router = APIRouter()

# Auto-sync threshold: 1 hour
_AUTO_SYNC_STALE_SECONDS = 3600


async def _auto_sync_wb_stocks_if_stale(project_id: int, db: AsyncSession) -> bool:
    """Check if WB stocks are stale (>1h) and trigger background sync. Returns True if sync started."""
    from sqlalchemy import func as sqlfunc

    from backend.models.integrations import WbWarehouseStock

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
    localization_optimized: bool = Query(
        False,
        description=(
            "Если True — потребность распределяется по ближайшим доступным "
            "WB-складам по координатам покупателя (идеальная локализация). "
            "Исключённые склады автоматически перебрасываются на следующие "
            "ближайшие available по haversine. Дополнительно делается "
            "district-pooling: сборки/транзит вычитаются из спроса всех "
            "складов того же ФО пропорционально, не только из конкретного."
        ),
    ),
    only_available: bool = Query(
        False,
        description=(
            "Если True — каждая клетка матрицы урезается жадно по ФФ-остатку "
            "артикула: «реально могу отправить» вместо «идеальная потребность»."
        ),
    ),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Compute restocking need per warehouse per article."""
    if await _auto_sync_wb_stocks_if_stale(project.id, db):
        background_tasks.add_task(_bg_sync_wb_stocks, project.id)

    from backend.services.warehouse_need_service import get_warehouse_need

    return await get_warehouse_need(
        db,
        project.id,
        supply_days,
        analysis_days,
        mode,
        localization_optimized=localization_optimized,
        only_available=only_available,
    )


@router.post("/stock_analytics/upload_order_cities")
async def upload_order_cities(
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Upload WB order feed Excel to extract city-level delivery mapping."""
    from backend.etl.parsers.order_city_parser import parse_order_city_excel

    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Файл должен быть в формате .xlsx")

    data = await file.read()

    from backend.config import settings as app_settings

    max_bytes = app_settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Файл слишком большой. Максимум: {app_settings.MAX_UPLOAD_SIZE_MB} МБ",
        )

    from backend.utils.file_validation import validate_file_content

    validate_file_content(data, file.filename or "upload.xlsx")

    mappings = parse_order_city_excel(data)
    if not mappings:
        raise HTTPException(status_code=400, detail="Не найдены данные городов в файле")

    from backend.services.order_geography_service import upsert_order_city_mappings

    inserted = await upsert_order_city_mappings(db, project.id, mappings)
    return {"ok": True, "total_mappings": len(mappings), "affected_rows": inserted}


@router.get("/stock_analytics/order_cities_status")
async def order_cities_status(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get status of uploaded order city mappings for hypothetical forecast."""
    from backend.services.order_geography_service import get_order_cities_status

    return await get_order_cities_status(db, project.id)


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


# ─── Cold-start: распределение SKU по WB-складам без своей истории ─────────


@router.post("/distribute_cold_start", response_model=DistributeResponse)
async def distribute_cold_start(
    req: DistributeRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> DistributeResponse:
    """Cold-start: рассчитать план распределения SKU по WB-складам.

    MVP "холодного старта" для новинок и SKU без своей истории заказов.
    Использует bench соседнего проекта-донора (`bench_from_project_id`)
    или общероссийский WB-фолбэк (если своих заказов <100 за окно).

    Read-only расчёт (никаких записей в БД), rate_limit_write не требуется.
    """
    from backend.services.cold_start_distribution_service import compute_distribution

    return await compute_distribution(db, project.id, req)


@router.get("/cold_start_table", response_model=ColdStartTableResponse)
async def cold_start_table(
    window_days: int = Query(30, ge=1, le=180),
    min_pack: int = Query(5, ge=1, le=1000),
    bench_from_project_id: int | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> ColdStartTableResponse:
    """Cold-start table: newcomer SKUs with FF-stock + per-warehouse allocation.

    Returns only SKUs where:
      - rf_qty > 0 (FF-stock present, qty to distribute), AND
      - first_sale_date IS NULL OR first_sale_date >= today-14
        (no sales history — regular localization can't work).

    For each SKU computes distribution of its rf_qty across the main warehouses
    of each federal district (excluded warehouses + min_pack + active assemblies
    are taken into account).
    """
    from backend.services.cold_start_distribution_service import (
        compute_cold_start_table,
    )

    return await compute_cold_start_table(
        db,
        project.id,
        window_days,
        min_pack,
        bench_from_project_id,
    )
