#!/usr/bin/env python3
"""
Перенос займов и их заёмщиков между проектами.

Зачем: реестр ИП Вяткина был залит в проект ООО «Плюс Вайб» (default). Займы
принадлежат другому юрлицу, поэтому переезжают в свой проект целиком — вместе
с контрагентами-заёмщиками, платежами и цепочками продлений.

Перенос физический (UPDATE project_id), а не перезаливка: сохраняются id,
`parent_loan_id`-цепочки, история платежей и правки, сделанные руками.

Идемпотентен: повторный запуск переносит 0 строк.

    python scripts/migrate_loans_to_project.py --from 4 --to 15 [--commit]

Без `--commit` — сухой прогон: показывает, что будет сделано, и откатывает.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from backend.cache import invalidate_project_reports  # noqa: E402
from backend.database import AsyncSessionLocal  # noqa: E402
from backend.models.auth import Project  # noqa: E402
from backend.models.counterparty import Counterparty  # noqa: E402
from backend.models.loan import Loan, LoanPayment  # noqa: E402
from backend.models.transactions import Transaction  # noqa: E402


async def _project_name(db: AsyncSession, project_id: int) -> str:
    row = (await db.execute(select(Project.name, Project.slug).where(Project.id == project_id))).first()
    if row is None:
        raise SystemExit(f"Проект {project_id} не найден")
    return f"{row.name} ({row.slug})"


async def migrate(src: int, dst: int, *, commit: bool) -> None:
    async with AsyncSessionLocal() as db:
        print(f"Откуда: {await _project_name(db, src)}")
        print(f"Куда:   {await _project_name(db, dst)}\n")

        loans = list(
            (
                await db.execute(
                    select(Loan).where(Loan.project_id == src, Loan.is_deleted == False)  # noqa: E712
                )
            )
            .scalars()
            .all()
        )
        if not loans:
            print("Займов для переноса нет — уже перенесены или не было.")
            return

        cp_ids = {loan.counterparty_id for loan in loans}
        cps = list(
            (await db.execute(select(Counterparty).where(Counterparty.id.in_(cp_ids)))).scalars().all()
        )

        # Контрагент едет только если он «чистый»: заведён импортом реестра и не
        # используется транзакциями исходного проекта. Иначе переезд оторвал бы
        # его от платежей — такого оставляем на месте и не трогаем.
        linked = {
            row[0]
            for row in (
                await db.execute(
                    select(Transaction.counterparty_id)
                    .where(Transaction.counterparty_id.in_(cp_ids))
                    .distinct()
                )
            ).all()
            if row[0] is not None
        }
        movable = [cp for cp in cps if cp.id not in linked]
        stuck = [cp for cp in cps if cp.id in linked]

        payments = (
            await db.execute(
                select(func.count())
                .select_from(LoanPayment)
                .where(LoanPayment.loan_id.in_([loan.id for loan in loans]))
            )
        ).scalar_one()
        chains = sum(1 for loan in loans if loan.parent_loan_id is not None)

        print(f"Займов:      {len(loans)}")
        print(f"Платежей:    {payments} (едут вместе с займами)")
        print(f"Цепочек:     {chains} связей продления сохранятся")
        print(f"Контрагентов:{len(movable)} переедет, {len(stuck)} останется (есть транзакции)")
        for cp in stuck:
            print(f"   ! остаётся: {cp.name}")

        # Коллизии по имени в целевом проекте: там мог быть тёзка
        dst_names = {
            name.strip().lower()
            for (name,) in (
                await db.execute(
                    select(Counterparty.name).where(
                        Counterparty.project_id == dst,
                        Counterparty.is_deleted == False,  # noqa: E712
                    )
                )
            ).all()
        }
        clashes = [cp for cp in movable if cp.name.strip().lower() in dst_names]
        if clashes:
            print(f"\n⚠ Тёзки в целевом проекте ({len(clashes)}) — переедут как отдельные карточки:")
            for cp in clashes[:10]:
                print(f"   {cp.name}")

        for cp in movable:
            cp.project_id = dst
        for loan in loans:
            loan.project_id = dst

        if not commit:
            await db.rollback()
            print("\nСУХОЙ ПРОГОН — ничего не записано. Повторите с --commit.")
            return

        await db.commit()
        await invalidate_project_reports(src)
        await invalidate_project_reports(dst)
        print(f"\n✓ Перенесено: {len(loans)} займов, {len(movable)} контрагентов")


def main() -> None:
    ap = argparse.ArgumentParser(description="Перенос займов между проектами")
    ap.add_argument("--from", dest="src", type=int, required=True, help="project_id источника")
    ap.add_argument("--to", dest="dst", type=int, required=True, help="project_id назначения")
    ap.add_argument("--commit", action="store_true", help="записать (иначе сухой прогон)")
    args = ap.parse_args()
    if args.src == args.dst:
        raise SystemExit("Источник и назначение совпадают")
    asyncio.run(migrate(args.src, args.dst, commit=args.commit))


if __name__ == "__main__":
    main()
