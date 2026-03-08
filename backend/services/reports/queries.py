"""
Reports — filtered queries (FX control, customs, income daily, filtered transactions).
"""

import calendar
from datetime import date
from typing import Optional

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Transaction
from backend.cache import cached


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
        if category == "Без категории":
            conditions.append(or_(Transaction.cat_lvl1_2 == None, Transaction.cat_lvl1_2 == ""))
        else:
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
    from backend.services import fx_service

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
        if category == "Без категории":
            conditions.append(or_(Transaction.cat_lvl1_2 == None, Transaction.cat_lvl1_2 == ""))
        else:
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

    # Load FX rates for conversion
    rates_map = await fx_service.get_rates_map(db, project_id, date_from, date_to)
    fallback_rate = await fx_service.get_rate_for_date(db, project_id, date_to) if not rates_map else None

    # Rows
    result = await db.execute(
        select(
            Transaction.date, Transaction.counterparty,
            Transaction.income, Transaction.expense,
            Transaction.purpose, Transaction.cat_lvl1_2,
            Transaction.account, Transaction.currency,
        ).where(*conditions)
        .order_by(Transaction.date.desc())
        .limit(limit).offset(offset)
    )

    items = []
    for r in result:
        dt = r.date
        income_val = float(r.income or 0)
        expense_val = float(r.expense or 0)
        currency = r.currency or "RUB"

        if currency == "CNY":
            day_date = dt.date() if hasattr(dt, 'date') else dt
            rate = fx_service.find_rate_for_date(rates_map, day_date) or fallback_rate or 1.0
            income_rub = income_val * rate
            expense_rub = expense_val * rate
        else:
            income_rub = income_val
            expense_rub = expense_val
            rate = None

        items.append({
            "date": dt.strftime("%Y-%m-%d") if dt else "",
            "counterparty": r.counterparty or "",
            "income": income_rub,
            "expense": expense_rub,
            "income_original": income_val if currency != "RUB" else None,
            "expense_original": expense_val if currency != "RUB" else None,
            "currency": currency,
            "fx_rate": rate,
            "purpose": r.purpose or "",
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
    """Return counterparties grouped within an expense category, with CNY→RUB conversion."""
    from backend.services import fx_service

    # Load FX rates for conversion
    rates_map = await fx_service.get_rates_map(db, project_id, date_from, date_to)
    fallback_rate = await fx_service.get_rate_for_date(db, project_id, date_to) if not rates_map else None

    # Compute average rate for the period
    if rates_map:
        avg_rate = sum(rates_map.values()) / len(rates_map)
    else:
        avg_rate = fallback_rate or 1.0

    cat_condition = (
        or_(Transaction.cat_lvl1_2 == None, Transaction.cat_lvl1_2 == "")
        if category == "Без категории"
        else Transaction.cat_lvl1_2 == category
    )

    result = await db.execute(
        select(
            func.coalesce(Transaction.cp_key, Transaction.counterparty).label("key"),
            Transaction.counterparty.label("name"),
            Transaction.currency,
            func.sum(Transaction.expense).label("total"),
            func.count().label("cnt"),
        ).where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.expense > 0,
            Transaction.is_cashflow2 == 1,
            cat_condition,
        ).group_by(
            func.coalesce(Transaction.cp_key, Transaction.counterparty),
            Transaction.counterparty,
            Transaction.currency,
        ).order_by(func.sum(Transaction.expense).desc())
    )

    # Merge counterparties across currencies
    cp_map: dict = {}
    for r in result:
        cp_key = r.key or ""
        total = float(r.total or 0)
        if r.currency == "CNY":
            total *= avg_rate
        if cp_key in cp_map:
            cp_map[cp_key]["total"] += total
            cp_map[cp_key]["count"] += r.cnt
        else:
            cp_map[cp_key] = {
                "key": cp_key,
                "name": r.name or "Без контрагента",
                "total": total,
                "count": r.cnt,
            }

    items = sorted(cp_map.values(), key=lambda x: x["total"], reverse=True)
    return items
