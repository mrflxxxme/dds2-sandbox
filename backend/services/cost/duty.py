"""
Cost — Duty Rules CRUD.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import DutyRule


async def get_duty_rules(db: AsyncSession, project_id: int, limit: int = 500, offset: int = 0):
    result = await db.execute(
        select(DutyRule)
        .where(DutyRule.project_id == project_id, DutyRule.is_deleted == False)
        .order_by(DutyRule.subject)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


async def upsert_duty_rule(db: AsyncSession, project_id: int, payload: dict):
    subject = payload.get("subject", "").strip()
    if not subject:
        return None, "subject required"
    result = await db.execute(
        select(DutyRule).where(
            DutyRule.project_id == project_id, DutyRule.is_deleted == False, DutyRule.subject == subject
        )
    )
    rule = result.scalar_one_or_none()
    if rule:
        rule.basis = payload.get("basis", rule.basis)
        rule.rate = Decimal(str(payload.get("rate", rule.rate)))
        rule.util_collect_rub = Decimal(str(payload.get("util_collect_rub", rule.util_collect_rub)))
        rule.note = payload.get("note", rule.note)
    else:
        rule = DutyRule(
            project_id=project_id,
            subject=subject,
            basis=payload.get("basis", "INVOICE"),
            rate=Decimal(str(payload.get("rate", 0))),
            util_collect_rub=Decimal(str(payload.get("util_collect_rub", 0))),
            note=payload.get("note"),
        )
        db.add(rule)
    await db.commit()
    return True, None


async def delete_duty_rule(db: AsyncSession, project_id: int, rule_id: int):
    result = await db.execute(
        select(DutyRule).where(DutyRule.id == rule_id, DutyRule.project_id == project_id, DutyRule.is_deleted == False)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        return None
    rule.soft_delete()
    await db.commit()
    return True
