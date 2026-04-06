"""
Reports — balance reports (current balance, daily balance).
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached
from backend.models import Account, OpeningBalance, Transaction


@cached(prefix="reports:balance", ttl=300)
async def get_balance(
    db: AsyncSession,
    project_id: int,
    as_of: date | None = None,
) -> list[dict]:
    """
    Current balance per account/currency.
    balance = opening_balance + sum(net) for transactions <= as_of
    """
    if as_of is None:
        as_of = date.today()

    # Get opening balances
    ob_result = await db.execute(select(OpeningBalance).where(OpeningBalance.project_id == project_id))
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
        .where(Transaction.project_id == project_id, Transaction.is_deleted == False, Transaction.date <= as_of)  # noqa: E712
        .group_by(Transaction.account, Transaction.currency)
    )
    net_map: dict[tuple, Decimal] = {}
    for row in result:
        net_map[(row.account, row.currency)] = Decimal(str(row.net_sum or 0))

    # Get account metadata
    accs = await db.execute(
        select(Account).where(
            Account.is_our_account == True,  # noqa: E712
            Account.project_id == project_id,
            Account.is_deleted == False,  # noqa: E712
        )
    )
    accounts = accs.scalars().all()

    balances = []
    for acc in accounts:
        key = (acc.account, acc.currency)
        ob: Decimal = opening_map.get(key, Decimal("0"))
        net: Decimal = net_map.get(key, Decimal("0"))
        balances.append(
            {
                "account": acc.account,
                "bank": acc.bank,
                "currency": acc.currency,
                "account_name": acc.account_name,
                "balance": float(ob + net),
            }
        )

    return balances


@cached(prefix="reports:balance_daily", ttl=300)
async def get_balance_daily(
    db: AsyncSession,
    project_id: int,
    account: str,
    currency: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    """Running daily balance for a specific account."""
    # Get opening balance
    ob_result = await db.execute(
        select(OpeningBalance).where(
            OpeningBalance.account == account,
            OpeningBalance.currency == currency,
            OpeningBalance.project_id == project_id,
        )
    )
    ob = ob_result.scalar_one_or_none()
    opening = Decimal(str(ob.opening_balance)) if ob else Decimal("0")

    q = select(
        func.date(Transaction.date).label("day"),
        func.sum(Transaction.net).label("daily_net"),
    ).where(
        Transaction.project_id == project_id,
        Transaction.is_deleted == False,  # noqa: E712
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
        output.append(
            {
                "date": str(row.day),
                "daily_net": float(row.daily_net or 0),
                "balance": float(running),
            }
        )
    return output
