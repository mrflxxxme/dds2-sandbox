"""
Planning — CRUD operations for Orders, Lead Times, Payments, Incomes.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    PlannedPayment, PlannedIncome, Order, LeadTime,
)

logger = logging.getLogger("dds.planning")


# ─── Orders CRUD ─────────────────────────────────────────────────────────────

async def get_orders(db: AsyncSession, project_id: int, limit: int = 500, offset: int = 0):
    result = await db.execute(
        select(Order).where(Order.project_id == project_id, Order.is_deleted == False)
        .order_by(Order.planned_ship_date.desc().nullslast())
        .limit(limit).offset(offset)
    )
    return result.scalars().all()


async def upsert_order(db: AsyncSession, project_id: int, data: dict, obj_id: int | None):
    if obj_id:
        result = await db.execute(
            select(Order).where(Order.id == obj_id, Order.project_id == project_id)
        )
        obj = result.scalar_one_or_none()
        if obj:
            for k, v in data.items():
                setattr(obj, k, v)
        else:
            obj = Order(**data, project_id=project_id)
            db.add(obj)
    else:
        obj = Order(**data, project_id=project_id)
        db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def delete_order(db: AsyncSession, project_id: int, order_id: int):
    result = await db.execute(
        select(Order).where(Order.id == order_id, Order.project_id == project_id)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    await db.delete(obj)
    await db.commit()
    return True


# ─── Lead Times CRUD ─────────────────────────────────────────────────────────

async def get_lead_times(db: AsyncSession, project_id: int):
    result = await db.execute(
        select(LeadTime).where(LeadTime.project_id == project_id)
        .order_by(LeadTime.direction)
    )
    return result.scalars().all()


async def upsert_lead_time(db: AsyncSession, project_id: int, direction: str, days: int):
    result = await db.execute(
        select(LeadTime).where(
            LeadTime.project_id == project_id,
            LeadTime.direction == direction,
        )
    )
    obj = result.scalar_one_or_none()
    if obj:
        obj.days = days
    else:
        obj = LeadTime(project_id=project_id, direction=direction, days=days)
        db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


# ─── Payments CRUD ───────────────────────────────────────────────────────────

async def get_payments(db: AsyncSession, project_id: int, order_no: int | None = None, limit: int = 500, offset: int = 0):
    q = select(PlannedPayment).where(PlannedPayment.project_id == project_id, PlannedPayment.is_deleted == False)
    if order_no:
        q = q.where(PlannedPayment.order_no == order_no)
    q = q.order_by(PlannedPayment.pay_date).limit(limit).offset(offset)
    result = await db.execute(q)
    return result.scalars().all()


async def upsert_payment(db: AsyncSession, project_id: int, data: dict, obj_id: int | None):
    if obj_id:
        result = await db.execute(
            select(PlannedPayment).where(
                PlannedPayment.id == obj_id, PlannedPayment.project_id == project_id
            )
        )
        obj = result.scalar_one_or_none()
        if obj:
            for k, v in data.items():
                setattr(obj, k, v)
        else:
            obj = PlannedPayment(**data, project_id=project_id)
            db.add(obj)
    else:
        obj = PlannedPayment(**data, project_id=project_id)
        db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def delete_payment(db: AsyncSession, project_id: int, payment_id: int):
    result = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.id == payment_id, PlannedPayment.project_id == project_id
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    await db.delete(obj)
    await db.commit()
    return True


async def mark_paid(db: AsyncSession, project_id: int, payment_id: int):
    result = await db.execute(
        select(PlannedPayment).where(
            PlannedPayment.id == payment_id, PlannedPayment.project_id == project_id
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    obj.is_paid = True
    await db.commit()
    return True


# ─── Incomes CRUD ────────────────────────────────────────────────────────────

async def get_incomes(db: AsyncSession, project_id: int, limit: int = 500, offset: int = 0):
    result = await db.execute(
        select(PlannedIncome).where(PlannedIncome.project_id == project_id)
        .order_by(PlannedIncome.date)
        .limit(limit).offset(offset)
    )
    return result.scalars().all()


async def upsert_income(db: AsyncSession, project_id: int, data: dict, obj_id: int | None):
    if obj_id:
        result = await db.execute(
            select(PlannedIncome).where(
                PlannedIncome.id == obj_id, PlannedIncome.project_id == project_id
            )
        )
        obj = result.scalar_one_or_none()
        if obj:
            obj.date = data.get("date", obj.date)
            obj.amount_rub = data.get("amount_rub", obj.amount_rub)
            obj.source = data.get("source", obj.source)
        else:
            obj = PlannedIncome(**data, project_id=project_id)
            db.add(obj)
    else:
        obj = PlannedIncome(**data, project_id=project_id)
        db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return obj


async def delete_income(db: AsyncSession, project_id: int, income_id: int):
    result = await db.execute(
        select(PlannedIncome).where(
            PlannedIncome.id == income_id, PlannedIncome.project_id == project_id
        )
    )
    obj = result.scalar_one_or_none()
    if not obj:
        return None
    await db.delete(obj)
    await db.commit()
    return True
