#!/usr/bin/env python3
"""
ВКЛ ВБ Банка «WB Рост» (договор № РЛ-21/26) — заведение линии как таковой.

`seed_bank_loans.py` умеет дописать линии комиссию за лимит и график возврата,
но саму линию не создаёт: локально её завели через экран, и на прод она поэтому
не переехала. Этот скрипт создаёт линию с нуля — со всеми выборками, историей
ставки и комиссией, ровно как в локальной базе.

Модель (сверена с выпиской ВБ Банка до копейки, см. память проекта):
- лимит 101 млн, выбран целиком шестью траншами за май–июнь 2026;
- ставка = ключевая ЦБ + 5 %: 19,5 % по 21.06.2026, 19,25 % с 22.06.2026;
- комиссия за установление лимита 0,25 % (252 500 ₽) — амортизируется на год;
- комиссия за НЕиспользованный лимит 2 % годовых платится 5-го числа
  следующего месяца (май 10 191,78 ₽, июнь 27 123,29 ₽);
- проценты платят в начале следующего месяца: 05.06 за май, 06.07 за июнь.

Каждый транш возвращается через 180 дней — график возврата заводится строками
`loan_schedule_entry` только по телу (`interest_due = 0`), иначе движок начал бы
брать проценты из графика вместо формулы и обнулил бы стоимость линии.

Идемпотентен: повторный запуск ничего не дублирует.

    python scripts/seed_wb_credit_line.py --project 4 [--commit]
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
    LoanRatePeriod,
    LoanScheduleEntry,
)

BANK_INN = "9701048328"
BANK_NAME = 'ООО "ВБ Банк"'
CONTRACT = "РЛ-21/26"

# (тип, сумма, дата) — выборки, проценты и комиссии по выписке
PAYMENTS: list[tuple[str, str, str]] = [
    ("DISBURSEMENT", "70000000.00", "2026-05-25"),
    ("COMMISSION", "252500.00", "2026-05-25"),  # за установление лимита, 0,25 %
    ("COMMISSION", "10191.78", "2026-06-05"),  # за неиспользованный лимит, май
    ("INTEREST_PAY", "224383.56", "2026-06-05"),  # проценты за май
    ("DISBURSEMENT", "12000000.00", "2026-06-09"),
    ("DISBURSEMENT", "6000000.00", "2026-06-16"),
    ("DISBURSEMENT", "4000000.00", "2026-06-19"),
    ("DISBURSEMENT", "5000000.00", "2026-06-23"),
    ("DISBURSEMENT", "4000000.00", "2026-06-25"),
    ("COMMISSION", "27123.29", "2026-07-06"),  # за неиспользованный лимит, июнь
    ("INTEREST_PAY", "1348267.12", "2026-07-06"),  # проценты за июнь
]

# (valid_from, ставка, ключевая ЦБ, спред)
RATES: list[tuple[str, str, str, str]] = [
    ("2026-05-25", "0.1950", "0.1450", "0.0500"),
    ("2026-06-22", "0.1925", "0.1425", "0.0500"),
]

# seq | начало | дата возврата | тело | остаток после | заметка
SCHEDULE: list[tuple[int, str, str, str, str, str]] = [
    (1, "2026-05-25", "2026-11-21", "70000000.00", "31000000.00", "возврат транша от 25.05.2026, 180 дней"),
    (2, "2026-06-09", "2026-12-06", "12000000.00", "19000000.00", "возврат транша от 09.06.2026, 180 дней"),
    (3, "2026-06-16", "2026-12-13", "6000000.00", "13000000.00", "возврат транша от 16.06.2026, 180 дней"),
    (4, "2026-06-19", "2026-12-16", "4000000.00", "9000000.00", "возврат транша от 19.06.2026, 180 дней"),
    (5, "2026-06-23", "2026-12-20", "5000000.00", "4000000.00", "возврат транша от 23.06.2026, 180 дней"),
    (6, "2026-06-25", "2026-12-22", "4000000.00", "0.00", "возврат транша от 25.06.2026, 180 дней"),
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


async def main() -> None:
    parser = argparse.ArgumentParser(description="ВКЛ ВБ Банка РЛ-21/26")
    parser.add_argument("--project", type=int, default=4)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(Loan).where(
                    Loan.project_id == args.project,
                    Loan.contract_number == CONTRACT,
                    Loan.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            print(f"Линия {CONTRACT} уже заведена (id {existing.id}) — пропускаю")
            return

        cp = await _counterparty(db, args.project)
        line = Loan(
            project_id=args.project,
            counterparty_id=cp.id,
            direction="INCOMING",
            # У револьверной линии тело задают выборки, а не поле займа.
            principal=Decimal("0"),
            currency="RUB",
            rate=None,  # ставка живёт в loan_rate_period: она плавающая
            contract_number=CONTRACT,
            contract_date=_d("2026-05-25"),
            start_date=_d("2026-05-25"),
            maturity_date=_d("2026-12-22"),  # возврат последнего транша
            status="ACTIVE",
            loan_kind="CREDIT_LINE",
            accrual_kind="CALENDAR_MONTH",
            credit_limit=Decimal("101000000.00"),
            unused_limit_rate=Decimal("0.0200"),
            lender_bank="ВБ Банк",
            notes=(
                "Продукт «WB Рост», договор № РЛ-21/26 от 25.05.2026. Лимит 101 млн "
                "выбран целиком шестью траншами. Ставка = ключевая ЦБ + 5 %. "
                "Начисление со дня, следующего за выдачей транша, по календарным "
                "месяцам; проценты и комиссия за неиспользованный лимит платятся "
                "5-го числа следующего месяца. Каждый транш возвращается через "
                "180 дней."
            ),
        )
        db.add(line)
        await db.flush()

        for valid_from, rate, base, spread in RATES:
            db.add(
                LoanRatePeriod(
                    loan_id=line.id,
                    valid_from=_d(valid_from),
                    rate=Decimal(rate),
                    base_rate=Decimal(base),
                    spread=Decimal(spread),
                )
            )

        limit_fee_payment: LoanPayment | None = None
        for kind, amount, when in PAYMENTS:
            pay = LoanPayment(
                loan_id=line.id,
                payment_type=kind,
                amount=Decimal(amount),
                currency="RUB",
                paid_at=_d(when),
            )
            db.add(pay)
            if kind == "COMMISSION" and amount == "252500.00":
                limit_fee_payment = pay
        await db.flush()

        db.add(
            LoanFee(
                loan_id=line.id,
                fee_kind="LIMIT_SETUP",
                amount=Decimal("252500.00"),
                charged_at=_d("2026-05-25"),
                amortize=True,
                amortize_from=_d("2026-05-25"),
                amortize_to=_d("2027-05-25"),
                payment_id=limit_fee_payment.id if limit_fee_payment else None,
                note="0,25 % от лимита 101 млн, разово при открытии",
            )
        )

        for seq, start, due, principal, after, note in SCHEDULE:
            db.add(
                LoanScheduleEntry(
                    loan_id=line.id,
                    seq=seq,
                    period_start=_d(start),
                    period_end=_d(due),
                    due_date=_d(due),
                    days=180,
                    principal_due=Decimal(principal),
                    # Только тело: проценты по линии считает движок по ставке,
                    # а строки с процентами отменили бы формулу.
                    interest_due=Decimal("0"),
                    payment_total=Decimal(principal),
                    balance_after=Decimal(after),
                    note=note,
                )
            )

        drawn = sum(Decimal(a) for k, a, _ in PAYMENTS if k == "DISBURSEMENT")
        print(
            f"Линия {CONTRACT}: создана (id {line.id}) | лимит 101 000 000 · "
            f"выбрано {drawn:,.2f} | {len(PAYMENTS)} движений, {len(RATES)} ставок, "
            f"1 комиссия, {len(SCHEDULE)} строк возврата"
        )

        if args.commit:
            await db.commit()
            await invalidate_project_reports(args.project)
            print("Записано. Кэш отчётов проекта сброшен.")
        else:
            await db.rollback()
            print("Сухой прогон — ничего не записано. Повторите с --commit.")


if __name__ == "__main__":
    asyncio.run(main())
