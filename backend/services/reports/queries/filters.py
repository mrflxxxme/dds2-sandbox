"""
Reports — dashboard filtered queries (daily aggregation, transactions, category counterparties).
"""

from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Transaction


def _build_category_condition(category: str):
    """Build SQLAlchemy condition for category filtering (shared by multiple queries)."""
    if category == "Без категории":
        return or_(Transaction.cat_lvl1_2 == None, Transaction.cat_lvl1_2 == "")  # noqa: E711
    return Transaction.cat_lvl1_2 == category


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
        conditions.append(func.coalesce(Transaction.cp_key, Transaction.counterparty) == cp_key)
    if category:
        conditions.append(_build_category_condition(category))

    result = await db.execute(
        select(
            func.date(Transaction.date).label("day"),
            func.coalesce(func.sum(Transaction.income), 0).label("income"),
            func.coalesce(func.sum(Transaction.expense), 0).label("expense"),
        )
        .where(*conditions)
        .group_by(func.date(Transaction.date))
        .order_by(func.date(Transaction.date))
    )

    rows = []
    for row in result:
        day_val = row.day
        day_str = day_val.isoformat() if hasattr(day_val, "isoformat") else str(day_val)
        rows.append(
            {
                "date": day_str,
                "income": float(row.income),
                "expense": float(row.expense),
            }
        )
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
        conditions.append(func.coalesce(Transaction.cp_key, Transaction.counterparty) == cp_key)
    if category:
        conditions.append(_build_category_condition(category))
    if flow == "income":
        conditions.append(Transaction.income > 0)
    elif flow == "expense":
        conditions.append(Transaction.expense > 0)

    # Count
    cnt_result = await db.execute(select(func.count()).select_from(Transaction).where(*conditions))
    total = cnt_result.scalar() or 0

    # Load FX rates for conversion
    rates_map = await fx_service.get_rates_map(db, project_id, date_from, date_to)
    fallback_rate = await fx_service.get_rate_for_date(db, project_id, date_to) if not rates_map else None

    # Rows
    result = await db.execute(
        select(
            Transaction.date,
            Transaction.counterparty,
            Transaction.income,
            Transaction.expense,
            Transaction.purpose,
            Transaction.cat_lvl1_2,
            Transaction.account,
            Transaction.currency,
        )
        .where(*conditions)
        .order_by(Transaction.date.desc())
        .limit(limit)
        .offset(offset)
    )

    items = []
    for r in result:
        dt = r.date
        income_val = float(r.income or 0)
        expense_val = float(r.expense or 0)
        currency = r.currency or "RUB"

        if currency == "CNY":
            day_date = dt.date() if hasattr(dt, "date") else dt
            rate = fx_service.find_rate_for_date(rates_map, day_date) or fallback_rate or 1.0
            income_rub = income_val * rate
            expense_rub = expense_val * rate
        else:
            income_rub = income_val
            expense_rub = expense_val
            rate = None

        items.append(
            {
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
            }
        )
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
    avg_rate = sum(rates_map.values()) / len(rates_map) if rates_map else fallback_rate or 1.0

    cat_condition = _build_category_condition(category)

    result = await db.execute(
        select(
            func.coalesce(Transaction.cp_key, Transaction.counterparty).label("key"),
            Transaction.counterparty.label("name"),
            Transaction.currency,
            func.sum(Transaction.expense).label("total"),
            func.count().label("cnt"),
        )
        .where(
            Transaction.project_id == project_id,
            Transaction.date >= date_from,
            Transaction.date <= date_to,
            Transaction.expense > 0,
            Transaction.is_cashflow2 == 1,
            cat_condition,
        )
        .group_by(
            func.coalesce(Transaction.cp_key, Transaction.counterparty),
            Transaction.counterparty,
            Transaction.currency,
        )
        .order_by(func.sum(Transaction.expense).desc())
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
