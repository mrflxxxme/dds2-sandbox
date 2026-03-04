"""
ETL service: orchestrates raw file -> normalize -> upsert -> master logic refresh.
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

import pandas as pd
import structlog
from sqlalchemy.orm import Session
from sqlalchemy import select, text

logger = structlog.get_logger("dds.etl")

from backend.models import (
    Transaction, Account, CounterpartyCategory, Override,
    ImportLog, CustomsTopup, PlannedPayment, CostOrder, CustomsAlloc,
    PaymentFactLink, CustomsDT, WbPayout,
)
from backend.etl.parsers import parse_statement
from backend.etl.master_logic import apply_master_logic


def _ensure_account(db: Session, account_no: str, source_type: str, project_id: int):
    """Create account if it doesn't exist yet (scoped by project_id)."""
    existing = db.execute(
        select(Account).where(
            Account.account == account_no,
            Account.project_id == project_id,
        )
    ).scalar_one_or_none()
    if existing:
        return

    # Guess bank/currency from source_type
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
    accounts = db.execute(select(Account).where(Account.project_id == project_id)).scalars().all()
    our_accounts = {a.account for a in accounts if a.is_our_account}
    customs_accounts = {a.account for a in accounts if a.is_customs_payee}

    cp_cats = db.execute(select(CounterpartyCategory).where(CounterpartyCategory.project_id == project_id)).scalars().all()
    cp_categories = {
        c.cp_key: {"cat_lvl1": c.cat_lvl1, "cat_lvl2": c.cat_lvl2}
        for c in cp_cats
    }

    overrides_db = db.execute(select(Override).where(Override.project_id == project_id)).scalars().all()
    overrides = {
        o.txn_id: {"cat_lvl1": o.cat_lvl1, "cat_lvl2": o.cat_lvl2}
        for o in overrides_db
    }

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
        imported_at=datetime.utcnow(),
    )

    log_ctx = logger.bind(
        filename=filename, source_type=source_type, project_id=project_id,
    )
    log_ctx.info("etl.import.start")

    # Save original file to MinIO (for audit trail / re-import)
    from backend.storage import upload_file
    file_url = upload_file(
        data=file_data,
        filename=filename,
        source_type=source_type,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    log.file_url = file_url

    db.add(log)
    db.flush()

    try:
        # Ensure account exists
        _ensure_account(db, account_no, source_type, project_id)

        # 1. Parse
        df, parse_skipped = parse_statement(source_type, file_data, account_no)
        log.rows_raw = len(df) + parse_skipped
        log_ctx.info("etl.parse.done", rows_raw=log.rows_raw, skipped=parse_skipped)

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

        # 3. Upsert
        inserted = 0
        skipped = 0

        txn_ids = df["txn_id"].tolist()
        if txn_ids:
            existing_txn_ids = set(
                row[0] for row in db.execute(
                    text("SELECT txn_id FROM transactions WHERE txn_id = ANY(:ids) AND project_id = :pid"),
                    {"ids": txn_ids, "pid": project_id},
                )
            )
        else:
            existing_txn_ids = set()

        for _, row in df.iterrows():
            txn_id = row["txn_id"]
            if txn_id in existing_txn_ids:
                skipped += 1
                continue

            def safe_dec(val):
                try:
                    return Decimal(str(val)) if val is not None and str(val) != 'nan' else Decimal("0")
                except Exception as e:
                    logger.warning("Invalid decimal value %r converted to 0: %s", val, e)
                    return Decimal("0")

            def safe_str(val):
                if val is None:
                    return None
                s = str(val).strip()
                return s if s and s != 'nan' else None

            txn = Transaction(
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
            db.add(txn)
            inserted += 1

        db.flush()

        # 4. Sync customs_topup
        _sync_customs_topup(db, project_id)

        # 5. Sync plan payments (auto-match fact from bank statements)
        _sync_plan_payments(db, project_id)

        # 6. Reconcile WB payouts with newly imported bank transactions
        _sync_wb_payouts(db, project_id)

        log.rows_inserted = inserted
        log.rows_skipped = skipped + parse_skipped
        log.status = "OK"
        if parse_skipped:
            log.error_msg = f"{parse_skipped} rows skipped during parsing (bad dates or format)"
        db.commit()

        log_ctx.info(
            "etl.import.done",
            status="OK", inserted=inserted, skipped=skipped + parse_skipped,
        )

        # 7. Invalidate report caches (async call from sync context)
        try:
            import asyncio
            from backend.cache import invalidate_cache
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(invalidate_cache("reports"))
            except RuntimeError:
                asyncio.run(invalidate_cache("reports"))
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


def _sync_customs_topup(db: Session, project_id: int):
    """Sync customs topup records from CUSTOMS_PAYMENT transactions (scoped by project_id)."""
    customs_txns = db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.event_type2 == "CUSTOMS_PAYMENT",
            Transaction.expense > 0,
        )
    ).scalars().all()

    existing_ids = set(
        row[0] for row in db.execute(
            text("SELECT topup_txn_id FROM customs_topup WHERE project_id = :pid"),
            {"pid": project_id},
        )
    )

    for txn in customs_txns:
        if txn.txn_id not in existing_ids:
            from backend.models import CustomsTopup
            topup = CustomsTopup(
                project_id=project_id,
                topup_txn_id=txn.txn_id,
                date=txn.date.date(),
                amount_rub=txn.expense,
                purpose=txn.purpose,
                account=txn.account,
                counterparty_account=txn.counterparty_account,
            )
            db.add(topup)


