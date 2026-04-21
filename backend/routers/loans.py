"""
Router: /loans — Loan CRUD + manual payment match.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.loan import (
    LoanCreate,
    LoanDetail,
    LoanFilter,
    LoanListResponse,
    LoanPaymentMatch,
    LoanPaymentResponse,
    LoanResponse,
    LoanUpdate,
)
from backend.services.loan_service import (
    LoanPaymentAlreadyExistsError,
    LoanService,
    ProjectMismatchError,
)
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/loans", tags=["Loans"])


# ─── List / Detail ───────────────────────────────────────────────────────────


@router.get("", response_model=LoanListResponse)
async def list_loans(
    direction: str | None = Query(None),
    status: str | None = Query(None),
    counterparty_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List loans with direction/status/counterparty filters + totals."""
    filters = LoanFilter(
        direction=direction,
        status=status,
        counterparty_id=counterparty_id,
        limit=limit,
        offset=offset,
    )
    service = LoanService(db)
    return await service.list(project_id=project.id, filters=filters)


@router.get("/{loan_id}", response_model=LoanDetail)
async def get_loan(
    loan_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get a loan with counterparty info + payments + schedule summary."""
    service = LoanService(db)
    return await service.get_detail(loan_id=loan_id, project_id=project.id)


# ─── Create / Update ─────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=LoanResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def create_loan(
    body: LoanCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Create a new loan."""
    service = LoanService(db)
    loan = await service.create(data=body, project_id=project.id)
    return LoanResponse.model_validate(loan)


@router.patch("/{loan_id}", response_model=LoanResponse, dependencies=[Depends(rate_limit_write)])
async def update_loan(
    loan_id: int,
    body: LoanUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Partial update of a loan."""
    service = LoanService(db)
    loan = await service.update(loan_id=loan_id, data=body, project_id=project.id)
    return LoanResponse.model_validate(loan)


# ─── Manual match: attach Transaction ↔ LoanPayment ─────────────────────────


@router.post(
    "/{loan_id}/payments/match",
    response_model=LoanPaymentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def match_payment(
    loan_id: int,
    body: LoanPaymentMatch,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Match an existing bank Transaction to this Loan as a LoanPayment."""
    service = LoanService(db)
    try:
        payment = await service.match_transaction(
            loan_id=loan_id,
            transaction_id=body.transaction_id,
            payment_type=body.payment_type,
            amount=body.amount,
            project_id=project.id,
        )
    except LoanPaymentAlreadyExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except ProjectMismatchError as e:
        raise HTTPException(status_code=403, detail=str(e)) from None
    return LoanPaymentResponse.model_validate(payment)
