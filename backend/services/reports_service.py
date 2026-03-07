"""
Reports service — business logic for balance, DDS, FX, income reports.

Extracted from routers/reports.py to enable reuse and testing.
"""

import calendar
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, and_, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Transaction, Account, OpeningBalance
from backend.models.planning import Order, PlannedPayment
from backend.cache import cached


@cached(prefix="reports:balance", ttl=300)
async def get_balance(
    db: AsyncSession,
    project_id: int,
    as_of: Optional[date] = None,
) -> list[dict]:
    """
    Current balance per account/currency.
    balance = opening_balance + sum(net) for transactions <= as_of
    """
    if as_of is None:
        as_of = date.today()

    # Get opening balances
    ob_result = await db.execute(
        select(OpeningBalance).where(OpeningBalance.project_id == project_id)
    )
    opening_map: dict[tuple, Decimal] = {}
    for ob in ob_result.scalars().all():
        opening_map[(ob.account, ob.currency)] = ob.opening_balance

    # Sum net per account/currency
    result = await db.execute(
        select(
            Transaction.account,
            Transaction.currency,
            func.sum(Transaction.net).label("net_sum"),
        )
        .where(Transaction.project_id == project_id, Transaction.date <= as_of)
        .group_by(Transaction.account, Transaction.currency)
    )
    net_map: dict[tuple, Decimal] = {}
    for row in result:
        net_map[(row.account, row.currency)] = Decimal(str(row.net_sum or 0))

    # Get account metadata
    accs = await db.execute(
        select(Account).where(
            Account.is_our_account == True,
            Account.project_id == project_id,
        )
    )
    accounts = accs.scalars().all()

    balances = []
    for acc in accounts:
        key = (acc.account, acc.currency)
        ob = opening_map.get(key, Decimal("0"))
        net = net_map.get(key, Decimal("0"))
        balances.append({
            "account": acc.account,
            "bank": acc.bank,
            "currency": acc.currency,
            "account_name": acc.account_name,
            "balance": float(ob + net),
        })

    return balances


@cached(prefix="reports:dds_month", ttl=300)
async def get_dds_month(
    db: AsyncSession,
    project_id: int,
    year: int,
    month: int,
    currency: str = "RUB",
) -> list[dict]:
    """DDS grouped by cat_lvl1_2/cat_lvl2_2 for a given month."""
    month_start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    month_end = date(year, month, last_day)

    result = await db.execute(
        select(
            Transaction.cat_lvl1_2,
            Transaction.cat_lvl2_2,
            func.sum(Transaction.income).label("income"),
            func.sum(Transaction.expense).label("expense"),
            func.sum(Transaction.net).label("net"),
        )
        .where(
            and_(
                Transaction.project_id == project_id,
                Transaction.date >= month_start,
                Transaction.date <= month_end,
                Transaction.currency == currency,
                Transaction.is_cashflow2 == 1,
            )
        )
        .group_by(Transaction.cat_lvl1_2, Transaction.cat_lvl2_2)
        .order_by(Transaction.cat_lvl1_2, Transaction.cat_lvl2_2)
    )

    rows = []
    for row in result:
        rows.append({
            "cat_lvl1": row.cat_lvl1_2,
            "cat_lvl2": row.cat_lvl2_2,
            "income": float(row.income or 0),
            "expense": float(row.expense or 0),
            "net": float(row.net or 0),
        })
    return rows


