"""
Refs service — CRUD operations for reference data.

Extracted from routers/refs.py to enable reuse and testing.
"""

from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Account, CounterpartyCategory, Override,
    OpeningBalance, CategoryRef,
)
from backend.cache import invalidate_cache

import logging
logger = logging.getLogger(__name__)


# ─── Accounts ────────────────────────────────────────────────────────────────


async def list_accounts(db: AsyncSession, project_id: int) -> list:
    """List all accounts for a project."""
    result = await db.execute(
        select(Account).where(Account.project_id == project_id)
    )
    return result.scalars().all()


async def upsert_account(
    db: AsyncSession, project_id: int, payload: dict
) -> Account:
    """Create or update an account."""
    if payload.get("id"):
        result = await db.execute(
            select(Account).where(
                Account.id == payload["id"],
                Account.project_id == project_id,
            )
        )
        acc = result.scalar_one_or_none()
        if acc:
            for k, v in payload.items():
                if k != "id":
                    setattr(acc, k, v)
            await db.commit()
            await db.refresh(acc)
            return acc

    acc = Account(**payload, project_id=project_id)
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    await invalidate_cache("reports")
    return acc


async def delete_account(db: AsyncSession, project_id: int, account_id: int) -> bool:
    """Delete an account. Returns True if deleted."""
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.project_id == project_id,
        )
    )
    acc = result.scalar_one_or_none()
    if not acc:
        return False
    await db.delete(acc)
    await db.commit()
    await invalidate_cache("reports")
    return True


# ─── Counterparty Categories ────────────────────────────────────────────────


async def list_cp_categories(db: AsyncSession, project_id: int) -> list:
    """List all counterparty categories for a project."""
    result = await db.execute(
        select(CounterpartyCategory).where(
            CounterpartyCategory.project_id == project_id
        )
    )
    return result.scalars().all()


async def upsert_cp_category(
    db: AsyncSession, project_id: int, payload: dict
) -> CounterpartyCategory:
    """Create or update a counterparty category."""
    if payload.get("id"):
        result = await db.execute(
            select(CounterpartyCategory).where(
                CounterpartyCategory.id == payload["id"],
                CounterpartyCategory.project_id == project_id,
            )
        )
        cpc = result.scalar_one_or_none()
        if cpc:
            for k, v in payload.items():
                if k != "id":
                    setattr(cpc, k, v)
            await db.commit()
            await db.refresh(cpc)
            return cpc

    payload["project_id"] = project_id
    cpc = CounterpartyCategory(**payload)
    db.add(cpc)
    await db.commit()
    await db.refresh(cpc)
    await invalidate_cache("reports")
    return cpc


async def delete_cp_category(db: AsyncSession, project_id: int, cpc_id: int) -> bool:
    """Delete a counterparty category. Returns True if deleted."""
    result = await db.execute(
        select(CounterpartyCategory).where(
            CounterpartyCategory.id == cpc_id,
            CounterpartyCategory.project_id == project_id,
        )
    )
    cpc = result.scalar_one_or_none()
    if not cpc:
        return False
    await db.delete(cpc)
    await db.commit()
    await invalidate_cache("reports")
    return True


# ─── Overrides ───────────────────────────────────────────────────────────────


async def list_overrides(db: AsyncSession, project_id: int) -> list:
    """List all overrides for a project."""
    result = await db.execute(
        select(Override).where(Override.project_id == project_id)
    )
    return result.scalars().all()


async def delete_override(db: AsyncSession, project_id: int, override_id: int) -> bool:
    """Delete an override. Returns True if deleted."""
    result = await db.execute(
        select(Override).where(
            Override.id == override_id,
            Override.project_id == project_id,
        )
    )
    ovr = result.scalar_one_or_none()
    if not ovr:
        return False
    await db.delete(ovr)
    await db.commit()
    await invalidate_cache("reports")
    return True


# ─── Opening Balances ────────────────────────────────────────────────────────


async def list_opening_balances(db: AsyncSession, project_id: int) -> list:
    """List all opening balances for a project."""
    result = await db.execute(
        select(OpeningBalance).where(OpeningBalance.project_id == project_id)
    )
    return result.scalars().all()


async def upsert_opening_balance(
    db: AsyncSession, project_id: int, payload: dict
) -> OpeningBalance:
    """Create or update an opening balance."""
    if payload.get("id"):
        result = await db.execute(
            select(OpeningBalance).where(
                OpeningBalance.id == payload["id"],
                OpeningBalance.project_id == project_id,
            )
        )
        ob = result.scalar_one_or_none()
        if ob:
            for k, v in payload.items():
                if k != "id":
                    setattr(ob, k, v)
            await db.commit()
            await db.refresh(ob)
            return ob

    payload["project_id"] = project_id
    ob = OpeningBalance(**payload)
    db.add(ob)
    await db.commit()
    await db.refresh(ob)
    await invalidate_cache("reports")
    return ob


# ─── Category Reference ─────────────────────────────────────────────────────


async def list_categories(db: AsyncSession, project_id: int) -> list[dict]:
    """List all categories for a project, structured as tree."""
    result = await db.execute(
        select(CategoryRef).where(CategoryRef.project_id == project_id)
        .order_by(CategoryRef.cat_lvl1, CategoryRef.cat_lvl2)
    )
    rows = result.scalars().all()

    # Build tree: group by cat_lvl1
    tree: dict[str, dict] = {}
    for r in rows:
        if r.cat_lvl1 not in tree:
            tree[r.cat_lvl1] = {"cat_lvl1": r.cat_lvl1, "children": [], "ids": []}
        if r.cat_lvl2:
            tree[r.cat_lvl1]["children"].append(r.cat_lvl2)
        tree[r.cat_lvl1]["ids"].append(r.id)

    return [
        {"cat_lvl1": k, "children": v["children"], "ids": v["ids"]}
        for k, v in tree.items()
    ]


async def add_category(
    db: AsyncSession, project_id: int, cat_lvl1: str, cat_lvl2: Optional[str] = None,
    direction: Optional[str] = None,
) -> CategoryRef:
    """Add a category reference."""
    cat = CategoryRef(
        project_id=project_id,
        direction=direction or "expense",
        cat_lvl1=cat_lvl1,
        cat_lvl2=cat_lvl2 or "",
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


async def delete_category(
    db: AsyncSession, cat_id: int, project_id: int
) -> bool:
    """Delete a category reference. Returns True if deleted."""
    result = await db.execute(
        select(CategoryRef).where(
            CategoryRef.id == cat_id,
            CategoryRef.project_id == project_id,
        )
    )
    cat = result.scalar_one_or_none()
    if not cat:
        return False
    await db.delete(cat)
    await db.commit()
    return True
