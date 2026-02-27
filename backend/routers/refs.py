"""
Router: /refs — accounts, cp_categories, overrides, opening_balances
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Account, CounterpartyCategory, Override, OpeningBalance
from backend.schemas import (
    AccountSchema, CounterpartyCategorySchema,
    OverrideSchema, OpeningBalanceSchema,
)

router = APIRouter(prefix="/refs")


# ─── Accounts ─────────────────────────────────────────────────────────────────

@router.get("/accounts", response_model=List[AccountSchema])
async def get_accounts(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).order_by(Account.bank, Account.currency))
    return result.scalars().all()


@router.post("/accounts", response_model=AccountSchema)
async def upsert_account(payload: AccountSchema, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.account == payload.account))
    acc = result.scalar_one_or_none()
    if acc:
        for field, val in payload.model_dump(exclude={"id"}).items():
            setattr(acc, field, val)
    else:
        acc = Account(**payload.model_dump(exclude={"id"}))
        db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return acc


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Account).where(Account.id == account_id))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(404, "Not found")
    await db.delete(acc)
    await db.commit()
    return {"ok": True}


# ─── Counterparty Categories ──────────────────────────────────────────────────

@router.get("/cp_categories", response_model=List[CounterpartyCategorySchema])
async def get_cp_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CounterpartyCategory).order_by(CounterpartyCategory.cat_lvl1, CounterpartyCategory.cp_name)
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


@router.delete("/cp_categories/{cpc_id}")
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
async def get_overrides(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Override).order_by(Override.updated_at.desc()).limit(500)
    )
    return result.scalars().all()


@router.delete("/overrides/{override_id}")
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
async def get_opening_balances(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(OpeningBalance).order_by(OpeningBalance.date_open))
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
