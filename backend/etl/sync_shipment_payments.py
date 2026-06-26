# ruff: noqa: RUF002, RUF003
"""
ETL sync: авто-связка заборов (OutboundShipment) с банковской выпиской.

Хук в ``persist_df`` ПОСЛЕ матчера заявок на оплату (``sync_payment_requests``):
для отгруженного/сданного забора без активной заявки ищем исходящий дебет
(expense>0), у которого ИНН перевозчика и сумма совпадают с ``pickup_cost``, и
проставляем прямую связь ``matched_transaction_id`` — БЕЗ заявки на оплату.

Зачем отдельно от заявок: логист часто платит перевозчику без формальной заявки;
тогда забор «оплачен», но в системе это не видно. Этот матчер закрывает разрыв.

Инварианты безопасности (зеркало ``sync_payment_requests``):
  • «debit consumed once» — одна транзакция матчит максимум один забор И не может
    быть уже занята заявкой на оплату (union consumed-set + partial-unique индекс
    uq_outbound_shipment_matched_txn);
  • забор с активной (не отменённой/отклонённой) заявкой НЕ трогаем — он идёт через
    ручной поток заявок, иначе две системы заявят один забор;
  • сравнение Decimal(18,2), не float; допуск 0.01;
  • пустой ИНН перевозчика / нет counterparty / pickup_cost ≤ 0 → пропуск;
  • дата-окно: дебет не раньше отгрузки минус буфер (предоплата/часовой пояс).

Идемпотентно: уже связанные заборы пропускаются. Объединённые/частичные платежи и
одинаковые суммы у нескольких заборов 1:1-матч ловит «жадно» по дате (как в
sync_payment_requests) — неоднозначные пары остаются несвязанными (ручной разбор).
"""

from datetime import timedelta
from decimal import Decimal

import structlog
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.models.assembly import AssemblyRequest  # noqa: F401  (FK target metadata)
from backend.models.counterparty import Counterparty
from backend.models.payment_request import PaymentRequest, PaymentRequestStatus
from backend.models.transactions import Transaction
from backend.models.warehouse import OutboundShipment, OutboundStatus
from backend.models.wb_fbo import WbFboSupply  # noqa: F401  (FK target metadata)
from backend.utils.time import utcnow

logger = structlog.get_logger("dds.etl")

_MATCH_TOLERANCE = Decimal("0.01")
_WINDOW_BACK_DAYS = 7   # дебет не раньше отгрузки − 7д (буфер на предоплату/часовой пояс)
_WINDOW_FWD_DAYS = 1    # верхняя граница — «сегодня + 1»

# Статусы заявки, при которых забор считается «занятым» ручным потоком оплат.
_ACTIVE_REQUEST_STATUSES = tuple(
    s.value
    for s in PaymentRequestStatus
    if s not in (PaymentRequestStatus.CANCELLED, PaymentRequestStatus.REJECTED)
)


def sync_shipment_payments(db: Session, project_id: int) -> None:
    """Link SHIPPED/DELIVERED заборы to outgoing debits by carrier INN + pickup_cost."""
    # Тот же namespace, что у sync_payment_requests: оба матчера сериализуются по проекту
    # в транзакции persist_df, два параллельных импорта не выберут одну транзакцию дважды.
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, :pid)"), {"ns": 0x50524D, "pid": project_id})

    # Заборы-кандидаты: отгружены/сданы, есть перевозчик и стоимость, ещё не связаны.
    rows = db.execute(
        select(OutboundShipment, Counterparty.inn)
        .join(Counterparty, OutboundShipment.counterparty_id == Counterparty.id)
        .where(
            OutboundShipment.project_id == project_id,
            OutboundShipment.is_deleted == False,  # noqa: E712
            OutboundShipment.status.in_([OutboundStatus.SHIPPED, OutboundStatus.DELIVERED]),
            OutboundShipment.counterparty_id.isnot(None),
            OutboundShipment.matched_transaction_id.is_(None),
            OutboundShipment.pickup_cost.isnot(None),
            OutboundShipment.pickup_cost > 0,
            Counterparty.is_deleted == False,  # noqa: E712
            Counterparty.inn.isnot(None),
        )
        .order_by(OutboundShipment.shipped_date.asc().nullslast(), OutboundShipment.id.asc())
    ).all()
    if not rows:
        return

    # Заборы, уже занятые активной заявкой на оплату (ручной поток владеет ими).
    requested_shipment_ids: set[int] = {
        sid
        for (sid,) in db.execute(
            select(PaymentRequest.outbound_shipment_id).where(
                PaymentRequest.project_id == project_id,
                PaymentRequest.outbound_shipment_id.isnot(None),
                PaymentRequest.is_deleted == False,  # noqa: E712
                PaymentRequest.status.in_(_ACTIVE_REQUEST_STATUSES),
            )
        ).all()
        if sid is not None
    }

    # Уже потреблённые транзакции: заявками ИЛИ другими заборами (этого проекта).
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
        consumed.update(tid for (tid,) in db.execute(stmt).all() if tid is not None)

    # Кандидаты-дебеты с ИНН, старые первыми (берём самый ранний матч).
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
    for os_row, cp_inn in rows:
        if os_row.id in requested_shipment_ids:
            continue
        inn = (cp_inn or "").strip()
        if not inn or os_row.pickup_cost is None:
            continue
        anchor = os_row.shipped_date or os_row.pickup_date or os_row.created_at.date()
        window_start = anchor - timedelta(days=_WINDOW_BACK_DAYS)
        window_end = today + timedelta(days=_WINDOW_FWD_DAYS)

        chosen: Transaction | None = None
        for t in by_inn.get(inn, []):
            if t.id in consumed:
                continue
            tdate = t.date.date()
            if tdate < window_start or tdate > window_end:
                continue
            if abs((t.expense or Decimal("0")) - os_row.pickup_cost) <= _MATCH_TOLERANCE:
                chosen = t
                break  # txns sorted ascending → earliest unconsumed match

        if chosen is None:
            continue

        consumed.add(chosen.id)
        os_row.matched_transaction_id = chosen.id
        os_row.matched_at = utcnow()
        matched += 1

    if matched:
        logger.info("shipment_payments.matched", project_id=project_id, count=matched)
    db.flush()
