#!/usr/bin/env python3
"""
Завести займы ООО МКК «ВБ Финанс» (продукт «ГИБКИЙ-2») в учёт ООО «Плюс Вайб».

Два займа, оба ЗАКРЫТЫ досрочно 23.03.2026 — но их стоимость (3,15 млн ₽ за
декабрь–март) в учёте отсутствовала, а значит ОПиУ тех месяцев занижал расходы
на обслуживание долга почти на всю эту сумму.

- **№ 2025121100153** от 11.12.2025 — 10,7 млн, 4.58 % в месяц (с 29-й недели
  1.83 %, до неё не дожили), комиссия за выдачу 2.99 % = 319 930 ₽.
- **№ 2026021100414** от 11.02.2026 — 20 млн, 4.21 % в месяц (с 29-й недели
  2.67 %), комиссия за выдачу 0.99 % = 198 000 ₽.

Ставка в договоре месячная; в учёт кладём годовую (месячная × 12), потому что
именно она воспроизводит акт сверки: 4.58 × 12 = 54.96 %, и начисление
«остаток × ставка / 365 × дни» сходится с колонкой «Начислено» до рублей.

Движения взяты из актов сверки от 28.07.2026 (колонки «Погашено» по телу и
процентам), а не из графика платежей: график — план на 550 дней, факт — 101 и
39 дней соответственно. График сюда НЕ заводим намеренно: движок при наличии
строк графика раскладывает проценты из него, и плановые цифры вытеснили бы
фактические.

Комиссия за выдачу амортизируется на ФАКТИЧЕСКИЙ срок жизни займа (выдача →
досрочное закрытие), а не на договорные 550 дней: заём закрыт, растягивать его
комиссию на 2027 год не на что.

Идемпотентен: повторный запуск ничего не дублирует.

    python scripts/seed_vb_finance_loans.py --project 4 [--commit]

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
from backend.models.loan import Loan, LoanFee, LoanPayment  # noqa: E402

VB_INN = "9707021650"
VB_NAME = 'ООО МКК "ВБ ФИНАНС"'

# (тип, сумма, дата) — строго по акту сверки от 28.07.2026.
LOAN_10_7_PAYMENTS: list[tuple[str, str, str]] = [
    ("COMMISSION", "319930.00", "2025-12-22"),
    ("PRINCIPAL_REPAY", "24791.65", "2025-12-22"),
    ("INTEREST_PAY", "161115.62", "2025-12-22"),
    ("PRINCIPAL_REPAY", "73387.65", "2025-12-29"),
    ("INTEREST_PAY", "112519.62", "2025-12-29"),
    ("PRINCIPAL_REPAY", "148322.35", "2026-01-12"),
    ("INTEREST_PAY", "223492.19", "2026-01-12"),
    ("PRINCIPAL_REPAY", "75724.53", "2026-01-19"),
    ("INTEREST_PAY", "110182.74", "2026-01-19"),
    ("PRINCIPAL_REPAY", "76522.69", "2026-01-26"),
    ("INTEREST_PAY", "109384.58", "2026-01-26"),
    ("PRINCIPAL_REPAY", "77329.26", "2026-02-02"),
    ("INTEREST_PAY", "108578.01", "2026-02-02"),
    ("PRINCIPAL_REPAY", "78144.33", "2026-02-09"),
    ("INTEREST_PAY", "107762.94", "2026-02-09"),
    ("PRINCIPAL_REPAY", "78968.00", "2026-02-16"),
    ("INTEREST_PAY", "106939.27", "2026-02-16"),
    ("PRINCIPAL_REPAY", "79800.34", "2026-02-24"),
    ("INTEREST_PAY", "106106.93", "2026-02-24"),
    ("PRINCIPAL_REPAY", "80521.30", "2026-03-02"),
    ("INTEREST_PAY", "105385.97", "2026-03-02"),
    ("PRINCIPAL_REPAY", "81490.17", "2026-03-10"),
    ("INTEREST_PAY", "104417.10", "2026-03-10"),
    ("PRINCIPAL_REPAY", "82226.40", "2026-03-16"),
    ("INTEREST_PAY", "103680.86", "2026-03-16"),
    # Досрочное закрытие: остаток тела одним платежом.
    ("PRINCIPAL_REPAY", "9742771.33", "2026-03-23"),
    ("INTEREST_PAY", "102691.48", "2026-03-23"),
]

LOAN_20_PAYMENTS: list[tuple[str, str, str]] = [
    ("COMMISSION", "198000.00", "2026-02-24"),
    ("PRINCIPAL_REPAY", "46558.84", "2026-02-24"),
    ("INTEREST_PAY", "304504.11", "2026-02-24"),
    ("PRINCIPAL_REPAY", "157674.26", "2026-03-02"),
    ("INTEREST_PAY", "193388.69", "2026-03-02"),
    ("PRINCIPAL_REPAY", "159266.37", "2026-03-10"),
    ("INTEREST_PAY", "191796.58", "2026-03-10"),
    ("PRINCIPAL_REPAY", "160589.03", "2026-03-16"),
    ("INTEREST_PAY", "190473.92", "2026-03-16"),
    ("PRINCIPAL_REPAY", "19475911.50", "2026-03-23"),
    ("INTEREST_PAY", "188697.57", "2026-03-23"),
]

# номер | дата договора | выдача | закрытие | тело | годовая ставка |
# комиссия | дата комиссии | заметка
LOANS: list[tuple] = [
    (
        "2025121100153",
        "2025-12-11",
        "2025-12-12",
        "2026-03-23",
        "10700000.00",
        "0.5496",  # 4.58 % в месяц × 12
        "319930.00",
        "2025-12-22",
        (
            "ООО МКК «ВБ Финанс», продукт «ГИБКИЙ-2». Ставка 4,58 % в месяц "
            "(54,96 % годовых) с 1-й по 28-ю неделю, далее 1,83 % в месяц — до "
            "второй ступени не дожили. Комиссия за выдачу 2,99 %. Договорный срок "
            "550 дней до 13.06.2027, фактически закрыт досрочно 23.03.2026 "
            "(101 день). Проценты и тело — по акту сверки от 28.07.2026."
        ),
        LOAN_10_7_PAYMENTS,
    ),
    (
        "2026021100414",
        "2026-02-11",
        "2026-02-12",
        "2026-03-23",
        "20000000.00",
        "0.5052",  # 4.21 % в месяц × 12
        "198000.00",
        "2026-02-24",
        (
            "ООО МКК «ВБ Финанс», продукт «ГИБКИЙ-2». Ставка 4,21 % в месяц "
            "(50,52 % годовых) с 1-й по 28-ю неделю, далее 2,67 % в месяц — до "
            "второй ступени не дожили. Комиссия за выдачу 0,99 %. Договорный срок "
            "551 день до 16.08.2027 (в акте сверки — 550 дней до 15.08.2027), "
            "фактически закрыт досрочно 23.03.2026 (39 дней). Проценты и тело — "
            "по акту сверки от 28.07.2026."
        ),
        LOAN_20_PAYMENTS,
    ),
]


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


async def _counterparty(db: AsyncSession, project_id: int) -> Counterparty:
    cp = (
        await db.execute(
            select(Counterparty).where(
                Counterparty.project_id == project_id,
                Counterparty.inn == VB_INN,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if cp is not None:
        print(f"Контрагент: {cp.name} (id {cp.id}) — уже есть")
        return cp
    cp = Counterparty(project_id=project_id, inn=VB_INN, name=VB_NAME, primary_type="OTHER")
    db.add(cp)
    await db.flush()
    print(f"Контрагент: {VB_NAME} — создан (id {cp.id})")
    return cp


async def _seed_loan(db: AsyncSession, project_id: int, spec: tuple) -> None:
    (
        contract,
        contract_date,
        issued,
        closed,
        principal,
        rate,
        fee_amount,
        fee_date,
        notes,
        payments,
    ) = spec

    existing = (
        await db.execute(
            select(Loan).where(
                Loan.project_id == project_id,
                Loan.contract_number == contract,
                Loan.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        print(f"Займ {contract} уже заведён (id {existing.id}) — пропускаю")
        return

    cp = await _counterparty(db, project_id)
    loan = Loan(
        project_id=project_id,
        counterparty_id=cp.id,
        direction="INCOMING",
        principal=Decimal(principal),
        currency="RUB",
        rate=Decimal(rate),
        contract_number=contract,
        contract_date=_d(contract_date),
        start_date=_d(issued),
        # Срок = фактическое закрытие: заём погашен, и «ближайший возврат» по нему
        # уже в прошлом. Договорная дата — в заметке.
        maturity_date=_d(closed),
        status="CLOSED",
        loan_kind="TERM",
        accrual_kind="CALENDAR_MONTH",
        lender_bank="ВБ Финанс",
        notes=notes,
    )
    db.add(loan)
    await db.flush()

    commission_payment: LoanPayment | None = None
    for kind, amount, when in payments:
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

    db.add(
        LoanFee(
            loan_id=loan.id,
            fee_kind="ORIGINATION",
            amount=Decimal(fee_amount),
            charged_at=_d(fee_date),
            amortize=True,
            amortize_from=_d(issued),
            amortize_to=_d(closed),
            payment_id=commission_payment.id if commission_payment else None,
            note="комиссия за выдачу займа, п. 5 индивидуальных условий",
        )
    )

    body = sum(Decimal(a) for k, a, _ in payments if k == "PRINCIPAL_REPAY")
    interest = sum(Decimal(a) for k, a, _ in payments if k == "INTEREST_PAY")
    print(
        f"Займ {contract}: создан (id {loan.id}), {len(payments)} движений, "
        f"тело погашено {body:,.2f}, процентов уплачено {interest:,.2f}, "
        f"комиссия {Decimal(fee_amount):,.2f}"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Займы ВБ Финанс в учёт")
    parser.add_argument("--project", type=int, default=4, help="id проекта (по умолчанию 4)")
    parser.add_argument("--commit", action="store_true", help="записать в БД")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        for spec in LOANS:
            await _seed_loan(db, args.project, spec)

        if args.commit:
            await db.commit()
            await invalidate_project_reports(args.project)
            print("Записано. Кэш отчётов проекта сброшен.")
        else:
            await db.rollback()
            print("Сухой прогон — ничего не записано. Повторите с --commit.")


if __name__ == "__main__":
    asyncio.run(main())
