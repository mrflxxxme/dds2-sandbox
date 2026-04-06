"""
Reports — income daily aggregation queries.
"""

import calendar
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models import Transaction


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
            func.date_trunc("day", Transaction.date).label("day"),
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
        rows.append(
            {
                "date": r.day.date().isoformat(),
                "bank": r.bank,
                "income": float(r.income or 0),
            }
        )
    return rows


async def get_income_by_category_daily(
    db: AsyncSession,
    project_id: int,
    year: int,
    month: int,
    currency: str = "RUB",
    cat_lvl1: str | None = None,
) -> list[dict]:
    """Daily income grouped by category for a given month."""
    date_from = date(year, month, 1)
    date_to = date(year, month, calendar.monthrange(year, month)[1])

    if cat_lvl1:
        group_col = func.coalesce(Transaction.cat_lvl2_2, "Без подкатегории").label("category")
        extra_filter = Transaction.cat_lvl1_2 == cat_lvl1
    else:
        group_col = func.coalesce(Transaction.cat_lvl1_2, "Без категории").label("category")
        extra_filter = None

    q = select(
        func.date_trunc("day", Transaction.date).label("day"),
        group_col,
        func.sum(Transaction.income).label("income"),
    ).where(
        Transaction.project_id == project_id,
        Transaction.currency == currency,
        Transaction.is_cashflow2 == 1,
        Transaction.income > 0,
        Transaction.date >= date_from,
        Transaction.date <= date_to,
    )
    if extra_filter is not None:
        q = q.where(extra_filter)
    q = q.group_by("day", "category").order_by("day")

    result = await db.execute(q)

    rows = []
    for r in result:
        rows.append(
            {
                "date": r.day.date().isoformat(),
                "category": r.category,
                "income": float(r.income or 0),
            }
        )
    return rows
