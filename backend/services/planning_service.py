"""
Planning service — business logic for planning module.

Handles:
- CRUD: orders, lead times, payments, incomes, customs topup/alloc
- Fact links & candidate transactions
- Customs DT (FTS report parsing)
- WB payouts (upload, list, reconcile)
- Cashflow calculation, order summary, WB forecast

Extracted from routers/planning.py to keep router thin (HTTP only).
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, and_, text, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    PlannedPayment, PlannedIncome, WbPayout, Order,
    Transaction, CustomsAlloc, PaymentFactLink,
    LeadTime, CustomsTopup, CustomsDT,
)

logger = logging.getLogger("dds.planning")


# ─── Orders CRUD ─────────────────────────────────────────────────────────────

async def get_orders(db: AsyncSession, project_id: int, limit: int = 500, offset: int = 0):
    result = await db.execute(
        select(Order).where(Order.project_id == project_id, Order.is_deleted == False)
        .order_by(Order.planned_ship_date.desc().nullslast())
        .limit(limit).offset(offset)
    )
    return result.scalars().all()


async def upsert_order(db: AsyncSession, project_id: int, data: dict, obj_id: int | None):
    if obj_id:
        result = await db.execute(
            select(Order).where(Order.id == obj_id, Order.project_id == project_id)
        )
        obj = result.scalar_one_or_none()
        if obj:
            for k, v in data.items():
                setattr(obj, k, v)
        else:
            obj = Order(**data, project_id=project_id)
            db.add(obj)
    else:
        obj = Order(**data, project_id=project_id)
        db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def delete_order(db: AsyncSession, project_id: int, order_id: int):
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.project_id == project_id)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    await db.delete(obj)
    await db.commit()
    return True


# ─── Lead Times CRUD ─────────────────────────────────────────────────────────

async def get_lead_times(db: AsyncSession, project_id: int):
    result = await db.execute(
        select(LeadTime).where(LeadTime.project_id == project_id)
        .order_by(LeadTime.direction)
    )
    return result.scalars().all()


async def upsert_lead_time(db: AsyncSession, project_id: int, direction: str, days: int):
    result = await db.execute(
        select(LeadTime).where(
            LeadTime.project_id == project_id,
            LeadTime.direction == direction,
        )
    )
    obj = result.scalar_one_or_none()
    if obj:
        obj.days = days
    else:
        obj = LeadTime(project_id=project_id, direction=direction, days=days)
        db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


# ─── Payments CRUD ───────────────────────────────────────────────────────────

async def get_payments(db: AsyncSession, project_id: int, order_no: int | None = None, limit: int = 500, offset: int = 0):
    q = select(PlannedPayment).where(PlannedPayment.project_id == project_id, PlannedPayment.is_deleted == False)
    if order_no:
        q = q.where(PlannedPayment.order_no == order_no)
    q = q.order_by(PlannedPayment.pay_date).limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


async def upsert_payment(db: AsyncSession, project_id: int, data: dict, obj_id: int | None):
    if obj_id:
        result = await db.execute(
            select(PlannedPayment).where(
                PlannedPayment.id == obj_id, PlannedPayment.project_id == project_id
            )
        )
        obj = result.scalar_one_or_none()
        if obj:
            for k, v in data.items():
                setattr(obj, k, v)
        else:
            obj = PlannedPayment(**data, project_id=project_id)
            db.add(obj)
    else:
        obj = PlannedPayment(**data, project_id=project_id)
        db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def delete_payment(db: AsyncSession, project_id: int, payment_id: int):
    result = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.id == payment_id, PlannedPayment.project_id == project_id
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    await db.delete(obj)
    await db.commit()
    return True


async def mark_paid(db: AsyncSession, project_id: int, payment_id: int):
    result = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.id == payment_id, PlannedPayment.project_id == project_id
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    obj.is_paid = True
    await db.commit()
    return True


# ─── Incomes CRUD ────────────────────────────────────────────────────────────

async def get_incomes(db: AsyncSession, project_id: int, limit: int = 500, offset: int = 0):
    result = await db.execute(
        select(PlannedIncome).where(PlannedIncome.project_id == project_id)
        .order_by(PlannedIncome.date)
        .limit(limit).offset(offset)
    )
    return result.scalars().all()


async def upsert_income(db: AsyncSession, project_id: int, data: dict, obj_id: int | None):
    if obj_id:
        result = await db.execute(
            select(PlannedIncome).where(
                PlannedIncome.id == obj_id, PlannedIncome.project_id == project_id
            )
        )
        obj = result.scalar_one_or_none()
        if obj:
            obj.date = data.get("date", obj.date)
            obj.amount_rub = data.get("amount_rub", obj.amount_rub)
            obj.source = data.get("source", obj.source)
        else:
            obj = PlannedIncome(**data, project_id=project_id)
            db.add(obj)
    else:
        obj = PlannedIncome(**data, project_id=project_id)
        db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def delete_income(db: AsyncSession, project_id: int, income_id: int):
    result = await db.execute(
        select(PlannedIncome).where(
            PlannedIncome.id == income_id, PlannedIncome.project_id == project_id
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    await db.delete(obj)
    await db.commit()
    return True


# ─── Customs Topup / Alloc ───────────────────────────────────────────────────

async def get_customs_topup(db: AsyncSession, project_id: int):
    result = await db.execute(
        select(CustomsTopup).where(CustomsTopup.project_id == project_id)
        .order_by(CustomsTopup.date.desc())
    )
    topups = result.scalars().all()

    alloc_result = await db.execute(
        select(
            CustomsAlloc.topup_txn_id,
            func.sum(CustomsAlloc.alloc_amount).label("allocated"),
        ).where(CustomsAlloc.project_id == project_id)
        .group_by(CustomsAlloc.topup_txn_id)
    )
    alloc_map = {row.topup_txn_id: Decimal(str(row.allocated or 0)) for row in alloc_result}

    return topups, alloc_map


async def get_customs_alloc(db: AsyncSession, project_id: int, topup_txn_id: str | None = None):
    q = select(CustomsAlloc).where(CustomsAlloc.project_id == project_id)
    if topup_txn_id:
        q = q.where(CustomsAlloc.topup_txn_id == topup_txn_id)
    q = q.order_by(CustomsAlloc.pay_date)
    result = await db.execute(q)
    return result.scalars().all()


async def create_alloc(db: AsyncSession, project_id: int, data: dict):
    obj = CustomsAlloc(project_id=project_id, **data)
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def delete_alloc(db: AsyncSession, project_id: int, alloc_id: int):
    result = await db.execute(
        select(CustomsAlloc).where(
            CustomsAlloc.id == alloc_id, CustomsAlloc.project_id == project_id
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    await db.delete(obj)
    await db.commit()
    return True


# ─── Fact Links ──────────────────────────────────────────────────────────────

async def get_fact_links(db: AsyncSession, payment_id: int):
    result = await db.execute(
        select(PaymentFactLink).where(PaymentFactLink.payment_id == payment_id)
    )
    return result.scalars().all()


async def create_fact_link(db: AsyncSession, payment_id: int, txn_id: str,
                           amount_rub: float, note: str | None = None):
    pp = await db.execute(select(PlannedPayment).where(PlannedPayment.id == payment_id))
    if not pp.scalar_one_or_none():
        return None, "Payment not found"

    txn = await db.execute(select(Transaction).where(Transaction.txn_id == txn_id))
    if not txn.scalar_one_or_none():
        return None, "Transaction not found"

    link = PaymentFactLink(
        payment_id=payment_id,
        txn_id=txn_id,
        amount_rub=Decimal(str(amount_rub)),
        note=note,
    )
    db.add(link)
    await db.commit()
    await update_payment_paid_amount(payment_id, db)
    return link, None


async def delete_fact_link(db: AsyncSession, link_id: int):
    result = await db.execute(
        select(PaymentFactLink).where(PaymentFactLink.id == link_id)
    )
    link = result.scalar_one_or_none()
    if not link:
        return None
    payment_id = link.payment_id
    await db.delete(link)
    await db.commit()
    await update_payment_paid_amount(payment_id, db)
    return True


async def get_candidate_transactions(db: AsyncSession, project_id: int,
                                     direction: str = "ЗАКАЗ",
                                     account: str | None = None):
    conditions = [Transaction.project_id == project_id, Transaction.is_deleted == False, Transaction.expense > 0]
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
    return result.scalars().all()


async def get_accounts_list(db: AsyncSession):
    from backend.models import Account
    result = await db.execute(select(Account))
    return result.scalars().all()


# ─── Customs DT ──────────────────────────────────────────────────────────────

async def upload_fts_and_create_dts(db: AsyncSession, project_id: int, parsed: list[dict]):
    """Create CustomsDT records from parsed FTS data, skipping duplicates."""
    created, skipped = 0, 0
    for item in parsed:
        existing = await db.execute(
            select(CustomsDT).where(
                CustomsDT.project_id == project_id,
                CustomsDT.dt_number == item["dt_number"],
            )
        )
        if existing.scalar_one_or_none():
            skipped += 1
            continue
        dt = CustomsDT(
            project_id=project_id,
            dt_number=item["dt_number"],
            dt_date=date.fromisoformat(item["dt_date"]),
            amount_rub=Decimal(str(item["amount_rub"])),
        )
        db.add(dt)
        created += 1
    await db.commit()
    return created, skipped


async def get_customs_dt_list(db: AsyncSession, project_id: int):
    result = await db.execute(
        select(CustomsDT).where(CustomsDT.project_id == project_id)
        .order_by(CustomsDT.dt_date.desc())
    )
    return result.scalars().all()


async def update_customs_dt(db: AsyncSession, project_id: int, dt_id: int, payload: dict):
    result = await db.execute(
        select(CustomsDT).where(CustomsDT.id == dt_id, CustomsDT.project_id == project_id)
    )
    dt = result.scalar_one_or_none()
    if not dt:
        return None
    if "order_no" in payload:
        dt.order_no = payload["order_no"] if payload["order_no"] else None
    if "note" in payload:
        dt.note = payload["note"]
    await db.commit()
    return True


async def delete_customs_dt(db: AsyncSession, project_id: int, dt_id: int):
    result = await db.execute(
        select(CustomsDT).where(CustomsDT.id == dt_id, CustomsDT.project_id == project_id)
    )
    dt = result.scalar_one_or_none()
    if not dt:
        return None
    await db.delete(dt)
    await db.commit()
    return True


# ─── WB Payouts ──────────────────────────────────────────────────────────────

async def upload_wb_payouts(db: AsyncSession, project_id: int, parsed: list[dict]):
    """Upsert WB payouts from parsed Excel data."""
    created, updated, skipped = 0, 0, 0
    for item in parsed:
        result = await db.execute(
            select(WbPayout).where(
                WbPayout.project_id == project_id,
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
            obj = WbPayout(project_id=project_id, **item)
            db.add(obj)
            created += 1
    await db.commit()
    return created, updated, skipped


async def get_wb_payouts(db: AsyncSession, project_id: int, status: str | None = None, limit: int = 500, offset: int = 0):
    q = select(WbPayout).where(WbPayout.project_id == project_id).order_by(WbPayout.created_at.desc())
    if status:
        q = q.where(WbPayout.status == status)
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


async def delete_wb_payout(db: AsyncSession, payout_id: int):
    result = await db.execute(select(WbPayout).where(WbPayout.id == payout_id))
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    await db.delete(obj)
    await db.commit()
    return True


async def manual_reconcile_wb(db: AsyncSession, payout_id: int, txn_id: str):
    """Manually match a WB payout with a bank transaction."""
    from datetime import datetime as dt_mod

    result = await db.execute(select(WbPayout).where(WbPayout.id == payout_id))
    payout = result.scalar_one_or_none()
    if not payout:
        return None, "Payout not found"

    txn = await db.execute(select(Transaction).where(Transaction.txn_id == txn_id))
    if not txn.scalar_one_or_none():
        return None, "Transaction not found"

    payout.matched_txn_id = txn_id
    payout.matched_at = datetime.utcnow()
    payout.status = "RECEIVED"
    await db.commit()
    return True, None


# ─── Cashflow daily ─────────────────────────────────────────────────────────

async def calculate_cashflow_daily(
    db: AsyncSession,
    project_id: int,
    days: int = 60,
    starting_balance: float = 0.0,
) -> list[dict]:
    """
    Calculate daily cashflow for the next `days` days.
    Includes overdue unpaid payments moved to today.
    Returns list of {date, planned_income, planned_expense, net, deficit_running}.
    If starting_balance is 0 (default), auto-calculates from current RUB account balances.
    """
    today = date.today()
    horizon = today + timedelta(days=days)

    # Auto-calculate starting balance from account balances if not provided
    if starting_balance == 0.0:
        from backend.models import Account, OpeningBalance
        # Sum opening balances for RUB accounts
        ob_result = await db.execute(
            select(OpeningBalance).where(
                OpeningBalance.project_id == project_id,
                OpeningBalance.currency == "RUB",
            )
        )
        total_ob = sum(
            ob.opening_balance for ob in ob_result.scalars().all()
        )
        # Sum net from all RUB transactions up to today
        net_result = await db.execute(
            select(func.coalesce(func.sum(Transaction.net), 0)).where(
                Transaction.project_id == project_id,
                Transaction.currency == "RUB",
                Transaction.date <= today,
            )
        )
        total_net = Decimal(str(net_result.scalar() or 0))
        rub_balance = total_ob + total_net

        # Add foreign currency balances (CNY, USD) converted to RUB
        fx_balance_rub = Decimal("0")
        for ccy in ("CNY", "USD"):
            # Get account balance in foreign currency
            ob_fx = await db.execute(
                select(OpeningBalance).where(
                    OpeningBalance.project_id == project_id,
                    OpeningBalance.currency == ccy,
                )
            )
            ccy_ob = sum(o.opening_balance for o in ob_fx.scalars().all())
            net_fx = await db.execute(
                select(func.coalesce(func.sum(Transaction.net), 0)).where(
                    Transaction.project_id == project_id,
                    Transaction.currency == ccy,
                    Transaction.date <= today,
                )
            )
            ccy_net = Decimal(str(net_fx.scalar() or 0))
            ccy_balance = ccy_ob + ccy_net
            if ccy_balance == 0:
                continue

            # Get average fx_rate from recent PlannedPayments for this currency
            avg_rate_result = await db.execute(
                select(func.avg(PlannedPayment.fx_rate)).where(
                    PlannedPayment.project_id == project_id,
                    PlannedPayment.currency.ilike(f"%{ccy}%"),
                    PlannedPayment.fx_rate.isnot(None),
                    PlannedPayment.fx_rate > 0,
                )
            )
            avg_rate = avg_rate_result.scalar()
            if avg_rate:
                fx_balance_rub += ccy_balance * Decimal(str(avg_rate))

        starting_balance = float(rub_balance + fx_balance_rub)

    # Planned incomes indexed by date
    inc_result = await db.execute(
        select(PlannedIncome).where(
            PlannedIncome.project_id == project_id,
            PlannedIncome.date <= horizon,
        )
    )
    income_map: dict[date, Decimal] = {}
    for inc in inc_result.scalars().all():
        income_map[inc.date] = income_map.get(inc.date, Decimal("0")) + inc.amount_rub

    # WB payouts in transit → expected income (created_at + 2 days)
    transit_result = await db.execute(
        select(WbPayout).where(WbPayout.status.in_(["TRANSIT", "PROCESSING"]))
    )
    for wp in transit_result.scalars().all():
        estimated = (
            wp.created_at.date() + timedelta(days=2)
            if hasattr(wp.created_at, "date")
            else wp.created_at + timedelta(days=2)
        )
        if estimated < today:
            estimated = today
        if estimated <= horizon:
            income_map[estimated] = income_map.get(estimated, Decimal("0")) + wp.amount_rub

    # Planned payments (unpaid)
    pay_result = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.project_id == project_id,
            PlannedPayment.is_deleted == False,
            PlannedPayment.is_paid == False,
            PlannedPayment.pay_date <= horizon,
        )
    )
    payments = pay_result.scalars().all()

    expense_map: dict[date, Decimal] = {}
    for p in payments:
        amt = p.amount_rub or Decimal("0")
        if amt == 0 and p.amount and p.fx_rate:
            amt = p.amount * p.fx_rate
        elif amt == 0 and p.amount:
            amt = p.amount
        effective_date = p.pay_date if (p.pay_date and p.pay_date >= today) else today
        expense_map[effective_date] = (
            expense_map.get(effective_date, Decimal("0")) + Decimal(str(amt))
        )

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


# ─── Order summary ──────────────────────────────────────────────────────────

async def get_order_summary(
    db: AsyncSession,
    project_id: int,
    order_no: int,
) -> Optional[dict]:
    """Return plan vs fact for a specific order."""
    result = await db.execute(
        select(Order).where(Order.order_no == order_no, Order.project_id == project_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        return None

    pp_result = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.order_no == order_no,
            PlannedPayment.project_id == project_id,
        )
    )
    planned_payments = pp_result.scalars().all()

    txn_order_result = await db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.annex_id == str(order_no),
            Transaction.purpose_tag == "Заказ",
        )
    )
    txn_orders = txn_order_result.scalars().all()

    txn_log_result = await db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.purpose_tag == "Логистика",
            Transaction.annex_id == str(order_no),
        )
    )
    txn_logistics = txn_log_result.scalars().all()

    alloc_result = await db.execute(
        select(CustomsAlloc).where(CustomsAlloc.order_no == order_no)
    )
    customs_allocs = alloc_result.scalars().all()

    return {
        "order": order,
        "planned_payments": planned_payments,
        "fact_order_payments": txn_orders,
        "fact_logistics": txn_logistics,
        "fact_customs_allocs": customs_allocs,
        "totals": {
            "plan_order": float(order.order_amount or 0),
            "plan_logistics_cny": float(order.logistics_cny or 0),
            "plan_customs_rub": float(order.customs_rub or 0),
            "fact_order": sum(float(t.expense) for t in txn_orders),
            "fact_customs": sum(float(a.alloc_amount or 0) for a in customs_allocs),
        },
    }


# ─── Payment paid amount ────────────────────────────────────────────────────

async def update_payment_paid_amount(payment_id: int, db: AsyncSession):
    """Re-calculate paid_rub for a planned payment from its fact links."""
    links_result = await db.execute(
        select(PaymentFactLink).where(PaymentFactLink.payment_id == payment_id)
    )
    links = links_result.scalars().all()
    total_paid = sum(l.amount_rub or Decimal("0") for l in links)

    pp_result = await db.execute(
        select(PlannedPayment).where(PlannedPayment.id == payment_id)
    )
    payment = pp_result.scalar_one_or_none()
    if payment:
        payment.paid_rub = total_paid
        if total_paid >= (payment.amount_rub or Decimal("0")):
            payment.is_paid = True
        await db.commit()


# ─── FTS PDF parsing ────────────────────────────────────────────────────────

def parse_fts_pdf(pdf_bytes: bytes) -> list[dict]:
    """Parse FTS customs report PDF and extract DT lines grouped by DT number."""
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
                m = re.match(
                    r'^\d+\s+'                    # row number
                    r'(\d{2}\.\d{2}\.\d{4})\s+'   # operation date
                    r'([\d\s]+[,\.]\d{2})\s+'     # amount
                    r'ДТ\s+'                       # doc type = ДТ
                    r'\d+\s+'                      # customs code
                    r'\d{2}\.\d{2}\.\d{4}\s+'     # doc date
                    r'(\d{8}/\d{6}/\d{7})',       # DT number
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


# ─── WB payout reconciliation ───────────────────────────────────────────────

async def reconcile_wb_payouts(db: AsyncSession):
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

    matched_result = await db.execute(
        select(WbPayout.matched_txn_id).where(WbPayout.matched_txn_id.isnot(None))
    )
    already_matched = {r[0] for r in matched_result}

    min_date = min(p.created_at for p in payouts) - timedelta(days=2)
    max_date = max(p.created_at for p in payouts) + timedelta(days=5)

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
            txn_date = txn.date.date() if hasattr(txn.date, 'date') else txn.date
            payout_date = payout.created_at.date() if hasattr(payout.created_at, 'date') else payout.created_at
            delta = (txn_date - payout_date).days
            if delta < -2 or delta > 5:
                continue
            diff = abs(float(txn.income) - float(payout.amount_rub))
            tolerance = float(payout.amount_rub) * 0.01
            if diff <= tolerance and diff < best_diff:
                best_diff = diff
                best_match = txn

        if best_match:
            payout.matched_txn_id = best_match.txn_id
            payout.matched_at = datetime.utcnow()
            payout.status = "RECEIVED"
            used_txn_ids.add(best_match.txn_id)

    await db.commit()


# ─── WB Forecast ─────────────────────────────────────────────────────────────

async def refresh_wb_forecast(
    db: AsyncSession,
    project_id: int,
    forecast_days: int = 60,
    trend_days: int = 7,
) -> dict:
    """
    Auto-generate PlannedIncome for next `forecast_days` days based on
    `trend_days` history of actual WB income, respecting day-of-week patterns
    (weekends = 0, Monday = accumulated payout for Sat+Sun+Mon).
    """
    today = date.today()
    lookback = today - timedelta(days=trend_days)

    # Get daily WB income for the lookback period
    result = await db.execute(
        select(
            func.date(Transaction.date).label("day"),
            func.sum(Transaction.income).label("daily_income"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.income > 0,
            Transaction.date >= lookback,
            Transaction.date <= today,
            or_(
                and_(
                    Transaction.cat_lvl1_2 == "Маркетплейсы",
                    Transaction.cat_lvl2_2 == "Wildberries",
                ),
                Transaction.counterparty.ilike("%вайлдберриз%"),
                Transaction.counterparty.ilike("%wildberries%"),
                Transaction.counterparty.ilike("%вайлд%"),
            )
        ).group_by(func.date(Transaction.date))
    )
    daily_data = {row.day: Decimal(str(row.daily_income or 0)) for row in result}

    if not daily_data:
        return {"ok": True, "daily_avg": 0, "message": f"Нет данных WB за последние {trend_days} дней"}

    # Build day-of-week pattern (0=Mon, 6=Sun)
    # Fill ALL days in the lookback period (including zeros)
    weekday_totals: dict[int, Decimal] = {i: Decimal("0") for i in range(7)}
    weekday_counts: dict[int, int] = {i: 0 for i in range(7)}

    for day_offset in range(trend_days):
        d = lookback + timedelta(days=day_offset + 1)
        if d > today:
            break
        wd = d.weekday()
        weekday_counts[wd] += 1
        if hasattr(d, 'isoformat'):
            # Check both date and string key formats
            amount = daily_data.get(d, Decimal("0"))
            if amount == 0:
                amount = daily_data.get(d.isoformat(), Decimal("0"))
        else:
            amount = daily_data.get(d, Decimal("0"))
        weekday_totals[wd] += amount

    # Calculate average per weekday
    weekday_avg: dict[int, Decimal] = {}
    for wd in range(7):
        if weekday_counts[wd] > 0:
            weekday_avg[wd] = (weekday_totals[wd] / Decimal(str(weekday_counts[wd]))).quantize(Decimal("0.01"))
        else:
            weekday_avg[wd] = Decimal("0")

    # Calculate overall: total income / total days = true daily average
    total_income = sum(daily_data.values())
    daily_avg = (total_income / Decimal(str(trend_days))).quantize(Decimal("0.01")) if trend_days > 0 else Decimal("0")
    weekly_total = sum(weekday_avg.values())

    if daily_avg <= 0:
        return {"ok": True, "daily_avg": 0, "message": "Нет данных WB за последние 28 дней"}

    # Delete stale auto-forecast
    await db.execute(
        text("DELETE FROM planned_incomes WHERE source = 'WB_AUTO' AND project_id = :pid"),
        {"pid": project_id},
    )

    # Get manual WB income dates to avoid overlap
    manual_result = await db.execute(
        select(PlannedIncome.date).where(
            PlannedIncome.source == "WB",
            PlannedIncome.project_id == project_id,
        )
    )
    manual_dates = {row[0] for row in manual_result}

    created = 0
    weekday_detail = {}
    for i in range(1, forecast_days + 1):
        d = today + timedelta(days=i)
        if d in manual_dates:
            continue
        wd = d.weekday()
        amount = weekday_avg.get(wd, Decimal("0"))
        if amount <= 0:
            continue
        pi = PlannedIncome(date=d, amount_rub=amount, source="WB_AUTO", project_id=project_id)
        db.add(pi)
        created += 1

    await db.commit()

    # Build weekday detail for response
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    for wd in range(7):
        weekday_detail[day_names[wd]] = float(weekday_avg[wd])

    return {
        "ok": True,
        "daily_avg": float(daily_avg),
        "weekly_total": float(weekly_total),
        "weekday_pattern": weekday_detail,
        "created": created,
        "forecast_days": forecast_days,
    }

