#!/usr/bin/env python3
"""
План погашения процентного долга ООО «Плюс Вайб» перед ИП Вяткиным.

По займу б/н от 04.03.2025 (зеркальная пара: проект 4 — «мы должны», проект 15 —
«нам должны») тело 31 095 100 ₽ под 27 % годовых, а проценты не платились с
марта — накопился долг 9 482 790,30 ₽ на 28.07.2026, и капает ещё 23 001,85 ₽
в день.

Решение (28.07.2026): ПВ платит **25-го числа каждого месяца** и закрывает весь
процентный долг до конца года — 5 равных платежей 25.08…25.12.2026:

    долг 9 482 790,30 + начисления 29.07→25.12 (150 дн. × 23 001,85 = 3 450 277,50)
    = 12 933 067,80 → 5 × 2 586 613,56

Строки помечены `is_debt_plan=True`: это план ПОГАШЕНИЯ уже начисленного, а не
график начисления. Без флага движок взял бы проценты из графика и обнулил бы
сам долг, который план гасит (см. миграцию `loan07_debt_plan`).

Заодно проставляет паре `payment_day=25` и `accrual_kind=PERIOD_25`: платежи
25-го числа → и период выплат должен считаться с 25-го по 25-е, иначе экран
«кому заплатить» покажет календарный месяц, которого в договорённости нет.

Идемпотентен: повторный запуск не дублирует строки.

    python scripts/seed_pv_interest_debt_plan.py [--commit]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from backend.cache import invalidate_project_reports  # noqa: E402
from backend.database import AsyncSessionLocal  # noqa: E402
from backend.models.loan import Loan, LoanScheduleEntry  # noqa: E402

CONTRACT = "б/н от 04.03.2025"
PAYMENT_DAY = 25

# seq | период с | период по | дата платежа | сумма
PLAN: list[tuple[int, str, str, str, str]] = [
    (1, "2026-07-29", "2026-08-25", "2026-08-25", "2586613.56"),
    (2, "2026-08-26", "2026-09-25", "2026-09-25", "2586613.56"),
    (3, "2026-09-26", "2026-10-25", "2026-10-25", "2586613.56"),
    (4, "2026-10-26", "2026-11-25", "2026-11-25", "2586613.56"),
    (5, "2026-11-26", "2026-12-25", "2026-12-25", "2586613.56"),
]
NOTE = "погашение долга по процентам, 1/5"


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


async def _apply(db: AsyncSession, loan: Loan) -> None:
    side = "мы должны" if loan.direction == "INCOMING" else "нам должны"
    print(f"Займ id {loan.id} (проект {loan.project_id}, {loan.direction} — {side}):")

    if loan.payment_day != PAYMENT_DAY or loan.accrual_kind != "PERIOD_25":
        loan.payment_day = PAYMENT_DAY
        loan.accrual_kind = "PERIOD_25"
        print(f"  payment_day=25, accrual_kind=PERIOD_25")
    else:
        print("  payment_day/accrual_kind уже проставлены")

    existing = (
        await db.execute(
            select(LoanScheduleEntry).where(LoanScheduleEntry.loan_id == loan.id)
        )
    ).scalars().all()
    if existing:
        print(f"  график уже есть ({len(existing)} строк) — план не трогаю")
        return

    total = Decimal("0")
    for seq, ps, pe, due, amount in PLAN:
        db.add(
            LoanScheduleEntry(
                loan_id=loan.id,
                seq=seq,
                period_start=_d(ps),
                period_end=_d(pe),
                due_date=_d(due),
                days=(_d(pe) - _d(ps)).days + 1,
                principal_due=Decimal("0"),  # тело по договорённости не гасим
                interest_due=Decimal(amount),
                payment_total=Decimal(amount),
                is_debt_plan=True,
                note=NOTE,
            )
        )
        total += Decimal(amount)
    print(f"  план: {len(PLAN)} платежей 25-го числа на {total:,.2f} ₽")


async def main() -> None:
    parser = argparse.ArgumentParser(description="План погашения процентов ПВ → Вяткин")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        loans = (
            await db.execute(
                select(Loan).where(
                    Loan.contract_number == CONTRACT,
                    Loan.mirror_loan_id.is_not(None),
                    Loan.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars().all()
        if not loans:
            print(f"Займ «{CONTRACT}» не найден")
            return
        for loan in sorted(loans, key=lambda x: x.project_id):
            await _apply(db, loan)

        if args.commit:
            await db.commit()
            for pid in {loan.project_id for loan in loans}:
                await invalidate_project_reports(pid)
            print("Записано. Кэш отчётов сброшен по обоим проектам.")
        else:
            await db.rollback()
            print("Сухой прогон — ничего не записано. Повторите с --commit.")


if __name__ == "__main__":
    asyncio.run(main())
