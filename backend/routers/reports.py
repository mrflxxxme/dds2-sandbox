"""
Router: /reports — balance, DDS month, FX, customs controls

Delegates all business logic to services/reports_service.py.
"""

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.schemas import (
    BalanceRow, DdsMonthRow, FxControlRow, BalanceDailyRow,
    IncomeDailyRow, IncomeByCategoryRow, TaxRateSaveRequest,
)
from backend.project_context import get_current_project
from backend.services import reports_service

router = APIRouter(prefix="/reports")


@router.get("/balance", response_model=list[BalanceRow])
async def get_balance(
    as_of: Optional[date] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Current balance per account/currency.
    balance = opening_balance + sum(net) for transactions <= as_of
    """
    return await reports_service.get_balance(db, project.id, as_of)


@router.get("/dds_month", response_model=list[DdsMonthRow])
async def get_dds_month(
    year: int = Query(...),
    month: int = Query(...),
    currency: str = Query("RUB"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """DDS grouped by cat_lvl1_2/cat_lvl2_2 for a given month."""
    return await reports_service.get_dds_month(db, project.id, year, month, currency)


@router.get("/dds_pnl")
async def get_dds_pnl(
    year: int = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """PnL-style DDS report: categories × months with counterparty drill-down."""
    return await reports_service.get_dds_pnl(db, project.id, year)


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
    from backend.services.wb_finance_sync import ensure_initial_sync
    try:
        await ensure_initial_sync(db, project.id)
    except (ValueError, Exception):
        pass  # No WB key — serve report with existing data (may be empty)
    from backend.services import wb_bdr_service
    return await wb_bdr_service.get_wb_bdr(
        db, project.id, date_from, date_to, brand=brand, article=article,
    )


@router.get("/wb_bdr/available_weeks")
async def get_wb_bdr_available_weeks(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Return available date ranges (rr_dt pairs) that have wb_finance data."""
    from backend.models.wb_finance import WbFinanceRow
    from sqlalchemy import distinct
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
    from backend.services.wb_finance_sync import ensure_initial_sync
    try:
        await ensure_initial_sync(db, project.id)
    except (ValueError, Exception):
        pass  # No WB key — serve report with existing data (may be empty)
    from backend.services import opiu_service
    return await opiu_service.get_opiu(
        db, project.id, date_from, date_to, brand=brand, article=article,
    )


@router.post("/wb_bdr/sync")
async def trigger_wb_bdr_sync(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger WB finance data sync (last 2 months).
    
    Runs in background — returns immediately with {status: 'started'}.
    """
    import asyncio
    from datetime import timedelta
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


@router.get("/fx_control", response_model=list[FxControlRow])
async def get_fx_control(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await reports_service.get_fx_control(db, project.id, date_from, date_to)


@router.get("/customs_control")
async def get_customs_control(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await reports_service.get_customs_control(db, project.id, date_from, date_to)


@router.get("/balance_daily", response_model=list[BalanceDailyRow])
async def get_balance_daily(
    account: str = Query(...),
    currency: str = Query(...),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Running daily balance for a specific account."""
    return await reports_service.get_balance_daily(
        db, project.id, account, currency, date_from, date_to
    )


@router.get("/income_daily", response_model=list[IncomeDailyRow])
async def get_income_daily(
    year: int = Query(...),
    month: int = Query(...),
    currency: str = Query("RUB"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Daily income breakdown by bank/source for a given month."""
    return await reports_service.get_income_daily(db, project.id, year, month, currency)


@router.get("/income_by_category_daily", response_model=list[IncomeByCategoryRow])
async def get_income_by_category_daily(
    year: int = Query(...),
    month: int = Query(...),
    currency: str = Query("RUB"),
    cat_lvl1: Optional[str] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Daily income grouped by category for a given month."""
    return await reports_service.get_income_by_category_daily(
        db, project.id, year, month, currency, cat_lvl1
    )


@router.get("/dashboard_summary")
async def get_dashboard_summary(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """All dashboard KPIs in a single call."""
    return await reports_service.get_dashboard_summary(db, project.id, date_from, date_to)


@router.get("/dashboard_daily_filtered")
async def get_dashboard_daily_filtered(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    cp_key: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Daily cashflow filtered by counterparty or expense category."""
    from datetime import date as date_cls
    df = date_from or date_cls(2020, 1, 1)
    dt = date_to or date_cls.today()
    return await reports_service.get_daily_filtered(db, project.id, df, dt, cp_key=cp_key, category=category)


@router.get("/dashboard_transactions")
async def get_dashboard_transactions(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    cp_key: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    flow: str = Query("all"),
    limit: int = Query(100),
    offset: int = Query(0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Transaction list filtered by counterparty/category for dashboard detail view."""
    from datetime import date as date_cls
    df = date_from or date_cls(2020, 1, 1)
    dt = date_to or date_cls.today()
    return await reports_service.get_filtered_transactions(
        db, project.id, df, dt, cp_key=cp_key, category=category,
        flow=flow, limit=limit, offset=offset,
    )


@router.get("/category_counterparties")
async def get_category_counterparties(
    category: str = Query(...),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Counterparties grouped within an expense category."""
    from datetime import date as date_cls
    df = date_from or date_cls(2020, 1, 1)
    dt = date_to or date_cls.today()
    return await reports_service.get_category_counterparties(
        db, project.id, category, df, dt,
    )


@router.post("/fx_rates/backfill")
async def backfill_fx_rates(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Extract FX rates from existing VTB conversion transactions."""
    from backend.services import fx_service
    return await fx_service.backfill_rates_from_transactions(db, project.id)


@router.get("/fx_rates")
async def get_fx_rates(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List FX rates for the project."""
    from datetime import date as date_cls
    from backend.models import FxRate
    from sqlalchemy import select

    df = date_from or date_cls(2020, 1, 1)
    dt = date_to or date_cls.today()
    result = await db.execute(
        select(FxRate).where(
            FxRate.project_id == project.id,
            FxRate.date >= df,
            FxRate.date <= dt,
        ).order_by(FxRate.date.desc())
    )
    rates = result.scalars().all()
    return [
        {"id": r.id, "date": str(r.date), "pair": r.pair, "rate": float(r.rate), "source": r.source}
        for r in rates
    ]


# ─── Tax Rates ──────────────────────────────────────────────────────

@router.get("/tax_rates")
async def get_tax_rates(
    year: int = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get tax rates for all brands for the given year."""
    from backend.services import tax_service
    return await tax_service.get_tax_rates(db, project.id, year)


@router.post("/tax_rates")
async def save_tax_rates(
    payload: TaxRateSaveRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Save project-level tax rates for one year (upsert 12 months)."""
    from backend.services import tax_service
    return await tax_service.save_tax_rates(db, project.id, payload.year, payload.tax_regime, payload.months)


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
    need_days: int = Query(14, ge=1, le=90),
    mode: str = Query("actual", regex="^(actual|hypothetical)$"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Compute restocking need per warehouse per article."""
    from backend.services.stock_analytics_service import get_warehouse_need
    return await get_warehouse_need(db, project.id, need_days, mode)

