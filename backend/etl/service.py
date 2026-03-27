"""
ETL service: orchestrates raw file -> normalize -> upsert -> master logic refresh.

Sync modules extracted:
- etl/sync_payments.py: customs topup + plan payment matching
- etl/sync_wb_payouts.py: WB payout bank reconciliation
"""

from decimal import Decimal

import structlog
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.utils.time import utcnow

logger = structlog.get_logger("dds.etl")

from backend.etl.master_logic import apply_master_logic
from backend.etl.parsers import parse_statement

# Re-export for backward compatibility (used by routers/planning.py)
from backend.etl.sync_payments import (
    sync_customs_topup as _sync_customs_topup,
    sync_plan_payments as _sync_plan_payments,
)
from backend.etl.sync_wb_payouts import sync_wb_payouts as _sync_wb_payouts
from backend.models import (
    Account,
    CounterpartyCategory,
    ImportLog,
    Override,
    Transaction,
)


def _ensure_account(db: Session, account_no: str, source_type: str, project_id: int):
    """Create account if it doesn't exist yet (scoped by project_id)."""
    existing = db.execute(
        select(Account).where(
            Account.account == account_no,
            Account.project_id == project_id,
            Account.is_deleted == False,
        )
    ).scalar_one_or_none()
    if existing:
        return

    bank_map = {
        "VTB_RUB_MAIN": ("VTB", "RUB", "VTB RUB Основной", False),
        "VTB_RUB_TRANSIT": ("VTB", "RUB", "VTB RUB Транзит", False),
        "VTB_CNY": ("VTB", "CNY", "VTB CNY", False),
        "WB_MAIN": ("WB", "RUB", "WB RUB Основной", False),
        "WB_PAYOUT": ("WB", "RUB", "WB RUB Транзит", False),
    }
    bank, currency, name, is_customs = bank_map.get(source_type, ("UNKNOWN", "RUB", account_no, False))
    acc = Account(
        project_id=project_id,
        account=account_no,
        bank=bank,
        currency=currency,
        account_name=name,
        is_our_account=True,
        is_customs_payee=is_customs,
    )
    db.add(acc)
    db.flush()


def _load_refs(db: Session, project_id: int) -> tuple:
    """Load reference data needed for master logic."""
    accounts = (
        db.execute(select(Account).where(Account.project_id == project_id, Account.is_deleted == False)).scalars().all()
    )
    our_accounts = {a.account for a in accounts if a.is_our_account}
    customs_accounts = {a.account for a in accounts if a.is_customs_payee}

    cp_cats = (
        db.execute(
            select(CounterpartyCategory).where(
                CounterpartyCategory.project_id == project_id, CounterpartyCategory.is_deleted == False
            )
        )
        .scalars()
        .all()
    )
    cp_categories = {c.cp_key: {"cat_lvl1": c.cat_lvl1, "cat_lvl2": c.cat_lvl2} for c in cp_cats}

    overrides_db = (
        db.execute(select(Override).where(Override.project_id == project_id, Override.is_deleted == False))
        .scalars()
        .all()
    )
    overrides = {o.txn_id: {"cat_lvl1": o.cat_lvl1, "cat_lvl2": o.cat_lvl2} for o in overrides_db}

    return our_accounts, customs_accounts, cp_categories, overrides


