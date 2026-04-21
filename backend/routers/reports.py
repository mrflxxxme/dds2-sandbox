# ruff: noqa: RUF002
"""
Router: /reports — balance, DDS month, FX, customs controls, dashboard, tax.

Sub-routers:
- reports_wb.py: WB BDR, OPIU, WB finance sync, cost history
- reports_stock.py: stock analytics, warehouse stocks, restocking, order geography
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.routers.reports_stock import router as _stock_router

# ─── Sub-routers ────────────────────────────────────────────────────────────
from backend.routers.reports_wb import router as _wb_router
from backend.schemas import (
    BalanceDailyRow,
    BalanceRow,
    DdsMonthRow,
    FxControlRow,
    IncomeByCategoryRow,
    IncomeDailyRow,
    TaxRateSaveRequest,
)
from backend.services import reports as reports_service

router = APIRouter(prefix="/reports")

# Include sub-routers (no prefix — inherits /reports from parent)
router.include_router(_wb_router)
router.include_router(_stock_router)


@router.get("/balance", response_model=list[BalanceRow])
async def get_balance(
    as_of: date | None = Query(None),
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


@router.get("/fx_control", response_model=list[FxControlRow])
async def get_fx_control(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await reports_service.get_fx_control(db, project.id, date_from, date_to)


@router.get("/customs_control")
async def get_customs_control(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await reports_service.get_customs_control(db, project.id, date_from, date_to)


@router.get("/balance_daily", response_model=list[BalanceDailyRow])
async def get_balance_daily(
    account: str = Query(...),
    currency: str = Query(...),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Running daily balance for a specific account."""
    return await reports_service.get_balance_daily(db, project.id, account, currency, date_from, date_to)


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
    cat_lvl1: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Daily income grouped by category for a given month."""
    return await reports_service.get_income_by_category_daily(db, project.id, year, month, currency, cat_lvl1)


@router.get("/dashboard_summary")
async def get_dashboard_summary(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """All dashboard KPIs in a single call."""
    return await reports_service.get_dashboard_summary(db, project.id, date_from, date_to)


@router.get("/dashboard_daily_filtered")
async def get_dashboard_daily_filtered(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    cp_key: str | None = Query(None),
    category: str | None = Query(None),
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
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    cp_key: str | None = Query(None),
    category: str | None = Query(None),
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
        db,
        project.id,
        df,
        dt,
        cp_key=cp_key,
        category=category,
        flow=flow,
        limit=limit,
        offset=offset,
    )


@router.get("/category_counterparties")
async def get_category_counterparties(
    category: str = Query(...),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Counterparties grouped within an expense category."""
    from datetime import date as date_cls

    df = date_from or date_cls(2020, 1, 1)
    dt = date_to or date_cls.today()
    return await reports_service.get_category_counterparties(
        db,
        project.id,
        category,
        df,
        dt,
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
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List FX rates for the project."""
    from datetime import date as date_cls

    from backend.models import FxRate

    df = date_from or date_cls(2020, 1, 1)
    dt = date_to or date_cls.today()
    result = await db.execute(
        select(FxRate)
        .where(
            FxRate.project_id == project.id,
            FxRate.date >= df,
            FxRate.date <= dt,
        )
        .order_by(FxRate.date.desc())
    )
    rates = result.scalars().all()
    return [{"id": r.id, "date": str(r.date), "pair": r.pair, "rate": float(r.rate), "source": r.source} for r in rates]


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


# ─── Counterparty turnovers (Phase 2: counterparties-loans) ────────────────


@router.get("/counterparty-turnovers")
async def get_counterparty_turnovers(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    type: str | None = Query(None),  # — query arg alias
    currency: str | None = Query(None, description="RUB or CNY (default: all)"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Counterparty turnovers pivot (month × counterparty), multi-currency.
    RUB and CNY are returned SEPARATELY — no conversion.
    Cached 300s; invalidated via invalidate_project_reports().
    """
    from backend.services.reports.counterparty_turnovers import get_counterparty_turnovers as svc

    return await svc(
        db,
        project.id,
        date_from=date_from,
        date_to=date_to,
        type=type,
        currency=currency,
    )
