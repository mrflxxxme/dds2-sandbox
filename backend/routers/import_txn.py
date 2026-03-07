"""
Router: /import and /transactions
Thin HTTP layer — all business logic is in services/transactions_service.py.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.middleware import get_project_id
from backend.schemas import (
    TransactionSchema, TransactionFilter,
    ImportLogSchema,
)
from backend.services import transactions_service

router = APIRouter()


# ─── Import ───────────────────────────────────────────────────────────────────

@router.post("/import/upload", response_model=ImportLogSchema)
async def upload_statement(
    file: UploadFile = File(...),
    source_type: str = Form(...),
    account_no: str = Form(""),
    project_id: int = Depends(get_project_id),
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
    loop = asyncio.get_event_loop()

    def _run():
        with SyncSessionLocal() as sync_db:
            log = import_statement(
                sync_db, safe_filename, source_type, account_no, data, project_id
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
async def get_import_logs(project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    from backend.models import ImportLog
    from sqlalchemy import select
    result = await db.execute(
        select(ImportLog).where(ImportLog.project_id == project_id).order_by(ImportLog.imported_at.desc()).limit(100)
    )
    return result.scalars().all()


# ─── Transactions ─────────────────────────────────────────────────────────────

@router.post("/transactions/search", response_model=List[TransactionSchema])
async def search_transactions(
    f: TransactionFilter,
    project_id: int = Depends(get_project_id),
    db: AsyncSession = Depends(get_db),
):
    return await transactions_service.search_transactions(
        db, project_id,
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
async def get_unassigned(limit: int = 200, project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    from backend.services import fx_service
    from datetime import date as date_cls

    txns = await transactions_service.get_unassigned(db, project_id, limit)

    # Load FX rates for conversion
    rates_map = await fx_service.get_rates_map(db, project_id, date_cls(2020, 1, 1), date_cls.today())
    fallback_rate = await fx_service.get_rate_for_date(db, project_id, date_cls.today()) if not rates_map else None

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
    payload: dict,
    project_id: int = Depends(get_project_id),
    db: AsyncSession = Depends(get_db),
):
    """
    Assign category to a transaction.
    scope='txn' → override for this txn only.
    scope='cp' → update counterparty_category (affects all txns with same cp_key).
    """
    txn_id = payload.get("txn_id")
    cat_lvl1 = payload.get("cat_lvl1")
    cat_lvl2 = payload.get("cat_lvl2")
    scope = payload.get("scope", "txn")

    if not txn_id or not cat_lvl1:
        raise HTTPException(400, "txn_id and cat_lvl1 required")

    result = await transactions_service.assign_category(
        db, project_id, txn_id, cat_lvl1, cat_lvl2, scope,
    )
    if "error" in result:
        raise HTTPException(result.get("status", 400), result["error"])
    return result


@router.get("/transactions/unassigned_grouped")
async def get_unassigned_grouped(project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    """Group uncategorized transactions by counterparty, showing income/expense totals."""
    return await transactions_service.get_unassigned_grouped(db, project_id)


@router.post("/transactions/assign_category_bulk")
async def assign_category_bulk(payload: dict, project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    """Assign category to all uncategorized transactions with given cp_key."""
    cp_key = payload.get("cp_key")
    cat_lvl1 = payload.get("cat_lvl1")
    cat_lvl2 = payload.get("cat_lvl2")

    if not cp_key or not cat_lvl1:
        raise HTTPException(400, "cp_key and cat_lvl1 required")

    return await transactions_service.assign_category_bulk(
        db, project_id, cp_key, cat_lvl1, cat_lvl2,
    )


@router.post("/transactions/assign_category_by_ids")
async def assign_category_by_ids(payload: dict, project_id: int = Depends(get_project_id), db: AsyncSession = Depends(get_db)):
    """Assign category to specific transactions by txn_id list."""
    txn_ids = payload.get("txn_ids", [])
    cat_lvl1 = payload.get("cat_lvl1")
    cat_lvl2 = payload.get("cat_lvl2")

    if not txn_ids or not cat_lvl1:
        raise HTTPException(400, "txn_ids and cat_lvl1 required")

    return await transactions_service.assign_category_by_ids(
        db, project_id, txn_ids, cat_lvl1, cat_lvl2,
    )