def import_statement(
    db: Session,
    filename: str,
    source_type: str,
    account_no: str,
    file_data: bytes,
    project_id: int,
) -> ImportLog:
    log = ImportLog(
        project_id=project_id,
        filename=filename,
        source_type=source_type,
        imported_at=utcnow(),
    )

    log_ctx = logger.bind(
        filename=filename,
        source_type=source_type,
        project_id=project_id,
    )
    log_ctx.info("etl.import.start")

    # Save original file to MinIO
    file_url = None
    try:
        import asyncio

        from backend.storage import upload_file

        async def _upload():
            return await upload_file(
                data=file_data,
                filename=filename,
                source_type=source_type,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        try:
            loop = asyncio.get_running_loop()
            future = asyncio.run_coroutine_threadsafe(_upload(), loop)
            file_url = future.result(timeout=30)
        except RuntimeError:
            file_url = asyncio.run(_upload())
    except Exception as e:
        logger.warning("MinIO upload skipped: %s", e)
    log.file_url = file_url

    db.add(log)
    db.flush()

    try:
        if source_type in ("VTB_MULTI", "WB_MULTI"):
            pass
        else:
            _ensure_account(db, account_no, source_type, project_id)

        # 1. Parse
        df, parse_skipped = parse_statement(source_type, file_data, account_no)
        log.rows_raw = len(df) + parse_skipped
        log_ctx.info("etl.parse.done", rows_raw=log.rows_raw, skipped=parse_skipped)

        if source_type in ("VTB_MULTI", "WB_MULTI") and not df.empty:
            for _, grp in df.groupby(["account", "currency"]):
                acc_no = str(grp.iloc[0]["account"])
                cur = str(grp.iloc[0]["currency"])
                if source_type == "VTB_MULTI":
                    multi_source = "VTB_CNY" if cur == "CNY" else "VTB_RUB_MAIN"
                else:
                    multi_source = "WB_MAIN"
                _ensure_account(db, acc_no, multi_source, project_id)

        if df.empty:
            log.status = "OK"
            log.rows_inserted = 0
            log.rows_skipped = 0
            db.commit()
            log_ctx.info("etl.import.done", status="OK", inserted=0, note="empty_file")
            return log

        # 2. Master logic
        our_accounts, customs_accounts, cp_categories, overrides = _load_refs(db, project_id)
        df = apply_master_logic(df, our_accounts, customs_accounts, cp_categories, overrides)
        log_ctx.info("etl.master_logic.done", rows=len(df))

        # 3. Bulk upsert
        inserted = 0
        skipped = 0

        txn_ids = df["txn_id"].tolist()
        if txn_ids:
            existing_txn_ids = set(
                row[0]
                for row in db.execute(
                    text("SELECT txn_id FROM transactions WHERE txn_id = ANY(:ids) AND project_id = :pid"),
                    {"ids": txn_ids, "pid": project_id},
                )
            )
        else:
            existing_txn_ids = set()

        def safe_dec(val):
            try:
                return Decimal(str(val)) if val is not None and str(val) != "nan" else Decimal("0")
            except Exception as e:
                logger.warning("Invalid decimal value %r converted to 0: %s", val, e)
                return Decimal("0")

        def safe_str(val):
            if val is None:
                return None
            s = str(val).strip()
            return s if s and s != "nan" else None

        batch = []
        for row in df.to_dict("records"):
            txn_id = row["txn_id"]
            if txn_id in existing_txn_ids:
                skipped += 1
                continue

            batch.append(
                Transaction(
                    project_id=project_id,
                    date=row["date"],
                    bank=safe_str(row["bank"]) or "UNKNOWN",
                    account=safe_str(row["account"]) or account_no,
                    currency=safe_str(row["currency"]) or "RUB",
                    counterparty=safe_str(row.get("counterparty")),
                    inn=safe_str(row.get("inn")),
                    counterparty_account=safe_str(row.get("counterparty_account")),
                    purpose=safe_str(row.get("purpose")),
                    income=safe_dec(row.get("income", 0)),
                    expense=safe_dec(row.get("expense", 0)),
                    txn_id=txn_id,
                    cp_key=safe_str(row.get("cp_key")),
                    net=safe_dec(row.get("net", 0)),
                    is_internal=bool(row.get("is_internal", 0)),
                    is_fx=bool(row.get("is_fx", 0)),
                    event_type2=safe_str(row.get("event_type2")) or "OPER",
                    is_cashflow2=int(row.get("is_cashflow2", 1)),
                    cat_lvl1_2=safe_str(row.get("cat_lvl1_2")),
                    cat_lvl2_2=safe_str(row.get("cat_lvl2_2")),
                    status=safe_str(row.get("status")) or "UNASSIGNED",
                    account_text=safe_str(row.get("account_text")),
                    purpose_tag=safe_str(row.get("purpose_tag")),
                    invoice_id=safe_str(row.get("invoice_id")),
                    annex_id=safe_str(row.get("annex_id")),
                )
            )

        if batch:
            db.add_all(batch)
            inserted = len(batch)
            log_ctx.info("etl.bulk_insert.done", inserted=inserted, skipped=skipped)

        db.flush()

        # 4. Sync customs_topup
        _sync_customs_topup(db, project_id)

        # 5. Sync plan payments
        _sync_plan_payments(db, project_id)

        # 6. Reconcile WB payouts
        _sync_wb_payouts(db, project_id)

        log.rows_inserted = inserted
        log.rows_skipped = skipped + parse_skipped
        log.status = "OK"
        if parse_skipped:
            log.error_msg = f"{parse_skipped} rows skipped during parsing (bad dates or format)"
        db.commit()

        log_ctx.info(
            "etl.import.done",
            status="OK",
            inserted=inserted,
            skipped=skipped + parse_skipped,
        )

        # 7. Invalidate caches
        try:
            import asyncio

            from backend.cache import invalidate_project_reports

            async def _invalidate_all():
                await invalidate_project_reports(project_id)

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_invalidate_all())
            except RuntimeError:
                asyncio.run(_invalidate_all())
        except Exception as e:
            logger.warning("Cache invalidation skipped: %s", e)

    except Exception as e:
        db.rollback()
        log.status = "ERROR"
        log.error_msg = str(e)[:1000]
        db.add(log)
        db.commit()
        log_ctx.error("etl.import.failed", error=str(e)[:500])
        raise

    return log


def reapply_categories(db: Session, project_id: int):
    """Reapply categories to all cashflow transactions."""
    _, _, cp_categories, overrides = _load_refs(db, project_id)
    txns = (
        db.execute(
            select(Transaction).where(
                Transaction.project_id == project_id,
                Transaction.is_cashflow2 == 1,
                Transaction.is_deleted == False,
            )
        )
        .scalars()
        .all()
    )
    for txn in txns:
        cp_key = txn.cp_key
        txn_id = txn.txn_id
        cat1, cat2 = None, None
        if txn_id in overrides:
            ov = overrides[txn_id]
            cat1, cat2 = ov.get("cat_lvl1"), ov.get("cat_lvl2")
        elif cp_key and cp_key in cp_categories:
            cp = cp_categories[cp_key]
            cat1, cat2 = cp.get("cat_lvl1"), cp.get("cat_lvl2")
        txn.cat_lvl1_2 = cat1
        txn.cat_lvl2_2 = cat2
        txn.status = "UNASSIGNED" if not cat1 else "OK"
    db.commit()
