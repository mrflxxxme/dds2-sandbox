"""
Router: /reports — balance, DDS month, FX, customs controls

Delegates all business logic to services/reports_service.py.
"""

from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.schemas import (
    BalanceRow, DdsMonthRow, FxControlRow, BalanceDailyRow,
    IncomeDailyRow, IncomeByCategoryRow,
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
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """All dashboard KPIs in a single call."""
    return await reports_service.get_dashboard_summary(db, project.id)
