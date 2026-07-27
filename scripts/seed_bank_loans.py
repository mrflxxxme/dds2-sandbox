#!/usr/bin/env python3
"""
Завести банковские продукты ООО «Плюс Вайб» в учёт займов.

Два продукта, которые не описываются обычным займом с одной ставкой:

- **Симпл Финанс** (E/2026/0087/01) — аннуитет 30 млн под 21 % на 11 мес. 7 дн.
  Платёж фиксирован (3 021 911,33 ₽), а деление на тело и проценты банк считает
  по своему округлению, поэтому график заводится из приложения № 1 к договору
  как есть. Первые 7 дней процентов нет — за них взята комиссия за выдачу 4,25 %.
- **ВКЛ ВБ Банка** (РЛ-21/26) — линия уже заведена; скрипт лишь дописывает ей
  разовую комиссию за установление лимита (252 500 ₽), чтобы она попала в
  стоимость денег амортизацией, а не одним ударом по маю.

Идемпотентен: повторный запуск ничего не дублирует.

    python scripts/seed_bank_loans.py --project 4 [--commit]

Без `--commit` — сухой прогон.
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
from backend.models.counterparty import Counterparty  # noqa: E402
from backend.models.loan import (  # noqa: E402
    Loan,
    LoanFee,
    LoanPayment,
    LoanScheduleEntry,
)

SIMPLE_INN = "7703381419"
SIMPLE_NAME = 'ООО МКК "СИМПЛФИНАНС"'
SIMPLE_CONTRACT = "E/2026/0087/01"
LINE_CONTRACT = "РЛ-21/26"

# Приложение № 1 к договору: № | начало | конец | дата платежа | дней |
# тело | проценты | платёж | остаток после платежа
SIMPLE_SCHEDULE: list[tuple] = [
    (1, "2026-03-18", "2026-03-25", "2026-03-25", 7, "0", "1275000.00", "1275000.00", "30000000.00"),
    (2, "2026-03-26", "2026-04-27", "2026-04-27", 33, "2452322.29", "569589.04", "3021911.33", "27547677.71"),
    (3, "2026-04-28", "2026-05-25", "2026-05-25", 28, "2578129.56", "443781.77", "3021911.33", "24969548.15"),
    (4, "2026-05-26", "2026-06-25", "2026-06-25", 31, "2576564.05", "445347.28", "3021911.33", "22392984.10"),
    (5, "2026-06-26", "2026-07-27", "2026-07-27", 32, "2609635.02", "412276.31", "3021911.33", "19783349.08"),
    (6, "2026-07-28", "2026-08-25", "2026-08-25", 29, "2691827.51", "330083.82", "3021911.33", "17091521.57"),
    (7, "2026-08-26", "2026-09-25", "2026-09-25", 31, "2717073.51", "304837.82", "3021911.33", "14374448.06"),
    (8, "2026-09-26", "2026-10-26", "2026-10-26", 31, "2765534.19", "256377.14", "3021911.33", "11608913.87"),
    (9, "2026-10-27", "2026-11-25", "2026-11-25", 30, "2821538.30", "200373.03", "3021911.33", "8787375.57"),
    (10, "2026-11-26", "2026-12-25", "2026-12-25", 30, "2870238.82", "151672.51", "3021911.33", "5917136.75"),
    (11, "2026-12-26", "2027-01-25", "2027-01-25", 31, "2916375.55", "105535.78", "3021911.33", "3000761.20"),
    (12, "2027-01-26", "2027-02-25", "2027-02-25", 31, "3000761.20", "53520.43", "3054281.63", "0"),
]

# Факт из выписок ВТБ и ВБ Банка. Платежи по одному кредиту ходят с РАЗНЫХ
# счетов, поэтому привязка только по номеру договора в назначении платежа.
SIMPLE_PAYMENTS: list[tuple[str, str, str]] = [
    ("COMMISSION", "1275000.00", "2026-03-25"),  # комиссия за выдачу 4,25 %
    ("PRINCIPAL_REPAY", "2452322.29", "2026-04-24"),  # платёж № 2, со счёта ВБ Банка
    ("INTEREST_PAY", "569589.04", "2026-04-24"),
    ("PRINCIPAL_REPAY", "2578129.56", "2026-05-26"),  # платёж № 3, днём позже срока
    ("INTEREST_PAY", "443781.77", "2026-05-26"),
    ("PENALTY", "1483.31", "2026-05-26"),  # пени за день просрочки
    ("PRINCIPAL_REPAY", "2576564.05", "2026-06-25"),  # платёж № 4
    ("INTEREST_PAY", "445347.28", "2026-06-25"),
]


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


async def _counterparty(db: AsyncSession, project_id: int) -> Counterparty:
    cp = (
        await db.execute(
            select(Counterparty).where(
                Counterparty.project_id == project_id,
                Counterparty.inn == SIMPLE_INN,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if cp is not None:
        print(f"Контрагент: {cp.name} (id {cp.id}) — уже есть")
        return cp
    cp = Counterparty(
        project_id=project_id, inn=SIMPLE_INN, name=SIMPLE_NAME, primary_type="OTHER"
    )
    db.add(cp)
    await db.flush()
    print(f"Контрагент: {SIMPLE_NAME} — создан (id {cp.id})")
    return cp


async def _seed_simple(db: AsyncSession, project_id: int) -> None:
    loan = (
        await db.execute(
            select(Loan).where(
                Loan.project_id == project_id,
                Loan.contract_number == SIMPLE_CONTRACT,
                Loan.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if loan is not None:
        print(f"Займ {SIMPLE_CONTRACT} уже заведён (id {loan.id}) — пропускаю")
        return

    cp = await _counterparty(db, project_id)
    loan = Loan(
        project_id=project_id,
        counterparty_id=cp.id,
        direction="INCOMING",
        principal=Decimal("30000000.00"),
        currency="RUB",
        rate=Decimal("0.21"),
        contract_number=SIMPLE_CONTRACT,
        contract_date=_d("2026-03-18"),
        start_date=_d("2026-03-18"),
        maturity_date=_d("2027-02-25"),
        status="ACTIVE",
        loan_kind="TERM",
        accrual_kind="PERIOD_25",
        notes=(
            "Рамочное соглашение E/2026/0087/00 от 18.03.2026. Аннуитет 3 021 911,33 ₽, "
            "12 платежей. Комиссия за выдачу 4,25 % (1 275 000 ₽) уплачена 25.03.2026."
        ),
    )
    db.add(loan)
    await db.flush()

    commission_payment: LoanPayment | None = None
    for kind, amount, when in SIMPLE_PAYMENTS:
        pay = LoanPayment(
            loan_id=loan.id,
            payment_type=kind,
            amount=Decimal(amount),
            currency="RUB",
            paid_at=_d(when),
        )
        db.add(pay)
        if kind == "COMMISSION":
            commission_payment = pay
    await db.flush()

    for seq, ps, pe, due, days, principal, interest, total, after in SIMPLE_SCHEDULE:
        db.add(
            LoanScheduleEntry(
                loan_id=loan.id,
                seq=seq,
                period_start=_d(ps),
                period_end=_d(pe),
                due_date=_d(due),
                days=days,
                principal_due=Decimal(principal),
                interest_due=Decimal(interest),
                payment_total=Decimal(total),
                balance_after=Decimal(after),
                # Первая строка — комиссия за выдачу: в графике она напечатана в
                # колонке процентов, но процентами не является.
                is_fee=(seq == 1),
                note="комиссия за выдачу 4,25 %" if seq == 1 else None,
            )
        )

    db.add(
        LoanFee(
            loan_id=loan.id,
            fee_kind="ORIGINATION",
            amount=Decimal("1275000.00"),
            charged_at=_d("2026-03-25"),
            amortize=True,
            amortize_from=_d("2026-03-18"),
            amortize_to=_d("2027-02-25"),
            payment_id=commission_payment.id if commission_payment else None,
            note="4,25 % от суммы займа, п. 2.3 договора",
        )
    )
    print(
        f"Займ {SIMPLE_CONTRACT}: создан (id {loan.id}), "
        f"{len(SIMPLE_PAYMENTS)} платежей, {len(SIMPLE_SCHEDULE)} строк графика, 1 комиссия"
    )


async def _seed_line_fee(db: AsyncSession, project_id: int) -> None:
    """Комиссия за установление лимита по ВКЛ — 0.25 % разово при открытии."""
    line = (
        await db.execute(
            select(Loan).where(
                Loan.project_id == project_id,
                Loan.contract_number == LINE_CONTRACT,
                Loan.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if line is None:
        print(f"Линия {LINE_CONTRACT} не найдена — комиссию за лимит пропускаю")
        return
    existing = (
        await db.execute(
            select(LoanFee).where(LoanFee.loan_id == line.id, LoanFee.fee_kind == "LIMIT_SETUP")
        )
    ).scalar_one_or_none()
    if existing is not None:
        print(f"Комиссия за лимит по {LINE_CONTRACT} уже заведена — пропускаю")
        return

    # Касса уже есть в движениях линии — цепляем расход к ней, чтобы не выглядело
    # как второй платёж.
    payment = (
        await db.execute(
            select(LoanPayment).where(
                LoanPayment.loan_id == line.id,
                LoanPayment.payment_type == "COMMISSION",
                LoanPayment.amount == Decimal("252500.00"),
            )
        )
    ).scalar_one_or_none()
    db.add(
        LoanFee(
            loan_id=line.id,
            fee_kind="LIMIT_SETUP",
            amount=Decimal("252500.00"),
            charged_at=_d("2026-05-25"),
            amortize=True,
            amortize_from=line.start_date,
            amortize_to=line.maturity_date,
            payment_id=payment.id if payment else None,
            note="0,25 % от лимита 101 млн, разово при открытии",
        )
    )
    print(f"Комиссия за лимит по {LINE_CONTRACT}: 252 500 ₽, размазана на срок линии")


async def seed(project_id: int, *, commit: bool) -> None:
    async with AsyncSessionLocal() as db:
        await _seed_simple(db, project_id)
        await _seed_line_fee(db, project_id)
        if not commit:
            await db.rollback()
            print("\nСУХОЙ ПРОГОН — ничего не записано. Повторите с --commit.")
            return
        await db.commit()
        await invalidate_project_reports(project_id)
        print("\n✓ Записано")


def main() -> None:
    ap = argparse.ArgumentParser(description="Банковские продукты в учёт займов")
    ap.add_argument("--project", type=int, required=True, help="project_id")
    ap.add_argument("--commit", action="store_true", help="записать (иначе сухой прогон)")
    args = ap.parse_args()
    asyncio.run(seed(args.project, commit=args.commit))


if __name__ == "__main__":
    main()
