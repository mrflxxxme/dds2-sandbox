"""ETL sync: WB payout reconciliation with bank transactions.

Extracted from etl/service.py for maintainability.
"""

from datetime import timedelta
from decimal import Decimal

import structlog
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, and_

from backend.utils.time import utcnow
from backend.models import Transaction, WbPayout

logger = structlog.get_logger("dds.etl")


def sync_wb_payouts(db: Session, project_id: int):
    """Reconcile WB payouts with bank transactions (scoped by project_id)."""
    unmatched = db.execute(
        select(WbPayout).where(
            WbPayout.project_id == project_id,
            WbPayout.is_deleted == False,
            WbPayout.status.in_(["TRANSIT", "PROCESSING", "PENDING"]),
        )
    ).scalars().all()
    if not unmatched:
        return

    already_matched = {
        r[0] for r in db.execute(
            select(WbPayout.matched_txn_id).where(
                WbPayout.project_id == project_id,
                WbPayout.matched_txn_id.isnot(None),
            )
        )
    }

    min_date = min(p.created_at for p in unmatched) - timedelta(days=2)
    max_date = max(p.created_at for p in unmatched) + timedelta(days=5)

    candidates = db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.is_deleted == False,
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
            payout.matched_at = utcnow()
            payout.status = "RECEIVED"
            used_txn_ids.add(best_match.txn_id)

    db.flush()
