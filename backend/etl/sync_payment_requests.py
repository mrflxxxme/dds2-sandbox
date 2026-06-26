# ruff: noqa: RUF002, RUF003
"""
ETL sync: авто-матч заявок на оплату с банковской выпиской.

Хук в ``persist_df`` (sync ORM, executor-поток): на каждый синк выписки Faktura
ищем исходящий дебет (expense>0), у которого ИНН и сумма совпадают с заявкой в
статусе DRAFT_CREATED, и флипаем её в PAID. Инварианты безопасности:
  • «debit consumed once» — одна транзакция матчит максимум одну заявку
    (in-memory set + partial-unique индекс uq_payment_request_matched_txn);
  • сравнение Decimal(18,2), не float; допуск 0.01;
  • дата-окно вокруг создания заявки (дебет не может предшествовать черновику);
  • пустой ИНН → пропуск (в выписке ИНН часто отсутствует).

Идемпотентно: уже сматченные заявки пропускаются. Объединённые/частичные/
округлённые платежи 1:1-матч НЕ ловит — они остаются в DRAFT_CREATED (ручной
разбор), это осознанное ограобчение v1.
"""

from datetime import timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.models.payment_request import PaymentRequest, PaymentRequestStatus
from backend.models.transactions import Transaction
from backend.services.payment_request_status import apply_transition
from backend.utils.time import utcnow

logger = structlog.get_logger("dds.etl")

_MATCH_TOLERANCE = Decimal("0.01")
_WINDOW_BACK_DAYS = 1   # дебет не может предшествовать созданию заявки (буфер на часовой пояс)
_WINDOW_FWD_DAYS = 1    # верхняя граница — «сегодня + 1»


def sync_payment_requests(db: Session, project_id: int) -> None:
    """Match DRAFT_CREATED payment requests to outgoing bank debits by INN + amount."""
    # Сериализуем матчер по проекту в транзакции persist_df: два параллельных импорта/
    # синка не выберут одну транзакцию дважды (иначе IntegrityError на
    # uq_payment_request_matched_txn откатит ВЕСЬ импорт). Lock держится до commit.
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, :pid)"), {"ns": 0x50524D, "pid": project_id})

    reqs = (
        db.execute(
            select(PaymentRequest)
            .where(
                PaymentRequest.project_id == project_id,
                PaymentRequest.status == PaymentRequestStatus.DRAFT_CREATED.value,
                PaymentRequest.is_deleted == False,  # noqa: E712
                PaymentRequest.matched_transaction_id.is_(None),
            )
            .order_by(PaymentRequest.created_at.asc())
        )
        .scalars()
        .all()
    )
    if not reqs:
        return

    # Already-consumed transactions: занятые заявкой ИЛИ заборами (этого проекта).
    # Симметрично sync_shipment_payments — транзакция не уходит на заявку, если уже
    # привязана к забору (авто или вручную на сверке оплат).
    from backend.models.warehouse import OutboundShipment

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
    ):
        consumed.update(row[0] for row in db.execute(stmt).all() if row[0] is not None)

    # Candidate outgoing debits with an INN, oldest first (so we pick the earliest match).
    txns = (
        db.execute(
            select(Transaction)
            .where(
                Transaction.project_id == project_id,
                Transaction.is_deleted == False,  # noqa: E712
                Transaction.expense > 0,
                Transaction.inn.isnot(None),
            )
            .order_by(Transaction.date.asc())
        )
        .scalars()
        .all()
    )
    by_inn: dict[str, list[Transaction]] = {}
    for t in txns:
        inn = (t.inn or "").strip()
        if inn:
            by_inn.setdefault(inn, []).append(t)

    today = utcnow().date()
    matched = 0
    for pr in reqs:
        inn = (pr.payee_inn or "").strip()
        if not inn or pr.amount is None:
            continue
        window_start = pr.created_at.date() - timedelta(days=_WINDOW_BACK_DAYS)
        window_end = today + timedelta(days=_WINDOW_FWD_DAYS)

        chosen: Transaction | None = None
        for t in by_inn.get(inn, []):
            if t.id in consumed:
                continue
            tdate = t.date.date()
            if tdate < window_start or tdate > window_end:
                continue
            if abs((t.expense or Decimal("0")) - pr.amount) <= _MATCH_TOLERANCE:
                chosen = t
                break  # txns are sorted ascending → earliest unconsumed match

        if chosen is None:
            continue

        consumed.add(chosen.id)
        pr.matched_transaction_id = chosen.id
        pr.matched_at = utcnow()
        apply_transition(
            db,
            pr,
            PaymentRequestStatus.PAID,
            changed_by="system:faktura_match",
            comment=f"Авто-матч по выписке (txn {chosen.txn_id})",
        )
        matched += 1

    if matched:
        logger.info("payment_requests.matched", project_id=project_id, count=matched)
    db.flush()
