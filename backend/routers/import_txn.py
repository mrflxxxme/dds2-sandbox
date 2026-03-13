"""
Router: /import and /transactions
Thin HTTP layer — all business logic is in services/transactions_service.py.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas import (
    TransactionSchema, TransactionFilter,
    ImportLogSchema,
    CategoryAssignment, BulkCategoryAssignment, CategoryAssignByIds,
)
from backend.services import transactions_service

router = APIRouter()


# ─── Import ───────────────────────────────────────────────────────────────────

@router.post("/import/upload", response_model=ImportLogSchema)
async def upload_statement(
    file: UploadFile = File(...),
    source_type: str = Form(...),
    account_no: str = Form(""),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Upload and import a bank statement file.
    Validates file extension and size before processing.
    """
    import os
    from backend.config import settings as app_settings
    from backend.etl.service import import_statement
    from backend.database import SyncSessionLocal

    # Validate file extension
    allowed_exts = [e.strip() for e in app_settings.ALLOWED_UPLOAD_EXTENSIONS.split(",")]
    file_ext = os.path.splitext(file.filename or "")[1].lower()
    if file_ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый формат файла: {file_ext}. Разрешены: {', '.join(allowed_exts)}",
        )

    # Sanitize filename — remove path separators
    safe_filename = os.path.basename(file.filename or "upload")

    data = await file.read()

    # Validate file content (magic bytes)
    from backend.utils.file_validation import validate_file_content
    validate_file_content(data, safe_filename)

    # Validate file size
    max_bytes = app_settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"Файл слишком большой. Максимум: {app_settings.MAX_UPLOAD_SIZE_MB} МБ",
        )

    # Run synchronously inside a thread (ETL uses sync ORM)
    import asyncio
    loop = asyncio.get_running_loop()

    def _run():
        with SyncSessionLocal() as sync_db:
            log = import_statement(
                sync_db, safe_filename, source_type, account_no, data, project.id
            )
            return {
                "id": log.id,
                "filename": log.filename,
                "source_type": log.source_type,
                "imported_at": log.imported_at,
                "rows_raw": log.rows_raw,
                "rows_inserted": log.rows_inserted,
                "rows_skipped": log.rows_skipped,
                "status": log.status,
                "error_msg": log.error_msg,
            }

    result = await loop.run_in_executor(None, _run)
    return result


@router.get("/import/logs", response_model=List[ImportLogSchema])
async def get_import_logs(project: Project = Depends(get_current_project), db: AsyncSession = Depends(get_db)):
    from backend.models import ImportLog
    from sqlalchemy import select
    result = await db.execute(
        select(ImportLog).where(ImportLog.project_id == project.id).order_by(ImportLog.imported_at.desc()).limit(100)
    )
    return result.scalars().all()


# ─── Transactions ─────────────────────────────────────────────────────────────

