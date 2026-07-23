# ruff: noqa: RUF002, RUF003
"""
FF billing — счета ФФ: CRUD, upload+parse, reconcile (пересборка строк + сверка
с нашим расчётом), кандидаты-платежи по реквизитам юрлиц склада, link/unlink,
создание заявки на оплату.

Допуск сверки: |amount − our_amount| ≤ max(0.01, 1% amount) → RECONCILED,
иначе DISPUTED. PAID сверять можно — статус остаётся PAID.
"""

import io
import logging
import re
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.config import settings
from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.counterparty import Counterparty
from backend.models.ff_billing import (
    FfInvoice,
    FfInvoiceKind,
    FfInvoiceLine,
    FfInvoiceLineKind,
    FfInvoiceStatus,
    FfServiceType,
    FfStorageDaily,
)
from backend.models.payment_request import PaymentRequest, PaymentRequestCategory
from backend.models.refs import Account
from backend.models.transactions import Transaction
from backend.models.warehouse import (
    InboundReceipt,
    InboundStatus,
    OutboundShipment,
    OutboundStatus,
    Warehouse,
    WarehouseCounterparty,
)
from backend.schemas.ff_billing import (
    FfCandidatePayment,
    FfInvoiceCreatePayload,
    FfInvoiceDetail,
    FfInvoiceLineRow,
    FfInvoiceListResponse,
    FfInvoicePaymentLinkPayload,
    FfInvoicePaymentUnlinkPayload,
    FfInvoiceRow,
    FfInvoiceUpdatePayload,
    FfInvoiceUploadResponse,
)
from backend.services.ff_billing.expected import compute_assembly_expected_cost
from backend.services.ff_billing.tariffs import _get_warehouse, resolve_rates
from backend.utils.time import utcnow

logger = logging.getLogger("dds.ff_billing")

_LIST_LIMIT = 500
_LINES_LIMIT = 1000
_CANDIDATES_LIMIT = 200
_MATCH_TOLERANCE = Decimal("0.01")
_CANDIDATE_WINDOW_DAYS = 45
_TWO_PLACES = Decimal("0.01")

_KIND_LABELS = {
    FfInvoiceKind.SHIPMENT.value: "услуги отправок",
    FfInvoiceKind.STORAGE.value: "хранение",
    FfInvoiceKind.RECEIVING.value: "выгрузку поставок",
    FfInvoiceKind.LOGISTICS.value: "перевозку",
    FfInvoiceKind.MIXED.value: "услуги фулфилмента",
}


# ─── Хелперы ─────────────────────────────────────────────────────────────────


async def _get_invoice(db: AsyncSession, project_id: int, invoice_id: int) -> FfInvoice:
    res = await db.execute(
        select(FfInvoice).where(
            FfInvoice.id == invoice_id,
            FfInvoice.project_id == project_id,
            FfInvoice.is_deleted == False,  # noqa: E712
        )
    )
    inv = res.scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="Счёт не найден")
    return inv


