"""
Reports — FX & customs control queries.
"""

from datetime import date

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Transaction


async def get_fx_control(
    db: AsyncSession,
    project_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
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
    ).where(Transaction.project_id == project_id, Transaction.is_fx == True, Transaction.is_deleted == False)  # noqa: E712

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
    date_from: date | None = None,
    date_to: date | None = None,
) -> list:
    """Customs payment transactions for control."""
    q = select(Transaction).where(
        Transaction.project_id == project_id,
        Transaction.event_type2 == "CUSTOMS_PAYMENT",
        Transaction.is_deleted == False,  # noqa: E712
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