@router.post("/transactions/search", response_model=List[TransactionSchema])
async def search_transactions(
    f: TransactionFilter,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await transactions_service.search_transactions(
        db, project.id,
        date_from=f.date_from,
        date_to=f.date_to,
        account=f.account,
        currency=f.currency,
        category=f.cat_lvl1_2,
        search=f.counterparty,
        status=f.status,
        is_cashflow2=f.is_cashflow2,
        limit=f.limit,
        offset=f.offset,
    )


@router.get("/transactions/unassigned")
async def get_unassigned(limit: int = 200, project: Project = Depends(get_current_project), db: AsyncSession = Depends(get_db)):
    from backend.services import fx_service
    from datetime import date as date_cls

    txns = await transactions_service.get_unassigned(db, project.id, limit)

    # Load FX rates for conversion
    rates_map = await fx_service.get_rates_map(db, project.id, date_cls(2020, 1, 1), date_cls.today())
    fallback_rate = await fx_service.get_rate_for_date(db, project.id, date_cls.today()) if not rates_map else None

    items = []
    for t in txns:
        inc = float(t.income or 0)
        exp = float(t.expense or 0)
        currency = t.currency or "RUB"
        rate = None
        if currency == "CNY":
            day_date = t.date.date() if hasattr(t.date, 'date') else t.date
            rate = fx_service.find_rate_for_date(rates_map, day_date) or fallback_rate or 1.0
            inc_rub = inc * rate
            exp_rub = exp * rate
        else:
            inc_rub = inc
            exp_rub = exp

        items.append({
            "id": t.id,
            "txn_id": t.txn_id,
            "date": t.date.isoformat() if t.date else None,
            "counterparty": t.counterparty,
            "cp_key": t.cp_key,
            "income": inc_rub,
            "expense": exp_rub,
            "income_original": inc if currency != "RUB" else None,
            "expense_original": exp if currency != "RUB" else None,
            "currency": currency,
            "fx_rate": rate,
            "purpose": t.purpose,
            "cat_lvl1_2": t.cat_lvl1_2,
            "cat_lvl2_2": t.cat_lvl2_2,
            "account": t.account,
            "event_type2": t.event_type2,
            "is_cashflow2": t.is_cashflow2,
        })
    return items


@router.post("/transactions/assign_category")
async def assign_category(
    payload: CategoryAssignment,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Assign category to a transaction.
    scope='txn' → override for this txn only.
    scope='cp' → update counterparty_category (affects all txns with same cp_key).
    """
    if not payload.txn_id or not payload.cat_lvl1:
        raise HTTPException(400, "txn_id and cat_lvl1 required")

    result = await transactions_service.assign_category(
        db, project.id, payload.txn_id, payload.cat_lvl1, payload.cat_lvl2, payload.scope,
    )
    if "error" in result:
        raise HTTPException(result.get("status", 400), result["error"])
    return result


@router.get("/transactions/unassigned_grouped")
async def get_unassigned_grouped(project: Project = Depends(get_current_project), db: AsyncSession = Depends(get_db)):
    """Group uncategorized transactions by counterparty, showing income/expense totals."""
    return await transactions_service.get_unassigned_grouped(db, project.id)


@router.post("/transactions/assign_category_bulk")
async def assign_category_bulk(payload: BulkCategoryAssignment, project: Project = Depends(get_current_project), db: AsyncSession = Depends(get_db)):
    """Assign category to all uncategorized transactions with given cp_key."""
    if not payload.cp_key or not payload.cat_lvl1:
        raise HTTPException(400, "cp_key and cat_lvl1 required")

    return await transactions_service.assign_category_bulk(
        db, project.id, payload.cp_key, payload.cat_lvl1, payload.cat_lvl2,
    )


@router.post("/transactions/assign_category_by_ids")
async def assign_category_by_ids(payload: CategoryAssignByIds, project: Project = Depends(get_current_project), db: AsyncSession = Depends(get_db)):
    """Assign category to specific transactions by txn_id list."""
    if not payload.txn_ids or not payload.cat_lvl1:
        raise HTTPException(400, "txn_ids and cat_lvl1 required")

    return await transactions_service.assign_category_by_ids(
        db, project.id, payload.txn_ids, payload.cat_lvl1, payload.cat_lvl2,
    )


# ─── Auto-Categorize ─────────────────────────────────────────────────────────

@router.get("/transactions/auto_categorize/rules")
async def get_auto_rules(project: Project = Depends(get_current_project), db: AsyncSession = Depends(get_db)):
    """Get all auto-categorization rules for the project."""
    from backend.services import auto_categorize
    rules = await auto_categorize.get_all_rules(db, project.id)
    return [
        {
            "id": r.id,
            "keyword": r.keyword,
            "direction": r.direction,
            "cat_lvl1": r.cat_lvl1,
            "cat_lvl2": r.cat_lvl2,
            "priority": r.priority,
            "is_active": r.is_active,
        }
        for r in rules
    ]


@router.post("/transactions/auto_categorize/rules")
async def create_auto_rule(
    payload: dict,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Create a new auto-categorization rule."""
    from backend.services import auto_categorize
    keyword = payload.get("keyword", "").strip()
    cat_lvl1 = payload.get("cat_lvl1", "").strip()
    if not keyword or not cat_lvl1:
        raise HTTPException(400, "keyword and cat_lvl1 required")

    rule = await auto_categorize.create_rule(
        db, project.id,
        keyword=keyword,
        cat_lvl1=cat_lvl1,
        cat_lvl2=payload.get("cat_lvl2"),
        direction=payload.get("direction", "expense"),
        priority=payload.get("priority", 0),
    )
    return {
        "id": rule.id,
        "keyword": rule.keyword,
        "direction": rule.direction,
        "cat_lvl1": rule.cat_lvl1,
        "cat_lvl2": rule.cat_lvl2,
        "priority": rule.priority,
        "is_active": rule.is_active,
    }


@router.delete("/transactions/auto_categorize/rules/{rule_id}")
async def delete_auto_rule(
    rule_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Delete an auto-categorization rule."""
    from backend.services import auto_categorize
    result = await auto_categorize.delete_rule(db, project.id, rule_id)
    if "error" in result:
        raise HTTPException(result.get("status", 400), result["error"])
    return result


@router.get("/transactions/auto_categorize/preview")
async def preview_auto_categorize(project: Project = Depends(get_current_project), db: AsyncSession = Depends(get_db)):
    """Preview which uncategorized transactions would be matched by keyword rules."""
    from backend.services import auto_categorize
    return await auto_categorize.preview_auto_categorize(db, project.id)


@router.post("/transactions/auto_categorize/apply")
async def apply_auto_categorize(project: Project = Depends(get_current_project), db: AsyncSession = Depends(get_db)):
    """Apply auto-categorization: assign categories to all matching uncategorized transactions."""
    from backend.services import auto_categorize
    return await auto_categorize.apply_auto_categorize(db, project.id)
