# ruff: noqa: RUF001
"""
Backfill: re-apply ``enrich_purpose()`` to already-imported transactions.

Fixes the historical gap where ``apply_master_logic`` never wired
``enrich_purpose`` — so ``contract_number`` / ``unk_number`` /
``loan_payment_type`` stayed permanently NULL on every imported row, and
deposits whose counter-account is not our own kept counting as cashflow.

Idempotent: a second run after ``--commit`` reports 0 changes. Only ever
*fills/refreshes* the enrichment columns from the purpose text; never clears a
value the regex no longer produces (manual edits and other matchers are safe).

Usage:
    python -m scripts.backfill_purpose_enrichment --project-id=4 --dry-run
    python -m scripts.backfill_purpose_enrichment --project-id=4 --commit
    python -m scripts.backfill_purpose_enrichment --all-projects --commit
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import select

# Ensure repo root on sys.path when run as a plain script
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.database import AsyncSessionLocal  # noqa: E402
from backend.etl.master_logic import enrich_purpose  # noqa: E402
from backend.models.transactions import Transaction  # noqa: E402

logger = logging.getLogger("backfill_purpose_enrichment")

_BATCH = 2000


async def _run(project_id: int | None, commit: bool) -> None:
    async with AsyncSessionLocal() as db:
        # One-shot maintenance script: stream all (soft-delete-filtered) rows,
        # optionally scoped to a single project, in id-ordered batches.
        changed = {"contract_number": 0, "unk_number": 0, "loan_payment_type": 0, "deposit": 0}
        touched = 0
        scanned = 0
        last_id = 0

        while True:
            stmt = (
                select(Transaction)
                .where(Transaction.is_deleted.is_(False), Transaction.id > last_id)
                .order_by(Transaction.id)
                .limit(_BATCH)
            )
            if project_id is not None:
                stmt = stmt.where(Transaction.project_id == project_id)
            rows = (await db.execute(stmt)).scalars().all()
            if not rows:
                break
            scanned += len(rows)
            last_id = rows[-1].id

            for t in rows:
                enr = enrich_purpose(t.purpose)
                row_changed = False
                if enr["contract_number"] and enr["contract_number"] != t.contract_number:
                    t.contract_number = enr["contract_number"]
                    changed["contract_number"] += 1
                    row_changed = True
                if enr["unk_number"] and enr["unk_number"] != t.unk_number:
                    t.unk_number = enr["unk_number"]
                    changed["unk_number"] += 1
                    row_changed = True
                if enr["loan_payment_type"] and enr["loan_payment_type"] != t.loan_payment_type:
                    t.loan_payment_type = enr["loan_payment_type"]
                    changed["loan_payment_type"] += 1
                    row_changed = True
                # Deposit override: only re-classify rows still OPER — a deposit
                # between our own accounts is already INTERNAL_TRANSFER and must
                # keep that classification.
                ev = enr["event_type2_override"]
                if ev and str(t.event_type2) == "OPER":
                    t.event_type2 = ev
                    t.is_cashflow2 = int(enr["is_cashflow2_override"] or 0)
                    changed["deposit"] += 1
                    row_changed = True
                if row_changed:
                    touched += 1

            await db.flush()

        logger.info("scanned=%d would-change=%d details=%s", scanned, touched, changed)
        if commit:
            await db.commit()
            logger.info("COMMITTED %d transactions", touched)
        else:
            await db.rollback()
            logger.info("DRY-RUN (rolled back) — pass --commit to apply")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Re-apply enrich_purpose to existing transactions")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--project-id", type=int, help="scope to one project")
    grp.add_argument("--all-projects", action="store_true", help="all projects")
    ap.add_argument("--commit", action="store_true", help="apply changes (default: dry-run)")
    args = ap.parse_args()
    project_id = None if args.all_projects else args.project_id
    asyncio.run(_run(project_id, args.commit))


if __name__ == "__main__":
    main()
