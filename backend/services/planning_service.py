"""
Planning service — business logic for cashflow, order summary,
plan-vs-fact, WB payout reconciliation, FTS parsing, WB forecast.

Extracted from routers/planning.py to enable reuse and testing.
"""

import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, and_, text, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    PlannedPayment, PlannedIncome, WbPayout, Order,
    Transaction, CustomsAlloc, PaymentFactLink,
)

logger = logging.getLogger("dds.planning")


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
    """
    today = date.today()
    horizon = today + timedelta(days=days)

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
            payout.matched_at = dt_mod.utcnow()
            payout.status = "RECEIVED"
            used_txn_ids.add(best_match.txn_id)

    await db.commit()


# ─── WB Forecast ─────────────────────────────────────────────────────────────

async def refresh_wb_forecast(
    db: AsyncSession,
    project_id: int,
) -> dict:
    """
    Auto-generate PlannedIncome for next 30 days based on
    7-day rolling average of actual WB income.
    """
    today = date.today()
    week_ago = today - timedelta(days=7)

    result = await db.execute(
        select(
            func.coalesce(func.sum(Transaction.income), 0)
        ).where(
            Transaction.project_id == project_id,
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
    for i in range(1, 31):
        d = today + timedelta(days=i)
        if d in manual_dates:
            continue
        pi = PlannedIncome(date=d, amount_rub=daily_avg, source="WB_AUTO", project_id=project_id)
        db.add(pi)
        created += 1

    await db.commit()
    return {"ok": True, "daily_avg": float(daily_avg), "created": created}
