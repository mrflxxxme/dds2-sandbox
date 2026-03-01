"""
Router: /refs — accounts, cp_categories, overrides, opening_balances
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.middleware import get_project_id
from backend.models import Account, CounterpartyCategory, Override, OpeningBalance, CategoryRef
from backend.schemas import (
    AccountSchema, CounterpartyCategorySchema, OverrideSchema,
    OpeningBalanceSchema, CategoryRefSchema, DeleteResponse, MessageResponse,
)

router = APIRouter(prefix="/refs")


# ─── Accounts ─────────────────────────────────────────────────────────────────

@router.get("/accounts", response_model=List[AccountSchema])
async def get_accounts(project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.project_id == project_id).order_by(Account.bank, Account.currency))
    return result.scalars().all()


@router.post("/accounts", response_model=AccountSchema)
async def upsert_account(payload: AccountSchema, project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.account == payload.account, Account.project_id == project_id))
    acc = result.scalar_one_or_none()
    if acc:
        for field, val in payload.model_dump(exclude={"id"}).items():
            setattr(acc, field, val)
    else:
        acc = Account(**payload.model_dump(exclude={"id"}), project_id=project_id)
        db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return acc


@router.delete("/accounts/{account_id}", response_model=DeleteResponse)
async def delete_account(account_id: int, project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.id == account_id, Account.project_id == project_id))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "Not found")
    await db.delete(acc)
    await db.commit()
    return {"ok": True}


# ─── Counterparty Categories ──────────────────────────────────────────────────

@router.get("/cp_categories", response_model=List[CounterpartyCategorySchema])
async def get_cp_categories(project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CounterpartyCategory).where(CounterpartyCategory.project_id == project_id).order_by(CounterpartyCategory.cat_lvl1, CounterpartyCategory.cp_name)
    )
    return result.scalars().all()


@router.post("/cp_categories", response_model=CounterpartyCategorySchema)
async def upsert_cp_category(
    payload: CounterpartyCategorySchema, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(CounterpartyCategory).where(CounterpartyCategory.cp_key == payload.cp_key)
    )
    cpc = result.scalar_one_or_none()
    if cpc:
        for field, val in payload.model_dump(exclude={"id"}).items():
            setattr(cpc, field, val)
    else:
        cpc = CounterpartyCategory(**payload.model_dump(exclude={"id"}))
        db.add(cpc)
    await db.commit()
    await db.refresh(cpc)
    return cpc


@router.delete("/cp_categories/{cpc_id}", response_model=DeleteResponse)
async def delete_cp_category(cpc_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CounterpartyCategory).where(CounterpartyCategory.id == cpc_id)
    )
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Not found")
    await db.delete(obj)
    await db.commit()
    return {"ok": True}


# ─── Overrides ────────────────────────────────────────────────────────────────

@router.get("/overrides", response_model=List[OverrideSchema])
async def get_overrides(project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Override).where(Override.project_id == project_id).order_by(Override.updated_at.desc()).limit(500)
    )
    return result.scalars().all()


@router.delete("/overrides/{override_id}", response_model=DeleteResponse)
async def delete_override(override_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Override).where(Override.id == override_id))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "Not found")
    await db.delete(obj)
    await db.commit()
    return {"ok": True}


# ─── Opening Balances ─────────────────────────────────────────────────────────

@router.get("/opening_balances", response_model=List[OpeningBalanceSchema])
async def get_opening_balances(project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OpeningBalance).where(OpeningBalance.project_id == project_id).order_by(OpeningBalance.date_open))
    return result.scalars().all()


@router.post("/opening_balances", response_model=OpeningBalanceSchema)
async def upsert_opening_balance(
    payload: OpeningBalanceSchema, db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import and_
    result = await db.execute(
        select(OpeningBalance).where(
            and_(
                OpeningBalance.date_open == payload.date_open,
                OpeningBalance.account == payload.account,
                OpeningBalance.currency == payload.currency,
            )
        )
    )
    ob = result.scalar_one_or_none()
    if ob:
        ob.opening_balance = payload.opening_balance
    else:
        ob = OpeningBalance(**payload.model_dump(exclude={"id"}))
        db.add(ob)
    await db.commit()
    await db.refresh(ob)
    return ob


# ─── Category Reference ──────────────────────────────────────────────────────

@router.get("/categories", response_model=list[CategoryRefSchema])
async def get_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CategoryRef).order_by(CategoryRef.direction, CategoryRef.sort_order, CategoryRef.cat_lvl1)
    )
    cats = result.scalars().all()
    return [
        {"id": c.id, "direction": c.direction, "cat_lvl1": c.cat_lvl1,
         "cat_lvl2": c.cat_lvl2, "sort_order": c.sort_order}
        for c in cats
    ]


@router.post("/categories", response_model=CategoryRefSchema)
async def add_category(payload: dict, db: AsyncSession = Depends(get_db)):
    direction = payload.get("direction", "expense")
    cat_lvl1 = payload.get("cat_lvl1", "").strip()
    cat_lvl2 = payload.get("cat_lvl2", "").strip()
    if not cat_lvl1 or not cat_lvl2:
        raise HTTPException(400, "cat_lvl1 and cat_lvl2 required")
    # Check duplicate
    existing = await db.execute(
        select(CategoryRef).where(
            CategoryRef.direction == direction,
            CategoryRef.cat_lvl1 == cat_lvl1,
            CategoryRef.cat_lvl2 == cat_lvl2,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"Категория '{cat_lvl1} / {cat_lvl2}' уже существует")
    cat = CategoryRef(
        direction=direction, cat_lvl1=cat_lvl1, cat_lvl2=cat_lvl2,
        sort_order=payload.get("sort_order", 0),
    )
    db.add(cat)
    await db.commit()
    return {"ok": True, "id": cat.id}


@router.delete("/categories/{cat_id}", response_model=DeleteResponse)
async def delete_category(cat_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CategoryRef).where(CategoryRef.id == cat_id))
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(404, "Not found")
    await db.delete(cat)
    await db.commit()
    return {"ok": True}
