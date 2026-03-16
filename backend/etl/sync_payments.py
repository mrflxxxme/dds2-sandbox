"""ETL sync: customs topup + plan payments auto-matching.

Extracted from etl/service.py for maintainability.
"""

import re as _re
import logging
from decimal import Decimal, InvalidOperation

import structlog
from sqlalchemy.orm import Session
from sqlalchemy import select, text

from backend.models import (
    Transaction, CustomsTopup, PlannedPayment, CustomsAlloc,
    CustomsDT, PaymentFactLink, CostOrder,
)

logger = structlog.get_logger("dds.etl")


def sync_customs_topup(db: Session, project_id: int):
    """Sync customs topup records from CUSTOMS_PAYMENT transactions."""
    customs_txns = db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.event_type2 == "CUSTOMS_PAYMENT",
            Transaction.expense > 0,
        )
    ).scalars().all()

    existing_ids = set(
        row[0] for row in db.execute(
            text("SELECT topup_txn_id FROM customs_topup WHERE project_id = :pid"),
            {"pid": project_id},
        )
    )

    for txn in customs_txns:
        if txn.txn_id not in existing_ids:
            topup = CustomsTopup(
                project_id=project_id,
                topup_txn_id=txn.txn_id,
                date=txn.date.date(),
                amount_rub=txn.expense,
                purpose=txn.purpose,
                account=txn.account,
                counterparty_account=txn.counterparty_account,
            )
            db.add(topup)


