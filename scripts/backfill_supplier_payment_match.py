"""
Backfill: apply ``sync_supplier_payments`` to already-imported transactions.

After registering counterparty identifiers (contracts / accounts), historical
statement rows are not re-attributed until the next statement import — this runs
the matcher once over existing data. Idempotent (re-running changes nothing new).

Usage:
    python -m scripts.backfill_supplier_payment_match --project-id=4
    python -m scripts.backfill_supplier_payment_match --project-id=4 --commit
    python -m scripts.backfill_supplier_payment_match --all-projects --commit
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import select

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.database import SyncSessionLocal  # noqa: E402
from backend.etl.sync_supplier_payments import sync_supplier_payments  # noqa: E402
from backend.models import Project  # noqa: E402

logger = logging.getLogger("backfill_supplier_payment_match")


def _run(project_id: int | None, commit: bool) -> None:
    with SyncSessionLocal() as db:
        if project_id is not None:
            pids = [project_id]
        else:
            pids = [p for (p,) in db.execute(select(Project.id)).all()]
        for pid in pids:
            sync_supplier_payments(db, pid)
        if commit:
            db.commit()
            logger.info("COMMITTED supplier-payment match for projects=%s", pids)
        else:
            db.rollback()
            logger.info("DRY-RUN (rolled back) projects=%s — pass --commit to apply", pids)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Apply sync_supplier_payments to existing transactions")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--project-id", type=int)
    grp.add_argument("--all-projects", action="store_true")
    ap.add_argument("--commit", action="store_true", help="apply changes (default: dry-run)")
    args = ap.parse_args()
    _run(None if args.all_projects else args.project_id, args.commit)


if __name__ == "__main__":
    main()