async def get_fx_control(
    db: AsyncSession,
    project_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[dict]:
    """FX transactions for control."""
    q = select(
        Transaction.date,
        Transaction.account,
        Transaction.currency,
        Transaction.counterparty,
        Transaction.purpose,
        Transaction.income,
        Transaction.expense,
        Transaction.net,
        Transaction.txn_id,
    ).where(Transaction.project_id == project_id, Transaction.is_fx == True)

    conditions = []
    if date_from:
        conditions.append(Transaction.date >= date_from)
    if date_to:
        conditions.append(Transaction.date <= date_to)
    if conditions:
        q = q.where(and_(*conditions))

    q = q.order_by(Transaction.date.desc())
    result = await db.execute(q)
    return [dict(row._mapping) for row in result]


async def get_customs_control(
    db: AsyncSession,
    project_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list:
    """Customs payment transactions for control."""
    q = select(Transaction).where(
        Transaction.project_id == project_id,
        Transaction.event_type2 == "CUSTOMS_PAYMENT",
    )
    conditions = []
    if date_from:
        conditions.append(Transaction.date >= date_from)
    if date_to:
        conditions.append(Transaction.date <= date_to)
    if conditions:
        q = q.where(and_(*conditions))
    q = q.order_by(Transaction.date.desc())
    result = await db.execute(q)
    return result.scalars().all()


@cached(prefix="reports:balance_daily", ttl=300)
async def get_balance_daily(
    db: AsyncSession,
    project_id: int,
    account: str,
    currency: str,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[dict]:
    """Running daily balance for a specific account."""
    # Get opening balance
    ob_result = await db.execute(
        select(OpeningBalance).where(
            OpeningBalance.account == account,
            OpeningBalance.currency == currency,
        )
    )
    ob = ob_result.scalar_one_or_none()
    opening = Decimal(str(ob.opening_balance)) if ob else Decimal("0")

    q = select(
        func.date(Transaction.date).label("day"),
        func.sum(Transaction.net).label("daily_net"),
    ).where(
        Transaction.project_id == project_id,
        Transaction.account == account,
        Transaction.currency == currency,
    )
    if date_from:
        q = q.where(Transaction.date >= date_from)
    if date_to:
        q = q.where(Transaction.date <= date_to)
    q = q.group_by(func.date(Transaction.date)).order_by(func.date(Transaction.date))

    result = await db.execute(q)
    rows = result.all()

    # Running balance
    running = opening
    output = []
    for row in rows:
        running += Decimal(str(row.daily_net or 0))
        output.append({
            "date": str(row.day),
            "daily_net": float(row.daily_net or 0),
            "balance": float(running),
        })
    return output


@cached(prefix="reports:income_daily", ttl=300)
async def get_income_daily(
    db: AsyncSession,
    project_id: int,
    year: int,
    month: int,
    currency: str = "RUB",
) -> list[dict]:
    """Daily income breakdown by bank/source for a given month."""
    date_from = date(year, month, 1)
    date_to = date(year, month, calendar.monthrange(year, month)[1])

    result = await db.execute(
        select(
            func.date_trunc('day', Transaction.date).label("day"),
            Transaction.bank,
            func.sum(Transaction.income).label("income"),
        )
        .where(
            Transaction.project_id == project_id,
            Transaction.currency == currency,
            Transaction.is_cashflow2 == 1,
            Transaction.income > 0,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
        )
        .group_by("day", Transaction.bank)
        .order_by("day")
    )

    rows = []
    for r in result:
        rows.append({
            "date": r.day.date().isoformat(),
            "bank": r.bank,
            "income": float(r.income or 0),
        })
    return rows


async def get_income_by_category_daily(
    db: AsyncSession,
    project_id: int,
    year: int,
    month: int,
    currency: str = "RUB",
    cat_lvl1: Optional[str] = None,
) -> list[dict]:
    """Daily income grouped by category for a given month."""
    date_from = date(year, month, 1)
    date_to = date(year, month, calendar.monthrange(year, month)[1])

    if cat_lvl1:
        group_col = func.coalesce(Transaction.cat_lvl2_2, 'Без подкатегории').label("category")
        extra_filter = Transaction.cat_lvl1_2 == cat_lvl1
    else:
        group_col = func.coalesce(Transaction.cat_lvl1_2, 'Без категории').label("category")
        extra_filter = None

    q = (
        select(
            func.date_trunc('day', Transaction.date).label("day"),
            group_col,
            func.sum(Transaction.income).label("income"),
        )
        .where(
            Transaction.project_id == project_id,
            Transaction.currency == currency,
            Transaction.is_cashflow2 == 1,
            Transaction.income > 0,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
        )
    )
    if extra_filter is not None:
        q = q.where(extra_filter)
    q = q.group_by("day", "category").order_by("day")

    result = await db.execute(q)

    rows = []
    for r in result:
        rows.append({
            "date": r.day.date().isoformat(),
            "category": r.category,
            "income": float(r.income or 0),
        })
    return rows


# ─── Dashboard Summary ──────────────────────────────────────────────────────

async def get_dashboard_summary(
    db: AsyncSession,
    project_id: int,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """All dashboard KPIs in a single call."""
    import logging
    logger = logging.getLogger("dds.dashboard")

    today = date.today()
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = date_to.replace(day=1)

    # 1. Balances (always current — reflects actual bank state)
    balances = await get_balance(db, project_id)
    balance_rub = sum(b["balance"] for b in balances if b["currency"] == "RUB")
    balance_cny = sum(b["balance"] for b in balances if b["currency"] == "CNY")

    # 2. Period income / expense (real cashflow only)
    month_result = await db.execute(
        select(
            func.coalesce(func.sum(Transaction.income), 0).label("income"),
            func.coalesce(func.sum(Transaction.expense), 0).label("expense"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.is_cashflow2 == 1,
        )
    )
    month_row = month_result.one()
    month_income = float(month_row.income)
    month_expense = float(month_row.expense)

    # 3. Orders summary (all active, no date filter — like debt)
    orders_q = select(
        func.count().label("cnt"),
        func.coalesce(func.sum(Order.order_amount), 0).label("total"),
    ).where(
        Order.project_id == project_id,
        or_(Order.is_deleted == False, Order.is_deleted.is_(None)),
    )
    orders_result = await db.execute(orders_q)
    orders_row = orders_result.one()
    orders_count = orders_row.cnt
    orders_total_cny = float(orders_row.total)

    # 4. Unpaid debt (planned_payments) — global, all unpaid
    #    For CNY/USD: use amount/paid_amount (original currency)
    #    For RUB: use amount_rub/paid_rub
    debt_result = await db.execute(
        select(
            PlannedPayment.currency,
            func.sum(PlannedPayment.amount_rub).label("total_amount_rub"),
            func.sum(PlannedPayment.paid_rub).label("total_paid_rub"),
            func.sum(PlannedPayment.amount).label("total_amount"),
            func.sum(PlannedPayment.paid_amount).label("total_paid"),
        ).where(
            PlannedPayment.project_id == project_id,
            PlannedPayment.is_paid == False,
            or_(PlannedPayment.is_deleted == False, PlannedPayment.is_deleted.is_(None)),
        ).group_by(PlannedPayment.currency)
    )
    debt_rub = 0.0
    debt_cny = 0.0
    for row in debt_result:
        is_foreign = row.currency and ("CNY" in row.currency or "USD" in row.currency)
        if is_foreign:
            total_amt = float(row.total_amount or 0)
            total_paid = float(row.total_paid or 0)
            remaining = total_amt - total_paid
            if remaining > 0:
                debt_cny += remaining
        else:
            total_amt = float(row.total_amount_rub or 0)
            total_paid = float(row.total_paid_rub or 0)
            remaining = total_amt - total_paid
            if remaining > 0:
                debt_rub += remaining

    # 5. Inbox count (unassigned transactions, within date range)
    inbox_result = await db.execute(
        select(func.count()).select_from(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            or_(
                Transaction.cat_lvl1.is_(None),
                Transaction.cat_lvl1 == "",
            ),
        )
    )
    inbox_count = inbox_result.scalar() or 0

    # 6. Daily cashflow (within date range, real cashflow only)
    daily_result = await db.execute(
        select(
            func.date(Transaction.date).label("day"),
            func.coalesce(func.sum(Transaction.income), 0).label("income"),
            func.coalesce(func.sum(Transaction.expense), 0).label("expense"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.is_cashflow2 == 1,
        ).group_by(func.date(Transaction.date)).order_by(func.date(Transaction.date))
    )
    daily_cashflow = []
    for row in daily_result:
        day_val = row.day
        if hasattr(day_val, 'isoformat'):
            day_str = day_val.isoformat()
        else:
            day_str = str(day_val)
        daily_cashflow.append({
            "date": day_str,
            "income": float(row.income),
            "expense": float(row.expense),
        })

    # 7. Expense by category (within date range, real cashflow)
    cat_result = await db.execute(
        select(
            func.coalesce(Transaction.cat_lvl1_2, text("'Без категории'")).label("category"),
            func.coalesce(func.sum(Transaction.expense), 0).label("expense"),
            func.count().label("cnt"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.expense > 0,
            Transaction.is_cashflow2 == 1,
        ).group_by(Transaction.cat_lvl1_2).order_by(
            func.sum(Transaction.expense).desc()
        )
    )
    expense_by_category = []
    for row in cat_result:
        expense_by_category.append({
            "name": row.category or "Без категории",
            "value": float(row.expense),
            "count": row.cnt,
        })

    # 8. Top income counterparties — group by cp_key (INN) to avoid name splits
    cp_result = await db.execute(
        select(
            func.coalesce(Transaction.cp_key, Transaction.counterparty).label("key"),
            func.min(Transaction.counterparty).label("name"),
            func.sum(Transaction.income).label("total"),
            func.count().label("cnt"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.income > 0,
            Transaction.is_cashflow2 == 1,
        ).group_by(
            func.coalesce(Transaction.cp_key, Transaction.counterparty)
        ).order_by(
            func.sum(Transaction.income).desc()
        ).limit(20)
    )
    income_counterparties = []
    for row in cp_result:
        income_counterparties.append({
            "key": row.key or "",
            "name": row.name or "Без контрагента",
            "total": float(row.total),
            "count": row.cnt,
        })

    return {
        "balance_rub": balance_rub,
        "balance_cny": balance_cny,
        "month_income": month_income,
        "month_expense": month_expense,
        "orders_count": orders_count,
        "orders_total_cny": orders_total_cny,
        "debt_rub": debt_rub,
        "debt_cny": debt_cny,
        "inbox_count": inbox_count,
        "accounts_count": len(balances),
        "daily_cashflow": daily_cashflow,
        "expense_by_category": expense_by_category,
        "income_counterparties": income_counterparties,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    }


async def get_daily_filtered(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
    cp_key: str | None = None,
    category: str | None = None,
) -> list[dict]:
    """Daily income/expense filtered by counterparty (cp_key) or expense category."""
    conditions = [
        Transaction.project_id == project_id,
        Transaction.date >= date_from,
        Transaction.date <= date_to,
        Transaction.is_cashflow2 == 1,
    ]
    if cp_key:
        conditions.append(
            func.coalesce(Transaction.cp_key, Transaction.counterparty) == cp_key
        )
    if category:
        conditions.append(Transaction.cat_lvl1_2 == category)

    result = await db.execute(
        select(
            func.date(Transaction.date).label("day"),
            func.coalesce(func.sum(Transaction.income), 0).label("income"),
            func.coalesce(func.sum(Transaction.expense), 0).label("expense"),
        ).where(*conditions)
        .group_by(func.date(Transaction.date))
        .order_by(func.date(Transaction.date))
    )

    rows = []
    for row in result:
        day_val = row.day
        day_str = day_val.isoformat() if hasattr(day_val, 'isoformat') else str(day_val)
        rows.append({
            "date": day_str,
            "income": float(row.income),
            "expense": float(row.expense),
        })
    return rows


async def get_filtered_transactions(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
    cp_key: str | None = None,
    category: str | None = None,
    flow: str = "all",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Return individual transactions filtered by cp_key/category for dashboard detail list."""
    conditions = [
        Transaction.project_id == project_id,
        Transaction.date >= date_from,
        Transaction.date <= date_to,
        Transaction.is_cashflow2 == 1,
    ]
    if cp_key:
        conditions.append(
            func.coalesce(Transaction.cp_key, Transaction.counterparty) == cp_key
        )
    if category:
        conditions.append(Transaction.cat_lvl1_2 == category)
    if flow == "income":
        conditions.append(Transaction.income > 0)
    elif flow == "expense":
        conditions.append(Transaction.expense > 0)

    # Count
    cnt_result = await db.execute(
        select(func.count()).select_from(Transaction).where(*conditions)
    )
    total = cnt_result.scalar() or 0

    # Rows
    result = await db.execute(
        select(
            Transaction.date, Transaction.counterparty,
            Transaction.income, Transaction.expense,
            Transaction.purpose, Transaction.cat_lvl1_2,
            Transaction.account,
        ).where(*conditions)
        .order_by(Transaction.date.desc())
        .limit(limit).offset(offset)
    )

    items = []
    for r in result:
        dt = r.date
        items.append({
            "date": dt.strftime("%Y-%m-%d") if dt else "",
            "counterparty": r.counterparty or "",
            "income": float(r.income or 0),
            "expense": float(r.expense or 0),
            "purpose": (r.purpose or "")[:120],
            "category": r.cat_lvl1_2 or "",
            "account": r.account or "",
        })
    return {"total": total, "items": items}


async def get_category_counterparties(
    db: AsyncSession,
    project_id: int,
    category: str,
    date_from: date,
    date_to: date,
) -> list[dict]:
    """Return counterparties grouped within an expense category."""
    result = await db.execute(
        select(
            func.coalesce(Transaction.cp_key, Transaction.counterparty).label("key"),
            Transaction.counterparty.label("name"),
            func.sum(Transaction.expense).label("total"),
            func.count().label("cnt"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.expense > 0,
            Transaction.is_cashflow2 == 1,
            Transaction.cat_lvl1_2 == category,
        ).group_by(
            func.coalesce(Transaction.cp_key, Transaction.counterparty),
            Transaction.counterparty,
        ).order_by(func.sum(Transaction.expense).desc())
    )
    items = []
    for r in result:
        items.append({
            "key": r.key or "",
            "name": r.name or "Без контрагента",
            "total": float(r.total or 0),
            "count": r.cnt,
        })
    return items
