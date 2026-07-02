"""
Supplier debt: «Заказано» (загруженные машины) vs «Оплачено» (выписка) по контрагенту.

Долг считается НА УРОВНЕ ПОСТАВЩИКА/торгового дома (counterparty):
  Заказано = Σ (CostOrderItem.price_cny × qty) позиций в машинах со статусом ≠ FORMING
             (machine→factory_order→supplier→counterparty); в валюте поставщика.
  Оплачено = Σ transactions.expense по counterparty_id (без internal/fx/депозитов).
  Долг     = Заказано − Оплачено  (отрицательный = переплата/предоплата вперёд).

Позиции загруженных машин, не сводимые к контрагенту (нет factory_order-связки или у
поставщика не задан counterparty_id), идут в бакет «без поставщика» — не теряются молча.

⚠ ``total_cny`` на позиции НЕ используем — поле часто пустое/устаревшее; источник
истины суммы заказа = ``price_cny × qty``.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.cost import CostOrder, CostOrderItem
from backend.models.counterparty import Counterparty, CounterpartyIdentifier, IdentifierKind
from backend.models.enums import VehicleStatus
from backend.models.supply_chain import FactoryOrder, FactoryOrderItem, Supplier, SupplierMachinePlan
from backend.models.transactions import Transaction

_EXCLUDED_EVENT_TYPES = (
    "INTERNAL_TRANSFER",
    "FX_BUY",
    "DEPOSIT_PLACE",
    "DEPOSIT_RETURN",
    "DEPOSIT_INTEREST",
)
_LOADED_STATUSES = (
    VehicleStatus.SHIPPED.value,
    VehicleStatus.CUSTOMS.value,
    VehicleStatus.DISPATCHED.value,
    VehicleStatus.DELIVERED.value,
)

_ZERO = Decimal("0")
# Допуск на «близкую сумму» при авто-распределении: банк/курс/округление дают
# отклонение платежа от себестоимости машины в доли процента (583 853 vs 583 358).
_AMOUNT_TOLERANCE = Decimal("0.01")  # ±1%

# Склейка банковской комиссии за SWIFT с самим платежом за заказ:
# у платежа в назначении «PMNT <сумма> CNY», у комиссии «на сумму <та же сумма>».
_RE_PMNT = re.compile(r"PMNT\s*([\d\s., ]+?)\s*CNY", re.IGNORECASE)
_RE_SUMMA = re.compile(r"на\s+сумму\s*([\d\s., ]+?)\s*['\"]?\s*CNY", re.IGNORECASE)
_RE_COMMISSION = re.compile(r"комисси|commission|тариф|bank\s*charge", re.IGNORECASE)
# Форвардинг/логистика — не товар поставщика (идёт в межд. логистику, не в кандидаты).
_RE_FORWARDING = re.compile(r"forward|freight|форвард|фрахт|экспедиц", re.IGNORECASE)


def _is_forwarding(t: "Transaction") -> bool:
    return bool(_RE_FORWARDING.search(t.purpose or ""))


def _parse_cny_amount(raw: str | None) -> Decimal | None:
    """Parse a European-formatted CNY amount: «595 707,30» / «520899.6» → Decimal."""
    if not raw:
        return None
    s = raw.strip().replace(" ", "").replace(" ", "")
    if "," in s and "." in s:  # «1.234,56» → 1234.56
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:  # «595707,30» → 595707.30
        s = s.replace(",", ".")
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _payment_key_amount(t: Transaction) -> Decimal:
    """The amount a commission references for this payment (PMNT, fallback debit)."""
    m = _RE_PMNT.search(t.purpose or "")
    if m:
        amt = _parse_cny_amount(m.group(1))
        if amt:
            return amt
    return (t.expense or _ZERO).quantize(Decimal("0.01"))


def _is_commission(t: Transaction) -> bool:
    return bool(_RE_COMMISSION.search(t.purpose or ""))


class SupplierDebtService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_overview(self, project_id: int) -> dict:
        """Return per-counterparty ordered/paid/debt + an unassigned-ordered bucket."""
        # ── Оплачено: Σ expense по counterparty_id по валютам (реальные операции) ──
        paid: dict[int, dict[str, Decimal]] = {}
        res = await self.db.execute(
            select(
                Transaction.counterparty_id,
                Transaction.currency,
                func.coalesce(func.sum(Transaction.expense), 0),
            ).where(
                Transaction.project_id == project_id,
                Transaction.is_deleted.is_(False),
                Transaction.counterparty_id.isnot(None),
                Transaction.is_internal.is_(False),
                Transaction.is_fx.is_(False),
                Transaction.event_type2.notin_(_EXCLUDED_EVENT_TYPES),
                Transaction.expense > 0,
            ).group_by(Transaction.counterparty_id, Transaction.currency)
        )
        for cp_id, currency, total in res.all():
            paid.setdefault(cp_id, {})[currency or "RUB"] = Decimal(total or 0)

        # ── Заказано: Σ price_cny×qty загруженных машин, через supplier→counterparty ──
        ordered: dict[int, tuple[str, Decimal]] = {}  # cp_id -> (currency, amount)
        res = await self.db.execute(
            select(
                Supplier.counterparty_id,
                Supplier.currency,
                func.coalesce(func.sum(CostOrderItem.price_cny * CostOrderItem.qty), 0),
            )
            .select_from(CostOrderItem)
            .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
            .join(FactoryOrderItem, CostOrderItem.factory_order_item_id == FactoryOrderItem.id)
            .join(FactoryOrder, FactoryOrderItem.factory_order_id == FactoryOrder.id)
            .join(Supplier, FactoryOrder.supplier_id == Supplier.id)
            .where(
                CostOrderItem.project_id == project_id,
                CostOrderItem.is_deleted.is_(False),
                CostOrder.is_deleted.is_(False),
                CostOrder.status.in_(_LOADED_STATUSES),
                FactoryOrderItem.is_deleted.is_(False),
                FactoryOrder.is_deleted.is_(False),
                Supplier.is_deleted.is_(False),
                Supplier.counterparty_id.isnot(None),
            )
            .group_by(Supplier.counterparty_id, Supplier.currency)
        )
        for cp_id, currency, amount in res.all():
            ordered[cp_id] = (currency or "CNY", Decimal(amount or 0))

        # ── «Без поставщика»: загруженные позиции, не сводимые к контрагенту ──
        unassigned = Decimal(
            (
                await self.db.execute(
                    select(func.coalesce(func.sum(CostOrderItem.price_cny * CostOrderItem.qty), 0))
                    .select_from(CostOrderItem)
                    .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
                    .outerjoin(
                        FactoryOrderItem,
                        (CostOrderItem.factory_order_item_id == FactoryOrderItem.id)
                        & (FactoryOrderItem.is_deleted.is_(False)),
                    )
                    .outerjoin(
                        FactoryOrder,
                        (FactoryOrderItem.factory_order_id == FactoryOrder.id)
                        & (FactoryOrder.is_deleted.is_(False)),
                    )
                    .outerjoin(
                        Supplier,
                        (FactoryOrder.supplier_id == Supplier.id) & (Supplier.is_deleted.is_(False)),
                    )
                    .where(
                        CostOrderItem.project_id == project_id,
                        CostOrderItem.is_deleted.is_(False),
                        CostOrder.is_deleted.is_(False),
                        CostOrder.status.in_(_LOADED_STATUSES),
                        Supplier.counterparty_id.is_(None),
                    )
                )
            ).scalar()
            or 0
        )

        # ── Названия контрагентов ──
        cp_ids = set(paid) | set(ordered)
        names: dict[int, str] = {}
        if cp_ids:
            res = await self.db.execute(
                select(Counterparty.id, Counterparty.name, Counterparty.primary_type).where(
                    Counterparty.id.in_(cp_ids),
                    Counterparty.project_id == project_id,
                )
            )
            types: dict[int, str] = {}
            for cid, name, ptype in res.all():
                names[cid] = name
                types[cid] = ptype
        else:
            types = {}

        items = []
        for cp_id in sorted(cp_ids, key=lambda c: names.get(c, "")):
            ptype = types.get(cp_id, "OTHER")
            ocur, oamount = ordered.get(cp_id, ("", _ZERO))
            # Фокус «Долга поставщикам»: реальные поставщики/торгдома. Контрагенты с
            # одними оплатами без заказов (налоги, перевозчики, банки) — не здесь.
            if oamount == _ZERO and ptype not in ("SUPPLIER", "TRADING_HOUSE"):
                continue
            paid_map = paid.get(cp_id, {})
            currency = ocur or (next(iter(paid_map), "RUB"))
            paid_amt = paid_map.get(currency, _ZERO)
            items.append(
                {
                    "counterparty_id": cp_id,
                    "name": names.get(cp_id, f"#{cp_id}"),
                    "primary_type": types.get(cp_id, "OTHER"),
                    "currency": currency,
                    "ordered": oamount,
                    "paid": paid_amt,
                    "debt": oamount - paid_amt,
                    "paid_by_currency": {k: v for k, v in paid_map.items()},
                }
            )

        return {"items": items, "unassigned_ordered": unassigned}

    # ─── Per-supplier finance (card): debt + payments + link candidates ──

    async def _supplier_machines(
        self, project_id: int, supplier_id: int, cp_id: int | None, currency: str
    ) -> list[dict[str, Any]]:
        """Загруженные машины (CostOrder, статус≠FORMING) с товаром поставщика.

        Сумма = доля поставщика в машине (Σ price×qty). «Заказано» = Σ этих машин —
        тот же базис, что в общей таблице долгов (overview), чтобы цифры сходились.
        Оплачено по машине = Σ оплат с machine_order_no, ПО ТЕМ ЖЕ гейтам, что KPI
        «Оплачено» (без комиссий/депозитов/internal/fx, в валюте поставщика).
        """
        res = await self.db.execute(
            select(
                CostOrder.order_no,
                CostOrder.status,
                CostOrder.invoice_no,
                CostOrder.ship_date,
                func.coalesce(func.sum(CostOrderItem.price_cny * CostOrderItem.qty), 0),
                func.count(CostOrderItem.id),
            )
            .select_from(CostOrderItem)
            .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
            .join(FactoryOrderItem, CostOrderItem.factory_order_item_id == FactoryOrderItem.id)
            .join(FactoryOrder, FactoryOrderItem.factory_order_id == FactoryOrder.id)
            .where(
                CostOrderItem.project_id == project_id,
                CostOrderItem.is_deleted.is_(False),
                CostOrder.is_deleted.is_(False),
                CostOrder.status.in_(_LOADED_STATUSES),
                FactoryOrderItem.is_deleted.is_(False),
                FactoryOrder.is_deleted.is_(False),
                FactoryOrder.supplier_id == supplier_id,
            )
            .group_by(CostOrder.order_no, CostOrder.status, CostOrder.invoice_no, CostOrder.ship_date)
            .order_by(func.sum(CostOrderItem.price_cny * CostOrderItem.qty).desc())
        )
        rows = res.all()

        # Оплачено по машине — те же гейты, что KPI «Оплачено» (согласованность).
        paid_by_machine: dict[str, Decimal] = {}
        if cp_id is not None and rows:
            pm_res = await self.db.execute(
                select(Transaction.machine_order_no, func.coalesce(func.sum(Transaction.expense), 0))
                .where(
                    Transaction.project_id == project_id,
                    Transaction.is_deleted.is_(False),
                    Transaction.counterparty_id == cp_id,
                    Transaction.currency == currency,
                    Transaction.is_internal.is_(False),
                    Transaction.is_fx.is_(False),
                    Transaction.event_type2.notin_(_EXCLUDED_EVENT_TYPES),
                    Transaction.expense > 0,
                    Transaction.machine_order_no.isnot(None),
                )
                .group_by(Transaction.machine_order_no)
            )
            paid_by_machine = {str(r[0]): Decimal(r[1] or 0) for r in pm_res.all() if r[0]}

        # Плановые даты оплаты остатка (per поставщик, per машина).
        plan_res = await self.db.execute(
            select(SupplierMachinePlan.order_no, SupplierMachinePlan.remaining_due_date).where(
                SupplierMachinePlan.project_id == project_id,
                SupplierMachinePlan.supplier_id == supplier_id,
            )
        )
        due_by: dict[str, Any] = {str(o): d for o, d in plan_res.all()}

        out: list[dict[str, Any]] = []
        for r in rows:
            amount = Decimal(r[4] or 0)
            mp = paid_by_machine.get(str(r[0]), _ZERO)
            out.append(
                {
                    "order_no": r[0],
                    "status": r[1],
                    "invoice_no": r[2],
                    "ship_date": r[3],
                    "amount": amount,
                    "items_count": r[5],
                    "paid": mp,
                    "remaining": amount - mp,
                    "remaining_due_date": due_by.get(str(r[0])),
                }
            )
        return out

    @staticmethod
    def _txn_item(t: Transaction, commission: Decimal = _ZERO) -> dict:
        expense = t.expense or _ZERO
        return {
            "id": t.id,
            "date": t.date,
            "expense": expense,
            "commission": commission,
            "total": expense + commission,
            "currency": t.currency,
            "purpose": t.purpose,
            "contract_number": t.contract_number,
            "counterparty_name": t.counterparty,
            "machine_order_no": t.machine_order_no,
        }

    async def get_supplier_finance(self, project_id: int, supplier_id: int) -> dict:
        """Card finance hub: debt + attributed payments + unlinked candidates."""
        sup = (
            await self.db.execute(
                select(Supplier).where(
                    Supplier.id == supplier_id,
                    Supplier.project_id == project_id,
                    Supplier.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if sup is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Supplier not found")

        currency = sup.currency or "CNY"
        cp_id = sup.counterparty_id

        # Банковские комиссии за перевод (CNY) — для склейки с платежами; их же
        # исключаем из кандидатов (это сбор банка, а не платёж поставщику).
        comm_res = await self.db.execute(
            select(Transaction).where(
                Transaction.project_id == project_id,
                Transaction.is_deleted.is_(False),
                Transaction.currency == currency,
                Transaction.is_internal.is_(False),
                Transaction.is_fx.is_(False),
                Transaction.event_type2.notin_(_EXCLUDED_EVENT_TYPES),
                Transaction.expense > 0,
                # SQL-prefilter — суперсет _RE_COMMISSION, чтобы не тащить ВСЮ выписку
                # в валюте в память; точная проверка остаётся за _is_commission ниже.
                Transaction.purpose.op("~*")("комисси|commission|тариф|bank"),
            )
        )
        commissions = [t for t in comm_res.scalars().all() if _is_commission(t)]
        commission_ids = {t.id for t in commissions}
        # сумма-ключ → суммарная комиссия по этой сумме перевода
        comm_by_amount: dict[Decimal, Decimal] = {}
        for c in commissions:
            m = _RE_SUMMA.search(c.purpose or "")
            amt = _parse_cny_amount(m.group(1)) if m else None
            if amt is not None:
                comm_by_amount[amt] = comm_by_amount.get(amt, _ZERO) + (c.expense or _ZERO)

        payments: list[dict] = []
        paid = _ZERO
        commission_total = _ZERO
        if cp_id is not None:
            pay_res = await self.db.execute(
                select(Transaction)
                .where(
                    Transaction.project_id == project_id,
                    Transaction.is_deleted.is_(False),
                    Transaction.counterparty_id == cp_id,
                    Transaction.currency == currency,
                    Transaction.is_internal.is_(False),
                    Transaction.is_fx.is_(False),
                    Transaction.event_type2.notin_(_EXCLUDED_EVENT_TYPES),
                    Transaction.expense > 0,
                )
                .order_by(Transaction.date.desc())
            )
            for t in pay_res.scalars().all():
                if t.id in commission_ids:
                    continue  # комиссия не отдельный платёж
                comm = comm_by_amount.pop(_payment_key_amount(t), _ZERO)
                payments.append(self._txn_item(t, comm))
            paid = sum((p["expense"] for p in payments), _ZERO)
            commission_total = sum((p["commission"] for p in payments), _ZERO)

        # Идентификаторы этого поставщика (контракты/счета) — блок настроек.
        identifiers: list[dict] = []
        if cp_id is not None:
            id_res = await self.db.execute(
                select(CounterpartyIdentifier)
                .where(
                    CounterpartyIdentifier.project_id == project_id,
                    CounterpartyIdentifier.counterparty_id == cp_id,
                    CounterpartyIdentifier.is_deleted.is_(False),
                )
                .order_by(CounterpartyIdentifier.kind, CounterpartyIdentifier.value)
            )
            identifiers = [
                {"id": i.id, "kind": i.kind, "value": i.value, "currency": i.currency}
                for i in id_res.scalars().all()
            ]

        # Контракты, закреплённые за ДРУГИМИ контрагентами — их платежи прячем из
        # кандидатов (умный фильтр: показываем только «этот поставщик или орфан»).
        other_q = select(CounterpartyIdentifier.value).where(
            CounterpartyIdentifier.project_id == project_id,
            CounterpartyIdentifier.kind == IdentifierKind.CONTRACT.value,
            CounterpartyIdentifier.is_deleted.is_(False),
        )
        if cp_id is not None:
            other_q = other_q.where(CounterpartyIdentifier.counterparty_id != cp_id)
        other_contracts = {v for (v,) in (await self.db.execute(other_q)).all() if v}

        # Кандидаты на ручную привязку: исходящие в валюте поставщика, не привязанные
        # (или на банке), БЕЗ комиссий, БЕЗ форвардинга, БЕЗ чужих контрактов.
        cand_res = await self.db.execute(
            select(Transaction)
            .outerjoin(Counterparty, Transaction.counterparty_id == Counterparty.id)
            .where(
                Transaction.project_id == project_id,
                Transaction.is_deleted.is_(False),
                Transaction.currency == currency,
                Transaction.is_internal.is_(False),
                Transaction.is_fx.is_(False),
                Transaction.event_type2.notin_(_EXCLUDED_EVENT_TYPES),
                Transaction.expense > 0,
                or_(Transaction.counterparty_id.is_(None), Counterparty.primary_type == "BANK"),
            )
            .order_by(Transaction.date.desc())
            .limit(120)
        )
        candidates = [
            self._txn_item(t)
            for t in cand_res.scalars().all()
            if t.id not in commission_ids
            and not _is_forwarding(t)
            and (t.contract_number or "").strip() not in other_contracts
        ]

        # Заказ = МАШИНЫ (CostOrder) с товаром поставщика — фактический заказ
        # (а не фабричный план). «Заказано» = Σ доли поставщика во всех его машинах.
        machines = await self._supplier_machines(project_id, supplier_id, cp_id, currency)
        ordered = sum((Decimal(m["amount"]) for m in machines), _ZERO)

        return {
            "supplier_id": supplier_id,
            "supplier_name": sup.name,
            "currency": currency,
            "linked": cp_id is not None,
            "counterparty_id": cp_id,
            "ordered": ordered,
            "paid": paid,
            "commission_total": commission_total,
            "debt": ordered - paid,
            "identifiers": identifiers,
            "machines": machines,
            "payments": payments,
            "unlinked_candidates": candidates[:50],
        }

    async def _ensure_supplier_counterparty(self, project_id: int, sup: Supplier) -> int:
        """Find-or-create a Counterparty for the supplier and link it."""
        if sup.counterparty_id is not None:
            return sup.counterparty_id
        existing = (
            await self.db.execute(
                select(Counterparty).where(
                    Counterparty.project_id == project_id,
                    Counterparty.name == sup.name,
                    Counterparty.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = Counterparty(
                project_id=project_id,
                name=sup.name,
                primary_type="SUPPLIER",
                inn=sup.inn,
            )
            self.db.add(existing)
            await self.db.flush()
        sup.counterparty_id = existing.id
        return existing.id

    async def link_payment(self, project_id: int, supplier_id: int, transaction_id: int) -> dict:
        """Manually attribute a statement transaction to this supplier."""
        from fastapi import HTTPException

        sup = (
            await self.db.execute(
                select(Supplier).where(
                    Supplier.id == supplier_id,
                    Supplier.project_id == project_id,
                    Supplier.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if sup is None:
            raise HTTPException(status_code=404, detail="Supplier not found")
        txn = (
            await self.db.execute(
                select(Transaction).where(
                    Transaction.id == transaction_id,
                    Transaction.project_id == project_id,
                    Transaction.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if txn is None:
            raise HTTPException(status_code=404, detail="Transaction not found")

        cp_id = await self._ensure_supplier_counterparty(project_id, sup)
        txn.counterparty_id = cp_id
        await self.db.commit()
        from backend.cache import invalidate_project_reports

        await invalidate_project_reports(project_id)
        return {"linked": True, "transaction_id": transaction_id, "counterparty_id": cp_id}

    async def unlink_payment(self, project_id: int, supplier_id: int, transaction_id: int) -> dict:
        """Detach a previously-attributed payment from this supplier (undo)."""
        from fastapi import HTTPException

        sup = (
            await self.db.execute(
                select(Supplier).where(
                    Supplier.id == supplier_id,
                    Supplier.project_id == project_id,
                    Supplier.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if sup is None:
            raise HTTPException(status_code=404, detail="Supplier not found")
        txn = (
            await self.db.execute(
                select(Transaction).where(
                    Transaction.id == transaction_id,
                    Transaction.project_id == project_id,
                    Transaction.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if txn is None:
            raise HTTPException(status_code=404, detail="Transaction not found")
        # Отвязываем только если платёж сейчас висит на ЭТОМ поставщике.
        if sup.counterparty_id is not None and txn.counterparty_id == sup.counterparty_id:
            txn.counterparty_id = None
            await self.db.commit()
            from backend.cache import invalidate_project_reports

            await invalidate_project_reports(project_id)
        return {"unlinked": True, "transaction_id": transaction_id}

    async def assign_payment_machine(
        self, project_id: int, supplier_id: int, transaction_id: int, order_no: str | None
    ) -> dict:
        """Привязать оплату к конкретной машине (order_no=None → снять привязку)."""
        from fastapi import HTTPException

        sup = await self._get_supplier(project_id, supplier_id)
        txn = (
            await self.db.execute(
                select(Transaction).where(
                    Transaction.id == transaction_id,
                    Transaction.project_id == project_id,
                    Transaction.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if txn is None:
            raise HTTPException(status_code=404, detail="Transaction not found")
        if order_no and order_no.strip():
            cp_id = await self._ensure_supplier_counterparty(project_id, sup)
            txn.counterparty_id = cp_id  # привязка к машине подразумевает привязку к поставщику
            txn.machine_order_no = order_no.strip()
        else:
            txn.machine_order_no = None
        await self.db.commit()
        from backend.cache import invalidate_project_reports

        await invalidate_project_reports(project_id)
        return await self.get_supplier_finance(project_id, supplier_id)

    async def assign_payments_machine_bulk(
        self, project_id: int, supplier_id: int, transaction_ids: list[int], order_no: str | None
    ) -> dict:
        """Привязать НЕСКОЛЬКО оплат к одной машине (депозит+доплата) одним действием.

        order_no=None → снять привязку к машине у всех выбранных.
        """
        sup = await self._get_supplier(project_id, supplier_id)
        target = order_no.strip() if order_no and order_no.strip() else None
        cp_id = await self._ensure_supplier_counterparty(project_id, sup) if target else None

        res = await self.db.execute(
            select(Transaction).where(
                Transaction.id.in_(transaction_ids[:200]),
                Transaction.project_id == project_id,
                Transaction.is_deleted.is_(False),
            )
        )
        for txn in res.scalars().all():
            if target is not None:
                txn.counterparty_id = cp_id  # привязка к машине ⇒ привязка к поставщику
                txn.machine_order_no = target
            else:
                txn.machine_order_no = None
        await self.db.commit()
        from backend.cache import invalidate_project_reports

        await invalidate_project_reports(project_id)
        return await self.get_supplier_finance(project_id, supplier_id)

    async def set_machine_plan(
        self, project_id: int, supplier_id: int, order_no: str, remaining_due_date: date | None
    ) -> dict:
        """Upsert плановой даты оплаты остатка по машине для поставщика (None = снять)."""
        await self._get_supplier(project_id, supplier_id)  # 404 + project-scope guard
        order_no = (order_no or "").strip()
        existing = (
            await self.db.execute(
                select(SupplierMachinePlan).where(
                    SupplierMachinePlan.project_id == project_id,
                    SupplierMachinePlan.supplier_id == supplier_id,
                    SupplierMachinePlan.order_no == order_no,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.remaining_due_date = remaining_due_date
        else:
            self.db.add(
                SupplierMachinePlan(
                    project_id=project_id,
                    supplier_id=supplier_id,
                    order_no=order_no,
                    remaining_due_date=remaining_due_date,
                )
            )
        await self.db.commit()
        return await self.get_supplier_finance(project_id, supplier_id)

    async def auto_distribute_machines(self, project_id: int, supplier_id: int) -> dict:
        """Авто-привязка неразмеченных оплат к машинам — ТОЛЬКО однозначные совпадения.

        Ключи по приоритету: (1) инвойс — transaction.invoice_id ↔ machine invoice_no,
        нормализованные (CC↔СС, склейка «+»); (2) точная сумма — == сумме машины ИЛИ её
        остатку (поддержка депозит+доплата). Неоднозначные/непонятные не трогаем — их
        оператор разнесёт вручную. Возвращает {assigned, finance}.
        """
        from backend.etl.master_logic import normalize_invoice_no

        sup = await self._get_supplier(project_id, supplier_id)
        cp_id = sup.counterparty_id
        if cp_id is None:
            finance = await self.get_supplier_finance(project_id, supplier_id)
            return {"assigned": 0, "finance": finance}
        currency = sup.currency or "CNY"

        machines = await self._supplier_machines(project_id, supplier_id, cp_id, currency)
        # Индексы: нормализ. инвойс-токен → order_no; остаток/сумма по машине.
        inv_index: dict[str, set[str]] = {}
        remaining_by: dict[str, Decimal] = {}
        amount_by: dict[str, Decimal] = {}
        for m in machines:
            order_no = str(m["order_no"])
            remaining_by[order_no] = Decimal(str(m["remaining"]))
            amount_by[order_no] = Decimal(str(m["amount"]))
            for tok in normalize_invoice_no(m.get("invoice_no")):
                inv_index.setdefault(tok, set()).add(order_no)

        # Неразмеченные оплаты поставщика (machine_order_no IS NULL), по дате (депозиты раньше).
        pay_res = await self.db.execute(
            select(Transaction)
            .where(
                Transaction.project_id == project_id,
                Transaction.is_deleted.is_(False),
                Transaction.counterparty_id == cp_id,
                Transaction.currency == currency,
                Transaction.is_internal.is_(False),
                Transaction.is_fx.is_(False),
                Transaction.event_type2.notin_(_EXCLUDED_EVENT_TYPES),
                Transaction.expense > 0,
                Transaction.machine_order_no.is_(None),
            )
            .order_by(Transaction.date.asc())
            .limit(500)
        )
        assigned = 0
        for t in pay_res.scalars().all():
            if _is_commission(t):
                continue
            amt = t.expense or _ZERO
            target: str | None = None
            # 1) по инвойсу — только если ОДНА машина несёт этот токен
            for tok in normalize_invoice_no(t.invoice_id):
                cands = inv_index.get(tok)
                if cands and len(cands) == 1:
                    target = next(iter(cands))
                    break
            # 2) по сумме (полная ИЛИ остаток): сначала точное, иначе ±tolerance.
            #    Привязываем ТОЛЬКО если в допуск попала РОВНО одна машина (иначе — оператору).
            if target is None:
                tol = amt * _AMOUNT_TOLERANCE
                full = {o for o, a in amount_by.items() if a == amt} or {
                    o for o, a in amount_by.items() if abs(a - amt) <= tol
                }
                rem = {
                    o for o, r in remaining_by.items()
                    if r > 0 and (r == amt or abs(r - amt) <= tol)
                }
                cands = full or rem
                if len(cands) == 1:
                    target = next(iter(cands))
            if target is not None:
                t.machine_order_no = target
                remaining_by[target] = remaining_by.get(target, _ZERO) - amt
                assigned += 1

        if assigned:
            await self.db.commit()
            from backend.cache import invalidate_project_reports

            await invalidate_project_reports(project_id)
        finance = await self.get_supplier_finance(project_id, supplier_id)
        return {"assigned": assigned, "finance": finance}

    # ─── Supplier identifiers (settings): contract / account / inn ──────

    async def _get_supplier(self, project_id: int, supplier_id: int) -> Supplier:
        from fastapi import HTTPException

        sup = (
            await self.db.execute(
                select(Supplier).where(
                    Supplier.id == supplier_id,
                    Supplier.project_id == project_id,
                    Supplier.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if sup is None:
            raise HTTPException(status_code=404, detail="Supplier not found")
        return sup

    async def add_identifier(
        self, project_id: int, supplier_id: int, kind: str, value: str, currency: str | None = None
    ) -> dict:
        """Register a contract/account/inn for the supplier and auto-attribute matching payments."""
        from fastapi import HTTPException

        if kind not in (IdentifierKind.CONTRACT.value, IdentifierKind.ACCOUNT.value, IdentifierKind.INN.value):
            raise HTTPException(status_code=400, detail="Invalid identifier kind")
        value = (value or "").strip()
        if not value:
            raise HTTPException(status_code=400, detail="Empty identifier value")

        sup = await self._get_supplier(project_id, supplier_id)
        cp_id = await self._ensure_supplier_counterparty(project_id, sup)

        # Может сосуществовать живая + soft-deleted строка с тем же (project,kind,value)
        # (partial-unique только WHERE is_deleted=false) → берём ЖИВУЮ первой, не падаем.
        existing = (
            await self.db.execute(
                select(CounterpartyIdentifier)
                .where(
                    CounterpartyIdentifier.project_id == project_id,
                    CounterpartyIdentifier.kind == kind,
                    CounterpartyIdentifier.value == value,
                )
                .order_by(CounterpartyIdentifier.is_deleted.asc())
            )
        ).scalars().first()
        if existing is not None:
            # Активный ключ уже закреплён за ДРУГИМ контрагентом — не отбираем молча.
            if not existing.is_deleted and existing.counterparty_id != cp_id:
                raise HTTPException(
                    status_code=409,
                    detail=f"Идентификатор уже закреплён за другим контрагентом (#{existing.counterparty_id})",
                )
            existing.restore()
            existing.counterparty_id = cp_id
            existing.currency = currency
        else:
            self.db.add(
                CounterpartyIdentifier(
                    project_id=project_id, counterparty_id=cp_id, kind=kind, value=value, currency=currency
                )
            )

        # Авто-привязка совпавших платежей к этому поставщику (контракт/счёт/ИНН —
        # авторитетный ключ: перебиваем NULL/банк/чужую привязку).
        col = {
            IdentifierKind.CONTRACT.value: Transaction.contract_number,
            IdentifierKind.ACCOUNT.value: Transaction.counterparty_account,
            IdentifierKind.INN.value: Transaction.inn,
        }[kind]
        # Перебиваем только НЕпривязанные (NULL) или висящие на БАНКЕ — ручную
        # привязку к другому контрагенту не отбираем (уважаем решение оператора).
        bank_ids = (
            select(Counterparty.id)
            .where(Counterparty.project_id == project_id, Counterparty.primary_type == "BANK")
            .scalar_subquery()
        )
        await self.db.execute(
            update(Transaction)
            .where(
                Transaction.project_id == project_id,
                Transaction.is_deleted.is_(False),
                col == value,
                Transaction.is_internal.is_(False),
                Transaction.is_fx.is_(False),
                Transaction.event_type2.notin_(_EXCLUDED_EVENT_TYPES),
                or_(Transaction.counterparty_id.is_(None), Transaction.counterparty_id.in_(bank_ids)),
            )
            .values(counterparty_id=cp_id)
        )
        await self.db.commit()
        from backend.cache import invalidate_project_reports

        await invalidate_project_reports(project_id)
        return await self.get_supplier_finance(project_id, supplier_id)

    async def delete_identifier(self, project_id: int, supplier_id: int, identifier_id: int) -> dict:
        """Soft-delete an identifier (keeps already-attributed payments)."""
        sup = await self._get_supplier(project_id, supplier_id)
        if sup.counterparty_id is not None:
            ident = (
                await self.db.execute(
                    select(CounterpartyIdentifier).where(
                        CounterpartyIdentifier.id == identifier_id,
                        CounterpartyIdentifier.counterparty_id == sup.counterparty_id,
                        CounterpartyIdentifier.project_id == project_id,
                        CounterpartyIdentifier.is_deleted.is_(False),
                    )
                )
            ).scalar_one_or_none()
            if ident is not None:
                ident.soft_delete()
                await self.db.commit()
                from backend.cache import invalidate_project_reports

                await invalidate_project_reports(project_id)
        return await self.get_supplier_finance(project_id, supplier_id)
