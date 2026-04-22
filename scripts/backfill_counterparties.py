# ruff: noqa: RUF001
"""
Backfill script: create Counterparty rows from existing Transaction INNs.

Idempotent:
- Running twice creates 0 new Counterparties (uses upsert_by_inn semantics).
- Sets Transaction.counterparty_id for rows where it is NULL.
- Known INNs get a mapped primary_type (see KNOWN_INN_TYPES below).

Usage:
    python -m scripts.backfill_counterparties --project-id=1
    python -m scripts.backfill_counterparties --project-id=1 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

# Ensure repo root on sys.path when run as a plain script
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.database import AsyncSessionLocal  # noqa: E402
from backend.models.counterparty import Counterparty  # noqa: E402
from backend.models.transactions import Transaction  # noqa: E402

logger = logging.getLogger("backfill_counterparties")


# ─── Known INN → primary_type (from analytics of statements) ────────────────

KNOWN_INN_TYPES: dict[str, tuple[str, str]] = {
    # inn -> (primary_type, name_hint)
    "7704217370": ("MARKETPLACE", "ООО Интернет Решения (OZON)"),
    "9714053621": ("MARKETPLACE", "РВБ ООО (Wildberries)"),
    "9707021650": ("BANK", "МКК ВБ Финанс"),
    "7702070139": ("BANK", "Банк ВТБ"),
    "7703381419": ("BANK", "МКК Симплфинанс"),
    "7727406020": ("GOVERNMENT", "ФНС (Казначейство)"),
    "7730176610": ("GOVERNMENT", "ФТС (Таможня)"),
    "7703381225": ("GOVERNMENT", "Росприроднадзор"),
    "7730176088": ("GOVERNMENT", "Росимущество (Роспатент)"),
    "3731001044": ("GOVERNMENT", "ОСФР"),
    "370212932304": ("AFFILIATED", "ИП Вяткин"),
    "370503709798": ("LANDLORD", "ИП Гаврош"),
    "371100232105": ("LANDLORD", "ИП Курылева"),
    "7731197997": ("CUSTOMS_BROKER", "ООО КВ ТЕРМИНАЛ"),
    "370229295741": ("CUSTOMS_BROKER", "Грекова А.М."),
    "165719153207": ("LEGAL", "ИП Измайлов (патенты)"),
    "4345497285": ("LEGAL", "ООО Патентное Бюро Железно"),
    "7816397410": ("IT_SERVICE", "ООО АТИ.СУ"),
    "182709205140": ("IT_SERVICE", "ИП Глухова (БульДок)"),
    "9729366786": ("IT_SERVICE", "ООО ЭД СЕТЬ (АСВД ЦИТТУ)"),
    "643921964912": ("DESIGNER", "Щеткина А.М."),
    "450163723834": ("DESIGNER", "Горбачева К.К."),
    "3702703225": ("FULFILLMENT", "ООО ТРИКОТАЖ НАТАЛИ"),
    "550508916039": ("FULFILLMENT", "ИП Перфильев А.В."),
    "370226099442": ("FULFILLMENT", "ИП Кожонов А.Н."),
    "772975711121": ("FULFILLMENT", "ИП Кузнецов А.К."),
    "503818940420": ("FULFILLMENT", "ИП Крикова М.С."),
    "370254749999": ("FULFILLMENT", "ИП Владимирова Я.Е."),
    "7839353264": ("FULFILLMENT", "ООО ЭФЭМДЖИ ШИППИНГ"),
}


# ─── Core logic ──────────────────────────────────────────────────────────────


async def run_backfill(
    db: AsyncSession,
    *,
    project_id: int,
    dry_run: bool = False,
) -> dict:
    """
    Idempotent backfill of Counterparty rows from Transaction.inn + link txns.

    Returns stats:
        {
            "dry_run": bool,
            "project_id": int,
            "unique_inns": int,
            "created": int,
            "existing": int,
            "linked_transactions": int,
        }
    """
    stats = {
        "dry_run": dry_run,
        "project_id": project_id,
        "unique_inns": 0,
        "created": 0,
        "existing": 0,
        "linked_transactions": 0,
    }

    # 1. Find unique INNs across project transactions
    inn_res = await db.execute(
        select(Transaction.inn)
        .where(
            Transaction.project_id == project_id,
            Transaction.is_deleted == False,  # noqa: E712
            Transaction.inn.isnot(None),
        )
        .distinct()
    )
    inns = {row[0] for row in inn_res.all() if row[0] and row[0].strip()}
    stats["unique_inns"] = len(inns)

    # 2. Fetch existing counterparties to decide create-vs-skip
    existing_res = await db.execute(
        select(Counterparty.id, Counterparty.inn).where(
            Counterparty.project_id == project_id,
            Counterparty.inn.in_(inns) if inns else False,
            Counterparty.is_deleted == False,  # noqa: E712
        )
    )
    existing_by_inn: dict[str, int] = {row.inn: row.id for row in existing_res.all()}
    stats["existing"] = len(existing_by_inn)

    # 3. Create missing
    for inn in inns:
        if inn in existing_by_inn:
            continue
        primary_type, hint_name = KNOWN_INN_TYPES.get(inn, ("OTHER", None))

        # Derive a name from a recent transaction (first non-null counterparty)
        name_res = await db.execute(
            select(Transaction.counterparty)
            .where(
                Transaction.project_id == project_id,
                Transaction.inn == inn,
                Transaction.counterparty.isnot(None),
            )
            .limit(1)
        )
        row = name_res.first()
        name = hint_name or (row.counterparty if row else None) or f"ИНН {inn}"

        if dry_run:
            stats["created"] += 1
            continue

        cp = Counterparty(
            project_id=project_id,
            inn=inn,
            name=name,
            primary_type=primary_type,
            created_by_import=True,
        )
        db.add(cp)
        await db.flush()
        existing_by_inn[inn] = cp.id
        stats["created"] += 1

    if not dry_run:
        await db.commit()

    # 4. Link Transaction.counterparty_id for rows where it is NULL
    linked = 0
    for inn, cp_id in existing_by_inn.items():
        upd_stmt = (
            update(Transaction)
            .where(
                Transaction.project_id == project_id,
                Transaction.inn == inn,
                Transaction.counterparty_id.is_(None),
                Transaction.is_deleted == False,  # noqa: E712
            )
            .values(counterparty_id=cp_id)
        )
        if dry_run:
            # Count candidates only
            count_res = await db.execute(
                select(Transaction.id).where(
                    Transaction.project_id == project_id,
                    Transaction.inn == inn,
                    Transaction.counterparty_id.is_(None),
                    Transaction.is_deleted == False,  # noqa: E712
                )
            )
            linked += len(count_res.all())
        else:
            res = await db.execute(upd_stmt)
            linked += res.rowcount or 0

    if not dry_run:
        await db.commit()
    stats["linked_transactions"] = linked

    return stats


# ─── CLI entry ───────────────────────────────────────────────────────────────


async def _main(project_id: int, dry_run: bool) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    async with AsyncSessionLocal() as session:
        stats = await run_backfill(session, project_id=project_id, dry_run=dry_run)
    logger.info("Backfill complete: %s", stats)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Counterparty rows from Transactions")
    parser.add_argument("--project-id", type=int, required=True)
    parser.add_argument("--dry-run", action="store_true", default=False)
    args = parser.parse_args()
    return asyncio.run(_main(args.project_id, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
