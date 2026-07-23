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
from backend.etl.sync_payment_requests import sync_payment_requests as _sync_payment_requests
from backend.etl.sync_shipment_payments import sync_shipment_payments as _sync_shipment_payments
from backend.etl.sync_wb_payouts import sync_wb_payouts as _sync_wb_payouts
from backend.models import (
    Account,
    CounterpartyCategory,
    ImportLog,
    Override,
    Transaction,
)


def _upsert_counterparties_from_df(db: Session, df, project_id: int) -> tuple[dict[str, int], dict[int, int]]:
    """
    Batch UPSERT Counterparty rows by (project_id, inn) for all unique INNs in df.

    Returns:
      - inn_to_cp_id: {inn: counterparty_id} for all upserted rows
      - cp_id_to_loan_id: {counterparty_id: loan_id} for counterparties with EXACTLY ONE active loan
        (ambiguous matches are skipped — user resolves via POST /loans/{id}/payments/match)
    """
    if df.empty or "inn" not in df.columns:
        return {}, {}

    # Collect unique INNs with longest name
    unique: dict[str, str] = {}
    for row in df.to_dict("records"):
        inn = row.get("inn")
        if inn is None or str(inn).strip() == "" or str(inn) == "nan":
            continue
        inn = str(inn).strip()
        if len(inn) < 10 or len(inn) > 12 or not inn.isdigit():
            continue
        name_raw = row.get("counterparty")
        name = str(name_raw).strip() if name_raw and str(name_raw) != "nan" else inn
        if inn not in unique or len(name) > len(unique[inn]):
            unique[inn] = name

    if not unique:
        return {}, {}

    # INNs belonging to warehouses' counterparties → classify as FULFILLMENT on upsert.
    # Only promotes new rows or existing rows still at OTHER (manual categories preserved).
    # Includes BOTH the warehouse's primary counterparty (warehouses.counterparty_id) AND
    # additional ones bound via the warehouse_counterparty link table.
    fulfillment_inns: set[str] = set()
    wh_inn_rows = db.execute(
        text("""
            SELECT DISTINCT cp.inn
            FROM warehouses wh
            JOIN counterparty cp ON cp.id = wh.counterparty_id
            WHERE wh.project_id = :pid
              AND wh.is_deleted = false
              AND cp.is_deleted = false
              AND cp.inn IS NOT NULL
            UNION
            SELECT DISTINCT cp.inn
            FROM warehouse_counterparty link
            JOIN warehouses wh ON wh.id = link.warehouse_id
            JOIN counterparty cp ON cp.id = link.counterparty_id
            WHERE link.project_id = :pid
              AND wh.is_deleted = false
              AND cp.is_deleted = false
              AND cp.inn IS NOT NULL
        """),
        {"pid": project_id},
    ).fetchall()
    for (wh_inn,) in wh_inn_rows:
        if wh_inn:
            fulfillment_inns.add(str(wh_inn).strip())

    # Upsert via ON CONFLICT on partial unique index (project_id, inn) WHERE inn IS NOT NULL AND is_deleted=false
    inn_to_cp_id: dict[str, int] = {}
    for inn, name in unique.items():
        default_type = "FULFILLMENT" if inn in fulfillment_inns else "OTHER"
        result = db.execute(
            text("""
                INSERT INTO counterparty (
                    project_id, inn, name, primary_type, created_by_import,
                    is_deleted, created_at, updated_at
                )
                VALUES (:pid, :inn, :name, :ptype, TRUE, FALSE, :now, :now)
                ON CONFLICT (project_id, inn) WHERE inn IS NOT NULL AND is_deleted = false
                DO UPDATE SET
                    name = CASE
                        WHEN LENGTH(EXCLUDED.name) > LENGTH(counterparty.name)
                        THEN EXCLUDED.name
                        ELSE counterparty.name
                    END,
                    primary_type = CASE
                        WHEN counterparty.primary_type = 'OTHER' AND EXCLUDED.primary_type <> 'OTHER'
                        THEN EXCLUDED.primary_type
                        ELSE counterparty.primary_type
                    END,
                    updated_at = EXCLUDED.updated_at
                RETURNING id
            """),
            {"pid": project_id, "inn": inn, "name": name, "ptype": default_type, "now": utcnow()},
        ).scalar()
        if result is not None:
            inn_to_cp_id[inn] = result
    db.flush()

    # Build cp_id_to_loan_id map: only counterparties with exactly 1 active loan
    cp_id_to_loan_id: dict[int, int] = {}
    if inn_to_cp_id:
        cp_ids = list(inn_to_cp_id.values())
        loans_rows = db.execute(
            text("""
                SELECT counterparty_id, id
                FROM loan
                WHERE project_id = :pid
                  AND counterparty_id = ANY(:cpids)
                  AND status = 'ACTIVE'
                  AND is_deleted = false
            """),
            {"pid": project_id, "cpids": cp_ids},
        ).fetchall()
        # Count loans per counterparty; keep only the ones with exactly 1
        loan_count: dict[int, int] = {}
        first_loan: dict[int, int] = {}
        for cp_id, loan_id in loans_rows:
            loan_count[cp_id] = loan_count.get(cp_id, 0) + 1
            first_loan.setdefault(cp_id, loan_id)
        cp_id_to_loan_id = {cp_id: first_loan[cp_id] for cp_id, cnt in loan_count.items() if cnt == 1}

    return inn_to_cp_id, cp_id_to_loan_id


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
        "FAKTURA_WB_BANK": ("WB_BANK", "RUB", "ВБ Банк (Faktura)", False),
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

    # Own-accounts whose statement we actually import ("statement owners"). Gates
    # internal-transfer detection: an own-account we never sync (e.g. a WB transit
    # account the bank API does not expose) must not hide income arriving from it.
    # See apply_master_logic — is_internal.
    synced_accounts = {
        row[0]
        for row in db.execute(
            text(
                "SELECT DISTINCT account FROM transactions "
                "WHERE project_id = :pid AND account IS NOT NULL AND is_deleted = false"
            ),
            {"pid": project_id},
        )
    }

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

    return our_accounts, customs_accounts, cp_categories, overrides, synced_accounts


