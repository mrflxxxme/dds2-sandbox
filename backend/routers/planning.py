"""
Router: /planning — orders, payments, incomes, lead_time, customs, cashflow
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy import select, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from sqlalchemy import or_

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
async def get_lead_times(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(LeadTime).order_by(LeadTime.direction))
    return result.scalars().all()


@router.post("/lead_times", response_model=LeadTimeSchema)
async def upsert_lead_time(payload: LeadTimeSchema, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(LeadTime).where(LeadTime.direction == payload.direction)
    )
    obj = result.scalar_one_or_none()
    if obj:
        obj.days = payload.days
    else:
        obj = LeadTime(**payload.model_dump(exclude={"id"}))
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
async def get_customs_topup(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CustomsTopup).order_by(CustomsTopup.date.desc())
    )
    topups = result.scalars().all()

    # Calculate allocated/remaining per topup
    alloc_result = await db.execute(
        select(
            CustomsAlloc.topup_txn_id,
            func.sum(CustomsAlloc.alloc_amount).label("allocated"),
        ).group_by(CustomsAlloc.topup_txn_id)
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
    db: AsyncSession = Depends(get_db),
):
    q = select(CustomsAlloc)
    if topup_txn_id:
        q = q.where(CustomsAlloc.topup_txn_id == topup_txn_id)
    q = q.order_by(CustomsAlloc.pay_date)
    result = await db.execute(q)
    return result.scalars().all()


@router.post("/customs/alloc", response_model=CustomsAllocSchema)
async def create_alloc(
    payload: CustomsAllocSchema, db: AsyncSession = Depends(get_db)
):
    obj = CustomsAlloc(**payload.model_dump(exclude={"id"}))
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


@router.delete("/customs/alloc/{alloc_id}")
async def delete_alloc(alloc_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CustomsAlloc).where(CustomsAlloc.id == alloc_id))
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
    """
    Calculate daily cashflow for the next `days` days.
    Includes overdue unpaid payments.
    """
    today = date.today()
    horizon = today + timedelta(days=days)

    # Planned incomes indexed by date
    inc_result = await db.execute(
        select(PlannedIncome).where(PlannedIncome.project_id == project.id, PlannedIncome.date <= horizon)
    )
    income_map: dict[date, Decimal] = {}
    for inc in inc_result.scalars().all():
        income_map[inc.date] = income_map.get(inc.date, Decimal("0")) + inc.amount_rub

    # WB payouts in transit → add as expected income (created_at + 2 days)
    transit_result = await db.execute(
        select(WbPayout).where(
            WbPayout.status.in_(["TRANSIT", "PROCESSING"]),
        )
    )
    for wp in transit_result.scalars().all():
        estimated = wp.created_at.date() + timedelta(days=2) if hasattr(wp.created_at, 'date') else wp.created_at + timedelta(days=2)
        if estimated < today:
            estimated = today  # overdue transit → show today
        if estimated <= horizon:
            income_map[estimated] = income_map.get(estimated, Decimal("0")) + wp.amount_rub

    # Planned payments (unpaid)
    pay_result = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.project_id == project.id,
            PlannedPayment.is_paid == False,
            PlannedPayment.pay_date <= horizon,
        )
    )
    payments = pay_result.scalars().all()

    # Overdue = pay_date < today → move to today
    expense_map: dict[date, Decimal] = {}
    for p in payments:
        amt = p.amount_rub or Decimal("0")
        if amt == 0 and p.amount and p.fx_rate:
            amt = p.amount * p.fx_rate
        elif amt == 0 and p.amount:
            amt = p.amount

        effective_date = p.pay_date if (p.pay_date and p.pay_date >= today) else today
        expense_map[effective_date] = expense_map.get(effective_date, Decimal("0")) + Decimal(str(amt))

    # Build daily series
    running = Decimal(str(starting_balance))
    rows = []
    for i in range(days + 1):
        d = today + timedelta(days=i)
        inc = income_map.get(d, Decimal("0"))
        exp = expense_map.get(d, Decimal("0"))
        net = inc - exp
        running += net
        rows.append({
            "date": str(d),
            "planned_income": float(inc),
            "planned_expense": float(exp),
            "net": float(net),
            "deficit_running": float(running),
        })

    return rows


# ─── Order Summary (plan vs fact) ─────────────────────────────────────────────

@router.get("/orders/{order_no}/summary")
async def order_summary(
    order_no: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Return plan vs fact for a specific order."""
    # Order plan
    result = await db.execute(select(Order).where(Order.order_no == order_no, Order.project_id == project.id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Order not found")

    # Planned payments
    pp_result = await db.execute(
        select(PlannedPayment).where(PlannedPayment.order_no == order_no, PlannedPayment.project_id == project.id)
    )
    planned_payments = pp_result.scalars().all()

    # Fact: order payments (annex_id = order_no)
    txn_order_result = await db.execute(
        select(Transaction).where(
            Transaction.project_id == project.id,
            Transaction.annex_id == str(order_no),
            Transaction.purpose_tag == "Заказ",
        )
    )
    txn_orders = txn_order_result.scalars().all()

    # Fact: logistics (invoice_id linked to this order's transactions)
    txn_log_result = await db.execute(
        select(Transaction).where(
            Transaction.project_id == project.id,
            Transaction.purpose_tag == "Логистика",
            Transaction.annex_id == str(order_no),
        )
    )
    txn_logistics = txn_log_result.scalars().all()

    # Fact: customs from customs_alloc
    alloc_result = await db.execute(
        select(CustomsAlloc).where(CustomsAlloc.order_no == order_no)
    )
    customs_allocs = alloc_result.scalars().all()

    from backend.schemas import OrderSchema, PlannedPaymentSchema, TransactionSchema, CustomsAllocSchema

    return {
        "order": OrderSchema.model_validate(order),
        "planned_payments": [PlannedPaymentSchema.model_validate(p) for p in planned_payments],
        "fact_order_payments": [TransactionSchema.model_validate(t) for t in txn_orders],
        "fact_logistics": [TransactionSchema.model_validate(t) for t in txn_logistics],
        "fact_customs_allocs": [CustomsAllocSchema.model_validate(a) for a in customs_allocs],
        "totals": {
            "plan_order": float(order.order_amount or 0),
            "plan_logistics_cny": float(order.logistics_cny or 0),
            "plan_customs_rub": float(order.customs_rub or 0),
            "fact_order": sum(float(t.expense) for t in txn_orders),
            "fact_customs": sum(float(a.alloc_amount or 0) for a in customs_allocs),
        }
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

    # Verify payment exists
    pp = await db.execute(select(PlannedPayment).where(PlannedPayment.id == payment_id))
    if not pp.scalar_one_or_none():
        raise HTTPException(404, "Payment not found")

    # Verify transaction exists
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

    # Re-sync this payment's paid_rub
    await _update_payment_paid(payment_id, db)

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

    # Re-sync this payment's paid_rub
    await _update_payment_paid(payment_id, db)

    return {"ok": True}


@router.get("/candidate_transactions")
async def get_candidate_transactions(
    direction: str = Query("ЗАКАЗ"),
    account: str = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get candidate transactions for manual linking. Filter by account or purpose_tag."""
    from backend.models import Account

    conditions = [Transaction.project_id == project.id, Transaction.expense > 0]

    if account:
        # Filter by specific account
        conditions.append(Transaction.account == account)
    else:
        # Fallback: filter by purpose_tag
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

def _parse_fts_pdf(pdf_bytes: bytes) -> list[dict]:
    """Parse FTS customs report PDF and extract DT lines grouped by DT number."""
    import re
    try:
        import pdfplumber
    except ImportError:
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pdfplumber",
                               "--break-system-packages", "-q"])
        import pdfplumber

    import io
    results = {}  # dt_number → {date, total, lines[]}

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Match DT lines: number, amount, ДТ, code, date, dt_number
                # Pattern: "2 05.02.2026 49 240,00 ДТ 10131010 05.02.2026 10131010/050226/5030475"
                m = re.match(
                    r'^\d+\s+'                    # row number
                    r'(\d{2}\.\d{2}\.\d{4})\s+'   # operation date
                    r'([\d\s]+[,\.]\d{2})\s+'     # amount (with spaces/comma)
                    r'ДТ\s+'                       # doc type = ДТ
                    r'\d+\s+'                      # customs code
                    r'\d{2}\.\d{2}\.\d{4}\s+'     # doc date
                    r'(\d{8}/\d{6}/\d{7})',            # DT number (8/6/7 digits)
                    line
                )
                if m:
                    op_date_str = m.group(1)
                    amount_str = m.group(2).replace(" ", "").replace(",", ".")
                    dt_number = m.group(3)
                    try:
                        amount = float(amount_str)
                        op_date = date(
                            int(op_date_str[6:10]),
                            int(op_date_str[3:5]),
                            int(op_date_str[0:2])
                        )
                    except (ValueError, IndexError):
                        continue

                    if dt_number not in results:
                        results[dt_number] = {"dt_date": op_date, "total": 0.0, "lines": []}
                    results[dt_number]["total"] += amount
                    results[dt_number]["lines"].append(amount)

    return [
        {"dt_number": k, "dt_date": v["dt_date"].isoformat(), "amount_rub": round(v["total"], 2),
         "lines": v["lines"]}
        for k, v in sorted(results.items(), key=lambda x: x[1]["dt_date"])
    ]


@router.post("/customs_dt/upload_fts")
async def upload_fts_report(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Upload FTS PDF report and parse DT declarations."""
    content = await file.read()
    parsed = _parse_fts_pdf(content)

    if not parsed:
        raise HTTPException(400, "Не удалось найти ДТ строки в PDF")

    created = 0
    skipped = 0
    for item in parsed:
        # Check if DT already exists
        existing = await db.execute(
            select(CustomsDT).where(CustomsDT.dt_number == item["dt_number"])
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue

        dt = CustomsDT(
            dt_number=item["dt_number"],
            dt_date=date.fromisoformat(item["dt_date"]),
            amount_rub=Decimal(str(item["amount_rub"])),
        )
        db.add(dt)
        created += 1

    await db.commit()
    return {"ok": True, "created": created, "skipped": skipped, "parsed": parsed}


@router.get("/customs_dt")
async def get_customs_dt_list(db: AsyncSession = Depends(get_db)):
    """List all parsed DT declarations."""
    result = await db.execute(select(CustomsDT).order_by(CustomsDT.dt_date.desc()))
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
async def update_customs_dt(dt_id: int, payload: dict, db: AsyncSession = Depends(get_db)):
    """Assign order_no to a DT declaration."""
    result = await db.execute(select(CustomsDT).where(CustomsDT.id == dt_id))
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
async def delete_customs_dt(dt_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CustomsDT).where(CustomsDT.id == dt_id))
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
    db: AsyncSession = Depends(get_db),
):
    """Upload WB payout Excel from seller cabinet. Upserts by request_id."""
    from backend.etl.parsers import parse_wb_payout_cabinet
    from datetime import datetime

    data = await file.read()
    parsed = parse_wb_payout_cabinet(data)

    if not parsed:
        raise HTTPException(400, "Не удалось распознать записи в файле")

    created, updated, skipped = 0, 0, 0
    for item in parsed:
        result = await db.execute(
            select(WbPayout).where(WbPayout.request_id == item["request_id"])
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
            obj = WbPayout(**item)
            db.add(obj)
            created += 1

    await db.commit()

    # Trigger reconciliation
    await _reconcile_wb_payouts(db)

    return {"ok": True, "created": created, "updated": updated, "skipped": skipped,
            "total_parsed": len(parsed)}


@router.get("/wb_payouts")
async def get_wb_payouts(
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List WB payouts, optionally filtered by status."""
    q = select(WbPayout).order_by(WbPayout.created_at.desc())
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


async def _reconcile_wb_payouts(db: AsyncSession):
    """
    Match WB payouts (not RECEIVED) with bank income transactions.
    Criteria: WB counterparty, amount ±1%, date within [-2, +5] days.
    """
    from datetime import datetime as dt_mod

    unmatched_result = await db.execute(
        select(WbPayout).where(WbPayout.status.in_(["TRANSIT", "PROCESSING", "PENDING"]))
    )
    payouts = unmatched_result.scalars().all()
    if not payouts:
        return

    # Already matched txn_ids
    matched_result = await db.execute(
        select(WbPayout.matched_txn_id).where(WbPayout.matched_txn_id.isnot(None))
    )
    already_matched = {r[0] for r in matched_result}

    # Date range
    min_date = min(p.created_at for p in payouts) - timedelta(days=2)
    max_date = max(p.created_at for p in payouts) + timedelta(days=5)

    # Candidate bank transactions (WB income)
    candidates_result = await db.execute(
        select(Transaction).where(
            Transaction.income > 0,
            Transaction.date >= min_date,
            Transaction.date <= max_date,
            or_(
                and_(
                    Transaction.cat_lvl1_2 == "Маркетплейсы",
                    Transaction.cat_lvl2_2 == "Wildberries",
                ),
                Transaction.counterparty.ilike("%вайлдберриз%"),
                Transaction.counterparty.ilike("%wildberries%"),
            )
        )
    )
    candidates = [t for t in candidates_result.scalars().all()
                  if t.txn_id not in already_matched]

    if not candidates:
        return

    # Greedy matching: largest payouts first
    used_txn_ids: set[str] = set()
    for payout in sorted(payouts, key=lambda p: p.amount_rub, reverse=True):
        best_match = None
        best_diff = float("inf")

        for txn in candidates:
            if txn.txn_id in used_txn_ids:
                continue

            # Date window
            txn_date = txn.date.date() if hasattr(txn.date, 'date') else txn.date
            payout_date = payout.created_at.date() if hasattr(payout.created_at, 'date') else payout.created_at
            delta = (txn_date - payout_date).days
            if delta < -2 or delta > 5:
                continue

            # Amount tolerance: ±1%
            diff = abs(float(txn.income) - float(payout.amount_rub))
            tolerance = float(payout.amount_rub) * 0.01
            if diff <= tolerance and diff < best_diff:
                best_diff = diff
                best_match = txn

        if best_match:
            payout.matched_txn_id = best_match.txn_id
            payout.matched_at = dt_mod.utcnow()
            payout.status = "RECEIVED"
            used_txn_ids.add(best_match.txn_id)

    await db.commit()


# ─── WB Forecast ─────────────────────────────────────────────────────────────

@router.post("/wb_forecast/refresh")
async def refresh_wb_forecast(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Auto-generate PlannedIncome for next 30 days based on
    7-day rolling average of actual WB income.
    """
    today = date.today()
    week_ago = today - timedelta(days=7)

    # Actual WB income from last 7 days
    result = await db.execute(
        select(
            func.coalesce(func.sum(Transaction.income), 0)
        ).where(
            Transaction.project_id == project.id,
            Transaction.income > 0,
            Transaction.is_cashflow2 == 1,
            Transaction.date >= week_ago,
            Transaction.date <= today,
            or_(
                and_(
                    Transaction.cat_lvl1_2 == "Маркетплейсы",
                    Transaction.cat_lvl2_2 == "Wildberries",
                ),
                Transaction.counterparty.ilike("%вайлдберриз%"),
                Transaction.counterparty.ilike("%wildberries%"),
            )
        )
    )
    total_7d = Decimal(str(result.scalar() or 0))
    daily_avg = (total_7d / Decimal("7")).quantize(Decimal("0.01"))

    if daily_avg <= 0:
        return {"ok": True, "daily_avg": 0, "message": "Нет данных WB за последние 7 дней"}

    # Delete stale auto-forecast
    await db.execute(
        text("DELETE FROM planned_incomes WHERE source = 'WB_AUTO' AND project_id = :pid"),
        {"pid": project.id},
    )

    # Get manual WB income dates to avoid overlap
    manual_result = await db.execute(
        select(PlannedIncome.date).where(PlannedIncome.source == "WB", PlannedIncome.project_id == project.id)
    )
    manual_dates = {row[0] for row in manual_result}

    # Insert forecast for next 30 days
    created = 0
    for i in range(1, 31):
        d = today + timedelta(days=i)
        if d in manual_dates:
            continue
        pi = PlannedIncome(date=d, amount_rub=daily_avg, source="WB_AUTO", project_id=project.id)
        db.add(pi)
        created += 1

    await db.commit()
    return {"ok": True, "daily_avg": float(daily_avg), "created": created}


async def _update_payment_paid(payment_id: int, db: AsyncSession):
    """Recalculate paid_rub for a payment from manual links."""
    links_result = await db.execute(
        select(PaymentFactLink).where(PaymentFactLink.payment_id == payment_id)
    )
    links = links_result.scalars().all()
    total_manual = sum(float(l.amount_rub or 0) for l in links)

    pp_result = await db.execute(select(PlannedPayment).where(PlannedPayment.id == payment_id))
    pp = pp_result.scalar_one_or_none()
    if pp:
        # Manual links add to whatever auto-sync found
        # For now, manual links directly set paid_rub
        pp.paid_rub = Decimal(str(round(total_manual, 2)))
        pp.is_paid = total_manual >= float(pp.amount_rub or 0) if pp.amount_rub else False
        await db.commit()
