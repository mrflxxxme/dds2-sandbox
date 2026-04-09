"""
Transactions service — business logic for search, categorization, and INBOX.

Extracted from routers/import_txn.py to enable reuse and testing.
"""

import logging
from datetime import date

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    CategoryChangeLog,
    CounterpartyCategory,
    Override,
    Transaction,
)

logger = logging.getLogger("dds.transactions")


def _escape_like(s: str) -> str:
    """Escape special LIKE characters (% and _) in search strings."""
    return s.replace("%", r"\%").replace("_", r"\_")


async def search_transactions(
    db: AsyncSession,
    project_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    account: str | None = None,
    currency: str | None = None,
    category: str | None = None,
    search: str | None = None,
    event_type: str | None = None,
    status: str | None = None,
    is_cashflow2: int | None = None,
    limit: int = 500,
    offset: int = 0,
) -> list:
    """Search and filter transactions."""
    q = select(Transaction).where(Transaction.project_id == project_id, Transaction.is_deleted == False)

    if date_from:
        q = q.where(Transaction.date >= date_from)
    if date_to:
        q = q.where(Transaction.date <= date_to)
    if account:
        q = q.where(Transaction.account == account)
    if currency:
        q = q.where(Transaction.currency == currency)
    if category:
        q = q.where(
            or_(
                Transaction.cat_lvl1_2 == category,
                Transaction.cat_lvl2_2 == category,
            )
        )
    if search:
        escaped = _escape_like(search)
        q = q.where(
            or_(
                Transaction.counterparty.ilike(f"%{escaped}%", escape="\\"),
                Transaction.purpose.ilike(f"%{escaped}%", escape="\\"),
            )
        )
    if event_type:
        q = q.where(Transaction.event_type2 == event_type)
    if status:
        q = q.where(Transaction.status == status)
    if is_cashflow2 is not None:
        q = q.where(Transaction.is_cashflow2 == is_cashflow2)

    q = q.order_by(Transaction.date.desc()).limit(limit).offset(offset)
    result = await db.execute(q)
    return list(result.scalars().all())


async def get_unassigned(
    db: AsyncSession,
    project_id: int,
    limit: int = 200,
) -> list:
    """Get uncategorized transactions (INBOX)."""
    q = (
        select(Transaction)
        .where(
            Transaction.project_id == project_id,
            Transaction.is_deleted == False,
            Transaction.is_cashflow2 == 1,
            or_(
                Transaction.cat_lvl1_2.is_(None),
                Transaction.cat_lvl1_2 == "",
                Transaction.cat_lvl1_2 == "UNASSIGNED",
            ),
        )
        .order_by(Transaction.date.desc())
        .limit(limit)
    )
    result = await db.execute(q)
    return list(result.scalars().all())


async def get_unassigned_with_fx(
    db: AsyncSession,
    project_id: int,
    limit: int = 200,
) -> list[dict]:
    """Get uncategorized transactions with FX conversion (CNY→RUB).

    Returns dicts with income/expense converted to RUB,
    plus original amounts and fx_rate for non-RUB currencies.
    """
    from datetime import date as date_cls

    from backend.services import fx_service

    txns = await get_unassigned(db, project_id, limit)

    # Load FX rates for conversion
    rates_map = await fx_service.get_rates_map(db, project_id, date_cls(2020, 1, 1), date_cls.today())
    fallback_rate = await fx_service.get_rate_for_date(db, project_id, date_cls.today()) if not rates_map else None

    items = []
    for t in txns:
        inc = float(t.income or 0)
        exp = float(t.expense or 0)
        currency = t.currency or "RUB"
        rate = None
        if currency == "CNY":
            day_date = t.date.date() if hasattr(t.date, "date") else t.date
            rate = fx_service.find_rate_for_date(rates_map, day_date) or fallback_rate or 1.0
            inc_rub = inc * rate
            exp_rub = exp * rate
        else:
            inc_rub = inc
            exp_rub = exp

        items.append(
            {
                "id": t.id,
                "txn_id": t.txn_id,
                "date": t.date.isoformat() if t.date else None,
                "counterparty": t.counterparty,
                "cp_key": t.cp_key,
                "income": inc_rub,
                "expense": exp_rub,
                "income_original": inc if currency != "RUB" else None,
                "expense_original": exp if currency != "RUB" else None,
                "currency": currency,
                "fx_rate": rate,
                "purpose": t.purpose,
                "cat_lvl1_2": t.cat_lvl1_2,
                "cat_lvl2_2": t.cat_lvl2_2,
                "account": t.account,
                "event_type2": t.event_type2,
                "is_cashflow2": t.is_cashflow2,
            }
        )
    return items


