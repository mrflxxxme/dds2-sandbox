# ruff: noqa: RUF002, RUF003
"""
ETL sync: авто-матч счетов ФФ (FfInvoice) с банковской выпиской.

Хук в ``persist_df`` ПОСЛЕ матчеров заявок на оплату и заборов:
  1. Дебет без привязок (не занят заявкой, забором или другим счётом ФФ) с ИНН
     юрлица ФФ-склада проекта и ровно ОДНИМ кандидатом-счётом (not PAID,
     |amount − expense| ≤ 0.01, период/дата счёта в окне ±45 дней) → авто-линк
     + PAID. Неоднозначность (≥2 кандидатов) → НЕ матчить (ручной разбор).
  2. Пропагация: счёт с payment_request_id, чья заявка перешла в PAID → счёт
     PAID (matched_at из заявки).

Инварианты безопасности (зеркало sync_shipment_payments):
  • advisory-lock того же namespace — матчеры сериализуются по проекту;
  • «debit consumed once»: дебет, занятый заявкой/забором/счётом, не матчится;
  • счёт с активной (не отменённой/отклонённой) заявкой на оплату идёт через
    поток заявок — авто-матчер его не трогает;
  • Decimal(18,2), допуск 0.01. Идемпотентно: связанные счета пропускаются.
"""

from datetime import date, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.models.counterparty import Counterparty
from backend.models.ff_billing import FfInvoice, FfInvoiceStatus
from backend.models.payment_request import PaymentRequest, PaymentRequestStatus
from backend.models.transactions import Transaction
from backend.models.warehouse import OutboundShipment, Warehouse, WarehouseCounterparty, WarehouseType
from backend.utils.time import utcnow

logger = structlog.get_logger("dds.etl")

_MATCH_TOLERANCE = Decimal("0.01")
_WINDOW_DAYS = 45
# Страховочный лимит выборки FF-дебетов на один импорт (окно дат режет раньше).
_DEBITS_LIMIT = 2000

# Статусы заявки, при которых счёт считается «занятым» ручным потоком оплат.
_ACTIVE_REQUEST_STATUSES = tuple(
    s.value
    for s in PaymentRequestStatus
    if s not in (PaymentRequestStatus.CANCELLED, PaymentRequestStatus.REJECTED)
)


def _ff_inns(db: Session, project_id: int) -> set[str]:
    """ИНН юрлиц ФФ-складов проекта (основное + доп. юрлица)."""
    cp_ids: set[int] = set()
    rows = db.execute(
        select(Warehouse.counterparty_id).where(
            Warehouse.project_id == project_id,
            Warehouse.warehouse_type == WarehouseType.FULFILLMENT.value,
            Warehouse.is_deleted == False,  # noqa: E712
            Warehouse.counterparty_id.isnot(None),
        )
    ).all()
    cp_ids.update(cid for (cid,) in rows if cid)
    rows = db.execute(
        select(WarehouseCounterparty.counterparty_id)
        .join(Warehouse, Warehouse.id == WarehouseCounterparty.warehouse_id)
        .where(
            WarehouseCounterparty.project_id == project_id,
            Warehouse.warehouse_type == WarehouseType.FULFILLMENT.value,
            Warehouse.is_deleted == False,  # noqa: E712
        )
    ).all()
    cp_ids.update(cid for (cid,) in rows if cid)
    if not cp_ids:
        return set()
    inns = db.execute(
        select(Counterparty.inn).where(
            Counterparty.project_id == project_id,
            Counterparty.id.in_(cp_ids),
            Counterparty.is_deleted == False,  # noqa: E712
            Counterparty.inn.isnot(None),
        )
    ).all()
    return {inn.strip() for (inn,) in inns if inn and inn.strip()}


def _propagate_paid_requests(db: Session, project_id: int) -> int:
    """Счета с payment_request_id, чья заявка PAID → счёт PAID (matched_at из PR)."""
    rows = db.execute(
        select(FfInvoice, PaymentRequest)
        .join(PaymentRequest, PaymentRequest.id == FfInvoice.payment_request_id)
        .where(
            FfInvoice.project_id == project_id,
            FfInvoice.is_deleted == False,  # noqa: E712
            FfInvoice.status != FfInvoiceStatus.PAID.value,
            FfInvoice.payment_request_id.isnot(None),
            PaymentRequest.is_deleted == False,  # noqa: E712
            PaymentRequest.status == PaymentRequestStatus.PAID.value,
        )
    ).all()
    for inv, pr in rows:
        inv.status = FfInvoiceStatus.PAID.value
        inv.matched_at = pr.matched_at or utcnow()
    return len(rows)


