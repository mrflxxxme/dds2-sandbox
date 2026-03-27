"""
Planning — Cashflow daily calculation and Order summary.
"""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models import (
    CustomsAlloc,
    Order,
    PlannedIncome,
    PlannedPayment,
    Transaction,
    WbPayout,
)


@cached(prefix="reports:cashflow", ttl=300)
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
        from backend.models import OpeningBalance

        # Sum opening balances for RUB accounts
        ob_result = await db.execute(
            select(OpeningBalance).where(
                OpeningBalance.project_id == project_id,
                OpeningBalance.currency == "RUB",
            )
        )
        total_ob = sum(ob.opening_balance for ob in ob_result.scalars().all())
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
                    PlannedPayment.currency == ccy,
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
            PlannedIncome.is_deleted == False,
            PlannedIncome.date <= horizon,
        )
    )
    income_map: dict[date, Decimal] = {}
    for inc in inc_result.scalars().all():
        income_map[inc.date] = income_map.get(inc.date, Decimal("0")) + inc.amount_rub

    # WB payouts in transit → expected income (created_at + 2 days)
    transit_result = await db.execute(
        select(WbPayout).where(
            WbPayout.project_id == project_id,
            WbPayout.is_deleted == False,
            WbPayout.status.in_(["TRANSIT", "PROCESSING"]),
        )
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
        rows.append(
            {
                "date": str(d),
                "planned_income": float(inc),
                "planned_expense": float(exp),
                "net": float(net),
                "deficit_running": float(running),
            }
        )

    return rows


# ─── Order summary ──────────────────────────────────────────────────────────


async def get_order_summary(
    db: AsyncSession,
    project_id: int,
    order_no: int,
) -> dict | None:
    """Return plan vs fact for a specific order."""
    result = await db.execute(
        select(Order).where(Order.order_no == order_no, Order.project_id == project_id, Order.is_deleted == False)
    )
    order = result.scalar_one_or_none()
    if not order:
        return None

    pp_result = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.order_no == order_no,
            PlannedPayment.project_id == project_id,
            PlannedPayment.is_deleted == False,
        )
    )
    planned_payments = pp_result.scalars().all()

    txn_order_result = await db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.annex_id == str(order_no),
            Transaction.purpose_tag == "Заказ",
            Transaction.is_deleted == False,
        )
    )
    txn_orders = txn_order_result.scalars().all()

    txn_log_result = await db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.purpose_tag == "Логистика",
            Transaction.annex_id == str(order_no),
            Transaction.is_deleted == False,
        )
    )
    txn_logistics = txn_log_result.scalars().all()

    alloc_result = await db.execute(
        select(CustomsAlloc).where(
            CustomsAlloc.order_no == order_no,
            CustomsAlloc.project_id == project_id,
        )
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
