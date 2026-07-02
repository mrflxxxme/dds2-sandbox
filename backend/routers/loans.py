"""
Router: /loans — Loan CRUD + manual payment match.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.loan import (
    LenderAccessCreate,
    LenderAccessCreated,
    LenderAccessInfo,
    LenderAccessListResponse,
    LoanByLenderResponse,
    LoanCreate,
    LoanDashboard,
    LoanDetail,
    LoanExtend,
    LoanFilter,
    LoanForecastResponse,
    LoanImportResult,
    LoanListResponse,
    LoanPaymentMatch,
    LoanPaymentResponse,
    LoanResponse,
    LoanUpdate,
)
from backend.services import lender_access_service, loan_analytics, loan_import
from backend.services.loan_service import (
    LoanPaymentAlreadyExistsError,
    LoanService,
    ProjectMismatchError,
)
from backend.utils.rate_limit import rate_limit_write
from backend.utils.time import utcnow

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


# ─── Analytics (static paths BEFORE /{loan_id}) ──────────────────────────────


@router.get("/dashboard", response_model=LoanDashboard)
async def loan_dashboard(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Admin dashboard: KPIs, splits by entity/rate, monthly timeline, top lenders."""
    return await loan_analytics.dashboard(db, project.id, utcnow().date())


@router.get("/by-lender", response_model=LoanByLenderResponse)
async def loan_by_lender(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Per-lender rollup: outstanding, weighted rate, accrued interest, next dates."""
    return await loan_analytics.by_lender(db, project.id, utcnow().date())


@router.get("/forecast", response_model=LoanForecastResponse)
async def loan_forecast(
    horizon_months: int = Query(12, ge=1, le=36),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Forecast: monthly interest accrual + principal due + upcoming events."""
    return await loan_analytics.forecast(db, project.id, utcnow().date(), horizon_months)


# ─── Lender portal access (admin provisioning, static before /{loan_id}) ──────


@router.get("/lenders/access", response_model=LenderAccessListResponse)
async def list_lender_access(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List counterparties that have a lender-portal login."""
    items = await lender_access_service.list_access(db, project.id)
    return LenderAccessListResponse(items=items)


@router.post(
    "/lenders/access",
    response_model=LenderAccessCreated,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def create_lender_access(
    body: LenderAccessCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Создать вход в лендер-портал для контрагента-заёмщика. Пароль — в ответе (один раз)."""
    return await lender_access_service.create_access(db, project.id, body)


@router.post(
    "/lenders/access/{counterparty_id}/reset",
    response_model=LenderAccessCreated,
    dependencies=[Depends(rate_limit_write)],
)
async def reset_lender_password(
    counterparty_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сбросить пароль заёмщика (и реактивировать доступ). Новый пароль — в ответе."""
    return await lender_access_service.reset_password(db, project.id, counterparty_id)


@router.post(
    "/lenders/access/{counterparty_id}/revoke",
    response_model=LenderAccessInfo,
    dependencies=[Depends(rate_limit_write)],
)
async def revoke_lender_access(
    counterparty_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Отозвать доступ заёмщика (блокировка входа)."""
    return await lender_access_service.revoke_access(db, project.id, counterparty_id)


# ─── Excel import ────────────────────────────────────────────────────────────


@router.post("/import", response_model=LoanImportResult, dependencies=[Depends(rate_limit_write)])
async def import_loans(
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Импорт реестра займов из Excel (.xlsx). Идемпотентно по (контрагент, № договора)."""
    content = await file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Файл больше {settings.MAX_UPLOAD_SIZE_MB} МБ")
    if not content:
        raise HTTPException(status_code=400, detail="Пустой файл")
    try:
        return await loan_import.import_loans_from_xlsx(db, project_id=project.id, content=content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None


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


@router.post(
    "/{loan_id}/extend",
    response_model=LoanResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def extend_loan(
    loan_id: int,
    body: LoanExtend,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Продлить займ: закрыть текущий и создать преемника с новой ставкой/сроком."""
    service = LoanService(db)
    successor = await service.extend(loan_id=loan_id, data=body, project_id=project.id)
    return LoanResponse.model_validate(successor)


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
