# ruff: noqa: RUF001, RUF002
"""
Backfill: link every counterparty_categories row to a Counterparty so the new
«Контрагенты» page surfaces it (the old flat «Категории контрагентов» editor is
removed). Categories already live in the shared `counterparty_categories` table —
this only fills `counterparty_id` and creates a Counterparty card for any
«orphaned» mapping (a cp_key with no matching counterparty).

Idempotent: re-running links/creates nothing already handled.

Matching (mirrors master_logic.make_cp_key): cp_key is an INN (10-12 digits) →
match Counterparty.inn; else match lower(name). No match → create a card
(inn=cp_key if numeric else None; name=cp_name||cp_key; primary_type=OTHER).

Usage:
    python -m scripts.backfill_cp_categories_link --project-id=1            # dry-run
    python -m scripts.backfill_cp_categories_link --project-id=1 --commit   # write
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.database import AsyncSessionLocal  # noqa: E402
from backend.models.counterparty import Counterparty  # noqa: E402
from backend.models.refs import CounterpartyCategory  # noqa: E402

logger = logging.getLogger("backfill_cp_categories_link")


def _is_inn(cp_key: str) -> bool:
    return cp_key.isdigit() and 10 <= len(cp_key) <= 12


async def run_backfill(db: AsyncSession, *, project_id: int, commit: bool) -> dict:
    stats = {"project_id": project_id, "commit": commit, "total": 0, "already": 0, "linked": 0, "created": 0}

    cat_res = await db.execute(
        select(CounterpartyCategory).where(
            CounterpartyCategory.project_id == project_id,
            CounterpartyCategory.is_deleted == False,  # noqa: E712
        )
    )
    cats = list(cat_res.scalars().all())
    stats["total"] = len(cats)

    for cat in cats:
        cp_key = (cat.cp_key or "").strip()
        if not cp_key:
            continue

        # Already linked to an active counterparty?
        if cat.counterparty_id:
            cp_chk = await db.execute(
                select(Counterparty.id).where(
                    Counterparty.id == cat.counterparty_id,
                    Counterparty.project_id == project_id,
                    Counterparty.is_deleted == False,  # noqa: E712
                )
            )
            if cp_chk.scalar_one_or_none() is not None:
                stats["already"] += 1
                continue

        # Find a matching counterparty by cp_key.
        if _is_inn(cp_key):
            cp_q = select(Counterparty).where(
                Counterparty.project_id == project_id,
                Counterparty.inn == cp_key,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        else:
            cp_q = select(Counterparty).where(
                Counterparty.project_id == project_id,
                func.lower(Counterparty.name) == cp_key,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        cp = (await db.execute(cp_q.limit(1))).scalar_one_or_none()

        if cp is not None:
            if not commit:
                stats["linked"] += 1
                continue
            cat.counterparty_id = cp.id
            stats["linked"] += 1
        else:
            name = (cat.cp_name or "").strip() or cp_key
            if not commit:
                stats["created"] += 1
                continue
            cp = Counterparty(
                project_id=project_id,
                inn=cp_key if _is_inn(cp_key) else None,
                name=name,
                primary_type="OTHER",
                created_by_import=True,
            )
            db.add(cp)
            await db.flush()
            cat.counterparty_id = cp.id
            stats["created"] += 1

    if commit:
        await db.commit()

    return stats


async def _main(project_id: int, commit: bool) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    async with AsyncSessionLocal() as session:
        stats = await run_backfill(session, project_id=project_id, commit=commit)
    logger.info("Backfill %s: %s", "COMMITTED" if commit else "DRY-RUN", stats)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Link counterparty_categories rows to Counterparty cards")
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--commit", action="store_true", default=False, help="write changes (default: dry-run)")
    args = parser.parse_args()
    return asyncio.run(_main(args.project_id, args.commit))


if __name__ == "__main__":
    raise SystemExit(main())