async def _warehouse_cp_ids(db: AsyncSession, project_id: int, warehouse_id: int) -> set[int]:
    """Юрлица склада: основное (Warehouse.counterparty_id) + доп. (WarehouseCounterparty)."""
    out: set[int] = set()
    main = (
        await db.execute(
            select(Warehouse.counterparty_id).where(
                Warehouse.id == warehouse_id,
                Warehouse.project_id == project_id,
                Warehouse.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if main:
        out.add(main)
    extra = await db.execute(
        select(WarehouseCounterparty.counterparty_id).where(
            WarehouseCounterparty.project_id == project_id,
            WarehouseCounterparty.warehouse_id == warehouse_id,
        )
    )
    out.update(cid for (cid,) in extra.all() if cid)
    return out


async def _warehouse_inns(db: AsyncSession, project_id: int, warehouse_id: int) -> set[str]:
    cp_ids = await _warehouse_cp_ids(db, project_id, warehouse_id)
    if not cp_ids:
        return set()
    rows = await db.execute(
        select(Counterparty.inn).where(
            Counterparty.project_id == project_id,
            Counterparty.id.in_(cp_ids),
            Counterparty.is_deleted == False,  # noqa: E712
            Counterparty.inn.isnot(None),
        )
    )
    return {inn.strip() for (inn,) in rows.all() if inn and inn.strip()}


def _reconciled_status(inv: FfInvoice) -> str:
    """Статус по допуску сверки; our_amount NULL → NEW (сверки не было)."""
    if inv.our_amount is None:
        return FfInvoiceStatus.NEW.value
    tolerance = max(_MATCH_TOLERANCE, (inv.amount * Decimal("0.01")))
    if abs(inv.amount - inv.our_amount) <= tolerance:
        return FfInvoiceStatus.RECONCILED.value
    return FfInvoiceStatus.DISPUTED.value


async def _names(
    db: AsyncSession, project_id: int, invoices: list[FfInvoice]
) -> tuple[dict[int, str], dict[int, str]]:
    """(warehouse_id→name, counterparty_id→name) для обогащения строк списка."""
    wh_ids = {i.warehouse_id for i in invoices if i.warehouse_id}
    cp_ids = {i.counterparty_id for i in invoices if i.counterparty_id}
    wh_names: dict[int, str] = {}
    cp_names: dict[int, str] = {}
    if wh_ids:
        rows = await db.execute(
            select(Warehouse.id, Warehouse.name).where(
                Warehouse.project_id == project_id, Warehouse.id.in_(wh_ids)
            )
        )
        wh_names = {i: n for i, n in rows.all()}
    if cp_ids:
        rows = await db.execute(
            select(Counterparty.id, Counterparty.name).where(
                Counterparty.project_id == project_id, Counterparty.id.in_(cp_ids)
            )
        )
        cp_names = {i: n for i, n in rows.all()}
    return wh_names, cp_names


def _row(inv: FfInvoice, wh_names: dict[int, str], cp_names: dict[int, str]) -> FfInvoiceRow:
    row = FfInvoiceRow.model_validate(inv)
    row.warehouse_name = wh_names.get(inv.warehouse_id) if inv.warehouse_id else None
    row.counterparty_name = cp_names.get(inv.counterparty_id) if inv.counterparty_id else None
    row.diff = (inv.amount - inv.our_amount) if inv.our_amount is not None else None
    row.has_document = bool(inv.minio_path)
    return row


async def get_invoice_detail(db: AsyncSession, project_id: int, invoice_id: int) -> FfInvoiceDetail:
    inv = await _get_invoice(db, project_id, invoice_id)
    wh_names, cp_names = await _names(db, project_id, [inv])
    base = _row(inv, wh_names, cp_names)

    lines = (
        await db.execute(
            select(FfInvoiceLine)
            .where(FfInvoiceLine.invoice_id == inv.id, FfInvoiceLine.project_id == project_id)
            .order_by(FfInvoiceLine.id.asc())
            .limit(_LINES_LIMIT)
        )
    ).scalars().all()

    # ref_number: номера заявок/приёмок/заборов одним батчем на тип.
    asm_ids = {ln.assembly_request_id for ln in lines if ln.assembly_request_id}
    rec_ids = {ln.inbound_receipt_id for ln in lines if ln.inbound_receipt_id}
    shp_ids = {ln.outbound_shipment_id for ln in lines if ln.outbound_shipment_id}
    asm_num: dict[int, str] = {}
    rec_num: dict[int, str] = {}
    shp_num: dict[int, str] = {}
    if asm_ids:
        rows = await db.execute(
            select(AssemblyRequest.id, AssemblyRequest.number).where(
                AssemblyRequest.project_id == project_id, AssemblyRequest.id.in_(asm_ids)
            )
        )
        asm_num = {i: n for i, n in rows.all()}
    if rec_ids:
        rows = await db.execute(
            select(InboundReceipt.id, InboundReceipt.number).where(
                InboundReceipt.project_id == project_id, InboundReceipt.id.in_(rec_ids)
            )
        )
        rec_num = {i: n for i, n in rows.all()}
    if shp_ids:
        rows = await db.execute(
            select(OutboundShipment.id, OutboundShipment.number).where(
                OutboundShipment.project_id == project_id, OutboundShipment.id.in_(shp_ids)
            )
        )
        shp_num = {i: n for i, n in rows.all()}

    line_rows: list[FfInvoiceLineRow] = []
    for ln in lines:
        lr = FfInvoiceLineRow.model_validate(ln)
        lr.ref_number = (
            asm_num.get(ln.assembly_request_id)
            if ln.assembly_request_id
            else rec_num.get(ln.inbound_receipt_id)
            if ln.inbound_receipt_id
            else shp_num.get(ln.outbound_shipment_id)
            if ln.outbound_shipment_id
            else None
        )
        line_rows.append(lr)

    return FfInvoiceDetail(
        **base.model_dump(),
        comment=inv.comment,
        original_filename=inv.original_filename,
        lines=line_rows,
    )


# ─── CRUD ────────────────────────────────────────────────────────────────────


@cached(prefix="ff_billing:invoices", ttl=300)
async def list_invoices(
    db: AsyncSession,
    project_id: int,
    *,
    warehouse_id: int | None = None,
    status: str | None = None,
    kind: str | None = None,
) -> dict:
    """JSON-сериализуемый ответ (кэшируется) — роутер валидирует в FfInvoiceListResponse."""
    conditions = [
        FfInvoice.project_id == project_id,
        FfInvoice.is_deleted == False,  # noqa: E712
    ]
    if warehouse_id is not None:
        conditions.append(FfInvoice.warehouse_id == warehouse_id)
    if status is not None:
        conditions.append(FfInvoice.status == status)
    if kind is not None:
        conditions.append(FfInvoice.kind == kind)

    total = (await db.execute(select(func.count()).select_from(FfInvoice).where(*conditions))).scalar() or 0
    invoices = (
        await db.execute(
            select(FfInvoice)
            .where(*conditions)
            .order_by(FfInvoice.invoice_date.desc().nullslast(), FfInvoice.id.desc())
            .limit(_LIST_LIMIT)
        )
    ).scalars().all()
    wh_names, cp_names = await _names(db, project_id, list(invoices))
    return FfInvoiceListResponse(
        items=[_row(i, wh_names, cp_names) for i in invoices], total=int(total)
    ).model_dump(mode="json")


async def create_invoice(
    db: AsyncSession, project_id: int, payload: FfInvoiceCreatePayload
) -> FfInvoiceDetail:
    if payload.warehouse_id is not None:
        await _get_warehouse(db, project_id, payload.warehouse_id)

    counterparty_id: int | None = None
    if payload.payee_inn:
        counterparty_id = (
            await db.execute(
                select(Counterparty.id).where(
                    Counterparty.project_id == project_id,
                    Counterparty.inn == payload.payee_inn,
                    Counterparty.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
    if counterparty_id is None and payload.warehouse_id is not None:
        cp_ids = await _warehouse_cp_ids(db, project_id, payload.warehouse_id)
        if len(cp_ids) == 1:
            counterparty_id = next(iter(cp_ids))

    inv = FfInvoice(
        project_id=project_id,
        warehouse_id=payload.warehouse_id,
        counterparty_id=counterparty_id,
        number=payload.number,
        invoice_date=payload.invoice_date,
        period_start=payload.period_start,
        period_end=payload.period_end,
        kind=payload.kind,
        status=FfInvoiceStatus.NEW.value,
        amount=payload.amount,
        payee_inn=payload.payee_inn,
        comment=payload.comment,
    )
    db.add(inv)
    await db.commit()
    await invalidate_cache("ff_billing:invoices")
    return await get_invoice_detail(db, project_id, inv.id)


async def update_invoice(
    db: AsyncSession, project_id: int, invoice_id: int, payload: FfInvoiceUpdatePayload
) -> FfInvoiceDetail:
    inv = await _get_invoice(db, project_id, invoice_id)
    data = payload.model_dump(exclude_unset=True)
    if "warehouse_id" in data and data["warehouse_id"] is not None:
        await _get_warehouse(db, project_id, data["warehouse_id"])
    for field in ("warehouse_id", "number", "invoice_date", "period_start", "period_end", "comment"):
        if field in data:
            setattr(inv, field, data[field])
    if data.get("kind") is not None:
        inv.kind = data["kind"]
    if data.get("amount") is not None:
        inv.amount = data["amount"]
        if inv.status in (FfInvoiceStatus.RECONCILED.value, FfInvoiceStatus.DISPUTED.value):
            inv.status = _reconciled_status(inv)  # сумма изменилась — допуск пересчитывается
    await db.commit()
    await invalidate_cache("ff_billing:invoices")
    return await get_invoice_detail(db, project_id, invoice_id)


async def delete_invoice(db: AsyncSession, project_id: int, invoice_id: int) -> None:
    inv = await _get_invoice(db, project_id, invoice_id)
    inv.soft_delete()
    await db.commit()
    await invalidate_cache("ff_billing:invoices")


# ─── Upload + parse ──────────────────────────────────────────────────────────


async def resolve_warehouse_by_inn(
    db: AsyncSession, project_id: int, inn: str
) -> tuple[int | None, list[str]]:
    """ИНН → контрагент → склад(ы), где он юрлицо (основное или доп.).
    Ровно один склад → его id; 0 или >1 → (None, warning)."""
    cp_id = (
        await db.execute(
            select(Counterparty.id).where(
                Counterparty.project_id == project_id,
                Counterparty.inn == inn,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if cp_id is None:
        return None, [f"Контрагент с ИНН {inn} не найден — выберите склад вручную"]

    wh_ids: set[int] = set()
    rows = await db.execute(
        select(Warehouse.id).where(
            Warehouse.project_id == project_id,
            Warehouse.counterparty_id == cp_id,
            Warehouse.is_deleted == False,  # noqa: E712
        )
    )
    wh_ids.update(i for (i,) in rows.all())
    rows = await db.execute(
        select(WarehouseCounterparty.warehouse_id)
        .join(Warehouse, Warehouse.id == WarehouseCounterparty.warehouse_id)
        .where(
            WarehouseCounterparty.project_id == project_id,
            WarehouseCounterparty.counterparty_id == cp_id,
            Warehouse.is_deleted == False,  # noqa: E712
        )
    )
    wh_ids.update(i for (i,) in rows.all())

    if len(wh_ids) == 1:
        return next(iter(wh_ids)), []
    if not wh_ids:
        return None, [f"Склад с юрлицом ИНН {inn} не найден — выберите вручную"]
    return None, [f"Несколько складов с юрлицом ИНН {inn} — выберите вручную"]


async def upload_invoice(
    db: AsyncSession,
    project_id: int,
    *,
    file_data: bytes,
    filename: str,
    mime_type: str | None,
) -> FfInvoiceUploadResponse:
    """Файл счёта → распознавание (invoice_parser) → черновик FfInvoice (NEW/MIXED)
    + файл в MinIO. Размер/тип файла валидирует роутер ДО вызова."""
    from backend.services import invoice_parser
    from backend.storage import get_minio

    own_rows = (
        await db.execute(
            select(Account.account).where(
                Account.project_id == project_id,
                Account.is_our_account.is_(True),
                Account.is_deleted.is_(False),
            )
        )
    ).scalars().all()
    own_accounts = frozenset(re.sub(r"\D", "", a) for a in own_rows if a)

    result = await invoice_parser.parse_invoice_async(file_data, filename, own_accounts)
    warnings = list(result.warnings)
    if result.amount is None or result.amount <= 0:
        raise HTTPException(
            status_code=422,
            detail="Не удалось распознать сумму счёта — создайте счёт вручную",
        )

    warehouse_id: int | None = None
    counterparty_id: int | None = None
    if result.payee_inn:
        warehouse_id, wh_warnings = await resolve_warehouse_by_inn(db, project_id, result.payee_inn)
        warnings.extend(wh_warnings)
        counterparty_id = (
            await db.execute(
                select(Counterparty.id).where(
                    Counterparty.project_id == project_id,
                    Counterparty.inn == result.payee_inn,
                    Counterparty.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
    else:
        warnings.append("ИНН получателя не распознан — укажите склад вручную")

    inv = FfInvoice(
        project_id=project_id,
        warehouse_id=warehouse_id,
        counterparty_id=counterparty_id,
        kind=FfInvoiceKind.MIXED.value,
        status=FfInvoiceStatus.NEW.value,
        amount=result.amount,
        payee_inn=result.payee_inn,
    )
    db.add(inv)
    await db.flush()

    client = await get_minio()
    if client is None:
        raise HTTPException(status_code=503, detail="File storage (MinIO) unavailable")
    object_name = f"ff_invoices/{inv.id}/{utcnow().strftime('%Y%m%d_%H%M%S')}_{filename}"
    await client.put_object(
        settings.MINIO_BUCKET,
        object_name,
        io.BytesIO(file_data),
        length=len(file_data),
        content_type=mime_type or "application/octet-stream",
    )
    inv.minio_path = object_name
    inv.original_filename = filename
    inv.mime_type = mime_type
    await db.commit()
    await invalidate_cache("ff_billing:invoices")

    detail = await get_invoice_detail(db, project_id, inv.id)
    return FfInvoiceUploadResponse(invoice=detail, parsed_warehouse_id=warehouse_id, warnings=warnings)


async def download_invoice_document(
    db: AsyncSession, project_id: int, invoice_id: int
) -> tuple[bytes, str, str]:
    from backend.storage import download_file

    inv = await _get_invoice(db, project_id, invoice_id)
    if not inv.minio_path:
        raise HTTPException(status_code=404, detail="У счёта нет файла")
    data = await download_file(inv.minio_path)
    if data is None:
        raise HTTPException(status_code=503, detail="Файл недоступен (MinIO)")
    return data, inv.original_filename or "invoice", inv.mime_type or "application/octet-stream"


# ─── Reconcile ───────────────────────────────────────────────────────────────


async def reconcile_invoice(db: AsyncSession, project_id: int, invoice_id: int) -> FfInvoiceDetail:
    """Пересобрать строки счёта целиком (delete+insert) и сверить с нашим расчётом."""
    inv = await _get_invoice(db, project_id, invoice_id)
    if not inv.warehouse_id or not inv.period_start or not inv.period_end:
        raise HTTPException(status_code=422, detail="Для сверки укажите склад и период счёта")

    await db.execute(
        sa_delete(FfInvoiceLine).where(
            FfInvoiceLine.invoice_id == inv.id, FfInvoiceLine.project_id == project_id
        )
    )

    kind = inv.kind
    mixed = kind == FfInvoiceKind.MIXED.value
    lines: list[FfInvoiceLine] = []

    if mixed or kind == FfInvoiceKind.SHIPMENT.value:
        req_ids = (
            await db.execute(
                select(AssemblyRequest.id)
                .where(
                    AssemblyRequest.project_id == project_id,
                    AssemblyRequest.warehouse_id == inv.warehouse_id,
                    AssemblyRequest.is_deleted == False,  # noqa: E712
                    AssemblyRequest.status.in_(
                        [
                            AssemblyStatus.SHIPPED.value,
                            AssemblyStatus.DELIVERED.value,
                            AssemblyStatus.CLOSED.value,
                        ]
                    ),
                    AssemblyRequest.shipped_at.isnot(None),
                    func.date(AssemblyRequest.shipped_at) >= inv.period_start,
                    func.date(AssemblyRequest.shipped_at) <= inv.period_end,
                )
                .order_by(AssemblyRequest.shipped_at.asc(), AssemblyRequest.id.asc())
                .limit(_LINES_LIMIT)
            )
        ).scalars().all()
        for rid in req_ids:
            exp = await compute_assembly_expected_cost(db, project_id, rid)
            lines.append(
                FfInvoiceLine(
                    project_id=project_id,
                    invoice_id=inv.id,
                    kind=FfInvoiceLineKind.ASSEMBLY.value,
                    assembly_request_id=rid,
                    qty_units=exp.units,
                    qty_boxes=exp.boxes,
                    qty_pallets=exp.pallets,
                    our_cost=exp.total,
                )
            )

    if mixed or kind == FfInvoiceKind.STORAGE.value:
        rows = (
            await db.execute(
                select(FfStorageDaily.pallets, FfStorageDaily.storage_cost).where(
                    FfStorageDaily.project_id == project_id,
                    FfStorageDaily.warehouse_id == inv.warehouse_id,
                    FfStorageDaily.snapshot_date >= inv.period_start,
                    FfStorageDaily.snapshot_date <= inv.period_end,
                )
            )
        ).all()
        pallet_days = sum(int(p or 0) for p, _ in rows)
        costs = [c for _, c in rows if c is not None]
        storage_cost = sum(costs, Decimal("0")).quantize(_TWO_PLACES) if costs else None
        lines.append(
            FfInvoiceLine(
                project_id=project_id,
                invoice_id=inv.id,
                kind=FfInvoiceLineKind.STORAGE.value,
                date_from=inv.period_start,
                date_to=inv.period_end,
                qty_pallets=pallet_days,
                our_cost=storage_cost,
            )
        )

    if mixed or kind == FfInvoiceKind.RECEIVING.value:
        receipts = (
            await db.execute(
                select(InboundReceipt.id, InboundReceipt.actual_date)
                .where(
                    InboundReceipt.project_id == project_id,
                    InboundReceipt.warehouse_id == inv.warehouse_id,
                    InboundReceipt.is_deleted == False,  # noqa: E712
                    InboundReceipt.status == InboundStatus.ACCEPTED.value,
                    InboundReceipt.actual_date >= inv.period_start,
                    InboundReceipt.actual_date <= inv.period_end,
                )
                .order_by(InboundReceipt.actual_date.asc(), InboundReceipt.id.asc())
                .limit(_LINES_LIMIT)
            )
        ).all()
        rate_by_date: dict = {}
        for rec_id, actual_date in receipts:
            if actual_date not in rate_by_date:
                rates = await resolve_rates(db, project_id, inv.warehouse_id, actual_date)
                entry = rates.get(FfServiceType.TRUCK_UNLOADING.value)
                rate_by_date[actual_date] = entry[0] if entry else None
            rate = rate_by_date[actual_date]
            lines.append(
                FfInvoiceLine(
                    project_id=project_id,
                    invoice_id=inv.id,
                    kind=FfInvoiceLineKind.RECEIVING.value,
                    inbound_receipt_id=rec_id,
                    date_from=actual_date,
                    date_to=actual_date,
                    our_cost=rate,
                )
            )

    if mixed or kind == FfInvoiceKind.LOGISTICS.value:
        cp_ids = await _warehouse_cp_ids(db, project_id, inv.warehouse_id)
        if cp_ids:
            anchor = func.coalesce(OutboundShipment.pickup_date, OutboundShipment.shipped_date)
            ships = (
                await db.execute(
                    select(OutboundShipment)
                    .where(
                        OutboundShipment.project_id == project_id,
                        OutboundShipment.warehouse_id == inv.warehouse_id,
                        OutboundShipment.is_deleted == False,  # noqa: E712
                        OutboundShipment.status.in_(
                            [OutboundStatus.SHIPPED.value, OutboundStatus.DELIVERED.value]
                        ),
                        OutboundShipment.counterparty_id.in_(cp_ids),
                        anchor >= inv.period_start,
                        anchor <= inv.period_end,
                    )
                    .order_by(OutboundShipment.id.asc())
                    .limit(_LINES_LIMIT)
                )
            ).scalars().all()
            for s in ships:
                lines.append(
                    FfInvoiceLine(
                        project_id=project_id,
                        invoice_id=inv.id,
                        kind=FfInvoiceLineKind.LOGISTICS.value,
                        outbound_shipment_id=s.id,
                        date_from=s.pickup_date or s.shipped_date,
                        date_to=s.pickup_date or s.shipped_date,
                        our_cost=s.pickup_cost,
                    )
                )

    # Наш расчёт + аллокация суммы счёта по строкам (Σ allocated == amount
    # копейка-в-копейку: последняя строка добирает остаток).
    cost_lines = [ln for ln in lines if ln.our_cost is not None]
    total_cost = Decimal("0")
    for ln in cost_lines:
        total_cost += ln.our_cost or Decimal("0")
    our_amount = total_cost.quantize(_TWO_PLACES) if cost_lines else None
    inv.our_amount = our_amount
    if our_amount is not None and our_amount > 0:
        allocated_sum = Decimal("0")
        for ln in cost_lines[:-1]:
            share = (ln.our_cost or Decimal("0")) / our_amount
            alloc = (inv.amount * share).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
            ln.allocated_amount = alloc
            allocated_sum += alloc
        cost_lines[-1].allocated_amount = inv.amount - allocated_sum

    if inv.status != FfInvoiceStatus.PAID.value:
        inv.status = _reconciled_status(inv)

    db.add_all(lines)
    await db.commit()
    await invalidate_cache("ff_billing:invoices")
    return await get_invoice_detail(db, project_id, invoice_id)


# ─── Платежи ─────────────────────────────────────────────────────────────────


async def list_candidate_payments(
    db: AsyncSession, project_id: int, invoice_id: int
) -> list[FfCandidatePayment]:
    """Незаматченные дебеты по ИНН юрлиц склада счёта, окно period_end ± 45 дней.
    Дебеты, занятые заявкой на оплату или забором, исключаются; уже привязанные
    к другим счетам ФФ — остаются (недельный платёж кроет N счетов)."""
    inv = await _get_invoice(db, project_id, invoice_id)
    if inv.warehouse_id is None:
        raise HTTPException(status_code=422, detail="Сначала укажите склад счёта")
    inns = await _warehouse_inns(db, project_id, inv.warehouse_id)
    if not inns:
        return []

    anchor = inv.period_end or inv.invoice_date or inv.created_at.date()
    win_start = datetime.combine(anchor - timedelta(days=_CANDIDATE_WINDOW_DAYS), time.min)
    win_end = datetime.combine(anchor + timedelta(days=_CANDIDATE_WINDOW_DAYS), time.max)

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
        consumed.update(tid for (tid,) in (await db.execute(stmt)).all() if tid)

    txns = (
        await db.execute(
            select(Transaction)
            .where(
                Transaction.project_id == project_id,
                Transaction.is_deleted == False,  # noqa: E712
                Transaction.expense > 0,
                Transaction.inn.in_(inns),
                Transaction.date >= win_start,
                Transaction.date <= win_end,
            )
            .order_by(Transaction.date.desc(), Transaction.id.desc())
            .limit(_CANDIDATES_LIMIT)
        )
    ).scalars().all()
    txns = [t for t in txns if t.id not in consumed]

    linked: dict[int, list[int]] = {}
    if txns:
        rows = await db.execute(
            select(FfInvoice.id, FfInvoice.matched_transaction_id).where(
                FfInvoice.project_id == project_id,
                FfInvoice.is_deleted == False,  # noqa: E712
                FfInvoice.matched_transaction_id.in_([t.id for t in txns]),
            )
        )
        for inv_id, tid in rows.all():
            linked.setdefault(tid, []).append(inv_id)

    return [
        FfCandidatePayment(
            transaction_id=t.id,
            date=t.date.date(),
            amount=t.expense,
            counterparty=t.counterparty,
            inn=t.inn,
            purpose=t.purpose,
            already_linked_invoice_ids=sorted(linked.get(t.id, [])),
        )
        for t in txns
    ]


async def link_invoice_payments(
    db: AsyncSession, project_id: int, payload: FfInvoicePaymentLinkPayload
) -> list[FfInvoiceRow]:
    """Привязка N счетов → 1 дебет: |Σ amounts − expense| ≤ 0.01, все счета → PAID."""
    txn = (
        await db.execute(
            select(Transaction).where(
                Transaction.id == payload.transaction_id,
                Transaction.project_id == project_id,
                Transaction.is_deleted == False,  # noqa: E712
                Transaction.expense > 0,
            )
        )
    ).scalar_one_or_none()
    if txn is None:
        raise HTTPException(status_code=404, detail="Платёж (дебет) не найден")

    ids = list(dict.fromkeys(payload.invoice_ids))
    invoices = (
        await db.execute(
            select(FfInvoice).where(
                FfInvoice.id.in_(ids),
                FfInvoice.project_id == project_id,
                FfInvoice.is_deleted == False,  # noqa: E712
            )
        )
    ).scalars().all()
    if len(invoices) != len(ids):
        raise HTTPException(status_code=404, detail="Часть счетов не найдена")
    # Идемпотентность: повтор того же линка (все счета уже на этом дебете) — no-op,
    # двойной клик / сетевой ретрай не должен пугать 422-ой.
    if all(i.matched_transaction_id == txn.id for i in invoices):
        wh_names, cp_names = await _names(db, project_id, list(invoices))
        return [_row(i, wh_names, cp_names) for i in invoices]
    paid = [
        i
        for i in invoices
        if i.status == FfInvoiceStatus.PAID.value and i.matched_transaction_id != txn.id
    ]
    if paid:
        nums = ", ".join(i.number or f"#{i.id}" for i in paid)
        raise HTTPException(
            status_code=422, detail=f"Счёт уже оплачен другим платежом или заявкой: {nums}"
        )

    total = sum((i.amount for i in invoices), Decimal("0"))
    if abs(total - (txn.expense or Decimal("0"))) > _MATCH_TOLERANCE:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Сумма счетов {total} ₽ не сходится с платежом {txn.expense} ₽ — "
                "привязка возможна только при точном совпадении (допуск 1 коп.)"
            ),
        )

    now = utcnow()
    for inv in invoices:
        inv.matched_transaction_id = txn.id
        inv.matched_at = now
        inv.status = FfInvoiceStatus.PAID.value
    await db.commit()
    await invalidate_cache("ff_billing:invoices")

    wh_names, cp_names = await _names(db, project_id, list(invoices))
    return [_row(i, wh_names, cp_names) for i in invoices]


async def unlink_invoice_payments(
    db: AsyncSession, project_id: int, payload: FfInvoicePaymentUnlinkPayload
) -> list[FfInvoiceRow]:
    """Снять привязку к дебету; статус восстановить: our_amount есть →
    RECONCILED/DISPUTED по допуску, иначе NEW."""
    ids = list(dict.fromkeys(payload.invoice_ids))
    invoices = (
        await db.execute(
            select(FfInvoice).where(
                FfInvoice.id.in_(ids),
                FfInvoice.project_id == project_id,
                FfInvoice.is_deleted == False,  # noqa: E712
            )
        )
    ).scalars().all()
    if len(invoices) != len(ids):
        raise HTTPException(status_code=404, detail="Часть счетов не найдена")

    for inv in invoices:
        if inv.matched_transaction_id is None:
            continue
        inv.matched_transaction_id = None
        inv.matched_at = None
        inv.status = _reconciled_status(inv)
    await db.commit()
    await invalidate_cache("ff_billing:invoices")

    wh_names, cp_names = await _names(db, project_id, list(invoices))
    return [_row(i, wh_names, cp_names) for i in invoices]


# ─── Заявка на оплату ────────────────────────────────────────────────────────


async def create_payment_request_for_invoice(
    db: AsyncSession, project_id: int, invoice_id: int, user_id: int | None
) -> int:
    """Создать PaymentRequest (category=FULFILLMENT) по счёту и связать. 409, если уже есть."""
    from backend.schemas.payment_request import PaymentRequestCreate
    from backend.services.payment_request_service import (
        PaymentRequestService,
        PaymentRequestValidationError,
    )

    inv = await _get_invoice(db, project_id, invoice_id)
    if inv.payment_request_id is not None:
        raise HTTPException(status_code=409, detail="У счёта уже есть заявка на оплату")
    if inv.counterparty_id is None:
        raise HTTPException(
            status_code=422, detail="У счёта не указан контрагент — привяжите юрлицо ФФ"
        )

    period = ""
    if inv.period_start and inv.period_end:
        period = f" {inv.period_start.strftime('%d.%m.%Y')}–{inv.period_end.strftime('%d.%m.%Y')}"
    number = f" {inv.number}" if inv.number else ""
    purpose = f"Оплата счёта{number} за {_KIND_LABELS.get(inv.kind, inv.kind)}{period}"

    service = PaymentRequestService(db)
    try:
        pr = await service.create_request(
            project_id,
            PaymentRequestCreate(
                source="MANUAL",
                category=PaymentRequestCategory.FULFILLMENT.value,
                counterparty_id=inv.counterparty_id,
                amount=inv.amount,
                purpose=purpose,
            ),
            user_id,
        )
    except PaymentRequestValidationError as e:
        raise HTTPException(status_code=422, detail="; ".join(e.missing)) from e

    inv.payment_request_id = pr.id
    await db.commit()
    await invalidate_cache("ff_billing:invoices")
    return pr.id
