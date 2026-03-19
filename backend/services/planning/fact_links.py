"""
Planning — Fact Links (payment ↔ transaction matching).
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import PaymentFactLink, PlannedPayment, Transaction

# ─── Fact Links ──────────────────────────────────────────────────────────────


async def get_fact_links(db: AsyncSession, project_id: int, payment_id: int):
    # Verify payment belongs to project
    pp = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.id == payment_id,
            PlannedPayment.project_id == project_id,
        )
    )
    if not pp.scalar_one_or_none():
        return []
    result = await db.execute(
        select(PaymentFactLink).where(
            PaymentFactLink.payment_id == payment_id,
            PaymentFactLink.is_deleted == False,
        )
    )
    return result.scalars().all()


async def create_fact_link(
    db: AsyncSession, project_id: int, payment_id: int, txn_id: str, amount_rub: float, note: str | None = None
):
    pp = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.id == payment_id,
            PlannedPayment.project_id == project_id,
        )
    )
    if not pp.scalar_one_or_none():
        return None, "Payment not found"

    txn = await db.execute(
        select(Transaction).where(
            Transaction.txn_id == txn_id,
            Transaction.project_id == project_id,
        )
    )
    if not txn.scalar_one_or_none():
        return None, "Transaction not found"

    link = PaymentFactLink(
        payment_id=payment_id,
        txn_id=txn_id,
        amount_rub=Decimal(str(amount_rub)),
        note=note,
    )
    db.add(link)
    await db.commit()
    await update_payment_paid_amount(payment_id, db, project_id)
    return link, None


async def delete_fact_link(db: AsyncSession, project_id: int, link_id: int):
    result = await db.execute(
        select(PaymentFactLink).where(
            PaymentFactLink.id == link_id,
            PaymentFactLink.is_deleted == False,
        )
    )
    link = result.scalar_one_or_none()
    if not link:
        return None
    # Verify ownership via payment
    pp = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.id == link.payment_id,
            PlannedPayment.project_id == project_id,
        )
    )
    if not pp.scalar_one_or_none():
        return None
    payment_id = link.payment_id
    link.soft_delete()
    await db.commit()
    await update_payment_paid_amount(payment_id, db, project_id)
    return True


async def get_candidate_transactions(
    db: AsyncSession, project_id: int, direction: str = "ЗАКАЗ", account: str | None = None
):
    conditions = [Transaction.project_id == project_id, Transaction.is_deleted == False, Transaction.expense > 0]
    if account:
        conditions.append(Transaction.account == account)
    else:
        tag_map = {"ЗАКАЗ": "Заказ", "ДОСТАВКА": "Логистика"}
        purpose_tag = tag_map.get(direction)
        if purpose_tag:
            conditions.append(Transaction.purpose_tag == purpose_tag)

    result = await db.execute(select(Transaction).where(*conditions).order_by(Transaction.date.desc()).limit(100))
    return result.scalars().all()


async def get_accounts_list(db: AsyncSession, project_id: int):
    from backend.models import Account

    result = await db.execute(select(Account).where(Account.project_id == project_id, Account.is_deleted == False))
    return result.scalars().all()


# ─── Payment paid amount ────────────────────────────────────────────────────


async def update_payment_paid_amount(payment_id: int, db: AsyncSession, project_id: int):
    """Re-calculate paid_rub for a planned payment from its fact links."""
    links_result = await db.execute(
        select(PaymentFactLink).where(
            PaymentFactLink.payment_id == payment_id,
            PaymentFactLink.is_deleted == False,
        )
    )
    links = links_result.scalars().all()
    total_paid = sum(l.amount_rub or Decimal("0") for l in links)

    pp_result = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.id == payment_id,
            PlannedPayment.project_id == project_id,
            PlannedPayment.is_deleted == False,
        )
    )
    payment = pp_result.scalar_one_or_none()
    if payment:
        payment.paid_rub = total_paid
        threshold = payment.amount_rub or Decimal("0")
        payment.is_paid = total_paid >= threshold if threshold > 0 else False
        await db.commit()