def _match_debits(db: Session, project_id: int) -> int:
    inns = _ff_inns(db, project_id)
    if not inns:
        return 0

    # Открытые счета-кандидаты (без прямой привязки; счёт с активной заявкой
    # на оплату принадлежит ручному потоку — пропускаем).
    inv_rows = db.execute(
        select(FfInvoice, PaymentRequest.status)
        .outerjoin(PaymentRequest, PaymentRequest.id == FfInvoice.payment_request_id)
        .where(
            FfInvoice.project_id == project_id,
            FfInvoice.is_deleted == False,  # noqa: E712
            FfInvoice.status != FfInvoiceStatus.PAID.value,
            FfInvoice.matched_transaction_id.is_(None),
        )
        .order_by(FfInvoice.id.asc())
    ).all()
    open_invoices = [
        inv for inv, pr_status in inv_rows
        if pr_status is None or pr_status not in _ACTIVE_REQUEST_STATUSES
    ]
    if not open_invoices:
        return 0

    # Уже потреблённые дебеты: заявками, заборами и счетами ФФ этого проекта.
    consumed: set[int] = set()
    for stmt in (
        select(PaymentRequest.matched_transaction_id).where(
            PaymentRequest.project_id == project_id,
            PaymentRequest.matched_transaction_id.isnot(None),
            PaymentRequest.is_deleted == False,  # noqa: E712
        ),
        select(OutboundShipment.matched_transaction_id).where(
            OutboundShipment.project_id == project_id,
            OutboundShipment.matched_transaction_id.isnot(None),
            OutboundShipment.is_deleted == False,  # noqa: E712
        ),
        select(FfInvoice.matched_transaction_id).where(
            FfInvoice.project_id == project_id,
            FfInvoice.matched_transaction_id.isnot(None),
            FfInvoice.is_deleted == False,  # noqa: E712
        ),
    ):
        consumed.update(tid for (tid,) in db.execute(stmt).all() if tid is not None)

    def _anchor(inv: FfInvoice) -> date:
        return inv.period_end or inv.invoice_date or inv.created_at.date()

    # Окно дат прямо в SQL: дебеты только вокруг якорей открытых счетов (±45 дн),
    # а не вся история ФФ-платежей на каждом импорте выписки.
    anchors = [_anchor(inv) for inv in open_invoices]
    date_lo = min(anchors) - timedelta(days=_WINDOW_DAYS)
    date_hi = max(anchors) + timedelta(days=_WINDOW_DAYS + 1)
    debits = (
        db.execute(
            select(Transaction)
            .where(
                Transaction.project_id == project_id,
                Transaction.is_deleted == False,  # noqa: E712
                Transaction.expense > 0,
                Transaction.inn.in_(inns),
                Transaction.date >= date_lo,
                Transaction.date < date_hi,
            )
            .order_by(Transaction.date.asc())
            .limit(_DEBITS_LIMIT)
        )
        .scalars()
        .all()
    )

    matched = 0
    linked_invoice_ids: set[int] = set()
    for txn in debits:
        if txn.id in consumed:
            continue
        tdate = txn.date.date()
        expense = txn.expense or Decimal("0")
        candidates = [
            inv
            for inv in open_invoices
            if inv.id not in linked_invoice_ids
            and abs(inv.amount - expense) <= _MATCH_TOLERANCE
            and abs((tdate - _anchor(inv)).days) <= _WINDOW_DAYS
        ]
        if len(candidates) != 1:
            continue  # 0 — нечего матчить; ≥2 — неоднозначность, ручной разбор

        inv = candidates[0]
        inv.matched_transaction_id = txn.id
        inv.matched_at = utcnow()
        inv.status = FfInvoiceStatus.PAID.value
        consumed.add(txn.id)
        linked_invoice_ids.add(inv.id)
        matched += 1
    return matched


def sync_ff_invoices(db: Session, project_id: int) -> None:
    """Авто-матч счетов ФФ с выпиской + пропагация PAID-заявок на счета."""
    # Тот же namespace, что у sync_payment_requests/sync_shipment_payments:
    # матчеры сериализуются по проекту в транзакции persist_df.
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, :pid)"), {"ns": 0x50524D, "pid": project_id})

    propagated = _propagate_paid_requests(db, project_id)
    matched = _match_debits(db, project_id)
    if propagated or matched:
        logger.info(
            "ff_invoices.synced", project_id=project_id, matched=matched, propagated=propagated
        )
    db.flush()