def _invalidate_reports_sync(project_id: int) -> None:
    """Invalidate cached project reports from a sync (executor-thread) context.

    Runs both from the manual file import and the Faktura.ru API auto-sync —
    both execute inside ``loop.run_in_executor`` (no running loop), so the
    ``except RuntimeError`` branch (``asyncio.run``) is the normal path.
    """
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


def persist_df(db: Session, df, project_id: int, account_no: str) -> tuple[int, int]:
    """Persist a normalized NORM_COLS dataframe → transactions + distribution syncs.

    Shared core of the import pipeline, reused by both the manual file import
    (``import_statement``) and the Faktura.ru API auto-sync
    (``services/faktura_service.py``):
    master logic → counterparty upsert → dedup by ``txn_id`` → bulk insert
    Transaction → customs/plan/wb-payout reconciliation.

    Does NOT commit and does NOT invalidate caches — the caller owns commit
    (cache invalidation must run AFTER commit). Returns ``(inserted, skipped)``.
    """
    # 1. Master logic (txn_id, cp_key, is_internal, event_type2, categories)
    our_accounts, customs_accounts, cp_categories, overrides, synced_accounts = _load_refs(db, project_id)
    df = apply_master_logic(
        df, our_accounts, customs_accounts, cp_categories, overrides, synced_accounts=synced_accounts
    )

    # 2. Auto-upsert Counterparty by INN + build (inn -> cp_id) / (cp_id -> active_loan_id) maps
    inn_to_cp_id, cp_id_to_loan_id = _upsert_counterparties_from_df(db, df, project_id)

    # 3. Bulk upsert (dedup by txn_id, scoped to project)
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
        # Within-batch dedup: одна выписка может содержать ДВЕ идентичные строки
        # (напр. две банковские комиссии 15₽ в один день с тем же назначением → один
        # txn_id). Без этого второй INSERT падает на transactions_txn_id_key и
        # откатывает ВЕСЬ импорт. Помечаем принятый txn_id как «существующий».
        existing_txn_ids.add(txn_id)

        row_inn = safe_str(row.get("inn"))
        cp_id = inn_to_cp_id.get(row_inn) if row_inn else None
        loan_payment_type = safe_str(row.get("loan_payment_type"))
        # Auto-match loan_id only if counterparty has exactly one active loan
        loan_id = cp_id_to_loan_id.get(cp_id) if (cp_id and loan_payment_type) else None
        batch.append(
            Transaction(
                project_id=project_id,
                date=row["date"],
                bank=safe_str(row["bank"]) or "UNKNOWN",
                account=safe_str(row["account"]) or account_no,
                currency=safe_str(row["currency"]) or "RUB",
                counterparty=safe_str(row.get("counterparty")),
                inn=row_inn,
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
                counterparty_id=cp_id,
                contract_number=safe_str(row.get("contract_number")),
                unk_number=safe_str(row.get("unk_number")),
                loan_id=loan_id,
                loan_payment_type=loan_payment_type,
            )
        )

    if batch:
        db.add_all(batch)
        inserted = len(batch)

    db.flush()

    # 4-6. Distribution syncs (idempotent: customs topup, plan payments, WB payouts)
    _sync_customs_topup(db, project_id)
    _sync_plan_payments(db, project_id)
    _sync_wb_payouts(db, project_id)
    # Авто-матч заявок на оплату с выпиской (DRAFT_CREATED → PAID по ИНН+сумма).
    _sync_payment_requests(db, project_id)
    # Прямая связка заборов с дебетом выписки (ИНН перевозчика + pickup_cost) —
    # ПОСЛЕ заявок: явная заявка забирает транзакцию первой.
    _sync_shipment_payments(db, project_id)
    # Реквизиты контрагентов из выписки (р/с) + тег CARRIER по отгрузкам.
    from backend.services.counterparty_enrich import enrich_counterparty_requisites

    enrich_counterparty_requisites(db, project_id)

    return inserted, skipped


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

        # 2-6. Persist: master logic → counterparty upsert → dedup insert → distribution syncs.
        # Shared core with the Faktura.ru API auto-sync (services/faktura_service.py).
        inserted, skipped = persist_df(db, df, project_id, account_no)
        log_ctx.info("etl.persist.done", inserted=inserted, skipped=skipped)

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

        # 7. Invalidate caches (after commit)
        _invalidate_reports_sync(project_id)

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
    _, _, cp_categories, overrides, _ = _load_refs(db, project_id)
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