def sync_plan_payments(db: Session, project_id: int):
    """Auto-match planned payments with fact from bank statements and customs_alloc.

    Matching rules:
    - ЗАКАЗ:    Transaction.annex_id == str(order_no) AND purpose_tag == 'Заказ'  → sum(expense)
    - ДОСТАВКА: Transaction.invoice_id == CostOrder.invoice_no AND purpose_tag == 'Логистика' → sum(expense)
    - ТАМОЖНЯ:  CustomsAlloc.order_no == order_no → sum(alloc_amount)
    """
    payments = db.execute(
        select(PlannedPayment).where(PlannedPayment.project_id == project_id)
    ).scalars().all()
    if not payments:
        return

    order_nos = {p.order_no for p in payments if p.order_no is not None}
    if not order_nos:
        return

    # Build invoice_no map: order_no_int → invoice_no (str)
    cost_orders = db.execute(
        select(CostOrder).where(CostOrder.project_id == project_id)
    ).scalars().all()
    from backend.services.cost.helpers import _order_no_to_int
    invoice_map = {}
    for co in cost_orders:
        try:
            invoice_map[_order_no_to_int(co.order_no)] = co.invoice_no
        except (ValueError, TypeError):
            pass

    # ЗАКАЗ fact
    order_fact_ccy = {}
    txns_order = db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.purpose_tag == "Заказ",
            Transaction.expense > 0,
        )
    ).scalars().all()
    for t in txns_order:
        if t.annex_id:
            try:
                ono = _order_no_to_int(t.annex_id)
                if ono not in order_fact_ccy:
                    order_fact_ccy[ono] = {}
                ccy = (t.currency or "RUB").upper()
                order_fact_ccy[ono][ccy] = order_fact_ccy[ono].get(ccy, Decimal("0")) + (t.expense or Decimal("0"))
            except (ValueError, TypeError):
                pass

    # ДОСТАВКА fact
    delivery_fact_ccy = {}
    txns_delivery = db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.purpose_tag == "Логистика",
            Transaction.expense > 0,
        )
    ).scalars().all()

    def _norm_invoice(s: str) -> str:
        return s.replace("С", "C").replace("с", "c").strip() if s else ""
    inv_to_order = {_norm_invoice(v): k for k, v in invoice_map.items() if v}
    for t in txns_delivery:
        inv_key = _norm_invoice(t.invoice_id) if t.invoice_id else ""
        if inv_key and inv_key in inv_to_order:
            ono = inv_to_order[inv_key]
            if ono not in delivery_fact_ccy:
                delivery_fact_ccy[ono] = {}
            ccy = (t.currency or "RUB").upper()
            delivery_fact_ccy[ono][ccy] = delivery_fact_ccy[ono].get(ccy, Decimal("0")) + (t.expense or Decimal("0"))

    # VTB Commission matching
    _re_vtb_commission_amount = _re.compile(
        r"ВТБ Шанхай.*?на сумму\s+([\d.,\s]+)\s*['\"]?CNY", _re.IGNORECASE
    )
    _re_pmnt_amount = _re.compile(
        r"PMNT\s+([\d\s,.]+)\s*CNY", _re.IGNORECASE
    )

    amount_to_payment = {}
    for p in payments:
        if p.amount and p.order_no:
            direction = (p.direction or "").upper()
            if direction in ("ЗАКАЗ", "ДОСТАВКА"):
                amount_to_payment[p.amount] = (p.order_no, direction)

    pmnt_to_order = {}
    for t in txns_order:
        if t.annex_id and t.purpose:
            pm = _re_pmnt_amount.search(t.purpose)
            if pm:
                try:
                    pmnt_raw = pm.group(1).replace(" ", "").replace(",", ".")
                    pmnt_amt = Decimal(pmnt_raw)
                    ono = _order_no_to_int(t.annex_id)
                    pmnt_to_order[pmnt_amt] = ono
                except (ValueError, TypeError, InvalidOperation):
                    pass

    txns_commission = db.execute(
        select(Transaction).where(
            Transaction.project_id == project_id,
            Transaction.purpose_tag == "Комиссия",
            Transaction.expense > 0,
            Transaction.purpose.ilike("%ВТБ Шанхай%"),
        )
    ).scalars().all()
    for t in txns_commission:
        purpose = t.purpose or ""
        m = _re_vtb_commission_amount.search(purpose)
        if m:
            try:
                raw_amount = m.group(1).replace(" ", "").replace(",", ".")
                comm_ref_amount = Decimal(raw_amount)

                matched = amount_to_payment.get(comm_ref_amount)
                matched_order = None
                matched_dir = None
                if matched:
                    matched_order, matched_dir = matched
                else:
                    fallback_order = pmnt_to_order.get(comm_ref_amount)
                    if fallback_order:
                        matched_order = fallback_order
                        matched_dir = "ЗАКАЗ"

                if matched_order and matched_dir:
                    ccy = (t.currency or "RUB").upper()
                    if matched_dir == "ЗАКАЗ":
                        if matched_order not in order_fact_ccy:
                            order_fact_ccy[matched_order] = {}
                        order_fact_ccy[matched_order][ccy] = order_fact_ccy[matched_order].get(ccy, Decimal("0")) + (t.expense or Decimal("0"))
                    elif matched_dir == "ДОСТАВКА":
                        if matched_order not in delivery_fact_ccy:
                            delivery_fact_ccy[matched_order] = {}
                        delivery_fact_ccy[matched_order][ccy] = delivery_fact_ccy[matched_order].get(ccy, Decimal("0")) + (t.expense or Decimal("0"))
            except (ValueError, InvalidOperation):
                pass

    # ТАМОЖНЯ fact
    customs_fact = {}
    allocs = db.execute(
        select(CustomsAlloc).where(CustomsAlloc.project_id == project_id)
    ).scalars().all()
    for a in allocs:
        if a.order_no:
            customs_fact[a.order_no] = customs_fact.get(a.order_no, Decimal("0")) + (a.alloc_amount or Decimal("0"))

    dts = db.execute(
        select(CustomsDT).where(
            CustomsDT.project_id == project_id,
            CustomsDT.order_no.isnot(None),
        )
    ).scalars().all()
    for d in dts:
        customs_fact[d.order_no] = customs_fact.get(d.order_no, Decimal("0")) + (d.amount_rub or Decimal("0"))

    # Manual fact links
    manual_links = db.execute(
        select(PaymentFactLink).join(
            PlannedPayment, PaymentFactLink.payment_id == PlannedPayment.id
        ).where(PlannedPayment.project_id == project_id)
    ).scalars().all()
    manual_map = {}
    for ml in manual_links:
        manual_map[ml.payment_id] = manual_map.get(ml.payment_id, Decimal("0")) + (ml.amount_rub or Decimal("0"))

    # Update each planned payment
    for p in payments:
        if p.order_no is None:
            continue

        paid_rub = Decimal("0")
        paid_amount = Decimal("0")
        direction = (p.direction or "").upper()
        pay_ccy = (p.currency or "RUB").upper()

        if direction == "ЗАКАЗ":
            ccy_map = order_fact_ccy.get(p.order_no, {})
            paid_amount = ccy_map.get(pay_ccy, Decimal("0"))
            paid_rub = ccy_map.get("RUB", Decimal("0"))
            if pay_ccy == "RUB":
                paid_amount = paid_rub
        elif direction == "ДОСТАВКА":
            ccy_map = delivery_fact_ccy.get(p.order_no, {})
            if "/" in pay_ccy:
                paid_amount = sum(ccy_map.get(c, Decimal("0")) for c in pay_ccy.split("/"))
            else:
                paid_amount = ccy_map.get(pay_ccy, Decimal("0"))
            paid_rub = ccy_map.get("RUB", Decimal("0"))
            if pay_ccy == "RUB":
                paid_amount = paid_rub
        elif direction == "ТАМОЖНЯ":
            paid_rub = customs_fact.get(p.order_no, Decimal("0"))
            paid_amount = paid_rub

        manual_rub = manual_map.get(p.id, Decimal("0"))
        paid_rub += manual_rub

        p.paid_rub = paid_rub
        p.paid_amount = paid_amount
        if not p.is_paid:
            if p.amount and p.amount > 0:
                p.is_paid = paid_amount >= p.amount
            elif p.amount_rub and p.amount_rub > 0:
                p.is_paid = paid_rub >= p.amount_rub

    db.flush()
