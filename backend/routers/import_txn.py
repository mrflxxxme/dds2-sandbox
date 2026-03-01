"""
Router: /import and /transactions
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select, func, and_, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import (
    Transaction, Account, CounterpartyCategory, Override,
    ImportLog, CategoryChangeLog,
)
from backend.schemas import (
    TransactionSchema, TransactionFilter, CategoryAssignment,
    ImportLogSchema, OverrideSchema, CounterpartyCategorySchema,
)

router = APIRouter()


# ─── Import ───────────────────────────────────────────────────────────────────

@router.post("/import/upload", response_model=ImportLogSchema)
async def upload_statement(
    file: UploadFile = File(...),
    source_type: str = Form(...),
    account_no: str = Form(...),
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
                sync_db, safe_filename, source_type, account_no, data
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
async def get_import_logs(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ImportLog).order_by(ImportLog.imported_at.desc()).limit(100)
    )
    return result.scalars().all()


# ─── Transactions ─────────────────────────────────────────────────────────────

@router.post("/transactions/search", response_model=List[TransactionSchema])
async def search_transactions(
    f: TransactionFilter,
    db: AsyncSession = Depends(get_db),
):
    q = select(Transaction)
    conditions = []
    if f.date_from:
        conditions.append(Transaction.date >= f.date_from)
    if f.date_to:
        conditions.append(Transaction.date <= f.date_to)
    if f.account:
        conditions.append(Transaction.account == f.account)
    if f.currency:
        conditions.append(Transaction.currency == f.currency)
    if f.counterparty:
        conditions.append(Transaction.counterparty.ilike(f"%{f.counterparty}%"))
    if f.status:
        conditions.append(Transaction.status == f.status)
    if f.is_cashflow2 is not None:
        conditions.append(Transaction.is_cashflow2 == f.is_cashflow2)
    if f.cat_lvl1_2:
        conditions.append(Transaction.cat_lvl1_2 == f.cat_lvl1_2)
    if conditions:
        q = q.where(and_(*conditions))
    q = q.order_by(Transaction.date.desc()).limit(f.limit).offset(f.offset)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/transactions/unassigned", response_model=List[TransactionSchema])
async def get_unassigned(limit: int = 200, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Transaction)
        .where(Transaction.is_cashflow2 == 1, Transaction.cat_lvl1_2.is_(None))
        .order_by(Transaction.expense.desc())
        .limit(limit)
    )
    return result.scalars().all()


@router.post("/transactions/assign_category")
async def assign_category(
    assignment: CategoryAssignment,
    db: AsyncSession = Depends(get_db),
):
    """
    Assign category to a transaction.
    scope='txn' → override for this txn only.
    scope='cp' → update counterparty_category (affects all txns with same cp_key).
    """
    # Get the transaction
    result = await db.execute(
        select(Transaction).where(Transaction.txn_id == assignment.txn_id)
    )
    txn = result.scalar_one_or_none()
    if not txn:
        raise HTTPException(404, "Transaction not found")

    old_cat1, old_cat2 = txn.cat_lvl1_2, txn.cat_lvl2_2

    if assignment.scope == "txn":
        # Upsert override
        res = await db.execute(
            select(Override).where(Override.txn_id == assignment.txn_id)
        )
        ov = res.scalar_one_or_none()
        if ov:
            ov.cat_lvl1 = assignment.cat_lvl1
            ov.cat_lvl2 = assignment.cat_lvl2
            ov.comment = assignment.comment
        else:
            ov = Override(
                txn_id=assignment.txn_id,
                cat_lvl1=assignment.cat_lvl1,
                cat_lvl2=assignment.cat_lvl2,
                comment=assignment.comment,
            )
            db.add(ov)
        txn.cat_lvl1_2 = assignment.cat_lvl1
        txn.cat_lvl2_2 = assignment.cat_lvl2
        txn.status = "OK"

    elif assignment.scope == "cp":
        cp_key = assignment.cp_key or txn.cp_key
        if not cp_key:
            raise HTTPException(400, "No cp_key available")

        # Upsert counterparty category
        res = await db.execute(
            select(CounterpartyCategory).where(CounterpartyCategory.cp_key == cp_key)
        )
        cpc = res.scalar_one_or_none()
        if cpc:
            cpc.cat_lvl1 = assignment.cat_lvl1
            cpc.cat_lvl2 = assignment.cat_lvl2
        else:
            cpc = CounterpartyCategory(
                cp_key=cp_key,
                cp_name=txn.counterparty,
                cat_lvl1=assignment.cat_lvl1,
                cat_lvl2=assignment.cat_lvl2,
            )
            db.add(cpc)

        # Update all transactions with this cp_key
        await db.execute(
            text(
                """UPDATE transactions
                   SET cat_lvl1_2 = :c1, cat_lvl2_2 = :c2,
                       status = CASE WHEN is_cashflow2=1 THEN 'OK' ELSE status END
                   WHERE cp_key = :cpk AND is_cashflow2 = 1"""
            ),
            {"c1": assignment.cat_lvl1, "c2": assignment.cat_lvl2, "cpk": cp_key},
        )

    # Log the change
    log = CategoryChangeLog(
        txn_id=assignment.txn_id,
        old_cat_lvl1=old_cat1,
        old_cat_lvl2=old_cat2,
        new_cat_lvl1=assignment.cat_lvl1,
        new_cat_lvl2=assignment.cat_lvl2,
        scope=assignment.scope,
    )
    db.add(log)
    await db.commit()
    return {"ok": True}


@router.get("/transactions/unassigned_grouped")
async def get_unassigned_grouped(db: AsyncSession = Depends(get_db)):
    """Group uncategorized transactions by counterparty, showing income/expense totals."""
    result = await db.execute(
        select(
            Transaction.cp_key,
            Transaction.counterparty,
            Transaction.currency,
            func.count(Transaction.txn_id).label("cnt"),
            func.sum(Transaction.income).label("total_income"),
            func.sum(Transaction.expense).label("total_expense"),
        )
        .where(Transaction.is_cashflow2 == 1, Transaction.cat_lvl1_2.is_(None))
        .group_by(Transaction.cp_key, Transaction.counterparty, Transaction.currency)
        .order_by(func.sum(Transaction.income).desc(), func.sum(Transaction.expense).desc())
    )

    rows = []
    for r in result:
        rows.append({
            "cp_key": r.cp_key,
            "counterparty": r.counterparty or "—",
            "currency": r.currency,
            "count": r.cnt,
            "total_income": float(r.total_income or 0),
            "total_expense": float(r.total_expense or 0),
        })
    return rows


@router.post("/transactions/assign_category_bulk")
async def assign_category_bulk(payload: dict, db: AsyncSession = Depends(get_db)):
    """Assign category to all uncategorized transactions with given cp_key."""
    cp_key = payload.get("cp_key")
    cat_lvl1 = payload.get("cat_lvl1")
    cat_lvl2 = payload.get("cat_lvl2")

    if not cp_key or not cat_lvl1:
        raise HTTPException(400, "cp_key and cat_lvl1 required")

    direction = payload.get("direction", "all")

    # Only upsert counterparty category if applying to ALL transactions
    if direction == "all":
        res = await db.execute(
            select(CounterpartyCategory).where(CounterpartyCategory.cp_key == cp_key)
        )
        cpc = res.scalar_one_or_none()
        if cpc:
            cpc.cat_lvl1 = cat_lvl1
            cpc.cat_lvl2 = cat_lvl2
        else:
            cpc = CounterpartyCategory(
                cp_key=cp_key,
                cp_name=payload.get("counterparty"),
                cat_lvl1=cat_lvl1,
                cat_lvl2=cat_lvl2,
            )
            db.add(cpc)

    # Update transactions with this cp_key
    if direction == "income":
        sql = """UPDATE transactions
                 SET cat_lvl1_2 = :c1, cat_lvl2_2 = :c2,
                     status = CASE WHEN is_cashflow2=1 THEN 'OK' ELSE status END
                 WHERE cp_key = :cpk AND is_cashflow2 = 1 AND cat_lvl1_2 IS NULL AND income > 0"""
    elif direction == "expense":
        sql = """UPDATE transactions
                 SET cat_lvl1_2 = :c1, cat_lvl2_2 = :c2,
                     status = CASE WHEN is_cashflow2=1 THEN 'OK' ELSE status END
                 WHERE cp_key = :cpk AND is_cashflow2 = 1 AND cat_lvl1_2 IS NULL AND expense > 0"""
    else:
        sql = """UPDATE transactions
                 SET cat_lvl1_2 = :c1, cat_lvl2_2 = :c2,
                     status = CASE WHEN is_cashflow2=1 THEN 'OK' ELSE status END
                 WHERE cp_key = :cpk AND is_cashflow2 = 1 AND cat_lvl1_2 IS NULL"""
    result = await db.execute(
        text(sql),
        {"c1": cat_lvl1, "c2": cat_lvl2, "cpk": cp_key},
    )

    await db.commit()
    return {"ok": True, "updated": result.rowcount}


@router.post("/transactions/assign_category_by_ids")
async def assign_category_by_ids(payload: dict, db: AsyncSession = Depends(get_db)):
    """Assign category to specific transactions by txn_id list."""
    txn_ids = payload.get("txn_ids", [])
    cat_lvl1 = payload.get("cat_lvl1")
    cat_lvl2 = payload.get("cat_lvl2")

    if not txn_ids or not cat_lvl1:
        raise HTTPException(400, "txn_ids and cat_lvl1 required")

    # Update each transaction
    updated = 0
    for tid in txn_ids:
        result = await db.execute(
            text(
                """UPDATE transactions
                   SET cat_lvl1_2 = :c1, cat_lvl2_2 = :c2,
                       status = CASE WHEN is_cashflow2=1 THEN 'OK' ELSE status END
                   WHERE txn_id = :tid AND is_cashflow2 = 1"""
            ),
            {"c1": cat_lvl1, "c2": cat_lvl2, "tid": tid},
        )
        updated += result.rowcount

    await db.commit()
    return {"ok": True, "updated": updated}
