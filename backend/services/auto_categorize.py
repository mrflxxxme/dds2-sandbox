"""
Auto-categorization service — match uncategorized transactions by keywords in purpose.
"""

import logging

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CategoryChangeLog, CategoryRule, Transaction

logger = logging.getLogger("dds.auto_categorize")


async def get_rules(db: AsyncSession, project_id: int) -> list:
    """Get all active categorization rules for a project."""
    q = (
        select(CategoryRule)
        .where(
            CategoryRule.project_id == project_id,
            CategoryRule.is_deleted == False,  # noqa: E712
            CategoryRule.is_active == True,  # noqa: E712
        )
        .order_by(CategoryRule.priority.desc(), CategoryRule.id)
    )
    result = await db.execute(q)
    return list(result.scalars().all())


async def get_all_rules(db: AsyncSession, project_id: int) -> list:
    """Get all categorization rules (including inactive) for a project."""
    q = (
        select(CategoryRule)
        .where(
            CategoryRule.project_id == project_id,
            CategoryRule.is_deleted == False,  # noqa: E712
        )
        .order_by(CategoryRule.priority.desc(), CategoryRule.id)
    )
    result = await db.execute(q)
    return list(result.scalars().all())


async def create_rule(
    db: AsyncSession,
    project_id: int,
    keyword: str,
    cat_lvl1: str,
    cat_lvl2: str | None = None,
    direction: str = "expense",
    priority: int = 0,
) -> CategoryRule:
    """Create a new auto-categorization rule."""
    rule = CategoryRule(
        project_id=project_id,
        keyword=keyword.lower().strip(),
        direction=direction,
        cat_lvl1=cat_lvl1,
        cat_lvl2=cat_lvl2,
        priority=priority,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


async def delete_rule(db: AsyncSession, project_id: int, rule_id: int) -> dict:
    """Soft-delete a categorization rule."""
    result = await db.execute(
        select(CategoryRule).where(
            CategoryRule.id == rule_id,
            CategoryRule.project_id == project_id,
            CategoryRule.is_deleted == False,  # noqa: E712
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        return {"error": "Rule not found", "status": 404}

    rule.is_deleted = True
    await db.commit()
    return {"ok": True, "deleted_id": rule_id}


async def preview_auto_categorize(db: AsyncSession, project_id: int) -> list[dict]:
    """
    Preview: which uncategorized txns would be matched by keyword rules.
    Returns list of {txn_id, date, counterparty, purpose, expense, income,
                     matched_keyword, suggested_cat_lvl1, suggested_cat_lvl2}.
    Does NOT modify anything.
    """
    rules = await get_rules(db, project_id)
    if not rules:
        return []

    # Get uncategorized transactions
    q = (
        select(Transaction)
        .where(
            Transaction.project_id == project_id,
            Transaction.is_deleted == False,  # noqa: E712
            Transaction.is_cashflow2 == 1,
            or_(
                Transaction.cat_lvl1_2.is_(None),
                Transaction.cat_lvl1_2 == "",
                Transaction.cat_lvl1_2 == "UNASSIGNED",
            ),
        )
        .order_by(Transaction.date.desc())
    )
    result = await db.execute(q)
    txns = result.scalars().all()

    matches = []
    for txn in txns:
        purpose_lower = (txn.purpose or "").lower()
        cp_lower = (txn.counterparty or "").lower()
        search_text = f"{purpose_lower} {cp_lower}"
        if not search_text.strip():
            continue

        for rule in rules:
            if rule.keyword in search_text:
                # Check direction match
                has_expense = float(txn.expense or 0) > 0
                has_income = float(txn.income or 0) > 0
                if rule.direction == "expense" and not has_expense:
                    continue
                if rule.direction == "income" and not has_income:
                    continue

                matches.append(
                    {
                        "txn_id": txn.txn_id,
                        "date": txn.date.isoformat() if txn.date else None,
                        "counterparty": txn.counterparty,
                        "purpose": txn.purpose,
                        "expense": float(txn.expense or 0),
                        "income": float(txn.income or 0),
                        "currency": txn.currency,
                        "matched_keyword": rule.keyword,
                        "suggested_cat_lvl1": rule.cat_lvl1,
                        "suggested_cat_lvl2": rule.cat_lvl2,
                    }
                )
                break  # first match wins

    return matches


async def apply_auto_categorize(db: AsyncSession, project_id: int) -> dict:
    """
    Apply auto-categorization: update all matching uncategorized transactions.
    Returns {ok, updated, details: [{txn_id, cat_lvl1, cat_lvl2, keyword}]}.
    """
    matches = await preview_auto_categorize(db, project_id)
    if not matches:
        return {"ok": True, "updated": 0, "details": []}

    updated_count = 0
    details = []

    for m in matches:
        cat_lvl1 = m["suggested_cat_lvl1"]
        cat_lvl2 = m["suggested_cat_lvl2"]
        txn_id = m["txn_id"]

        result = await db.execute(
            update(Transaction)
            .where(
                Transaction.txn_id == txn_id,
                Transaction.project_id == project_id,
            )
            .values(cat_lvl1_2=cat_lvl1, cat_lvl2_2=cat_lvl2)
        )

        if result.rowcount > 0:  # type: ignore[attr-defined]
            updated_count += 1
            details.append(
                {
                    "txn_id": txn_id,
                    "cat_lvl1": cat_lvl1,
                    "cat_lvl2": cat_lvl2,
                    "keyword": m["matched_keyword"],
                }
            )

            # Log the change
            log = CategoryChangeLog(
                project_id=project_id,
                txn_id=txn_id,
                old_cat_lvl1=None,
                old_cat_lvl2=None,
                new_cat_lvl1=cat_lvl1,
                new_cat_lvl2=cat_lvl2,
                changed_by="auto_categorize",
                scope="auto",
            )
            db.add(log)

    await db.commit()

    # Invalidate caches
    from backend.cache import invalidate_project_reports

    await invalidate_project_reports(project_id)

    logger.info(f"Auto-categorized {updated_count} transactions for project {project_id}")
    return {"ok": True, "updated": updated_count, "details": details}
