#!/usr/bin/env python3
"""
Займы ИП Вяткина у ООО «ВБ Банк» — по актам сверки от 28.07.2026.

Два займа под очень дорогие деньги, обоим 21.07.2026 банк дал **льготный период**
(письмо от 24.07.2026): 3 месяца под 0 % годовых с 21.07 по 19.10.2026, платежи
переносятся, срок продлевается на те же 3 месяца. Тело при этом гасить не нужно —
но и не прощают.

- **№ 2025091800498** от 18.09.2025 — 2 млн, 60 % годовых до 02.04.2026, далее
  27 %. Комиссия за открытие лимита 60 000 ₽. Остаток тела 1 152 560,86.
- **№ 2026022500271** от 25.02.2026 — 10 млн, 50,52 % годовых до 09.09.2026,
  далее 32,04 %. Комиссия 99 000 ₽. Остаток тела 8 340 240,39.

Движения — из колонок «Гашение кредита» и «Гашение учтенных процентов» акта.
Внутренние проводки банка («Отражение процентов за льготный период», «Списание
корректировки») НЕ заводим: они взаимно закрываются, а остаток тела считается
как выдача − гашения и сходится с актом до копейки (проверено обоими актами).

Идемпотентен: повторный запуск ничего не дублирует.

    python scripts/seed_vb_bank_vyatkin_loans.py --project 15 [--commit]
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
from backend.models.loan import Loan, LoanFee, LoanPayment, LoanRatePeriod  # noqa: E402

BANK_INN = "9701048328"
BANK_NAME = 'ООО "Вайлдберриз Банк"'

# Льготный период по обоим договорам — письмо ВБ Банка от 24.07.2026.
GRACE_FROM = "2026-07-21"
GRACE_TO = "2026-10-20"  # ставка возвращается с 20.10.2026 (льгота по 19.10 включительно)

PAYMENTS_2025091800498: list[tuple[str, str, str]] = [
    ("COMMISSION", "60000.00", "2025-09-29"),
    ("INTEREST_PAY", "32876.71", "2025-09-29"),
    ("PRINCIPAL_REPAY", "3055.91", "2025-09-29"),
    ("INTEREST_PAY", "22978.53", "2025-10-06"),
    ("PRINCIPAL_REPAY", "12954.09", "2025-10-06"),
    ("INTEREST_PAY", "22829.47", "2025-10-13"),
    ("PRINCIPAL_REPAY", "13103.15", "2025-10-13"),
    ("INTEREST_PAY", "22678.70", "2025-10-20"),
    ("PRINCIPAL_REPAY", "13253.92", "2025-10-20"),
    ("INTEREST_PAY", "22526.19", "2025-10-27"),
    ("PRINCIPAL_REPAY", "13406.43", "2025-10-27"),
    ("INTEREST_PAY", "22371.92", "2025-11-03"),
    ("PRINCIPAL_REPAY", "13560.70", "2025-11-03"),
    ("INTEREST_PAY", "22215.88", "2025-11-10"),
    ("PRINCIPAL_REPAY", "13716.74", "2025-11-10"),
    ("INTEREST_PAY", "22058.04", "2025-11-17"),
    ("PRINCIPAL_REPAY", "13874.58", "2025-11-17"),
    ("INTEREST_PAY", "21898.39", "2025-11-24"),
    ("PRINCIPAL_REPAY", "14034.23", "2025-11-24"),
    ("INTEREST_PAY", "21736.90", "2025-12-01"),
    ("PRINCIPAL_REPAY", "14195.72", "2025-12-01"),
    ("INTEREST_PAY", "21573.55", "2025-12-08"),
    ("PRINCIPAL_REPAY", "14359.07", "2025-12-08"),
    ("INTEREST_PAY", "21408.33", "2025-12-15"),
    ("PRINCIPAL_REPAY", "14524.29", "2025-12-15"),
    ("INTEREST_PAY", "21241.20", "2025-12-22"),
    ("PRINCIPAL_REPAY", "14691.42", "2025-12-22"),
    ("INTEREST_PAY", "21072.15", "2025-12-29"),
    ("PRINCIPAL_REPAY", "14860.47", "2025-12-29"),
    ("INTEREST_PAY", "1.00", "2026-01-01"),
    ("INTEREST_PAY", "41802.30", "2026-01-12"),
    ("PRINCIPAL_REPAY", "30062.94", "2026-01-12"),
    ("INTEREST_PAY", "20555.22", "2026-01-19"),
    ("PRINCIPAL_REPAY", "15377.40", "2026-01-19"),
    ("INTEREST_PAY", "20378.27", "2026-01-26"),
    ("PRINCIPAL_REPAY", "15554.35", "2026-01-26"),
    ("INTEREST_PAY", "20199.29", "2026-02-02"),
    ("PRINCIPAL_REPAY", "15733.33", "2026-02-02"),
    ("INTEREST_PAY", "20018.25", "2026-02-09"),
    ("PRINCIPAL_REPAY", "15914.37", "2026-02-09"),
    ("INTEREST_PAY", "19835.13", "2026-02-16"),
    ("PRINCIPAL_REPAY", "16097.49", "2026-02-16"),
    ("INTEREST_PAY", "19649.89", "2026-02-24"),
    ("PRINCIPAL_REPAY", "16282.73", "2026-02-24"),
    ("INTEREST_PAY", "19489.30", "2026-03-02"),
    ("PRINCIPAL_REPAY", "16443.32", "2026-03-02"),
    ("INTEREST_PAY", "19273.32", "2026-03-10"),
    ("PRINCIPAL_REPAY", "16659.30", "2026-03-10"),
    ("INTEREST_PAY", "19109.01", "2026-03-16"),
    ("PRINCIPAL_REPAY", "16823.61", "2026-03-16"),
    ("INTEREST_PAY", "18888.04", "2026-03-23"),
    ("PRINCIPAL_REPAY", "17044.58", "2026-03-23"),
    ("INTEREST_PAY", "18691.91", "2026-03-30"),
    ("PRINCIPAL_REPAY", "17240.71", "2026-03-30"),
    ("INTEREST_PAY", "12681.27", "2026-04-06"),
    ("PRINCIPAL_REPAY", "23251.35", "2026-04-06"),
    ("INTEREST_PAY", "8201.69", "2026-04-13"),
    ("PRINCIPAL_REPAY", "27730.93", "2026-04-13"),
    ("INTEREST_PAY", "8058.09", "2026-04-20"),
    ("PRINCIPAL_REPAY", "27874.53", "2026-04-20"),
    ("INTEREST_PAY", "7913.76", "2026-04-27"),
    ("PRINCIPAL_REPAY", "28018.86", "2026-04-27"),
    ("INTEREST_PAY", "7767.68", "2026-05-04"),
    ("PRINCIPAL_REPAY", "28163.95", "2026-05-04"),
    ("INTEREST_PAY", "7622.84", "2026-05-12"),
    ("PRINCIPAL_REPAY", "28309.78", "2026-05-12"),
    ("INTEREST_PAY", "7497.19", "2026-05-18"),
    ("PRINCIPAL_REPAY", "28435.43", "2026-05-18"),
    ("INTEREST_PAY", "7329.01", "2026-05-25"),
    ("PRINCIPAL_REPAY", "28603.61", "2026-05-25"),
    ("INTEREST_PAY", "7180.90", "2026-06-01"),
    ("PRINCIPAL_REPAY", "28751.72", "2026-06-01"),
    ("INTEREST_PAY", "7032.02", "2026-06-08"),
    ("PRINCIPAL_REPAY", "28900.60", "2026-06-08"),
    ("INTEREST_PAY", "4915.98", "2026-06-15"),
    ("INTEREST_PAY", "1966.39", "2026-06-15"),
    ("PRINCIPAL_REPAY", "29050.25", "2026-06-15"),
    ("INTEREST_PAY", "6731.94", "2026-06-22"),
    ("PRINCIPAL_REPAY", "29200.68", "2026-06-22"),
    ("INTEREST_PAY", "6580.74", "2026-06-29"),
    ("PRINCIPAL_REPAY", "29351.88", "2026-06-29"),
    ("INTEREST_PAY", "6428.75", "2026-07-06"),
    ("PRINCIPAL_REPAY", "29503.87", "2026-07-06"),
    ("INTEREST_PAY", "6275.98", "2026-07-13"),
    ("PRINCIPAL_REPAY", "29656.64", "2026-07-13"),
    ("INTEREST_PAY", "6122.41", "2026-07-20"),
    ("PRINCIPAL_REPAY", "29810.21", "2026-07-20"),
]

PAYMENTS_2026022500271: list[tuple[str, str, str]] = [
    ("COMMISSION", "99000.00", "2026-03-10"),
    ("INTEREST_PAY", "152252.05", "2026-03-10"),
    ("PRINCIPAL_REPAY", "23279.42", "2026-03-10"),
    ("INTEREST_PAY", "96694.34", "2026-03-16"),
    ("PRINCIPAL_REPAY", "78837.13", "2026-03-16"),
    ("INTEREST_PAY", "95898.29", "2026-03-23"),
    ("PRINCIPAL_REPAY", "79633.18", "2026-03-23"),
    ("INTEREST_PAY", "95126.74", "2026-03-30"),
    ("PRINCIPAL_REPAY", "80404.73", "2026-03-30"),
    ("INTEREST_PAY", "0.01", "2026-04-01"),
    ("INTEREST_PAY", "94347.72", "2026-04-06"),
    ("PRINCIPAL_REPAY", "81183.75", "2026-04-06"),
    ("INTEREST_PAY", "93561.15", "2026-04-13"),
    ("PRINCIPAL_REPAY", "81970.32", "2026-04-13"),
    ("INTEREST_PAY", "92766.96", "2026-04-20"),
    ("PRINCIPAL_REPAY", "82764.51", "2026-04-20"),
    ("INTEREST_PAY", "91965.07", "2026-04-27"),
    ("PRINCIPAL_REPAY", "83566.40", "2026-04-27"),
    ("INTEREST_PAY", "91155.40", "2026-05-04"),
    ("PRINCIPAL_REPAY", "84376.06", "2026-05-04"),
    ("INTEREST_PAY", "90337.91", "2026-05-12"),
    ("PRINCIPAL_REPAY", "85193.56", "2026-05-12"),
    ("INTEREST_PAY", "89630.41", "2026-05-18"),
    ("PRINCIPAL_REPAY", "85901.06", "2026-05-18"),
    ("INTEREST_PAY", "88680.22", "2026-05-25"),
    ("PRINCIPAL_REPAY", "86851.25", "2026-05-25"),
    ("INTEREST_PAY", "87838.74", "2026-06-01"),
    ("PRINCIPAL_REPAY", "87692.73", "2026-06-01"),
    ("INTEREST_PAY", "86989.10", "2026-06-08"),
    ("PRINCIPAL_REPAY", "88542.37", "2026-06-08"),
    ("INTEREST_PAY", "61522.32", "2026-06-15"),
    ("INTEREST_PAY", "24608.92", "2026-06-15"),
    ("PRINCIPAL_REPAY", "89400.23", "2026-06-15"),
    ("INTEREST_PAY", "85265.06", "2026-06-22"),
    ("PRINCIPAL_REPAY", "90266.41", "2026-06-22"),
    ("INTEREST_PAY", "84390.49", "2026-06-29"),
    ("PRINCIPAL_REPAY", "91140.98", "2026-06-29"),
    ("INTEREST_PAY", "83507.44", "2026-07-06"),
    ("PRINCIPAL_REPAY", "92024.03", "2026-07-06"),
    ("INTEREST_PAY", "82615.84", "2026-07-13"),
    ("PRINCIPAL_REPAY", "92915.63", "2026-07-13"),
    ("INTEREST_PAY", "81715.61", "2026-07-20"),
    ("PRINCIPAL_REPAY", "93815.86", "2026-07-20"),
]

# договор | дата | выдача | тело | базовая ставка | maturity | комиссия | дата комиссии |
# ставки (valid_from, rate, note) | движения | заметка
LOANS: list[tuple] = [
    (
        "2025091800498",
        "2025-09-18",
        "2025-09-19",
        "2000000.00",
        "0.6000",
        "2027-09-29",
        "60000.00",
        "2025-09-29",
        [
            ("2025-09-19", "0.6000", "ставка по договору, 1-я ступень"),
            ("2026-04-03", "0.2700", "2-я ступень по договору"),
            (GRACE_FROM, "0.0000", "льготный период 21.07–19.10.2026, письмо от 24.07.2026"),
            (GRACE_TO, "0.2700", "возврат к договорной ставке после льготного периода"),
        ],
        PAYMENTS_2025091800498,
        (
            "ООО «ВБ Банк», ИП Вяткин. Ставка 60 % годовых по 02.04.2026, далее 27 %. "
            "Комиссия за открытие лимита 60 000 ₽ (29.09.2025). Льготный период "
            "21.07–19.10.2026 под 0 % — срок продлён на 3 мес. (договорный конец "
            "29.06.2027 → 29.09.2027). Остаток на 28.07.2026 по акту: тело "
            "1 152 560,86, проценты 852,58."
        ),
    ),
    (
        "2026022500271",
        "2026-02-25",
        "2026-02-26",
        "10000000.00",
        "0.5052",
        "2028-02-23",
        "99000.00",
        "2026-03-10",
        [
            ("2026-02-26", "0.5052", "ставка по договору, 1-я ступень"),
            (GRACE_FROM, "0.0000", "льготный период 21.07–19.10.2026, письмо от 24.07.2026"),
            (GRACE_TO, "0.3204", "2-я ступень: к концу льготного периода 1-я истекла"),
        ],
        PAYMENTS_2026022500271,
        (
            "ООО «ВБ Банк», ИП Вяткин. Ставка 50,52 % годовых по 09.09.2026, далее "
            "32,04 %. Комиссия за открытие лимита 99 000 ₽ (10.03.2026). Льготный "
            "период 21.07–19.10.2026 под 0 % — срок продлён на 3 мес. (договорный "
            "конец 23.11.2027 → 23.02.2028). ⚠ Переход на 2-ю ступень (10.09.2026) "
            "попадает внутрь льготного периода: после него ставка взята 32,04 %. "
            "Остаток на 28.07.2026 по акту: тело 8 340 240,39, проценты 11 543,81."
        ),
    ),
]


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


async def _counterparty(db: AsyncSession, project_id: int) -> Counterparty:
    cp = (
        await db.execute(
            select(Counterparty).where(
                Counterparty.project_id == project_id,
                Counterparty.inn == BANK_INN,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if cp is not None:
        print(f"Контрагент: {cp.name} (id {cp.id}) — уже есть")
        return cp
    cp = Counterparty(project_id=project_id, inn=BANK_INN, name=BANK_NAME, primary_type="OTHER")
    db.add(cp)
    await db.flush()
    print(f"Контрагент: {BANK_NAME} — создан (id {cp.id})")
    return cp


async def _seed(db: AsyncSession, project_id: int, spec: tuple) -> None:
    (contract, cdate, issued, principal, rate, maturity, fee_amt, fee_date,
     rates, payments, notes) = spec

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
        contract_date=_d(cdate),
        start_date=_d(issued),
        maturity_date=_d(maturity),
        status="ACTIVE",
        loan_kind="TERM",
        accrual_kind="CALENDAR_MONTH",
        entity_type="IP",
        lender_bank="ВБ Банк",
        notes=notes,
    )
    db.add(loan)
    await db.flush()

    for valid_from, r, note in rates:
        db.add(
            LoanRatePeriod(
                loan_id=loan.id, valid_from=_d(valid_from), rate=Decimal(r), note=note
            )
        )

    commission: LoanPayment | None = None
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
            commission = pay
    await db.flush()

    db.add(
        LoanFee(
            loan_id=loan.id,
            fee_kind="LIMIT_SETUP",
            amount=Decimal(fee_amt),
            charged_at=_d(fee_date),
            amortize=True,
            amortize_from=_d(issued),
            amortize_to=_d(maturity),
            payment_id=commission.id if commission else None,
            note="плата за открытие лимита/счёта",
        )
    )

    body = sum(Decimal(a) for k, a, _ in payments if k == "PRINCIPAL_REPAY")
    interest = sum(Decimal(a) for k, a, _ in payments if k == "INTEREST_PAY")
    print(
        f"Займ {contract}: создан (id {loan.id}), {len(payments)} движений, "
        f"{len(rates)} периодов ставки | тело погашено {body:,.2f} "
        f"→ остаток {Decimal(principal) - body:,.2f} | процентов уплачено {interest:,.2f}"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Займы ИП Вяткина у ВБ Банка")
    parser.add_argument("--project", type=int, default=15)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        for spec in LOANS:
            await _seed(db, args.project, spec)
        if args.commit:
            await db.commit()
            await invalidate_project_reports(args.project)
            print("Записано. Кэш отчётов проекта сброшен.")
        else:
            await db.rollback()
            print("Сухой прогон — ничего не записано. Повторите с --commit.")


if __name__ == "__main__":
    asyncio.run(main())