async def get_unassigned_grouped(
    db: AsyncSession,
    project_id: int,
) -> list[dict]:
    """Group uncategorized transactions by counterparty, converting CNY→RUB."""
    from datetime import date as date_cls

    from backend.services import fx_service

    # Load FX rates
    rates_map = await fx_service.get_rates_map(db, project_id, date_cls(2020, 1, 1), date_cls.today())
    fallback_rate = await fx_service.get_rate_for_date(db, project_id, date_cls.today()) if not rates_map else None
    avg_rate = (sum(rates_map.values()) / len(rates_map)) if rates_map else (fallback_rate or 1.0)

    q = (
        select(
            Transaction.cp_key,
            Transaction.counterparty,
            Transaction.currency,
            func.count().label("cnt"),
            func.sum(Transaction.income).label("total_income"),
            func.sum(Transaction.expense).label("total_expense"),
        )
        .where(
            Transaction.project_id == project_id,
            Transaction.is_deleted == False,
            Transaction.is_cashflow2 == 1,
            or_(
                Transaction.cat_lvl1_2.is_(None),
                Transaction.cat_lvl1_2 == "",
                Transaction.cat_lvl1_2 == "UNASSIGNED",
            ),
        )
        .group_by(Transaction.cp_key, Transaction.counterparty, Transaction.currency)
        .order_by(func.count().desc())
    )
    result = await db.execute(q)

    # Merge by counterparty across currencies
    cp_map: dict = {}
    for r in result:
        cp_key = r.cp_key or r.counterparty or ""
        income = float(r.total_income or 0)
        expense = float(r.total_expense or 0)
        if r.currency == "CNY":
            income *= avg_rate
            expense *= avg_rate
        if cp_key in cp_map:
            cp_map[cp_key]["count"] += r.cnt
            cp_map[cp_key]["total_income"] += income
            cp_map[cp_key]["total_expense"] += expense
        else:
            cp_map[cp_key] = {
                "cp_key": r.cp_key,
                "counterparty": r.counterparty,
                "count": r.cnt,
                "total_income": income,
                "total_expense": expense,
            }

    rows = sorted(cp_map.values(), key=lambda x: x["count"], reverse=True)
    return rows


