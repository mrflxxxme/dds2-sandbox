"""
Router: /planning — orders, payments, incomes, lead_time, customs, cashflow.
Thin HTTP layer — complex business logic is in services/planning_service.py.
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import (
    Order, LeadTime, PlannedPayment, PlannedIncome,
    CustomsTopup, CustomsAlloc, Transaction, PaymentFactLink, CustomsDT,
    WbPayout, Project,
)
from backend.schemas import (
    OrderSchema, LeadTimeSchema, PlannedPaymentSchema,
    PlannedIncomeSchema, CustomsTopupSchema, CustomsAllocSchema,
    WbPayoutSchema,
)
from backend.project_context import get_current_project
from backend.services import planning_service

router = APIRouter(prefix="/planning")


# ─── Orders ──────────────────────────────────────────────────────────────────

@router.get("/orders", response_model=List[OrderSchema])
async def get_orders(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Order).where(Order.project_id == project.id)
        .order_by(Order.planned_ship_date.desc().nullslast())
    )
    return result.scalars().all()


@router.post("/orders", response_model=OrderSchema)
async def upsert_order(
    payload: OrderSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    if payload.id:
        result = await db.execute(select(Order).where(Order.id == payload.id, Order.project_id == project.id))
        obj = result.scalar_one_or_none()
        if obj:
            for k, v in payload.model_dump(exclude={"id"}).items():
                setattr(obj, k, v)
        else:
            obj = Order(**payload.model_dump(exclude={"id"}), project_id=project.id)
            db.add(obj)
    else:
        obj = Order(**payload.model_dump(exclude={"id"}), project_id=project.id)
        db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/orders/{order_id}")
async def delete_order(
    order_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Order).where(Order.id == order_id, Order.project_id == project.id))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Not found")
    await db.delete(obj)
    await db.commit()
    return {"ok": True}


# ─── Lead Times ───────────────────────────────────────────────────────────────

@router.get("/lead_times", response_model=List[LeadTimeSchema])
async def get_lead_times(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LeadTime).where(LeadTime.project_id == project.id)
        .order_by(LeadTime.direction)
    )
    return result.scalars().all()


@router.post("/lead_times", response_model=LeadTimeSchema)
async def upsert_lead_time(
    payload: LeadTimeSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(LeadTime).where(
            LeadTime.project_id == project.id,
            LeadTime.direction == payload.direction,
        )
    )
    obj = result.scalar_one_or_none()
    if obj:
        obj.days = payload.days
    else:
        obj = LeadTime(project_id=project.id, **payload.model_dump(exclude={"id"}))
        db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


# ─── Planned Payments ─────────────────────────────────────────────────────────

@router.get("/payments", response_model=List[PlannedPaymentSchema])
async def get_payments(
    order_no: Optional[int] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    q = select(PlannedPayment).where(PlannedPayment.project_id == project.id)
    if order_no:
        q = q.where(PlannedPayment.order_no == order_no)
    q = q.order_by(PlannedPayment.pay_date)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/payments", response_model=PlannedPaymentSchema)
async def upsert_payment(
    payload: PlannedPaymentSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    if payload.id:
        result = await db.execute(
            select(PlannedPayment).where(PlannedPayment.id == payload.id, PlannedPayment.project_id == project.id)
        )
        obj = result.scalar_one_or_none()
        if obj:
            for k, v in payload.model_dump(exclude={"id"}).items():
                setattr(obj, k, v)
        else:
            obj = PlannedPayment(**payload.model_dump(exclude={"id"}), project_id=project.id)
            db.add(obj)
    else:
        obj = PlannedPayment(**payload.model_dump(exclude={"id"}), project_id=project.id)
        db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/payments/{payment_id}")
async def delete_payment(
    payment_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PlannedPayment).where(PlannedPayment.id == payment_id, PlannedPayment.project_id == project.id)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Not found")
    await db.delete(obj)
    await db.commit()
    return {"ok": True}


@router.post("/payments/{payment_id}/mark_paid")
async def mark_paid(
    payment_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PlannedPayment).where(PlannedPayment.id == payment_id, PlannedPayment.project_id == project.id)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Not found")
    obj.is_paid = True
    await db.commit()
    return {"ok": True}


# ─── Planned Incomes ──────────────────────────────────────────────────────────

@router.get("/incomes", response_model=List[PlannedIncomeSchema])
async def get_incomes(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PlannedIncome).where(PlannedIncome.project_id == project.id)
        .order_by(PlannedIncome.date)
    )
    return result.scalars().all()


@router.post("/incomes", response_model=PlannedIncomeSchema)
async def upsert_income(
    payload: PlannedIncomeSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    if payload.id:
        result = await db.execute(
            select(PlannedIncome).where(PlannedIncome.id == payload.id, PlannedIncome.project_id == project.id)
        )
        obj = result.scalar_one_or_none()
        if obj:
            obj.date = payload.date
            obj.amount_rub = payload.amount_rub
            obj.source = payload.source
        else:
            obj = PlannedIncome(**payload.model_dump(exclude={"id"}), project_id=project.id)
            db.add(obj)
    else:
        obj = PlannedIncome(**payload.model_dump(exclude={"id"}), project_id=project.id)
        db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/incomes/{income_id}")
async def delete_income(
    income_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PlannedIncome).where(PlannedIncome.id == income_id, PlannedIncome.project_id == project.id)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Not found")
    await db.delete(obj)
    await db.commit()
    return {"ok": True}


# ─── Customs TOPUP / ALLOC ────────────────────────────────────────────────────

@router.get("/customs/topup", response_model=List[CustomsTopupSchema])
async def get_customs_topup(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CustomsTopup).where(CustomsTopup.project_id == project.id)
        .order_by(CustomsTopup.date.desc())
    )
    topups = result.scalars().all()

    alloc_result = await db.execute(
        select(
            CustomsAlloc.topup_txn_id,
            func.sum(CustomsAlloc.alloc_amount).label("allocated"),
        ).where(CustomsAlloc.project_id == project.id)
        .group_by(CustomsAlloc.topup_txn_id)
    )
    alloc_map = {row.topup_txn_id: Decimal(str(row.allocated or 0)) for row in alloc_result}

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
    q = select(CustomsAlloc).where(CustomsAlloc.project_id == project.id)
    if topup_txn_id:
        q = q.where(CustomsAlloc.topup_txn_id == topup_txn_id)
    q = q.order_by(CustomsAlloc.pay_date)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/customs/alloc", response_model=CustomsAllocSchema)
async def create_alloc(
    payload: CustomsAllocSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    obj = CustomsAlloc(project_id=project.id, **payload.model_dump(exclude={"id"}))
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/customs/alloc/{alloc_id}")
async def delete_alloc(
    alloc_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CustomsAlloc).where(CustomsAlloc.id == alloc_id, CustomsAlloc.project_id == project.id)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Not found")
    await db.delete(obj)
    await db.commit()
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
async def sync_plan_payments_endpoint(db: AsyncSession = Depends(get_db)):
    """Re-sync all planned payments with fact from bank statements and customs_alloc."""
    import asyncio
    from backend.database import SyncSessionLocal
    from backend.etl.service import _sync_plan_payments

    loop = asyncio.get_event_loop()

    def _run():
        with SyncSessionLocal() as sync_db:
            _sync_plan_payments(sync_db)
            sync_db.commit()

    await loop.run_in_executor(None, _run)
    return {"ok": True}


# ─── Manual Fact Links ───────────────────────────────────────────────────────

@router.get("/fact_links/{payment_id}")
async def get_fact_links(payment_id: int, db: AsyncSession = Depends(get_db)):
    """Get manual fact links for a planned payment."""
    result = await db.execute(
        select(PaymentFactLink).where(PaymentFactLink.payment_id == payment_id)
    )
    links = result.scalars().all()
    return [
        {"id": l.id, "payment_id": l.payment_id, "txn_id": l.txn_id,
         "amount_rub": float(l.amount_rub), "note": l.note}
        for l in links
    ]


@router.post("/fact_links")
async def create_fact_link(payload: dict, db: AsyncSession = Depends(get_db)):
    """Manually link a transaction to a planned payment."""
    payment_id = payload.get("payment_id")
    txn_id = payload.get("txn_id")
    amount_rub = payload.get("amount_rub")

    if not payment_id or not txn_id or amount_rub is None:
        raise HTTPException(400, "payment_id, txn_id, amount_rub required")

    pp = await db.execute(select(PlannedPayment).where(PlannedPayment.id == payment_id))
    if not pp.scalar_one_or_none():
        raise HTTPException(404, "Payment not found")

    txn = await db.execute(select(Transaction).where(Transaction.txn_id == txn_id))
    if not txn.scalar_one_or_none():
        raise HTTPException(404, "Transaction not found")

    link = PaymentFactLink(
        payment_id=payment_id,
        txn_id=txn_id,
        amount_rub=Decimal(str(amount_rub)),
        note=payload.get("note"),
    )
    db.add(link)
    await db.commit()

    await planning_service.update_payment_paid_amount(payment_id, db)
    return {"ok": True, "id": link.id}


@router.delete("/fact_links/{link_id}")
async def delete_fact_link(link_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PaymentFactLink).where(PaymentFactLink.id == link_id))
    link = result.scalar_one_or_none()
    if not link:
        raise HTTPException(404, "Not found")
    payment_id = link.payment_id
    await db.delete(link)
    await db.commit()

    await planning_service.update_payment_paid_amount(payment_id, db)
    return {"ok": True}


@router.get("/candidate_transactions")
async def get_candidate_transactions(
    direction: str = Query("ЗАКАЗ"),
    account: str = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get candidate transactions for manual linking."""
    from backend.models import Account

    conditions = [Transaction.project_id == project.id, Transaction.expense > 0]

    if account:
        conditions.append(Transaction.account == account)
    else:
        tag_map = {"ЗАКАЗ": "Заказ", "ДОСТАВКА": "Логистика"}
        purpose_tag = tag_map.get(direction)
        if purpose_tag:
            conditions.append(Transaction.purpose_tag == purpose_tag)

    result = await db.execute(
        select(Transaction).where(*conditions)
        .order_by(Transaction.date.desc()).limit(100)
    )
    txns = result.scalars().all()
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
async def get_accounts_list(db: AsyncSession = Depends(get_db)):
    """Get all accounts for filtering."""
    from backend.models import Account
    result = await db.execute(select(Account))
    accs = result.scalars().all()
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

    created = 0
    skipped = 0
    for item in parsed:
        existing = await db.execute(
            select(CustomsDT).where(
                CustomsDT.project_id == project.id,
                CustomsDT.dt_number == item["dt_number"],
            )
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue

        dt = CustomsDT(
            project_id=project.id,
            dt_number=item["dt_number"],
            dt_date=date.fromisoformat(item["dt_date"]),
            amount_rub=Decimal(str(item["amount_rub"])),
        )
        db.add(dt)
        created += 1

    await db.commit()
    return {"ok": True, "created": created, "skipped": skipped, "parsed": parsed}


@router.get("/customs_dt")
async def get_customs_dt_list(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List all parsed DT declarations."""
    result = await db.execute(
        select(CustomsDT).where(CustomsDT.project_id == project.id)
        .order_by(CustomsDT.dt_date.desc())
    )
    dts = result.scalars().all()
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
    payload: dict,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Assign order_no to a DT declaration."""
    result = await db.execute(
        select(CustomsDT).where(CustomsDT.id == dt_id, CustomsDT.project_id == project.id)
    )
    dt = result.scalar_one_or_none()
    if not dt:
        raise HTTPException(404, "Not found")

    if "order_no" in payload:
        dt.order_no = payload["order_no"] if payload["order_no"] else None
    if "note" in payload:
        dt.note = payload["note"]

    await db.commit()
    return {"ok": True}


@router.delete("/customs_dt/{dt_id}")
async def delete_customs_dt(
    dt_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CustomsDT).where(CustomsDT.id == dt_id, CustomsDT.project_id == project.id)
    )
    dt = result.scalar_one_or_none()
    if not dt:
        raise HTTPException(404, "Not found")
    await db.delete(dt)
    await db.commit()
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
    from datetime import datetime

    data = await file.read()

    from backend.utils.file_validation import validate_file_content
    validate_file_content(data, file.filename or "payouts.xlsx")

    parsed = parse_wb_payout_cabinet(data)

    if not parsed:
        raise HTTPException(400, "Не удалось распознать записи в файле")

    created, updated, skipped = 0, 0, 0
    for item in parsed:
        result = await db.execute(
            select(WbPayout).where(
                WbPayout.project_id == project.id,
                WbPayout.request_id == item["request_id"],
            )
        )
        obj = result.scalar_one_or_none()
        if obj:
            if obj.status != "RECEIVED":
                obj.wb_status_raw = item["wb_status_raw"]
                obj.status = item["status"]
                obj.bank_comment = item["bank_comment"]
                updated += 1
            else:
                skipped += 1
        else:
            obj = WbPayout(project_id=project.id, **item)
            db.add(obj)
            created += 1

    await db.commit()

    await planning_service.reconcile_wb_payouts(db)

    return {"ok": True, "created": created, "updated": updated, "skipped": skipped,
            "total_parsed": len(parsed)}


@router.get("/wb_payouts")
async def get_wb_payouts(
    status: Optional[str] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List WB payouts, optionally filtered by status."""
    q = select(WbPayout).where(WbPayout.project_id == project.id).order_by(WbPayout.created_at.desc())
    if status:
        q = q.where(WbPayout.status == status)
    result = await db.execute(q)
    payouts = result.scalars().all()
    return [WbPayoutSchema.model_validate(p).model_dump() for p in payouts]


@router.delete("/wb_payouts/{payout_id}")
async def delete_wb_payout(payout_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(WbPayout).where(WbPayout.id == payout_id))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Not found")
    await db.delete(obj)
    await db.commit()
    return {"ok": True}


@router.post("/wb_payouts/{payout_id}/reconcile")
async def manual_reconcile_wb(
    payout_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Manually match a WB payout with a bank transaction."""
    txn_id = payload.get("txn_id")
    if not txn_id:
        raise HTTPException(400, "txn_id required")

    result = await db.execute(select(WbPayout).where(WbPayout.id == payout_id))
    payout = result.scalar_one_or_none()
    if not payout:
        raise HTTPException(404, "Payout not found")

    txn = await db.execute(select(Transaction).where(Transaction.txn_id == txn_id))
    if not txn.scalar_one_or_none():
        raise HTTPException(404, "Transaction not found")

    from datetime import datetime as dt_mod
    payout.matched_txn_id = txn_id
    payout.matched_at = dt_mod.utcnow()
    payout.status = "RECEIVED"
    await db.commit()
    return {"ok": True}


# ─── WB Forecast ─────────────────────────────────────────────────────────────

@router.post("/wb_forecast/refresh")
async def refresh_wb_forecast(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Auto-generate PlannedIncome for next 30 days based on 7-day rolling average."""
    return await planning_service.refresh_wb_forecast(db, project.id)