def reapply_categories(db: Session, project_id: int):
    """Reapply categories to all cashflow transactions (scoped by project_id)."""
    _, _, cp_categories, overrides = _load_refs(db, project_id)
    txns = db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.is_cashflow2 == 1,
        )
    ).scalars().all()
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


def _sync_plan_payments(db: Session, project_id: int):
    """
    Auto-match planned payments with fact from bank statements and customs_alloc.
    Scoped by project_id to ensure multi-tenant isolation.

    Matching rules:
    - ЗАКАЗ:    Transaction.annex_id == str(order_no) AND purpose_tag == 'Заказ'  → sum(expense)
    - ДОСТАВКА: Transaction.invoice_id == CostOrder.invoice_no AND purpose_tag == 'Логистика' → sum(expense)
    - ТАМОЖНЯ:  CustomsAlloc.order_no == order_no → sum(alloc_amount)

    Updates paid_rub and is_paid (True when paid_rub >= amount_rub).
    """
    payments = db.execute(
        select(PlannedPayment).where(PlannedPayment.project_id == project_id)
    ).scalars().all()
    if not payments:
        return

    # Collect unique order_nos
    order_nos = {p.order_no for p in payments if p.order_no is not None}
    if not order_nos:
        return

    # Build invoice_no map: order_no (int) → invoice_no (str)
    cost_orders = db.execute(
        select(CostOrder).where(CostOrder.project_id == project_id)
    ).scalars().all()
    invoice_map = {}  # int(order_no) → invoice_no
    for co in cost_orders:
        try:
            invoice_map[int(co.order_no)] = co.invoice_no
        except (ValueError, TypeError):
            pass

    # Preload all relevant transactions
    # ЗАКАЗ fact: annex_id matches order_no, purpose_tag = Заказ
    order_fact = {}  # order_no → sum(expense)
    txns_order = db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.purpose_tag == "Заказ",
            Transaction.expense > 0,
        )
    ).scalars().all()
    for t in txns_order:
        if t.annex_id:
            try:
                ono = int(t.annex_id)
                order_fact[ono] = order_fact.get(ono, Decimal("0")) + (t.expense or Decimal("0"))
            except (ValueError, TypeError):
                pass

    # ДОСТАВКА fact: invoice_id matches CostOrder.invoice_no, purpose_tag = Логистика
    delivery_fact = {}  # order_no → sum(expense)
    txns_delivery = db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.purpose_tag == "Логистика",
            Transaction.expense > 0,
        )
    ).scalars().all()
    # Reverse map: invoice_no → order_no
    inv_to_order = {v: k for k, v in invoice_map.items() if v}
    for t in txns_delivery:
        if t.invoice_id and t.invoice_id in inv_to_order:
            ono = inv_to_order[t.invoice_id]
            delivery_fact[ono] = delivery_fact.get(ono, Decimal("0")) + (t.expense or Decimal("0"))

    # ТАМОЖНЯ fact: from customs_alloc + customs_dt
    customs_fact = {}  # order_no → sum(amount)
    allocs = db.execute(
        select(CustomsAlloc).where(CustomsAlloc.project_id == project_id)
    ).scalars().all()
    for a in allocs:
        if a.order_no:
            customs_fact[a.order_no] = customs_fact.get(a.order_no, Decimal("0")) + (a.alloc_amount or Decimal("0"))

    # Also from parsed FTS DT declarations
    dts = db.execute(
        select(CustomsDT).where(
            CustomsDT.project_id == project_id,
            CustomsDT.order_no.isnot(None),
        )
    ).scalars().all()
    for d in dts:
        customs_fact[d.order_no] = customs_fact.get(d.order_no, Decimal("0")) + (d.amount_rub or Decimal("0"))

    # Manual fact links
    manual_links = db.execute(
        select(PaymentFactLink).join(
            PlannedPayment, PaymentFactLink.payment_id == PlannedPayment.id
        ).where(PlannedPayment.project_id == project_id)
    ).scalars().all()
    manual_map = {}  # payment_id → sum(amount_rub)
    for ml in manual_links:
        manual_map[ml.payment_id] = manual_map.get(ml.payment_id, Decimal("0")) + (ml.amount_rub or Decimal("0"))

    # Update each planned payment
    for p in payments:
        if p.order_no is None:
            continue

        paid = Decimal("0")
        direction = (p.direction or "").upper()

        if direction == "ЗАКАЗ":
            paid = order_fact.get(p.order_no, Decimal("0"))
        elif direction == "ДОСТАВКА":
            paid = delivery_fact.get(p.order_no, Decimal("0"))
        elif direction == "ТАМОЖНЯ":
            paid = customs_fact.get(p.order_no, Decimal("0"))

        # Add manual links
        paid += manual_map.get(p.id, Decimal("0"))

        p.paid_rub = paid
        p.is_paid = paid >= (p.amount_rub or Decimal("0")) if (p.amount_rub and p.amount_rub > 0) else False

    db.flush()