async def assign_category(
    db: AsyncSession,
    project_id: int,
    txn_id: str,
    cat_lvl1: str,
    cat_lvl2: str | None,
    scope: str = "txn",
    user_id: int | None = None,
) -> dict:
    """
    Assign category to a transaction.
    scope='txn' → override for this txn only.
    scope='cp' → update counterparty_category (affects all txns with same cp_key).
    """
    # Find transaction
    result = await db.execute(
        select(Transaction).where(
            Transaction.txn_id == txn_id,
            Transaction.project_id == project_id,
            Transaction.is_deleted == False,
        )
    )
    txn = result.scalar_one_or_none()
    if not txn:
        return {"error": "Transaction not found", "status": 404}

    old_cat1, old_cat2 = txn.cat_lvl1_2, txn.cat_lvl2_2

    if scope == "txn":
        # Create override
        ovr = Override(
            txn_id=txn_id,
            cat_lvl1=cat_lvl1,
            cat_lvl2=cat_lvl2,
            project_id=project_id,
        )
        db.add(ovr)
        txn.cat_lvl1_2 = cat_lvl1
        txn.cat_lvl2_2 = cat_lvl2

    elif scope == "cp":
        if not txn.cp_key:
            return {"error": "Transaction has no cp_key", "status": 400}

        # Upsert counterparty_category
        existing = await db.execute(
            select(CounterpartyCategory).where(
                CounterpartyCategory.cp_key == txn.cp_key,
                CounterpartyCategory.project_id == project_id,
                CounterpartyCategory.is_deleted == False,
            )
        )
        cpc = existing.scalar_one_or_none()
        if cpc:
            cpc.cat_lvl1 = cat_lvl1
            cpc.cat_lvl2 = cat_lvl2
        else:
            cpc = CounterpartyCategory(
                cp_key=txn.cp_key,
                counterparty=txn.counterparty,
                cat_lvl1=cat_lvl1,
                cat_lvl2=cat_lvl2,
                project_id=project_id,
            )
            db.add(cpc)

        # Update all matching transactions
        await db.execute(
            update(Transaction)
            .where(
                Transaction.cp_key == txn.cp_key,
                Transaction.project_id == project_id,
            )
            .values(cat_lvl1_2=cat_lvl1, cat_lvl2_2=cat_lvl2)
        )

    # Log the change
    log = CategoryChangeLog(
        project_id=project_id,
        txn_id=txn_id,
        old_cat_lvl1=old_cat1,
        old_cat_lvl2=old_cat2,
        new_cat_lvl1=cat_lvl1,
        new_cat_lvl2=cat_lvl2,
        changed_by=user_id,
    )
    db.add(log)
    await db.commit()

    from backend.cache import invalidate_project_reports

    await invalidate_project_reports(project_id)

    return {"ok": True, "txn_id": txn_id, "scope": scope}


async def assign_category_bulk(
    db: AsyncSession,
    project_id: int,
    cp_key: str,
    cat_lvl1: str,
    cat_lvl2: str | None = None,
) -> dict:
    """Assign category to all uncategorized transactions with given cp_key."""
    # Upsert counterparty_category
    existing = await db.execute(
        select(CounterpartyCategory).where(
            CounterpartyCategory.cp_key == cp_key,
            CounterpartyCategory.project_id == project_id,
            CounterpartyCategory.is_deleted == False,
        )
    )
    cpc = existing.scalar_one_or_none()
    if cpc:
        cpc.cat_lvl1 = cat_lvl1
        cpc.cat_lvl2 = cat_lvl2
    else:
        cpc = CounterpartyCategory(
            cp_key=cp_key,
            cat_lvl1=cat_lvl1,
            cat_lvl2=cat_lvl2,
            project_id=project_id,
        )
        db.add(cpc)

    # Update transactions
    result = await db.execute(
        update(Transaction)
        .where(
            Transaction.cp_key == cp_key,
            Transaction.project_id == project_id,
            or_(
                Transaction.cat_lvl1_2.is_(None),
                Transaction.cat_lvl1_2 == "",
                Transaction.cat_lvl1_2 == "UNASSIGNED",
            ),
        )
        .values(cat_lvl1_2=cat_lvl1, cat_lvl2_2=cat_lvl2)
    )
    await db.commit()

    from backend.cache import invalidate_project_reports

    await invalidate_project_reports(project_id)

    return {"ok": True, "updated": result.rowcount}


async def assign_category_by_ids(
    db: AsyncSession,
    project_id: int,
    txn_ids: list[str],
    cat_lvl1: str,
    cat_lvl2: str | None = None,
) -> dict:
    """Assign category to specific transactions by txn_id list."""
    result = await db.execute(
        update(Transaction)
        .where(
            Transaction.txn_id.in_(txn_ids),
            Transaction.project_id == project_id,
        )
        .values(cat_lvl1_2=cat_lvl1, cat_lvl2_2=cat_lvl2)
    )
    await db.commit()

    from backend.cache import invalidate_project_reports

    await invalidate_project_reports(project_id)

    return {"ok": True, "updated": result.rowcount}


async def get_import_logs(db: AsyncSession, project_id: int, limit: int = 100) -> list:
    """Get recent import logs for a project."""
    from backend.models import ImportLog

    result = await db.execute(
        select(ImportLog).where(ImportLog.project_id == project_id).order_by(ImportLog.imported_at.desc()).limit(limit)
    )
    return list(result.scalars().all())
