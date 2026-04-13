"""
Cost — Duty Rules CRUD + Nomenclature area_m2.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.models import DutyRule
from backend.models.cost import Nomenclature


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


async def bulk_update_nomenclature_area(
    db: AsyncSession,
    project_id: int,
    items: list[dict],
) -> tuple[int, list[str]]:
    """Update area_m2 for nomenclature items by barcode."""
    result = await db.execute(select(Nomenclature).where(Nomenclature.project_id == project_id))
    nom_map = {n.barcode: n for n in result.scalars().all()}

    updated = 0
    not_found: list[str] = []
    for item in items:
        bc = str(item["barcode"]).strip()
        nom = nom_map.get(bc)
        if nom:
            nom.area_m2 = Decimal(str(item["area_m2"]))
            updated += 1
        else:
            not_found.append(bc)

    await db.commit()
    await invalidate_cache(f"cost:project_id={project_id}")
    return updated, not_found
