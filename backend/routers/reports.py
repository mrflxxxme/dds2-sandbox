"""
Router: /reports — balance, DDS month, FX, customs controls
"""

from datetime import date
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, text, and_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Transaction, Account, OpeningBalance
from backend.schemas import DashboardBalances, DdsMonthRow

router = APIRouter(prefix="/reports")


@router.get("/balance")
async def get_balance(
    as_of: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Current balance per account/currency.
    balance = opening_balance + sum(net) for transactions <= as_of
    """
    if as_of is None:
        as_of = date.today()

    # Get opening balances
    ob_result = await db.execute(select(OpeningBalance))
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
        .where(Transaction.date <= as_of)
        .group_by(Transaction.account, Transaction.currency)
    )
    net_map: dict[tuple, Decimal] = {}
    for row in result:
        net_map[(row.account, row.currency)] = Decimal(str(row.net_sum or 0))

    # Get account metadata
    accs = await db.execute(select(Account).where(Account.is_our_account == True))
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


@router.get("/dds_month")
async def get_dds_month(
    year: int = Query(...),
    month: int = Query(...),
    currency: str = Query("RUB"),
    db: AsyncSession = Depends(get_db),
):
    """DDS grouped by cat_lvl1_2/cat_lvl2_2 for a given month."""
    from datetime import timedelta
    import calendar
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


@router.get("/fx_control")
async def get_fx_control(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
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
    ).where(Transaction.is_fx == True)

    conditions = []
    if date_from:
        conditions.append(Transaction.date >= date_from)
    if date_to:
        conditions.append(Transaction.date <= date_to)
    if conditions:
        from sqlalchemy import and_
        q = q.where(and_(*conditions))

    q = q.order_by(Transaction.date.desc())
    result = await db.execute(q)
    rows = [dict(row._mapping) for row in result]
    return rows


@router.get("/customs_control")
async def get_customs_control(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(Transaction).where(Transaction.event_type2 == "CUSTOMS_PAYMENT")
    conditions = []
    if date_from:
        conditions.append(Transaction.date >= date_from)
    if date_to:
        conditions.append(Transaction.date <= date_to)
    if conditions:
        from sqlalchemy import and_
        q = q.where(and_(*conditions))
    q = q.order_by(Transaction.date.desc())
    result = await db.execute(q)
    from backend.schemas import TransactionSchema
    return result.scalars().all()


@router.get("/balance_daily")
async def get_balance_daily(
    account: str = Query(...),
    currency: str = Query(...),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: AsyncSession = Depends(get_db),
):
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
    opening_date = ob.date_open if ob else date(2000, 1, 1)

    q = select(
        func.date(Transaction.date).label("day"),
        func.sum(Transaction.net).label("daily_net"),
    ).where(
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


@router.get("/income_daily")
async def get_income_daily(
    year: int = Query(...),
    month: int = Query(...),
    currency: str = Query("RUB"),
    db: AsyncSession = Depends(get_db),
):
    """
    Daily income breakdown by bank/source for a given month.
    """
    from sqlalchemy import extract, cast, Date
    date_from = date(year, month, 1)
    import calendar
    date_to = date(year, month, calendar.monthrange(year, month)[1])

    result = await db.execute(
        select(
            func.date_trunc('day', Transaction.date).label("day"),
            Transaction.bank,
            func.sum(Transaction.income).label("income"),
        )
        .where(
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


@router.get("/income_by_category_daily")
async def get_income_by_category_daily(
    year: int = Query(...),
    month: int = Query(...),
    currency: str = Query("RUB"),
    cat_lvl1: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Daily income grouped by category for a given month.
    If cat_lvl1 is provided, filters by that cat_lvl1 and groups by cat_lvl2.
    Otherwise groups by cat_lvl1.
    """
    import calendar
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
