# ruff: noqa: RUF002, RUF003
"""
Payment-request service — список/деталка/создание/правка/submit заявок на оплату.

Бизнес-логика домена «Заявка на оплату» (логика — здесь, роутер только HTTP).
Все запросы scoped по project_id + is_deleted == False. После мутаций —
invalidate_cache('payment_request_list'/'payment_request_detail').
"""

import logging
from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.cache import invalidate_cache
from backend.models import Project
from backend.models.counterparty import Counterparty
from backend.models.payment_request import (
    PaymentRequest,
    PaymentRequestCategory,
    PaymentRequestDocType,
    PaymentRequestShipment,
    PaymentRequestSource,
    PaymentRequestStatus,
)
from backend.models.transactions import Transaction
from backend.models.warehouse import OutboundShipment, OutboundStatus, Warehouse
from backend.models.wb_fbo import WbFboSupply
from backend.schemas.payment_request import (
    CounterpartyReconciliation,
    PaymentRequestCreate,
    PaymentRequestUpdate,
    ReconciliationPaymentRow,
    ShippableShipmentRow,
)
from backend.utils.time import utcnow
from backend.services import payment_category_service as pcs
from backend.services import payment_request_status as prs

logger = logging.getLogger("dds.payment_request")

_LIST_LIMIT = 2000
# Поля, которые разрешено править через PATCH (защита от mass-assignment при расширении схемы).
_EDITABLE_FIELDS = frozenset({
    "counterparty_id", "category", "brand", "payee_inn", "payee_kpp", "payee_account", "payee_bik",
    "payee_bank_name", "payee_corr_account", "payee_name", "amount", "currency",
    "pickup_date", "purpose",
})


