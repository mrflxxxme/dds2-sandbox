"""Router: /reports/stock* — stock analytics, warehouse stocks, restocking, order geography.

Sub-router extracted from reports.py for maintainability.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project

router = APIRouter()


@router.get("/stock_analytics")
async def get_stock_analytics(
    trend_days: int = Query(7, ge=1, le=90),
    subject: Optional[str] = Query(None),
    brand: Optional[str] = Query(None),
    article: Optional[str] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Stock depletion forecast based on WB funnel sales data."""
    from backend.services import stock_analytics_service
    return await stock_analytics_service.get_stock_analytics(
        db, project.id, trend_days,
        subject_filter=subject, brand_filter=brand, article_filter=article,
    )


@router.post("/stock_warehouses/sync")
async def sync_warehouse_stocks(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Sync warehouse stock levels from WB API supplier/stocks."""
    from backend.services.funnel.wb_api_client import get_wb_key, fetch_warehouse_stocks
    from backend.services.stock_analytics_service import sync_warehouse_stocks as do_sync

    api_key = await get_wb_key(db, project.id, "wb")
    if not api_key:
        raise HTTPException(status_code=400, detail="WB API key not configured")

    items = await fetch_warehouse_stocks(api_key)
    count = await do_sync(db, project.id, items)
    return {"synced": count}


@router.get("/stock_warehouses")
async def get_warehouse_stocks(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get warehouse stock levels grouped by warehouse."""
    from backend.services.stock_analytics_service import get_warehouse_stocks as get_wh
    return await get_wh(db, project.id)


@router.get("/stock_need")
async def get_stock_need(
    supply_days: int = Query(14, ge=1, le=90, description="Target stock level in days"),
    analysis_days: int = Query(14, ge=1, le=90, description="Lookback period for avg daily orders"),
    mode: str = Query("actual", pattern="^(actual|hypothetical)$"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Compute restocking need per warehouse per article."""
    from backend.services.stock_analytics_service import get_warehouse_need
    return await get_warehouse_need(db, project.id, supply_days, analysis_days, mode)


@router.post("/stock_analytics/upload_order_cities")
async def upload_order_cities(
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Upload WB order feed Excel to extract city-level delivery mapping."""
    from backend.etl.parsers.order_city_parser import parse_order_city_excel
    from backend.models.order_city import OrderCityMap
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Файл должен быть в формате .xlsx")

    data = await file.read()
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Файл слишком большой (макс 50 МБ)")

    mappings = parse_order_city_excel(data)
    if not mappings:
        raise HTTPException(status_code=400, detail="Не найдены данные городов в файле")

    inserted = 0
    updated = 0
    batch_size = 500
    for i in range(0, len(mappings), batch_size):
        batch = mappings[i : i + batch_size]
        stmt = pg_insert(OrderCityMap).values(
            [{"project_id": project.id, "srid": m["srid"], "city": m["city"], "okrug": m.get("okrug")} for m in batch]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["project_id", "srid"],
            set_={"city": stmt.excluded.city, "okrug": stmt.excluded.okrug},
        )
        result = await db.execute(stmt)
        affected = result.rowcount or 0
        inserted += affected

    await db.commit()
    return {"ok": True, "total_mappings": len(mappings), "affected_rows": inserted}


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
        db, project.id, date_from, date_to,
        brand=brand, category=category, article=article,
    )
