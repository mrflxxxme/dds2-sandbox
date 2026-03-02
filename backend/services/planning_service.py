"""
Planning service — business logic for cashflow, order summary, and plan-vs-fact.

Extracted from routers/planning.py to enable reuse and testing.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    PlannedPayment, PlannedIncome, WbPayout, Order,
    Transaction, CustomsAlloc, PaymentFactLink,
)


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

    # ── Planned incomes indexed by date ──
    inc_result = await db.execute(
        select(PlannedIncome).where(
            PlannedIncome.project_id == project_id,
            PlannedIncome.date <= horizon,
        )
    )
    income_map: dict[date, Decimal] = {}
    for inc in inc_result.scalars().all():
        income_map[inc.date] = income_map.get(inc.date, Decimal("0")) + inc.amount_rub

    # ── WB payouts in transit → expected income (created_at + 2 days) ──
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
            estimated = today  # overdue transit → show today
        if estimated <= horizon:
            income_map[estimated] = income_map.get(estimated, Decimal("0")) + wp.amount_rub

    # ── Planned payments (unpaid) ──
    pay_result = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.project_id == project_id,
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
        expense_map[effective_date] = (
            expense_map.get(effective_date, Decimal("0")) + Decimal(str(amt))
        )

    # ── Build daily series ──
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


async def get_order_summary(
    db: AsyncSession,
    project_id: int,
    order_no: int,
) -> dict:
    """
    Return plan vs fact for a specific order.

    Aggregates planned payments, actual bank transactions,
    logistics, and customs allocations.
    """
    # Order plan
    result = await db.execute(
        select(Order).where(Order.order_no == order_no, Order.project_id == project_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        return None

    # Planned payments
    pp_result = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.order_no == order_no,
            PlannedPayment.project_id == project_id,
        )
    )
    planned_payments = pp_result.scalars().all()

    # Fact: order payments (annex_id = order_no)
    txn_order_result = await db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.annex_id == str(order_no),
            Transaction.purpose_tag == "Заказ",
        )
    )
    txn_orders = txn_order_result.scalars().all()

    # Fact: logistics
    txn_log_result = await db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
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


async def update_payment_paid_amount(payment_id: int, db: AsyncSession):
    """
    Re-calculate paid_rub for a planned payment from its fact links.
    Updates the payment's paid_rub and status accordingly.
    """
    # Sum all linked fact amounts
    links_result = await db.execute(
        select(PaymentFactLink).where(PaymentFactLink.payment_id == payment_id)
    )
    links = links_result.scalars().all()
    total_paid = sum(l.amount_rub or Decimal("0") for l in links)

    # Update payment
    pp_result = await db.execute(
        select(PlannedPayment).where(PlannedPayment.id == payment_id)
    )
    payment = pp_result.scalar_one_or_none()
    if payment:
        payment.paid_rub = total_paid
        if total_paid >= (payment.amount_rub or Decimal("0")):
            payment.is_paid = True
        await db.commit()