class PaymentRequestValidationError(Exception):
    """Submit/transition validation failed — carries a list of human-readable reasons."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("; ".join(missing))


async def _invalidate(project_id: int | None) -> None:
    await invalidate_cache(f"payment_request_list:project_id={project_id}")
    await invalidate_cache(f"payment_request_detail:project_id={project_id}")


def _project_scope(project_id: int | None) -> ColumnElement[bool]:
    """Видимость заявки: свой проект ИЛИ «общая» (project_id IS NULL). Общие заявки —
    единый инбокс согласования, видны из любого проекта. Изоляция именованных проектов
    сохраняется (заявка проекта X не видна из проекта Y). project_id=None → только общие."""
    if project_id is None:
        return PaymentRequest.project_id.is_(None)
    return or_(PaymentRequest.project_id == project_id, PaymentRequest.project_id.is_(None))


class PaymentRequestService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ─── numbering ────────────────────────────────────────────────────────

    async def _next_number(self, project_id: int | None) -> str:
        """Следующий номер. «Общие» (без проекта) заявки — отдельная серия «ОБЩ-…»,
        чтобы номера не пересекались с проектными «ОПЛ-…» в едином списке."""
        cond: ColumnElement[bool]
        if project_id is None:
            cond = PaymentRequest.project_id.is_(None)
            prefix = "ОБЩ"
        else:
            cond = PaymentRequest.project_id == project_id
            prefix = "ОПЛ"
        res = await self.db.execute(select(func.count()).select_from(PaymentRequest).where(cond))
        n = (res.scalar() or 0) + 1
        return f"{prefix}-{n:05d}"

    # ─── reads ────────────────────────────────────────────────────────────

    async def list_requests(
        self,
        project_id: int,
        *,
        status: str | None = None,
        category: str | None = None,
        brand: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        counterparty_id: int | None = None,
        search: str | None = None,
        limit: int = _LIST_LIMIT,
    ) -> tuple[list[PaymentRequest], int, dict[int, int], dict[int, str]]:
        """Return (rows, total, doc_counts, project_names). Видит свой проект + «общие»
        (project_id IS NULL). doc_counts: pr_id → active doc count; project_names: project_id → name."""
        conds = [_project_scope(project_id), PaymentRequest.is_deleted == False]  # noqa: E712
        if status:
            conds.append(PaymentRequest.status == status)
        if category:
            conds.append(PaymentRequest.category == category)
        if brand:
            conds.append(PaymentRequest.brand == brand)
        if counterparty_id is not None:
            conds.append(PaymentRequest.counterparty_id == counterparty_id)
        if date_from is not None:
            conds.append(PaymentRequest.pickup_date >= date_from)
        if date_to is not None:
            conds.append(PaymentRequest.pickup_date <= date_to)
        if search:
            like = f"%{search.strip()}%"
            conds.append(
                or_(
                    PaymentRequest.number.ilike(like),
                    PaymentRequest.payee_name.ilike(like),
                    PaymentRequest.payee_inn.ilike(like),
                )
            )

        total_res = await self.db.execute(select(func.count()).select_from(PaymentRequest).where(*conds))
        total = total_res.scalar() or 0

        res = await self.db.execute(
            select(PaymentRequest).where(*conds).order_by(PaymentRequest.created_at.desc()).limit(limit)
        )
        rows = list(res.scalars().all())

        doc_counts: dict[int, int] = {}
        project_names: dict[int, str] = {}
        if rows:
            ids = [r.id for r in rows]
            from backend.models.payment_request import PaymentRequestDocument

            dc_res = await self.db.execute(
                select(PaymentRequestDocument.payment_request_id, func.count())
                .where(
                    PaymentRequestDocument.payment_request_id.in_(ids),
                    PaymentRequestDocument.is_deleted == False,  # noqa: E712
                )
                .group_by(PaymentRequestDocument.payment_request_id)
            )
            doc_counts = {pid: cnt for pid, cnt in dc_res.all()}

            pids = {r.project_id for r in rows if r.project_id is not None}
            if pids:
                pn_res = await self.db.execute(select(Project.id, Project.name).where(Project.id.in_(pids)))
                project_names = {pid: name for pid, name in pn_res.all()}
        return rows, total, doc_counts, project_names

    async def get_request(self, project_id: int | None, request_id: int) -> PaymentRequest | None:
        res = await self.db.execute(
            select(PaymentRequest)
            .where(
                PaymentRequest.id == request_id,
                _project_scope(project_id),  # свой проект ИЛИ общая (без проекта)
                PaymentRequest.is_deleted == False,  # noqa: E712
            )
            .options(
                selectinload(PaymentRequest.documents),
                selectinload(PaymentRequest.events),
                selectinload(PaymentRequest.project),  # имя проекта для деталки
            )
        )
        return res.scalar_one_or_none()

    async def covered_shipment_numbers(self, project_id: int, request_id: int) -> list[str]:
        """Номера заборов, покрытых заявкой (одна заявка может покрывать несколько отгрузок)."""
        res = await self.db.execute(
            select(OutboundShipment.number)
            .join(
                PaymentRequestShipment,
                PaymentRequestShipment.outbound_shipment_id == OutboundShipment.id,
            )
            .where(
                PaymentRequestShipment.payment_request_id == request_id,
                PaymentRequestShipment.project_id == project_id,
            )
            .order_by(OutboundShipment.number)
        )
        return [n for (n,) in res.all()]

    async def list_shippable(
        self,
        project_id: int,
        *,
        search: str | None = None,
        has_request: bool | None = None,
        archived: bool = False,
        counterparty_id: int | None = None,
    ) -> list[ShippableShipmentRow]:
        """Отгруженные/сданные заборы — кандидаты на заявку на оплату (+статус заявки, +архив).

        ``counterparty_id`` ограничивает выборку одним перевозчиком (сверка на карточке).
        """
        from backend.models.payment_request import PaymentRequest as PR
        from backend.models.payment_request import PaymentRequestShipment as PRS
        from backend.models.payment_request import PaymentShipmentArchive

        # Archived shipment ids (рабочий список логиста скрывает их по умолчанию).
        arch_res = await self.db.execute(
            select(PaymentShipmentArchive.outbound_shipment_id).where(
                PaymentShipmentArchive.project_id == project_id
            )
        )
        archived_ids = {a for (a,) in arch_res.all()}

        # Map shipment_id → latest non-deleted payment request (id, status) — через child-таблицу,
        # чтобы ВСЕ заборы одной мульти-заявки помечались «занятыми», а не только основной.
        req_res = await self.db.execute(
            select(PRS.outbound_shipment_id, PR.id, PR.status)
            .join(PR, PR.id == PRS.payment_request_id)
            .where(
                PRS.project_id == project_id,
                PR.is_deleted == False,  # noqa: E712
            )
            .order_by(PR.id.asc())  # asc → последняя перезапись = самая свежая заявка
        )
        req_by_shipment: dict[int, tuple[int, str]] = {}
        for sid, rid, st in req_res.all():
            if sid is not None:
                req_by_shipment[sid] = (rid, st)

        conds = [
            OutboundShipment.project_id == project_id,
            OutboundShipment.is_deleted == False,  # noqa: E712
            # Все заборы логиста: отгруженные И уже сданные/доставленные.
            OutboundShipment.status.in_([OutboundStatus.SHIPPED, OutboundStatus.DELIVERED]),
            OutboundShipment.assembly_request_id.isnot(None),
        ]
        if counterparty_id is not None:
            conds.append(OutboundShipment.counterparty_id == counterparty_id)
        res = await self.db.execute(
            select(
                OutboundShipment, Counterparty, Warehouse.name, WbFboSupply.wb_supply_id, Transaction.date
            )
            .outerjoin(Counterparty, OutboundShipment.counterparty_id == Counterparty.id)
            .outerjoin(Warehouse, OutboundShipment.warehouse_id == Warehouse.id)
            .outerjoin(WbFboSupply, OutboundShipment.wb_fbo_supply_id == WbFboSupply.id)
            .outerjoin(
                Transaction,
                and_(
                    OutboundShipment.matched_transaction_id == Transaction.id,
                    Transaction.project_id == project_id,
                ),
            )
            .where(*conds)
            .order_by(OutboundShipment.shipped_date.desc().nullslast(), OutboundShipment.id.desc())
            .limit(_LIST_LIMIT)
        )
        rows: list[ShippableShipmentRow] = []
        for os_row, cp, src_wh_name, fbo_supply_no, matched_txn_dt in res.all():
            in_archive = os_row.id in archived_ids
            if archived != in_archive:  # archived view ↔ archived rows; default view ↔ active rows
                continue
            latest = req_by_shipment.get(os_row.id)
            request_id = latest[0] if latest else None
            request_status = latest[1] if latest else None
            # «Занят» заявкой, если последняя заявка не отменена/не отклонена.
            already = latest is not None and latest[1] not in (
                PaymentRequestStatus.CANCELLED.value,
                PaymentRequestStatus.REJECTED.value,
            )
            if has_request is False and already:
                continue
            if has_request is True and not already:
                continue
            carrier_inn = cp.inn if cp else None
            carrier_name = cp.name if cp else None
            if search:
                s = search.strip().lower()
                hay = " ".join(
                    str(x or "").lower()
                    for x in (os_row.number, os_row.destination, carrier_name, carrier_inn)
                )
                if s not in hay:
                    continue
            rows.append(
                ShippableShipmentRow(
                    outbound_shipment_id=os_row.id,
                    assembly_request_id=os_row.assembly_request_id,
                    number=os_row.number,
                    destination=os_row.destination,
                    shipped_date=os_row.shipped_date,
                    pickup_cost=os_row.pickup_cost,
                    counterparty_id=os_row.counterparty_id,
                    carrier_inn=carrier_inn,
                    carrier_name=carrier_name,
                    carrier_kpp=cp.kpp if cp else None,
                    carrier_bank_account=cp.bank_account if cp else None,
                    carrier_bik=cp.bik if cp else None,
                    carrier_bank_name=cp.bank_name if cp else None,
                    carrier_corr_account=cp.corr_account if cp else None,
                    already_requested=already,
                    request_id=request_id,
                    request_status=request_status,
                    matched_transaction_id=os_row.matched_transaction_id,
                    matched_at=os_row.matched_at,
                    matched_txn_date=matched_txn_dt.date() if matched_txn_dt else None,
                    pallets_count=os_row.pallets_count,
                    shipped_as_boxes=os_row.shipped_as_boxes,
                    pickup_date=os_row.pickup_date,
                    source_warehouse=src_wh_name,
                    wb_supply_number=os_row.wb_supply_id or fbo_supply_no,
                )
            )
        return rows

    async def archive_shipments(self, project_id: int, shipment_ids: list[int], user_id: int | None) -> int:
        """Перенести заборы в архив рабочего списка (идемпотентно). Возвращает число добавленных."""
        from backend.models.payment_request import PaymentShipmentArchive

        if not shipment_ids:
            return 0
        existing_res = await self.db.execute(
            select(PaymentShipmentArchive.outbound_shipment_id).where(
                PaymentShipmentArchive.project_id == project_id,
                PaymentShipmentArchive.outbound_shipment_id.in_(shipment_ids),
            )
        )
        existing = {a for (a,) in existing_res.all()}
        added = 0
        for sid in set(shipment_ids):
            if sid in existing:
                continue
            self.db.add(
                PaymentShipmentArchive(
                    project_id=project_id, outbound_shipment_id=sid, archived_by_user_id=user_id
                )
            )
            added += 1
        await self.db.commit()
        return added

    async def unarchive_shipments(self, project_id: int, shipment_ids: list[int]) -> int:
        """Вернуть заборы из архива. Возвращает число удалённых маркеров."""
        from backend.models.payment_request import PaymentShipmentArchive

        if not shipment_ids:
            return 0
        res = await self.db.execute(
            select(PaymentShipmentArchive).where(
                PaymentShipmentArchive.project_id == project_id,
                PaymentShipmentArchive.outbound_shipment_id.in_(shipment_ids),
            )
        )
        rows = list(res.scalars().all())
        for r in rows:
            await self.db.delete(r)  # no-soft-delete-check: PaymentShipmentArchive — plain marker, без SoftDeleteMixin
        await self.db.commit()
        return len(rows)

    # ─── reconciliation (карточка контрагента) ──────────────────────────────

    async def get_counterparty_reconciliation(
        self, project_id: int, counterparty_id: int, *, archived: bool = False
    ) -> CounterpartyReconciliation:
        """Сверка перевозчика: заборы (оплачено/нет) + ВСЕ платежи из выписки с привязанными заборами."""
        from backend.models.payment_request import PaymentTxnArchive

        # 1) Заборы перевозчика (с авто-/ручной связкой / заявкой).
        shipments = await self.list_shippable(
            project_id, archived=archived, counterparty_id=counterparty_id
        )

        cp = (
            await self.db.execute(
                select(Counterparty).where(
                    Counterparty.id == counterparty_id,
                    Counterparty.project_id == project_id,  # multi-tenant: чужой контрагент → пусто
                )
            )
        ).scalar_one_or_none()
        inn = (cp.inn or "").strip() if cp and cp.inn else None

        payments: list[ReconciliationPaymentRow] = []
        payments_truncated = False
        if inn:
            # Привязки считаем по ТЕМ ЖЕ заборам, что показаны (shipments, с тем же
            # archived-фильтром) — иначе бейдж «привязано N» разойдётся с чипами на фронте.
            links_by_txn: dict[int, list[tuple[int, Decimal]]] = {}
            for s in shipments:
                if s.matched_transaction_id is not None:
                    links_by_txn.setdefault(s.matched_transaction_id, []).append(
                        (s.outbound_shipment_id, s.pickup_cost or Decimal("0"))
                    )

            # Платежи, занятые формальной заявкой на оплату, не показываем — ими управляет
            # поток заявок (страница «Оплаты»), а не ручная сверка заборов.
            pr_consumed = {
                t
                for (t,) in (
                    await self.db.execute(
                        select(PaymentRequest.matched_transaction_id).where(
                            PaymentRequest.project_id == project_id,
                            PaymentRequest.matched_transaction_id.isnot(None),
                            PaymentRequest.is_deleted == False,  # noqa: E712
                        )
                    )
                ).all()
                if t is not None
            }

            archived_txn_ids = {
                t
                for (t,) in (
                    await self.db.execute(
                        select(PaymentTxnArchive.transaction_id).where(
                            PaymentTxnArchive.project_id == project_id
                        )
                    )
                ).all()
            }

            txns = (
                (
                    await self.db.execute(
                        select(Transaction)
                        .where(
                            Transaction.project_id == project_id,
                            Transaction.is_deleted == False,  # noqa: E712
                            Transaction.expense > 0,
                            Transaction.inn == inn,
                        )
                        .order_by(Transaction.date.desc())
                        .limit(_LIST_LIMIT + 1)  # +1 → детект усечения
                    )
                )
                .scalars()
                .all()
            )
            if len(txns) > _LIST_LIMIT:
                payments_truncated = True
                txns = txns[:_LIST_LIMIT]
            for t in txns:
                if t.id in pr_consumed:
                    continue
                in_archive = t.id in archived_txn_ids
                if archived != in_archive:
                    continue
                linked = links_by_txn.get(t.id, [])
                linked_sum = sum((c for _, c in linked), Decimal("0"))
                amount = t.expense or Decimal("0")
                payments.append(
                    ReconciliationPaymentRow(
                        transaction_id=t.id,
                        date=t.date,
                        amount=amount,
                        purpose=t.purpose,
                        account=t.account,
                        counterparty_name=t.counterparty,
                        inn=t.inn,
                        archived=in_archive,
                        linked_shipment_ids=[sid for sid, _ in linked],
                        linked_count=len(linked),
                        linked_sum=linked_sum,
                        diff=amount - linked_sum,
                    )
                )

        matched_count = sum(
            1
            for s in shipments
            if s.matched_transaction_id is not None
            or s.request_status == PaymentRequestStatus.PAID.value
        )
        return CounterpartyReconciliation(
            shipments=shipments,
            payments=payments,
            matched_count=matched_count,
            unpaid_count=len(shipments) - matched_count,
            payment_count=len(payments),
            unlinked_payment_count=sum(1 for p in payments if p.linked_count == 0),
            payments_truncated=payments_truncated,
        )

    async def link_shipments_to_payment(
        self, project_id: int, transaction_id: int, shipment_ids: list[int], user_id: int | None
    ) -> dict:
        """Привязать N заборов к одному платежу выписки. Возвращает сверку суммы."""
        txn = (
            await self.db.execute(
                select(Transaction).where(
                    Transaction.id == transaction_id,
                    Transaction.project_id == project_id,
                    Transaction.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if txn is None:
            raise PaymentRequestValidationError(["Платёж не найден"])
        # Привязать можно только исходящий платёж (расход) — не приход/комиссию с минусом.
        if not (txn.expense and txn.expense > 0):
            raise PaymentRequestValidationError(["Привязать можно только исходящий платёж (расход)"])
        txn_inn = (txn.inn or "").strip()

        # Не под advisory-lock матчеров намеренно: запись скалярного matched_transaction_id
        # идемпотентна и не участвует в consumed-set матчеров до следующего импорта; гонок
        # с авто-матчером нет (он трогает только matched_transaction_id IS NULL).
        now = utcnow()
        if shipment_ids:
            rows = (
                await self.db.execute(
                    select(OutboundShipment, Counterparty.inn)
                    .join(Counterparty, OutboundShipment.counterparty_id == Counterparty.id)
                    .where(
                        OutboundShipment.id.in_(shipment_ids),
                        OutboundShipment.project_id == project_id,
                        OutboundShipment.is_deleted == False,  # noqa: E712
                    )
                )
            ).all()
            # Сначала валидируем ВСЕ (атомарно: ни одной мутации до проверки), потом пишем.
            for s, cp_inn in rows:
                if (cp_inn or "").strip() != txn_inn:
                    raise PaymentRequestValidationError(
                        [f"Забор {s.number}: ИНН перевозчика не совпадает с ИНН платежа"]
                    )
            for s, _ in rows:
                if s.matched_transaction_id is not None and s.matched_transaction_id != transaction_id:
                    logger.info(
                        "payment_link: re-link shipment=%d old_txn=%s new_txn=%d project=%d user=%s",
                        s.id, s.matched_transaction_id, transaction_id, project_id, user_id,
                    )
                s.matched_transaction_id = transaction_id
                s.matched_at = now
            await self.db.commit()

        # Текущая сверка по платежу.
        link_rows = (
            await self.db.execute(
                select(OutboundShipment.pickup_cost).where(
                    OutboundShipment.project_id == project_id,
                    OutboundShipment.matched_transaction_id == transaction_id,
                    OutboundShipment.is_deleted == False,  # noqa: E712
                )
            )
        ).all()
        linked_sum = sum((c for (c,) in link_rows if c is not None), Decimal("0"))
        amount = txn.expense or Decimal("0")
        return {
            "transaction_id": transaction_id,
            "linked_count": len(link_rows),
            "amount": amount,
            "linked_sum": linked_sum,
            "diff": amount - linked_sum,
        }

    async def unlink_shipments(self, project_id: int, shipment_ids: list[int]) -> int:
        """Отвязать заборы от платежа (очистить matched_transaction_id). Возвращает число."""
        if not shipment_ids:
            return 0
        ships = (
            (
                await self.db.execute(
                    select(OutboundShipment).where(
                        OutboundShipment.id.in_(shipment_ids),
                        OutboundShipment.project_id == project_id,
                        OutboundShipment.is_deleted == False,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        n = 0
        for s in ships:
            if s.matched_transaction_id is not None:
                s.matched_transaction_id = None
                s.matched_at = None
                n += 1
        await self.db.commit()
        return n

    async def archive_txns(self, project_id: int, transaction_ids: list[int], user_id: int | None) -> int:
        """Пометить бесхозные платежи разобранными (идемпотентно). Возвращает число добавленных."""
        from backend.models.payment_request import PaymentTxnArchive

        if not transaction_ids:
            return 0
        # Multi-tenant: архивируем только транзакции этого проекта (чужой id → игнор).
        valid_res = await self.db.execute(
            select(Transaction.id).where(
                Transaction.id.in_(transaction_ids),
                Transaction.project_id == project_id,
                Transaction.is_deleted == False,  # noqa: E712
            )
        )
        valid_ids = {t for (t,) in valid_res.all()}
        if not valid_ids:
            return 0
        existing_res = await self.db.execute(
            select(PaymentTxnArchive.transaction_id).where(
                PaymentTxnArchive.project_id == project_id,
                PaymentTxnArchive.transaction_id.in_(valid_ids),
            )
        )
        existing = {t for (t,) in existing_res.all()}
        added = 0
        for tid in valid_ids:
            if tid in existing:
                continue
            self.db.add(
                PaymentTxnArchive(
                    project_id=project_id, transaction_id=tid, archived_by_user_id=user_id
                )
            )
            added += 1
        await self.db.commit()
        return added

    async def unarchive_txns(self, project_id: int, transaction_ids: list[int]) -> int:
        """Вернуть бесхозные платежи из архива. Возвращает число удалённых маркеров."""
        from backend.models.payment_request import PaymentTxnArchive

        if not transaction_ids:
            return 0
        res = await self.db.execute(
            select(PaymentTxnArchive).where(
                PaymentTxnArchive.project_id == project_id,
                PaymentTxnArchive.transaction_id.in_(transaction_ids),
            )
        )
        rows = list(res.scalars().all())
        for r in rows:
            await self.db.delete(r)  # no-soft-delete-check: PaymentTxnArchive — plain marker, без SoftDeleteMixin
        await self.db.commit()
        return len(rows)

    # ─── writes ───────────────────────────────────────────────────────────

    async def create_request(
        self, project_id: int | None, data: PaymentRequestCreate, user_id: int | None
    ) -> PaymentRequest:
        """project_id: целевой проект (None = «общая» заявка без проекта). Резолвится в роутере."""
        payee = {
            "payee_inn": data.payee_inn,
            "payee_kpp": data.payee_kpp,
            "payee_account": data.payee_account,
            "payee_bik": data.payee_bik,
            "payee_bank_name": data.payee_bank_name,
            "payee_corr_account": data.payee_corr_account,
            "payee_name": data.payee_name,
        }
        amount = data.amount
        pickup_date = data.pickup_date
        counterparty_id = data.counterparty_id
        assembly_request_id: int | None = None
        purpose = data.purpose

        # Заборы на оплату: список (несколько отгрузок на один счёт) ИЛИ один.
        ship_ids: list[int] = []
        if data.outbound_shipment_ids:
            ship_ids = list(dict.fromkeys(data.outbound_shipment_ids))  # дедуп, порядок выбора
        elif data.outbound_shipment_id is not None:
            ship_ids = [data.outbound_shipment_id]

        covered_shipments: list[OutboundShipment] = []
        if data.source == PaymentRequestSource.COUNTERPARTY.value and ship_ids:
            if project_id is None:
                # Заявка по забору — это логистика конкретного проекта, не «общая».
                raise PaymentRequestValidationError(["Заявка по забору требует указания проекта"])
            os_res = await self.db.execute(
                select(OutboundShipment).where(
                    OutboundShipment.id.in_(ship_ids),
                    OutboundShipment.project_id == project_id,
                    OutboundShipment.is_deleted == False,  # noqa: E712
                )
            )
            by_id = {s.id: s for s in os_res.scalars().all()}
            covered_shipments = [by_id[i] for i in ship_ids if i in by_id]
            if len(covered_shipments) != len(ship_ids):
                raise PaymentRequestValidationError(["Часть отгрузок не найдена"])
            if len({s.counterparty_id for s in covered_shipments}) > 1:
                raise PaymentRequestValidationError(["Заборы должны быть одного перевозчика"])
            await self._assert_shipments_free(project_id, ship_ids)

            first = covered_shipments[0]
            assembly_request_id = first.assembly_request_id
            if amount is None:
                total = sum((s.pickup_cost or Decimal("0") for s in covered_shipments), Decimal("0"))
                amount = total if total > 0 else None
            if pickup_date is None:
                pickup_date = first.pickup_date or first.shipped_date
            if counterparty_id is None:
                counterparty_id = first.counterparty_id

        if counterparty_id is not None:
            cp_res = await self.db.execute(
                select(Counterparty).where(
                    Counterparty.id == counterparty_id,
                    Counterparty.project_id == project_id,
                    Counterparty.is_deleted == False,  # noqa: E712
                )
            )
            cp = cp_res.scalar_one_or_none()
            if cp is not None:
                # manual fields win; fill blanks from the counterparty
                payee["payee_inn"] = payee["payee_inn"] or cp.inn
                payee["payee_kpp"] = payee["payee_kpp"] or cp.kpp
                payee["payee_account"] = payee["payee_account"] or cp.bank_account
                payee["payee_bik"] = payee["payee_bik"] or cp.bik
                payee["payee_bank_name"] = payee["payee_bank_name"] or cp.bank_name
                payee["payee_corr_account"] = payee["payee_corr_account"] or cp.corr_account
                payee["payee_name"] = payee["payee_name"] or cp.name

                # Запоминаем введённые реквизиты на перевозчике (только пустые поля) —
                # в следующий раз подтянутся авто-заполнением.
                if data.source == PaymentRequestSource.COUNTERPARTY.value:
                    written: list[str] = []
                    if not cp.bank_account and payee["payee_account"]:
                        cp.bank_account = payee["payee_account"]; written.append("bank_account")
                    if not cp.bik and payee["payee_bik"]:
                        cp.bik = payee["payee_bik"]; written.append("bik")
                    if not cp.bank_name and payee["payee_bank_name"]:
                        cp.bank_name = payee["payee_bank_name"]; written.append("bank_name")
                    if not cp.corr_account and payee["payee_corr_account"]:
                        cp.corr_account = payee["payee_corr_account"]; written.append("corr_account")
                    if not cp.kpp and payee["payee_kpp"]:
                        cp.kpp = payee["payee_kpp"]; written.append("kpp")
                    if written:
                        # Аудит: банковские реквизиты — money-routing поля, фиксируем кто/что записал.
                        logger.info(
                            "payment_request: requisites written back to counterparty id=%d project=%d fields=%s user=%s",
                            cp.id, project_id, written, user_id,
                        )

        if amount is None or Decimal(amount) <= 0:
            raise PaymentRequestValidationError(["Не указана стоимость (amount)"])

        # Заявка создаётся сразу «На проверке» (без статуса «Черновик»), поэтому реквизиты
        # получателя обязательны уже на создании (раньше их проверял submit).
        req_missing: list[str] = []
        if not payee["payee_name"]:
            req_missing.append("Не указано наименование получателя")
        if not payee["payee_inn"]:
            req_missing.append("Не указан ИНН получателя")
        if not payee["payee_account"]:
            req_missing.append("Не указан расчётный счёт")
        if not payee["payee_bik"]:
            req_missing.append("Не указан БИК")
        if req_missing:
            raise PaymentRequestValidationError(req_missing)

        if not purpose:
            purpose = self._build_purpose(covered_shipments, pickup_date)

        # Назначение: явное из формы → иначе LOGISTICS для заявок по забору, OTHER для прочих.
        category = data.category or (
            PaymentRequestCategory.LOGISTICS.value
            if covered_shipments
            else PaymentRequestCategory.OTHER.value
        )
        if not await pcs.is_valid_code(self.db, project_id, category):
            raise PaymentRequestValidationError([f"Неизвестная категория оплаты: {category}"])

        number = await self._next_number(project_id)
        pr = PaymentRequest(
            project_id=project_id,
            number=number,
            status=PaymentRequestStatus.PENDING_REVIEW.value,
            source=data.source,
            category=category,
            brand=data.brand or None,  # пусто/None → «Все бренды»
            counterparty_id=counterparty_id,
            outbound_shipment_id=(covered_shipments[0].id if covered_shipments else data.outbound_shipment_id),
            assembly_request_id=assembly_request_id,
            amount=Decimal(amount),
            currency=data.currency or "RUB",
            pickup_date=pickup_date,
            purpose=purpose,
            created_by_user_id=user_id,
            **payee,
        )
        self.db.add(pr)
        await self.db.flush()
        for s in covered_shipments:
            # covered_shipments непусты только для проектной заявки (забор найден по project_id),
            # поэтому s.project_id — реальный int (PaymentRequestShipment остаётся project-scoped).
            self.db.add(
                PaymentRequestShipment(
                    project_id=s.project_id, payment_request_id=pr.id, outbound_shipment_id=s.id
                )
            )
        prs.log_event(
            self.db, pr.id, None, PaymentRequestStatus.PENDING_REVIEW.value,
            changed_by="user", comment="Создана",
        )
        await self.db.commit()
        await self.db.refresh(pr)
        await _invalidate(pr.project_id)
        return pr

    async def update_request(
        self, project_id: int, request_id: int, data: PaymentRequestUpdate
    ) -> PaymentRequest:
        pr = await self.get_request(project_id, request_id)
        if pr is None:
            raise PaymentRequestValidationError(["Заявка не найдена"])
        if pr.status not in (
            PaymentRequestStatus.DRAFT.value,
            PaymentRequestStatus.PENDING_REVIEW.value,
        ):
            raise ValueError("Редактировать можно только до создания платёжки в банке")
        if data.category is not None and not await pcs.is_valid_code(self.db, project_id, data.category):
            raise PaymentRequestValidationError([f"Неизвестная категория оплаты: {data.category}"])
        for field, value in data.model_dump(exclude_unset=True).items():
            if field in _EDITABLE_FIELDS:  # allowlist — не даём писать status/bank_*/matched_* при расширении схемы
                setattr(pr, field, value)
        await self.db.commit()
        await self.db.refresh(pr)
        await _invalidate(pr.project_id)
        return pr

    async def submit_request(self, project_id: int, request_id: int, comment: str | None = None) -> PaymentRequest:
        pr = await self.get_request(project_id, request_id)
        if pr is None:
            raise PaymentRequestValidationError(["Заявка не найдена"])

        # Новый flow создаёт заявку сразу PENDING_REVIEW — submit идемпотентен (legacy DRAFT).
        if pr.status == PaymentRequestStatus.PENDING_REVIEW.value:
            return pr

        missing = self._validate_complete(pr)
        if missing:
            raise PaymentRequestValidationError(missing)

        prs.apply_transition(
            self.db, pr, PaymentRequestStatus.PENDING_REVIEW, changed_by="user", comment=comment or "Передана в оплату"
        )
        await self.db.commit()
        await self.db.refresh(pr)
        await _invalidate(pr.project_id)
        return pr

    async def cancel_request(
        self, project_id: int, request_id: int, comment: str | None = None
    ) -> PaymentRequest:
        """Отменить заявку (PENDING_REVIEW/DRAFT → CANCELLED). После отмены её заборы снова
        свободны (list_shippable/_assert_shipments_free исключают CANCELLED). Отмену заявки,
        по которой уже создана платёжка в банке (DRAFT_CREATED), не делаем — это требует
        отзыва документа на стороне банка."""
        pr = await self.get_request(project_id, request_id)
        if pr is None:
            raise PaymentRequestValidationError(["Заявка не найдена"])
        if pr.status not in (
            PaymentRequestStatus.PENDING_REVIEW.value,
            PaymentRequestStatus.DRAFT.value,
        ):
            raise ValueError("Отменить можно только заявку на проверке (до создания платёжки в банке)")
        prs.apply_transition(
            self.db, pr, PaymentRequestStatus.CANCELLED, changed_by="user", comment=comment or "Отменено"
        )
        await self.db.commit()
        await self.db.refresh(pr)
        await _invalidate(pr.project_id)
        return pr

    async def approve_request(
        self, project_id: int, request_id: int, comment: str | None = None, changed_by: str = "user"
    ) -> PaymentRequest:
        """Согласовать заявку (PENDING_REVIEW → APPROVED). Оплату админ проводит вне системы."""
        pr = await self.get_request(project_id, request_id)
        if pr is None:
            raise PaymentRequestValidationError(["Заявка не найдена"])
        prs.apply_transition(
            self.db, pr, PaymentRequestStatus.APPROVED, changed_by=changed_by, comment=comment or "Согласовано"
        )
        if pr.project_id is None:  # общую заявку согласует админ любого проекта — фиксируем кто
            logger.info("general payment request approved: id=%d by=%s", pr.id, changed_by)
        await self.db.commit()
        await self.db.refresh(pr)
        await _invalidate(pr.project_id)
        return pr

    async def reject_request(
        self, project_id: int, request_id: int, comment: str | None = None, changed_by: str = "user"
    ) -> PaymentRequest:
        """Отклонить заявку (PENDING_REVIEW/APPROVED → REJECTED). Терминально; заборы освобождаются."""
        pr = await self.get_request(project_id, request_id)
        if pr is None:
            raise PaymentRequestValidationError(["Заявка не найдена"])
        prs.apply_transition(
            self.db, pr, PaymentRequestStatus.REJECTED, changed_by=changed_by, comment=comment or "Отклонено"
        )
        if pr.project_id is None:  # общую заявку отклоняет админ любого проекта — фиксируем кто
            logger.info("general payment request rejected: id=%d by=%s", pr.id, changed_by)
        await self.db.commit()
        await self.db.refresh(pr)
        await _invalidate(pr.project_id)
        return pr

    async def mark_paid_request(
        self, project_id: int, request_id: int, comment: str | None = None, changed_by: str = "user"
    ) -> PaymentRequest:
        """Отметить согласованную заявку оплаченной вручную (APPROVED → PAID)."""
        pr = await self.get_request(project_id, request_id)
        if pr is None:
            raise PaymentRequestValidationError(["Заявка не найдена"])
        prs.apply_transition(
            self.db, pr, PaymentRequestStatus.PAID, changed_by=changed_by, comment=comment or "Оплачено вручную"
        )
        await self.db.commit()
        await self.db.refresh(pr)
        await _invalidate(pr.project_id)
        return pr

    # ─── validation ───────────────────────────────────────────────────────

    async def _assert_shipments_free(
        self, project_id: int, shipment_ids: list[int], exclude_request_id: int | None = None
    ) -> None:
        """Проверить, что ни один забор ещё не привязан к активной заявке на оплату."""
        q = (
            select(PaymentRequestShipment.outbound_shipment_id)
            .join(PaymentRequest, PaymentRequest.id == PaymentRequestShipment.payment_request_id)
            .where(
                PaymentRequestShipment.project_id == project_id,
                PaymentRequestShipment.outbound_shipment_id.in_(shipment_ids),
                PaymentRequest.is_deleted == False,  # noqa: E712
                PaymentRequest.status.notin_(
                    [PaymentRequestStatus.CANCELLED.value, PaymentRequestStatus.REJECTED.value]
                ),
            )
        )
        if exclude_request_id is not None:
            q = q.where(PaymentRequest.id != exclude_request_id)
        taken = sorted({r for (r,) in (await self.db.execute(q)).all()})
        if taken:
            raise PaymentRequestValidationError(
                [f"Забор (отгрузка id={t}) уже привязан к активной заявке" for t in taken]
            )

    @staticmethod
    def _build_purpose(shipments: list[OutboundShipment], pickup_date: date | None) -> str:
        """Назначение платежа: кол-во палет + дата(ы) забора + куда сдано (≤210 симв.)."""
        if not shipments:
            ref = f"от {pickup_date.strftime('%d.%m.%Y')}" if pickup_date else ""
            return f"Оплата транспортных услуг {ref}".strip()
        pallets = sum(s.pallets_count or 0 for s in shipments)
        dates = sorted({d for d in (s.pickup_date or s.shipped_date for s in shipments) if d})
        dests: list[str] = []
        for s in shipments:
            if s.destination and s.destination not in dests:
                dests.append(s.destination)
        parts = ["Транспортные услуги"]
        if pallets:
            parts.append(f"{pallets} палет")
        if dates:
            if len(dates) == 1:
                parts.append(f"забор {dates[0].strftime('%d.%m.%Y')}")
            else:
                parts.append(f"забор {dates[0].strftime('%d.%m')}–{dates[-1].strftime('%d.%m.%Y')}")
        if dests:
            parts.append("сдача " + ", ".join(dests))
        return ", ".join(parts)[:210]

    @staticmethod
    def _validate_complete(pr: PaymentRequest) -> list[str]:
        missing: list[str] = []
        if not pr.payee_inn:
            missing.append("Не указан ИНН получателя")
        if not pr.payee_account:
            missing.append("Не указан расчётный счёт")
        if not pr.payee_bik:
            missing.append("Не указан БИК")
        if not pr.payee_name:
            missing.append("Не указано наименование получателя")
        if pr.amount is None or pr.amount <= 0:
            missing.append("Не указана стоимость")
        active_docs: Sequence = [d for d in pr.documents if not d.is_deleted]
        if not any(d.doc_type == PaymentRequestDocType.INVOICE.value for d in active_docs):
            missing.append("Не приложен счёт")
        if not any(d.doc_type == PaymentRequestDocType.ACT.value for d in active_docs):
            missing.append("Не приложен акт")
        return missing
