#!/usr/bin/env python3
"""
Завести займ ИП Вяткина в ООО «Плюс Вайб» — обе стороны одного договора.

Договор б/н от 04.03.2025, револьверный, до востребования. Ставка 2 % годовых
до 31.10.2025 и 27 % с 01.11.2025. Источник движений — карточка счёта 67.03
из 1С («Плюс Вайб»), сверенная с выписками ИП: все 118 строк совпали, кроме
одного платежа 643 000 ₽ от 27.08.2025, который в выписках ИП не виден
(похоже, ушёл с личного счёта Вяткина-физлица). Итоги карточки:
получено 108 251 000, возвращено 77 155 900, сальдо 31 095 100 на 23.07.2026.

Заводится ДВЕ записи: в проекте «Плюс Вайб» — полученный займ (долг), в проекте
ИП Вяткина — выданный (актив). Связаны через `mirror_loan_id`.

Идемпотентен: повторный запуск ничего не дублирует.

    python scripts/seed_vyatkin_pv_loan.py --pv-project 4 --ip-project 15 [--commit]

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
from backend.models.loan import Loan, LoanPayment, LoanRatePeriod  # noqa: E402

CONTRACT = "б/н от 04.03.2025"
IP_INN = "370212932304"
IP_NAME = "Индивидуальный предприниматель ВЯТКИН ВЛАДИСЛАВ ВЛАДИМИРОВИЧ"
PV_INN = "1800027275"
PV_NAME = 'ОБЩЕСТВО С ОГРАНИЧЕННОЙ ОТВЕТСТВЕННОСТЬЮ "ПЛЮС ВАЙБ"'

# Ставка: 2 % до 31.10.2025, 27 % с 01.11.2025 (подтверждено фактическими
# выплатами процентов — 210 682,08 за 04.03–30.09.2025 и 82 252,55 за октябрь).
RATES = [
    ("2025-03-04", "0.02", "ставка по договору"),
    ("2025-11-01", "0.27", "повышение ставки с 01.11.2025"),
]

# Фактически уплаченные «Плюс Вайб» проценты (обе выплаты — из выписок ИП).
INTEREST_PAID = [
    ("2025-10-23", "210682.08"),  # за период 04.03.2025 — 30.09.2025
    ("2025-11-12", "82252.55"),   # за октябрь 2025
]

# Движения тела из карточки 67.03: «+» — «Плюс Вайб» получил, «−» — вернул.
MOVES: list[tuple[str, str, str]] = [
    ("2025-03-05", "830000", "+"),
    ("2025-03-18", "303000", "+"),
    ("2025-03-25", "28000", "+"),
    ("2025-03-26", "40000", "+"),
    ("2025-04-07", "35000", "+"),
    ("2025-04-07", "550000", "+"),
    ("2025-04-08", "1100000", "+"),
    ("2025-04-08", "1900000", "+"),
    ("2025-04-09", "935000", "+"),
    ("2025-04-09", "3205000", "+"),
    ("2025-04-16", "13000", "+"),
    ("2025-04-28", "2527000", "+"),
    ("2025-05-07", "22000", "+"),
    ("2025-05-16", "1100000", "+"),
    ("2025-05-16", "2000000", "+"),
    ("2025-05-21", "2600000", "+"),
    ("2025-05-30", "125000", "+"),
    ("2025-06-04", "600000", "+"),
    ("2025-06-05", "2638000", "+"),
    ("2025-06-19", "600000", "+"),
    ("2025-06-27", "1040000", "+"),
    ("2025-06-27", "2140000", "+"),
    ("2025-07-01", "1000000", "-"),
    ("2025-07-17", "710000", "+"),
    ("2025-08-05", "2750000", "-"),
    ("2025-08-07", "100000", "+"),
    ("2025-08-21", "4890000", "+"),
    ("2025-08-27", "500000", "+"),
    ("2025-08-27", "643000", "+"),
    ("2025-08-29", "170000", "+"),
    ("2025-08-29", "600000", "+"),
    ("2025-09-08", "70000", "+"),
    ("2025-09-09", "140000", "+"),
    ("2025-09-09", "280000", "+"),
    ("2025-09-09", "2100000", "+"),
    ("2025-09-10", "700000", "+"),
    ("2025-09-10", "1200000", "+"),
    ("2025-09-11", "35000", "+"),
    ("2025-09-11", "82000", "+"),
    ("2025-09-12", "85000", "+"),
    ("2025-09-15", "3600000", "+"),
    ("2025-09-16", "200000", "+"),
    ("2025-09-16", "930000", "+"),
    ("2025-09-19", "540000", "+"),
    ("2025-09-19", "3000000", "+"),
    ("2025-09-19", "3000000", "+"),
    ("2025-09-22", "28000", "+"),
    ("2025-09-23", "70000", "+"),
    ("2025-09-25", "3150000", "+"),
    ("2025-09-29", "80000", "+"),
    ("2025-09-30", "95000", "+"),
    ("2025-10-13", "600000", "+"),
    ("2025-10-24", "1800000", "+"),
    ("2025-10-27", "690000", "+"),
    ("2025-10-28", "5730000", "+"),
    ("2025-10-31", "35000", "+"),
    ("2025-10-31", "100000", "+"),
    ("2025-10-31", "170000", "+"),
    ("2025-11-06", "4000000", "-"),
    ("2025-11-13", "970000", "-"),
    ("2025-11-17", "750000", "-"),
    ("2025-11-18", "1260000", "-"),
    ("2025-11-20", "750000", "-"),
    ("2025-11-26", "4000000", "+"),
    ("2025-12-03", "4000000", "-"),
    ("2025-12-05", "4400000", "-"),
    ("2025-12-11", "450000", "+"),
    ("2025-12-11", "450000", "+"),
    ("2025-12-15", "10300000", "-"),
    ("2025-12-19", "4000000", "+"),
    ("2025-12-23", "1500000", "+"),
    ("2025-12-30", "6000000", "+"),
    ("2026-01-30", "1700000", "+"),
    ("2026-02-09", "1900000", "-"),
    ("2026-02-10", "1400000", "+"),
    ("2026-02-24", "880000", "+"),
    ("2026-02-24", "1300000", "+"),
    ("2026-02-27", "4200000", "+"),
    ("2026-03-03", "1000000", "+"),
    ("2026-03-25", "1000000", "+"),
    ("2026-03-27", "100000", "+"),
    ("2026-04-17", "300000", "+"),
    ("2026-04-20", "4600000", "-"),
    ("2026-04-22", "1550000", "-"),
    ("2026-04-24", "4400000", "+"),
    ("2026-04-28", "1100000", "+"),
    ("2026-04-28", "2100000", "+"),
    ("2026-04-29", "548000", "-"),
    ("2026-04-30", "200000", "-"),
    ("2026-05-02", "100000", "-"),
    ("2026-05-05", "1100000", "-"),
    ("2026-05-07", "350000", "+"),
    ("2026-05-12", "190000", "-"),
    ("2026-05-13", "90000", "+"),
    ("2026-05-13", "350000", "+"),
    ("2026-05-18", "900000", "+"),
    ("2026-05-18", "900000", "+"),
    ("2026-05-18", "2850000", "+"),
    ("2026-05-19", "250000", "+"),
    ("2026-05-19", "75000", "+"),
    ("2026-05-22", "200000", "-"),
    ("2026-05-25", "102000", "+"),
    ("2026-05-25", "2500000", "-"),
    ("2026-05-27", "12300000", "-"),
    ("2026-06-01", "800000", "-"),
    ("2026-06-02", "13800000", "-"),
    ("2026-06-03", "1500000", "-"),
    ("2026-06-03", "80000", "-"),
    ("2026-06-04", "1000", "-"),
    ("2026-06-16", "2000000", "+"),
    ("2026-06-16", "3200000", "+"),
    ("2026-06-25", "499900", "-"),
    ("2026-06-29", "1507000", "-"),
    ("2026-06-30", "550000", "+"),
    ("2026-07-01", "200000", "-"),
    ("2026-07-03", "300000", "+"),
    ("2026-07-21", "2800000", "-"),
    ("2026-07-21", "600000", "-"),
]


def _d(iso: str) -> date:
    return date.fromisoformat(iso)


async def _counterparty(db: AsyncSession, project_id: int, inn: str, name: str) -> Counterparty:
    cp = (
        await db.execute(
            select(Counterparty).where(
                Counterparty.project_id == project_id, Counterparty.inn == inn
            )
        )
    ).scalar_one_or_none()
    if cp is not None:
        if cp.is_deleted:
            cp.restore()
        print(f"  контрагент {cp.name[:40]} (id {cp.id}) — уже есть")
        return cp
    cp = Counterparty(project_id=project_id, inn=inn, name=name, primary_type="AFFILIATED")
    db.add(cp)
    await db.flush()
    print(f"  контрагент {name[:40]} — создан (id {cp.id})")
    return cp


def _make_loan(project_id: int, cp_id: int, direction: str) -> Loan:
    return Loan(
        project_id=project_id,
        counterparty_id=cp_id,
        direction=direction,
        # Тело револьверное: растёт на выдачах, падает на возвратах. Само поле
        # `principal` символическое — деньги приходят траншами.
        principal=Decimal("0"),
        currency="RUB",
        rate=Decimal("0.27"),
        contract_number=CONTRACT,
        contract_date=_d("2025-03-04"),
        start_date=_d("2025-03-04"),
        maturity_date=None,  # до востребования
        status="ACTIVE",
        entity_type="IP",
        # Начисление по календарному месяцу: так считает и бухгалтерия «Плюс Вайб»,
        # и наш ОПиУ. Дня платежа нет — проценты платят по факту.
        accrual_kind="CALENDAR_MONTH",
        notes=(
            "Займ учредителя (ИП Вяткин) в ООО «Плюс Вайб», револьверный, до востребования. "
            "Ставка 2 % до 31.10.2025, 27 % с 01.11.2025. Движения — из карточки счёта 67.03, "
            "сверены с выписками ИП. Финпомощь 2 047 000 ₽ (11.02 и 17.02.2025) в тело не входит."
        ),
    )


def _add_movements(db: AsyncSession, loan: Loan) -> None:
    for iso, amount, sign in MOVES:
        db.add(
            LoanPayment(
                loan_id=loan.id,
                payment_type="DISBURSEMENT" if sign == "+" else "PRINCIPAL_REPAY",
                amount=Decimal(amount),
                currency="RUB",
                paid_at=_d(iso),
            )
        )
    for iso, amount in INTEREST_PAID:
        db.add(
            LoanPayment(
                loan_id=loan.id,
                payment_type="INTEREST_PAY",
                amount=Decimal(amount),
                currency="RUB",
                paid_at=_d(iso),
            )
        )
    for iso, rate, note in RATES:
        db.add(
            LoanRatePeriod(
                loan_id=loan.id, valid_from=_d(iso), rate=Decimal(rate), note=note
            )
        )


async def seed(pv_project: int, ip_project: int, *, commit: bool) -> None:
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(Loan).where(
                    Loan.project_id == pv_project,
                    Loan.contract_number == CONTRACT,
                    Loan.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            print(f"Займ «{CONTRACT}» уже заведён (id {existing.id}) — пропускаю")
            return

        print("Проект «Плюс Вайб» (полученный займ):")
        ip_cp = await _counterparty(db, pv_project, IP_INN, IP_NAME)
        print("Проект ИП Вяткина (выданный займ):")
        pv_cp = await _counterparty(db, ip_project, PV_INN, PV_NAME)

        debt = _make_loan(pv_project, ip_cp.id, "INCOMING")
        asset = _make_loan(ip_project, pv_cp.id, "OUTGOING")
        db.add_all([debt, asset])
        await db.flush()
        debt.mirror_loan_id = asset.id
        asset.mirror_loan_id = debt.id
        _add_movements(db, debt)
        _add_movements(db, asset)
        await db.flush()

        drawn = sum(Decimal(a) for _, a, s in MOVES if s == "+")
        repaid = sum(Decimal(a) for _, a, s in MOVES if s == "-")
        print()
        print(f"  движений тела:      {len(MOVES)}")
        print(f"  выдано:             {drawn:>16,.2f} ₽")
        print(f"  возвращено:         {repaid:>16,.2f} ₽")
        print(f"  остаток тела:       {drawn - repaid:>16,.2f} ₽")
        print(f"  уплачено процентов: {sum(Decimal(a) for _, a in INTEREST_PAID):>16,.2f} ₽")
        print(f"  займы: долг id {debt.id} (проект {pv_project}) ⇄ актив id {asset.id} (проект {ip_project})")

        if not commit:
            await db.rollback()
            print("\nСУХОЙ ПРОГОН — ничего не записано. Повторите с --commit.")
            return
        await db.commit()
        await invalidate_project_reports(pv_project)
        await invalidate_project_reports(ip_project)
        print("\n✓ Записано")


def main() -> None:
    ap = argparse.ArgumentParser(description="Займ ИП Вяткина в ООО «Плюс Вайб»")
    ap.add_argument("--pv-project", type=int, required=True, help="project_id «Плюс Вайб»")
    ap.add_argument("--ip-project", type=int, required=True, help="project_id ИП Вяткина")
    ap.add_argument("--commit", action="store_true", help="записать (иначе сухой прогон)")
    args = ap.parse_args()
    if args.pv_project == args.ip_project:
        raise SystemExit("Проекты должны быть разными")
    asyncio.run(seed(args.pv_project, args.ip_project, commit=args.commit))


if __name__ == "__main__":
    main()
