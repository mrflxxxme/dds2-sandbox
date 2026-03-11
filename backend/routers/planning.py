"""
Router: /planning — orders, payments, incomes, lead_time, customs, cashflow.
Thin HTTP layer — all business logic is in services/planning_service.py.
"""

from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.schemas import (
    OrderSchema, LeadTimeSchema, PlannedPaymentSchema,
    PlannedIncomeSchema, CustomsTopupSchema, CustomsAllocSchema,
    WbPayoutSchema, FactLinkCreate, CustomsDTUpdate, WbReconcileRequest,
)
from backend.project_context import get_current_project
from backend.services import planning_service

router = APIRouter(prefix="/planning")


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


# ─── Customs TOPUP / ALLOC ────────────────────────────────────────────────────

@router.get("/customs/topup", response_model=List[CustomsTopupSchema])
async def get_customs_topup(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    topups, alloc_map = await planning_service.get_customs_topup(db, project.id)
    out = []
    for t in topups:
        allocated = alloc_map.get(t.topup_txn_id, Decimal("0"))
        remaining = t.amount_rub - allocated
        d = CustomsTopupSchema.model_validate(t)
        d.allocated = allocated
        d.remaining = remaining
        out.append(d)
    return out


@router.get("/customs/alloc", response_model=List[CustomsAllocSchema])
async def get_customs_alloc(
    topup_txn_id: Optional[str] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await planning_service.get_customs_alloc(db, project.id, topup_txn_id)


@router.post("/customs/alloc", response_model=CustomsAllocSchema)
async def create_alloc(
    payload: CustomsAllocSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await planning_service.create_alloc(
        db, project.id, payload.model_dump(exclude={"id"})
    )


@router.delete("/customs/alloc/{alloc_id}")
async def delete_alloc(
    alloc_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await planning_service.delete_alloc(db, project.id, alloc_id)
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
    """Calculate daily cashflow for the next `days` days."""
    return await planning_service.calculate_cashflow_daily(db, project.id, days, starting_balance)


# ─── Order Summary (plan vs fact) ─────────────────────────────────────────────

@router.get("/orders/{order_no}/summary")
async def order_summary(
    order_no: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Return plan vs fact for a specific order."""
    from backend.schemas import TransactionSchema

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
    """Re-sync all planned payments with fact from bank statements and customs_alloc."""
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
    """Get manual fact links for a planned payment."""
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
    """Manually link a transaction to a planned payment."""
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
    """Get candidate transactions for manual linking."""
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
    """Get all accounts for filtering."""
    accs = await planning_service.get_accounts_list(db, project.id)
    return [
        {"id": a.id, "account": a.account, "bank": a.bank, "currency": a.currency}
        for a in accs
    ]


# ─── Customs DT (FTS report) ─────────────────────────────────────────────────

@router.post("/customs_dt/upload_fts")
async def upload_fts_report(
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Upload FTS PDF report and parse DT declarations."""
    content = await file.read()

    from backend.utils.file_validation import validate_file_content
    validate_file_content(content, file.filename or "report.pdf")

    parsed = planning_service.parse_fts_pdf(content)
    if not parsed:
        raise HTTPException(400, "Не удалось найти ДТ строки в PDF")

    created, skipped = await planning_service.upload_fts_and_create_dts(db, project.id, parsed)
    return {"ok": True, "created": created, "skipped": skipped, "parsed": parsed}


@router.get("/customs_dt")
async def get_customs_dt_list(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List all parsed DT declarations."""
    dts = await planning_service.get_customs_dt_list(db, project.id)
    return [
        {
            "id": d.id, "dt_number": d.dt_number,
            "dt_date": d.dt_date.isoformat(),
            "amount_rub": float(d.amount_rub),
            "order_no": d.order_no, "note": d.note,
        }
        for d in dts
    ]


@router.put("/customs_dt/{dt_id}")
async def update_customs_dt(
    dt_id: int,
    payload: CustomsDTUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Assign order_no to a DT declaration."""
    result = await planning_service.update_customs_dt(db, project.id, dt_id, payload.model_dump(exclude_unset=True))
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}


@router.delete("/customs_dt/{dt_id}")
async def delete_customs_dt(
    dt_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await planning_service.delete_customs_dt(db, project.id, dt_id)
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}


# ─── WB Payouts ──────────────────────────────────────────────────────────────

@router.post("/wb_payouts/upload")
async def upload_wb_payouts(
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Upload WB payout Excel from seller cabinet. Upserts by request_id."""
    from backend.etl.parsers import parse_wb_payout_cabinet

    data = await file.read()

    from backend.utils.file_validation import validate_file_content
    validate_file_content(data, file.filename or "payouts.xlsx")

    parsed = parse_wb_payout_cabinet(data)
    if not parsed:
        raise HTTPException(400, "Не удалось распознать записи в файле")

    created, updated, skipped = await planning_service.upload_wb_payouts(db, project.id, parsed)
    await planning_service.reconcile_wb_payouts(db)

    return {"ok": True, "created": created, "updated": updated, "skipped": skipped,
            "total_parsed": len(parsed)}


@router.get("/wb_payouts")
async def get_wb_payouts(
    status: Optional[str] = Query(None),
    limit: int = Query(500),
    offset: int = Query(0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List WB payouts, optionally filtered by status."""
    payouts = await planning_service.get_wb_payouts(db, project.id, status, limit, offset)
    return [WbPayoutSchema.model_validate(p).model_dump() for p in payouts]


@router.delete("/wb_payouts/{payout_id}")
async def delete_wb_payout(
    payout_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await planning_service.delete_wb_payout(db, project.id, payout_id)
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}


@router.post("/wb_payouts/{payout_id}/reconcile")
async def manual_reconcile_wb(
    payout_id: int,
    payload: WbReconcileRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Manually match a WB payout with a bank transaction."""
    result, error = await planning_service.manual_reconcile_wb(db, project.id, payout_id, payload.txn_id)
    if error:
        raise HTTPException(404, error)
    return {"ok": True}


# ─── WB Forecast ─────────────────────────────────────────────────────────────

@router.post("/wb_forecast/refresh")
async def refresh_wb_forecast(
    trend_days: int = Query(7, ge=1, le=90),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Auto-generate PlannedIncome for next 60 days based on trend_days rolling average."""
    return await planning_service.refresh_wb_forecast(db, project.id, trend_days=trend_days)
