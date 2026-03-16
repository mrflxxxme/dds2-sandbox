"""
Router: /planning — orders, payments, incomes, lead_time, cashflow, fact links, wb forecast.

Sub-routers:
- planning_customs.py: customs DT/topup/alloc
- planning_wb_payouts.py: WB payouts upload/reconcile
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.schemas import (
    OrderSchema, LeadTimeSchema, PlannedPaymentSchema,
    PlannedIncomeSchema, FactLinkCreate,
)
from backend.project_context import get_current_project
from backend.services import planning as planning_service

# Import sub-routers
from backend.routers.planning_customs import router as customs_router
from backend.routers.planning_wb_payouts import router as wb_payouts_router

router = APIRouter(prefix="/planning")

# Include sub-routers
router.include_router(customs_router)
router.include_router(wb_payouts_router)


# ─── Orders ──────────────────────────────────────────────────────────────────

@router.get("/orders", response_model=List[OrderSchema])
async def get_orders(
    limit: int = Query(500),
    offset: int = Query(0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await planning_service.get_orders(db, project.id, limit, offset)


@router.post("/orders", response_model=OrderSchema)
async def upsert_order(
    payload: OrderSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    data = payload.model_dump(exclude={"id"})
    return await planning_service.upsert_order(db, project.id, data, payload.id)


@router.delete("/orders/{order_id}")
async def delete_order(
    order_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await planning_service.delete_order(db, project.id, order_id)
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}


# ─── Lead Times ───────────────────────────────────────────────────────────────

@router.get("/lead_times", response_model=List[LeadTimeSchema])
async def get_lead_times(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await planning_service.get_lead_times(db, project.id)


@router.post("/lead_times", response_model=LeadTimeSchema)
async def upsert_lead_time(
    payload: LeadTimeSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await planning_service.upsert_lead_time(
        db, project.id, payload.direction, payload.days
    )


# ─── Planned Payments ─────────────────────────────────────────────────────────

@router.get("/payments", response_model=List[PlannedPaymentSchema])
async def get_payments(
    order_no: Optional[int] = Query(None),
    limit: int = Query(500),
    offset: int = Query(0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await planning_service.get_payments(db, project.id, order_no, limit, offset)


@router.post("/payments", response_model=PlannedPaymentSchema)
async def upsert_payment(
    payload: PlannedPaymentSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    data = payload.model_dump(exclude={"id"})
    return await planning_service.upsert_payment(db, project.id, data, payload.id)


@router.delete("/payments/{payment_id}")
async def delete_payment(
    payment_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await planning_service.delete_payment(db, project.id, payment_id)
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}


@router.post("/payments/{payment_id}/mark_paid")
async def mark_paid(
    payment_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await planning_service.mark_paid(db, project.id, payment_id)
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}


# ─── Planned Incomes ──────────────────────────────────────────────────────────

@router.get("/incomes", response_model=List[PlannedIncomeSchema])
async def get_incomes(
    limit: int = Query(500),
    offset: int = Query(0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await planning_service.get_incomes(db, project.id, limit, offset)


@router.post("/incomes", response_model=PlannedIncomeSchema)
async def upsert_income(
    payload: PlannedIncomeSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    data = payload.model_dump(exclude={"id"})
    return await planning_service.upsert_income(db, project.id, data, payload.id)


@router.delete("/incomes/{income_id}")
async def delete_income(
    income_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await planning_service.delete_income(db, project.id, income_id)
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}


# ─── Cashflow Daily ───────────────────────────────────────────────────────────

@router.get("/cashflow_daily")
async def get_cashflow_daily(
    days: int = Query(60),
    starting_balance: float = Query(0.0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await planning_service.calculate_cashflow_daily(db, project.id, days, starting_balance)


# ─── Order Summary (plan vs fact) ─────────────────────────────────────────────

@router.get("/orders/{order_no}/summary")
async def order_summary(
    order_no: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    from backend.schemas import TransactionSchema, CustomsAllocSchema
    data = await planning_service.get_order_summary(db, project.id, order_no)
    if not data:
        raise HTTPException(404, "Order not found")
    return {
        "order": OrderSchema.model_validate(data["order"]),
        "planned_payments": [PlannedPaymentSchema.model_validate(p) for p in data["planned_payments"]],
        "fact_order_payments": [TransactionSchema.model_validate(t) for t in data["fact_order_payments"]],
        "fact_logistics": [TransactionSchema.model_validate(t) for t in data["fact_logistics"]],
        "fact_customs_allocs": [CustomsAllocSchema.model_validate(a) for a in data["fact_customs_allocs"]],
        "totals": data["totals"],
    }


# ─── Manual Sync Plan Payments ───────────────────────────────────────────────

@router.post("/sync_plan_payments")
async def sync_plan_payments_endpoint(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    import asyncio
    from backend.database import SyncSessionLocal
    from backend.etl.service import _sync_plan_payments

    pid = project.id
    loop = asyncio.get_running_loop()

    def _run():
        with SyncSessionLocal() as sync_db:
            _sync_plan_payments(sync_db, pid)
            sync_db.commit()

    await loop.run_in_executor(None, _run)
    return {"ok": True}


# ─── Manual Fact Links ───────────────────────────────────────────────────────

@router.get("/fact_links/{payment_id}")
async def get_fact_links(
    payment_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    links = await planning_service.get_fact_links(db, project.id, payment_id)
    return [
        {"id": l.id, "payment_id": l.payment_id, "txn_id": l.txn_id,
         "amount_rub": float(l.amount_rub), "note": l.note}
        for l in links
    ]


@router.post("/fact_links")
async def create_fact_link(
    payload: FactLinkCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    link, error = await planning_service.create_fact_link(
        db, project.id, payload.payment_id, payload.txn_id, float(payload.amount_rub), payload.note
    )
    if error:
        raise HTTPException(404, error)
    return {"ok": True, "id": link.id}


@router.delete("/fact_links/{link_id}")
async def delete_fact_link(
    link_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await planning_service.delete_fact_link(db, project.id, link_id)
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}


@router.get("/candidate_transactions")
async def get_candidate_transactions(
    direction: str = Query("ЗАКАЗ"),
    account: str = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    txns = await planning_service.get_candidate_transactions(db, project.id, direction, account)
    return [
        {
            "txn_id": t.txn_id,
            "date": t.date.isoformat() if t.date else None,
            "expense": float(t.expense),
            "currency": t.currency,
            "account": t.account,
            "counterparty": t.counterparty,
            "purpose": (t.purpose or "")[:200],
            "annex_id": t.annex_id,
            "invoice_id": t.invoice_id,
        }
        for t in txns
    ]


@router.get("/accounts_list")
async def get_accounts_list(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    accs = await planning_service.get_accounts_list(db, project.id)
    return [
        {"id": a.id, "account": a.account, "bank": a.bank, "currency": a.currency}
        for a in accs
    ]


# ─── WB Forecast ─────────────────────────────────────────────────────────────

@router.post("/wb_forecast/refresh")
async def refresh_wb_forecast(
    trend_days: int = Query(7, ge=1, le=90),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await planning_service.refresh_wb_forecast(db, project.id, trend_days=trend_days)
