"""
Router: /loans — Loan CRUD + manual payment match.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.loan import (
    CreditLineDetail,
    CreditLineDraw,
    CreditLineListResponse,
    LoanAccrualMonthsResponse,
    LenderAccessCreate,
    LenderDetail,
    LenderAccessCreated,
    LenderAccessInfo,
    LenderAccessListResponse,
    LoanByLenderResponse,
    LoanCreate,
    LoanDashboard,
    LoanDetail,
    LoanExtend,
    LoanFeeIn,
    LoanFeeListResponse,
    LoanFilter,
    LoanForecastResponse,
    LoanImportResult,
    LoanListResponse,
    LoanPaymentMatch,
    LoanPaymentResponse,
    LoanRepay,
    LoanRatePeriodIn,
    LoanResponse,
    LoanScheduleReplace,
    LoanScheduleResponse,
    LoanStuckResponse,
    LoanUpdate,
)
from backend.services import lender_access_service, loan_analytics, loan_import, loan_schedule
from backend.services.loan_service import (
    LoanPaymentAlreadyExistsError,
    LoanService,
    ProjectMismatchError,
    draw_credit_line,
    get_credit_line,
    get_lender_detail,
    list_credit_lines,
    list_stuck_loans,
    set_rate_period,
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
    period_end: date | None = Query(
        None, description="Дата выплаты (конец периода 25→25). По умолчанию — текущий период."
    ),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Свод по заёмщикам: остаток, ставка, начисленные проценты, ближайшие даты.

    `period_end` показывает исторический срез: «сколько было к выплате 25.07».
    Считаем на день ДО даты выплаты — иначе попадаем уже в следующий период.
    """
    return await loan_analytics.by_lender(db, project.id, utcnow().date(), period_end)


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


@router.get("/accrual-months", response_model=LoanAccrualMonthsResponse)
async def accrual_months(
    months: int = Query(12, ge=1, le=36),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Начисленные проценты и комиссии по КАЛЕНДАРНЫМ месяцам — база для ОПиУ.

    Считается по дням, поэтому разные календари выплат (25→25, месяц + 5-е,
    аннуитет) сводятся в один месячный P&L без искажений.
    """
    return await loan_analytics.accrual_by_month(db, project.id, utcnow().date(), months)


@router.get("/credit-lines", response_model=CreditLineListResponse)
async def credit_lines(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Все кредитные линии проекта: лимиты, выборка, свободный остаток."""
    return await list_credit_lines(db, project_id=project.id)


@router.get("/stuck", response_model=LoanStuckResponse)
async def stuck_loans(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Зависшие займы: срок вышел, а возврата или продления не сделали."""
    return await list_stuck_loans(db, project_id=project.id)


@router.get("/lenders/{counterparty_id}", response_model=LenderDetail)
async def lender_detail(
    counterparty_id: int,
    months_back: int = Query(12, ge=1, le=60),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Карточка заёмщика: итоги, история займов, проценты по периодам 25→25."""
    return await get_lender_detail(
        db, counterparty_id=counterparty_id, project_id=project.id, months_back=months_back
    )


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


@router.post(
    "/{loan_id}/repay",
    response_model=LoanResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def repay_loan(
    loan_id: int,
    body: LoanRepay,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Отметить возврат тела займа. Полный возврат закрывает займ."""
    service = LoanService(db)
    loan = await service.repay(loan_id=loan_id, data=body, project_id=project.id)
    return LoanResponse.model_validate(loan)


# ─── Кредитная линия ─────────────────────────────────────────────────────────


@router.get("/{loan_id}/credit-line", response_model=CreditLineDetail)
async def credit_line(
    loan_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Состояние линии: лимит, выбрано, свободно, движения и история ставок."""
    return await get_credit_line(db, loan_id=loan_id, project_id=project.id)


@router.post(
    "/{loan_id}/credit-line/draw",
    response_model=CreditLineDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def credit_line_draw(
    loan_id: int,
    body: CreditLineDraw,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Выборка транша по линии — увеличивает тело долга, проверяет лимит."""
    return await draw_credit_line(db, loan_id=loan_id, project_id=project.id, data=body)


@router.post(
    "/{loan_id}/rate-periods",
    response_model=CreditLineDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def add_rate_period(
    loan_id: int,
    body: LoanRatePeriodIn,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Ставка с даты (плавающая: ключевая ЦБ + надбавка). Повтор даты перезаписывает."""
    return await set_rate_period(db, loan_id=loan_id, project_id=project.id, data=body)


# ─── График платежей и разовые комиссии ──────────────────────────────────────


@router.get("/{loan_id}/schedule", response_model=LoanScheduleResponse)
async def loan_schedule_get(
    loan_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Плановый график платежей со сверкой факта: что заплатили, когда, с каким отклонением."""
    return await loan_schedule.get_schedule(db, loan_id=loan_id, project_id=project.id)


@router.put(
    "/{loan_id}/schedule",
    response_model=LoanScheduleResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def loan_schedule_put(
    loan_id: int,
    body: LoanScheduleReplace,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Заменить график целиком — он приходит из договора одной таблицей."""
    return await loan_schedule.replace_schedule(
        db, loan_id=loan_id, project_id=project.id, data=body
    )


@router.get("/{loan_id}/fees", response_model=LoanFeeListResponse)
async def loan_fees_list(
    loan_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Разовые комиссии по займу (за выдачу, за установление лимита)."""
    return await loan_schedule.list_fees(db, loan_id=loan_id, project_id=project.id)


@router.post(
    "/{loan_id}/fees",
    response_model=LoanFeeListResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def loan_fee_add(
    loan_id: int,
    body: LoanFeeIn,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Завести разовую комиссию. По умолчанию амортизируется на срок договора."""
    return await loan_schedule.add_fee(db, loan_id=loan_id, project_id=project.id, data=body)


@router.delete(
    "/{loan_id}/fees/{fee_id}",
    response_model=LoanFeeListResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def loan_fee_delete(
    loan_id: int,
    fee_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Удалить разовую комиссию."""
    return await loan_schedule.delete_fee(
        db, loan_id=loan_id, project_id=project.id, fee_id=fee_id
    )


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
