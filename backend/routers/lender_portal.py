# ruff: noqa: RUF002, RUF003
"""
Лендер-портал — HTTP-эндпоинты для внешнего заёмщика (кредитора).

Кросс-проектные, скоуп по контрагентам заёмщика (см. backend/lender_context.py).
Доступ внешних аккаунтов ТОЛЬКО к /api/v1/lender/* и /api/v1/ff/* — гарантируется
middleware (block_external_users). Только чтение.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.lender_context import LenderContext, get_lender_context
from backend.schemas.lender_portal import (
    LenderLoanDetail,
    LenderLoanListResponse,
    LenderMeResponse,
)
from backend.services import lender_portal_service as svc

router = APIRouter(prefix="/lender", tags=["Lender Portal"])


@router.get("/me", response_model=LenderMeResponse)
async def lender_me(
    ctx: LenderContext = Depends(get_lender_context),
    db: AsyncSession = Depends(get_db),
):
    return await svc.me(db, ctx)


@router.get("/loans", response_model=LenderLoanListResponse)
async def lender_loans(
    status: str | None = Query(None),
    limit: int = Query(svc.DEFAULT_LIMIT, ge=1, le=svc.MAX_LIMIT),
    offset: int = Query(0, ge=0),
    ctx: LenderContext = Depends(get_lender_context),
    db: AsyncSession = Depends(get_db),
):
    return await svc.list_loans(db, ctx, status=status, limit=limit, offset=offset)


@router.get("/loans/{loan_id}", response_model=LenderLoanDetail)
async def lender_loan_detail(
    loan_id: int,
    ctx: LenderContext = Depends(get_lender_context),
    db: AsyncSession = Depends(get_db),
):
    loan = await svc.get_scoped_loan(db, ctx, loan_id)
    return await svc.loan_detail(db, ctx, loan)
