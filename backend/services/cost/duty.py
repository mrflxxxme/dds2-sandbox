# ruff: noqa: RUF002, RUF003
"""
Cost — Duty Rules CRUD + Nomenclature area_m2.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import DutyException, DutyRule
from backend.models.cost import Nomenclature
from backend.services.cost.recalc import (
    find_orders_by_articles,
    find_orders_by_barcodes,
    find_orders_by_subjects,
    recalc_orders,
)


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
    # Auto-recalc себес of orders in this category (basis/rate/util changed).
    await recalc_orders(db, project_id, await find_orders_by_subjects(db, project_id, [subject]))
    return True, None


async def delete_duty_rule(db: AsyncSession, project_id: int, rule_id: int):
    result = await db.execute(
        select(DutyRule).where(DutyRule.id == rule_id, DutyRule.project_id == project_id, DutyRule.is_deleted == False)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        return None
    subject = rule.subject
    rule.soft_delete()
    await db.commit()
    # Removing a rule drops duty for that category → recalc affected orders.
    await recalc_orders(db, project_id, await find_orders_by_subjects(db, project_id, [subject]))
    return True


async def get_duty_exceptions(db: AsyncSession, project_id: int, limit: int = 500, offset: int = 0):
    result = await db.execute(
        select(DutyException)
        .where(DutyException.project_id == project_id, DutyException.is_deleted == False)
        .order_by(DutyException.article_seller)
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


async def upsert_duty_exception(db: AsyncSession, project_id: int, payload: dict):
    """Create/update an article-level duty override (keyed by article_seller).

    Overrides only the duty basis+rate; util_collect_rub is intentionally not
    stored here — it stays sourced from the category DutyRule at calc time.
    """
    article = (payload.get("article_seller") or "").strip()
    if not article:
        return None, "article_seller required"
    result = await db.execute(
        select(DutyException).where(
            DutyException.project_id == project_id,
            DutyException.is_deleted == False,
            DutyException.article_seller == article,
        )
    )
    exc = result.scalar_one_or_none()
    if exc:
        exc.basis = payload.get("basis", exc.basis)
        exc.rate = Decimal(str(payload.get("rate", exc.rate)))
        exc.note = payload.get("note", exc.note)
    else:
        exc = DutyException(
            project_id=project_id,
            article_seller=article,
            basis=payload.get("basis", "INVOICE"),
            rate=Decimal(str(payload.get("rate", 0))),
            note=payload.get("note"),
        )
        db.add(exc)
    await db.commit()
    # Auto-recalc себес of orders containing this article (its duty basis/rate changed).
    await recalc_orders(db, project_id, await find_orders_by_articles(db, project_id, [article]))
    return True, None


async def delete_duty_exception(db: AsyncSession, project_id: int, exc_id: int):
    result = await db.execute(
        select(DutyException).where(
            DutyException.id == exc_id,
            DutyException.project_id == project_id,
            DutyException.is_deleted == False,
        )
    )
    exc = result.scalar_one_or_none()
    if not exc:
        return None
    article = exc.article_seller
    exc.soft_delete()
    await db.commit()
    # Removing the exception reverts this article to its category rule → recalc.
    await recalc_orders(db, project_id, await find_orders_by_articles(db, project_id, [article]))
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
    changed: list[str] = []
    for item in items:
        bc = str(item["barcode"]).strip()
        nom = nom_map.get(bc)
        if nom:
            nom.area_m2 = Decimal(str(item["area_m2"]))
            updated += 1
            changed.append(bc)
        else:
            not_found.append(bc)

    await db.commit()
    # Auto-recalc себес of orders containing these barcodes (AREA-basis duty depends on area).
    await recalc_orders(db, project_id, await find_orders_by_barcodes(db, project_id, changed))
    return updated, not_found


async def bulk_update_nomenclature_weight(
    db: AsyncSession,
    project_id: int,
    items: list[dict],
) -> tuple[int, list[str]]:
    """Update reference weight_kg for nomenclature items by barcode."""
    result = await db.execute(select(Nomenclature).where(Nomenclature.project_id == project_id))
    nom_map = {n.barcode: n for n in result.scalars().all()}

    updated = 0
    not_found: list[str] = []
    changed: list[str] = []
    for item in items:
        bc = str(item["barcode"]).strip()
        nom = nom_map.get(bc)
        if nom:
            nom.weight_kg = Decimal(str(item["weight_kg"]))
            updated += 1
            changed.append(bc)
        else:
            not_found.append(bc)

    await db.commit()
    # Auto-recalc себес of orders containing these barcodes (WEIGHT-basis duty depends on weight).
    await recalc_orders(db, project_id, await find_orders_by_barcodes(db, project_id, changed))
    return updated, not_found
