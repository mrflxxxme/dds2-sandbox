"""
Router: /refs — accounts, cp_categories, overrides, opening_balances, category_ref

Delegates CRUD logic to services/refs_service.py.
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.schemas import (
    AccountSchema, CounterpartyCategorySchema, OpeningBalanceSchema,
)
from backend.project_context import get_current_project
from backend.middleware import get_project_id
from backend.services import refs_service

router = APIRouter(prefix="/refs")


# ─── Accounts ─────────────────────────────────────────────────────────────────

@router.get("/accounts")
async def get_accounts(project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    return await refs_service.list_accounts(db, project_id)


@router.post("/accounts")
async def upsert_account(payload: AccountSchema, project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    return await refs_service.upsert_account(db, project_id, payload.model_dump(exclude_unset=True))


@router.delete("/accounts/{account_id}")
async def delete_account(account_id: int, project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    deleted = await refs_service.delete_account(db, account_id)
    if not deleted:
        raise HTTPException(404, "Account not found")
    return {"ok": True}


# ─── Counterparty Categories ──────────────────────────────────────────────────

@router.get("/cp_categories")
async def get_cp_categories(project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    return await refs_service.list_cp_categories(db, project_id)


@router.post("/cp_categories")
async def upsert_cp_category(
    payload: CounterpartyCategorySchema, db: AsyncSession = Depends(get_db)
):
    return await refs_service.upsert_cp_category(db, payload.model_dump(exclude_unset=True))


@router.delete("/cp_categories/{cpc_id}")
async def delete_cp_category(cpc_id: int, db: AsyncSession = Depends(get_db)):
    deleted = await refs_service.delete_cp_category(db, cpc_id)
    if not deleted:
        raise HTTPException(404, "Category not found")
    return {"ok": True}


# ─── Overrides ────────────────────────────────────────────────────────────────

@router.get("/overrides")
async def get_overrides(project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    return await refs_service.list_overrides(db, project_id)


@router.delete("/overrides/{override_id}")
async def delete_override(override_id: int, db: AsyncSession = Depends(get_db)):
    deleted = await refs_service.delete_override(db, override_id)
    if not deleted:
        raise HTTPException(404, "Override not found")
    return {"ok": True}


# ─── Opening Balances ─────────────────────────────────────────────────────────

@router.get("/opening_balances")
async def get_opening_balances(project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    return await refs_service.list_opening_balances(db, project_id)


@router.post("/opening_balances")
async def upsert_opening_balance(
    payload: OpeningBalanceSchema, db: AsyncSession = Depends(get_db)
):
    return await refs_service.upsert_opening_balance(db, payload.model_dump(exclude_unset=True))


# ─── Category Reference ──────────────────────────────────────────────────────

@router.get("/categories")
async def get_categories(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await refs_service.list_categories(db, project.id)


@router.post("/categories")
async def add_category(
    payload: dict,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    cat_lvl1 = payload.get("cat_lvl1", "").strip()
    cat_lvl2 = payload.get("cat_lvl2", "").strip() or None
    if not cat_lvl1:
        raise HTTPException(400, "cat_lvl1 is required")

    cat = await refs_service.add_category(db, project.id, cat_lvl1, cat_lvl2)
    return {"ok": True, "id": cat.id}


@router.delete("/categories/{cat_id}")
async def delete_category(
    cat_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    deleted = await refs_service.delete_category(db, cat_id, project.id)
    if not deleted:
        raise HTTPException(404, "Category not found")
    return {"ok": True}