def _sync_wb_payouts(db: Session, project_id: int):
    """Reconcile WB payouts with bank transactions (scoped by project_id)."""
    from datetime import datetime as dt_mod, timedelta
    from sqlalchemy import or_, and_

    unmatched = db.execute(
        select(WbPayout).where(
            WbPayout.project_id == project_id,
            WbPayout.status.in_(["TRANSIT", "PROCESSING", "PENDING"]),
        )
    ).scalars().all()
    if not unmatched:
        return

    already_matched = {
        r[0] for r in db.execute(
            select(WbPayout.matched_txn_id).where(WbPayout.matched_txn_id.isnot(None))
        )
    }

    min_date = min(p.created_at for p in unmatched) - timedelta(days=2)
    max_date = max(p.created_at for p in unmatched) + timedelta(days=5)

    candidates = db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.income > 0,
            Transaction.date >= min_date,
            Transaction.date <= max_date,
            or_(
                and_(
                    Transaction.cat_lvl1_2 == "Маркетплейсы",
                    Transaction.cat_lvl2_2 == "Wildberries",
                ),
                Transaction.counterparty.ilike("%вайлдберриз%"),
                Transaction.counterparty.ilike("%wildberries%"),
            )
        )
    ).scalars().all()
    candidates = [t for t in candidates if t.txn_id not in already_matched]

    if not candidates:
        return

    used_txn_ids: set = set()
    for payout in sorted(unmatched, key=lambda p: p.amount_rub, reverse=True):
        best_match = None
        best_diff = float("inf")

        for txn in candidates:
            if txn.txn_id in used_txn_ids:
                continue

            txn_date = txn.date.date() if hasattr(txn.date, 'date') else txn.date
            payout_date = payout.created_at.date() if hasattr(payout.created_at, 'date') else payout.created_at
            delta = (txn_date - payout_date).days
            if delta < -2 or delta > 5:
                continue

            diff = abs(float(txn.income) - float(payout.amount_rub))
            tolerance = float(payout.amount_rub) * 0.01
            if diff <= tolerance and diff < best_diff:
                best_diff = diff
                best_match = txn

        if best_match:
            payout.matched_txn_id = best_match.txn_id
            payout.matched_at = dt_mod.utcnow()
            payout.status = "RECEIVED"
            used_txn_ids.add(best_match.txn_id)

    db.flush()
