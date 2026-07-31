# ruff: noqa: RUF002, RUF003
"""
Service: migfull-портал — создание ПОСТАВКИ (приёмки) на склад ФФ «Натали» из DDS.

Источник — наша приёмка машины (``InboundReceipt`` склада «Натали», обычно
создана отгрузкой машины V-… со статусом EXPECTED). Раньше Натали заводила
приёмки (PVB-…) у себя вручную — теперь заводим ИЗ DDS: на портале это
зеркальный Filament-ресурс ``/app/submissions`` (в read-API — submissions,
kind=inbound в зеркале ``FulfillmentRequest``).

Креды/сессия/анти-дубль — общие с заявками на отгрузку
(``migfull_portal_service``): ``IntegrationKey(service="migfull_portal")``,
cookie-сессия с перелогином, audit ``MigfullShipmentOrder`` (здесь — с
``inbound_receipt_id`` вместо ``assembly_request_id``).

build_inbound_draft — локально (без портала): prefill шапки + превью описи
                      (короба/россыпь) + флаг «уже отправлена».
send_submission     — РЕАЛЬНОЕ создание поставки (шапка + загрузка описи).
                      НЕОБРАТИМО (у клиента портала нет delete/cancel).
                      Только по явной кнопке пользователя — никаких джобов.

Автосвязь: created guid сразу пишется в ``FulfillmentRequest(provider="migfull",
kind=inbound, inbound_receipt_id=…)`` — когда PVB прилетит read-синком, upsert
пойдёт по тому же (project, provider, external_id) и связь с нашей приёмкой
уже будет на месте (синк ручные связи не трогает).
"""

import asyncio

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.integrations.migfull_portal_client import MigfullPortalError
from backend.integrations.resilience import CircuitOpenError
from backend.models import (
    CostOrder,
    FulfillmentRequest,
    InboundReceipt,
    MigfullShipmentOrder,
    MigfullShipmentStatus,
)
from backend.models.fulfillment import FfRequestKind
from backend.models.warehouse import InboundStatus
from backend.schemas.migfull_portal import (
    MigfullInboundDraftResponse,
    MigfullInboundPrefill,
    MigfullInboundSendRequest,
    MigfullSendResult,
)
from backend.services.migfull_opis import OPIS_CONTENT_TYPE, build_opis_xlsx
from backend.services.migfull_portal_service import (
    MIGFULL_PROVIDER,
    MigfullPortalServiceError,
    _client_from_key,
    _get_key,
    _with_portal_session,
    compute_opis_lines_from_qty,
)
from backend.utils.time import utcnow

logger = structlog.get_logger("dds.migfull_portal_inbound")


# ─── Загрузка приёмки/машины ─────────────────────────────────────────────────


async def _load_receipt(db: AsyncSession, project_id: int, receipt_id: int) -> InboundReceipt | None:
    result = await db.execute(
        select(InboundReceipt)
        .where(
            InboundReceipt.id == receipt_id,
            InboundReceipt.project_id == project_id,
            InboundReceipt.is_deleted.is_(False),
        )
        .options(selectinload(InboundReceipt.items))
    )
    return result.scalar_one_or_none()


async def _load_vehicle(db: AsyncSession, project_id: int, receipt: InboundReceipt) -> CostOrder | None:
    """Машина-источник приёмки (V-…): InboundReceipt.cost_order_id → CostOrder."""
    if receipt.cost_order_id is None:
        return None
    result = await db.execute(
        select(CostOrder).where(
            CostOrder.id == receipt.cost_order_id,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted.is_(False),
        )
    )
    return result.scalar_one_or_none()


def _qty_maps(receipt: InboundReceipt) -> tuple[dict[str, int], dict[str, int]]:
    """Состав приёмки → {ШК: заявлено штук}, {ШК: nomenclature_id}."""
    qty_by_bc: dict[str, int] = {}
    nom_by_bc: dict[str, int] = {}
    for it in receipt.items:
        bc = (it.barcode or "").strip()
        if not bc:
            continue
        qty_by_bc[bc] = qty_by_bc.get(bc, 0) + int(it.expected_qty or 0)
        if it.nomenclature_id:
            nom_by_bc.setdefault(bc, it.nomenclature_id)
    return qty_by_bc, nom_by_bc


def _default_notes(receipt: InboundReceipt, vehicle: CostOrder | None) -> str:
    """Примечание поставки в портале Натали — оператор видит связь с DDS."""
    parts = ["DDS"]
    if vehicle is not None and vehicle.order_no:
        parts.append(f"машина {vehicle.order_no}")
    if receipt.number:
        parts.append(f"приёмка {receipt.number}")
    return " · ".join(parts)


def _opis_filename(receipt: InboundReceipt, vehicle: CostOrder | None) -> str:
    base = (vehicle.order_no if vehicle else None) or receipt.number or "opis"
    safe = "".join(ch for ch in base if ch.isalnum() or ch in "-_") or "opis"
    return f"opis_{safe}.xlsx"


# ─── Анти-дубль ──────────────────────────────────────────────────────────────


async def _already_pushed(
    db: AsyncSession, project_id: int, receipt_id: int
) -> tuple[bool, str | None, str | None]:
    """Уже создавали поставку для этой приёмки? (audit SENT либо связанная
    FulfillmentRequest kind=inbound — в т.ч. заведённая самой Натали и
    привязанная вручную: вторая поставка на тот же состав — дубль)."""
    order = (
        await db.execute(
            select(MigfullShipmentOrder)
            .where(
                MigfullShipmentOrder.project_id == project_id,
                MigfullShipmentOrder.inbound_receipt_id == receipt_id,
                MigfullShipmentOrder.status == MigfullShipmentStatus.SENT,
            )
            .order_by(MigfullShipmentOrder.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if order is not None:
        return True, order.shipment_guid, order.shipment_number
    existing = (
        await db.execute(
            select(FulfillmentRequest.external_id, FulfillmentRequest.number)
            .where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.provider == MIGFULL_PROVIDER,
                FulfillmentRequest.kind == FfRequestKind.INBOUND.value,
                FulfillmentRequest.inbound_receipt_id == receipt_id,
            )
            .limit(1)
        )
    ).first()
    if existing is not None:
        return True, existing.external_id, existing.number
    return False, None, None


# ─── Draft (confirm-модалка) ─────────────────────────────────────────────────


async def build_inbound_draft(db: AsyncSession, project_id: int, receipt_id: int) -> MigfullInboundDraftResponse:
    key = await _get_key(db, project_id)
    receipt = await _load_receipt(db, project_id, receipt_id)
    if receipt is None:
        raise MigfullPortalServiceError("Приёмка не найдена", status_code=404)
    vehicle = await _load_vehicle(db, project_id, receipt)

    eligible = key.warehouse_id is not None and receipt.warehouse_id == key.warehouse_id
    qty_by_bc, nom_by_bc = _qty_maps(receipt)
    lines, warnings = await compute_opis_lines_from_qty(db, project_id, receipt.warehouse_id, qty_by_bc, nom_by_bc)
    already, guid, number = await _already_pushed(db, project_id, receipt_id)

    prefill = MigfullInboundPrefill(
        number=(vehicle.order_no if vehicle else None) or receipt.number,
        submission_date=receipt.planned_date or (vehicle.estimated_arrival_date if vehicle else None),
        notes=_default_notes(receipt, vehicle),
        vehicle_order_no=vehicle.order_no if vehicle else None,
        receipt_number=receipt.number,
    )
    return MigfullInboundDraftResponse(
        eligible=eligible,
        already_sent=already,
        sent_guid=guid,
        sent_number=number,
        prefill=prefill,
        opis_lines=lines,
        total_boxes=sum(line.quantity for line in lines if line.is_box),
        total_pieces=sum(line.pieces for line in lines),
        warnings=list(warnings),
    )


# ─── Автосвязь созданной поставки с нашей приёмкой ───────────────────────────


async def _upsert_ff_link(
    db: AsyncSession, project_id: int, warehouse_id: int, receipt_id: int,
    guid: str, number: str | None, total_qty: int,
) -> None:
    """Связать созданную поставку (provider=migfull, external_id=guid) с приёмкой.

    Read-sync НЕ трогает inbound_receipt_id на upsert → связь переживает синки:
    прилетевшая синком PVB сольётся в эту же строку по (project, provider, guid).
    """
    existing = (
        await db.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.provider == MIGFULL_PROVIDER,
                FulfillmentRequest.external_id == guid,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.inbound_receipt_id = receipt_id
        if number:
            existing.number = number
        return
    db.add(
        FulfillmentRequest(
            project_id=project_id,
            warehouse_id=warehouse_id,
            provider=MIGFULL_PROVIDER,
            external_id=guid,
            number=number,
            kind=FfRequestKind.INBOUND.value,
            type_name="Приёмка",
            status="new",
            inbound_receipt_id=receipt_id,
            total_qty=total_qty or None,
            synced_at=utcnow(),
        )
    )


async def _record_outcome(
    db: AsyncSession, *, project_id: int, receipt_id: int, warehouse_id: int,
    status: str, guid: str | None, reference: str | None, payload: dict,
    filename: str, excerpt: str | None, error: str | None, actor: str | None,
    total_qty: int,
) -> MigfullShipmentOrder:
    """Зафиксировать исход. Audit-строку коммитим ПЕРВОЙ (необратимый факт создания
    не должен зависеть от FF-link); затем best-effort связь с приёмкой, терпящая
    гонку с read-sync по тому же guid."""
    order = MigfullShipmentOrder(
        project_id=project_id, inbound_receipt_id=receipt_id, status=status,
        shipment_guid=guid, shipment_number=reference, payload=payload,
        opis_filename=filename, response_excerpt=excerpt, error=error, created_by=actor,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)

    if guid and status in (MigfullShipmentStatus.SENT, MigfullShipmentStatus.UNCERTAIN):
        try:
            await _upsert_ff_link(db, project_id, warehouse_id, receipt_id, guid, reference, total_qty)
            await db.commit()
        except IntegrityError:
            # Гонка: read-sync создал FulfillmentRequest с тем же guid между SELECT и INSERT.
            await db.rollback()
            existing = (
                await db.execute(
                    select(FulfillmentRequest).where(
                        FulfillmentRequest.project_id == project_id,
                        FulfillmentRequest.provider == MIGFULL_PROVIDER,
                        FulfillmentRequest.external_id == guid,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.inbound_receipt_id = receipt_id
                if reference:
                    existing.number = reference
                await db.commit()
            else:
                logger.warning("migfull_portal_inbound.ff_link_race", project_id=project_id, guid=guid)
    return order


# ─── Send (реальное создание поставки) ───────────────────────────────────────


async def send_submission(
    db: AsyncSession, project_id: int, receipt_id: int, req: MigfullInboundSendRequest, actor: str | None = None
) -> MigfullSendResult:
    key = await _get_key(db, project_id)
    receipt = await _load_receipt(db, project_id, receipt_id)
    if receipt is None:
        raise MigfullPortalServiceError("Приёмка не найдена", status_code=404)
    if key.warehouse_id is None or receipt.warehouse_id != key.warehouse_id:
        raise MigfullPortalServiceError(
            "Эта приёмка не на склад ФФ «Натали» — создание поставки недоступно", status_code=400
        )
    if receipt.status == InboundStatus.CANCELLED.value:
        raise MigfullPortalServiceError("Нельзя создать поставку по отменённой приёмке", status_code=400)
    if receipt.status == InboundStatus.ACCEPTED.value:
        raise MigfullPortalServiceError("Приёмка уже принята — поставка у ФФ не нужна", status_code=400)

    # Анти-дубль: создание НЕОБРАТИМО (нет delete/cancel у клиента портала).
    if not req.force_resend:
        already, _, num = await _already_pushed(db, project_id, receipt_id)
        if already:
            raise MigfullPortalServiceError(
                f"Поставка для этой приёмки уже есть в ФФ ({num or '—'}). Подтвердите повторную отправку.",
                status_code=409,
            )

    qty_by_bc, nom_by_bc = _qty_maps(receipt)
    lines, warnings = await compute_opis_lines_from_qty(db, project_id, receipt.warehouse_id, qty_by_bc, nom_by_bc)
    if not lines:
        raise MigfullPortalServiceError("В приёмке нет позиций для описи", status_code=400)

    vehicle = await _load_vehicle(db, project_id, receipt)
    submission_date = (
        req.submission_date or receipt.planned_date or (vehicle.estimated_arrival_date if vehicle else None)
    )
    number = req.number or (vehicle.order_no if vehicle else None) or receipt.number
    header: dict[str, object] = {
        "number": number,
        "submission_date": submission_date.isoformat() if submission_date else None,
        "notes": req.notes or _default_notes(receipt, vehicle),
    }
    xlsx = build_opis_xlsx(
        lines,
        incoming_number=number,
        incoming_date=submission_date.isoformat() if submission_date else None,
    )
    filename = _opis_filename(receipt, vehicle)
    payload = {"header": {k: v for k, v in header.items() if v is not None},
               "opis_lines": [line.model_dump() for line in lines], "warnings": warnings}

    total_pieces = sum(line.pieces for line in lines)
    status = MigfullShipmentStatus.FAILED
    guid: str | None = None
    reference: str | None = None
    message = ""
    excerpt: str | None = None
    error: str | None = None

    def _uncertain_if_guid() -> str:
        # guid есть → шапка УЖЕ создана на портале (необратимо), даже если опись упала.
        return MigfullShipmentStatus.UNCERTAIN if guid else MigfullShipmentStatus.FAILED

    try:
        async with _client_from_key(key) as client:
            created = await _with_portal_session(client, lambda: client.create_submission(header))
            guid = created.guid
            upload = await _with_portal_session(
                client, lambda: client.upload_submission_opis(guid, filename, xlsx, OPIS_CONTENT_TYPE)
            )
        reference = upload.reference
        status = MigfullShipmentStatus.SENT if upload.ok else MigfullShipmentStatus.UNCERTAIN
        message = (
            f"Поставка создана ({reference or guid}), опись загружена"
            if upload.ok
            else f"Поставка создана ({reference or guid}), но загрузка описи не подтверждена — проверьте в кабинете"
        )
        excerpt = upload.excerpt or None
    except asyncio.CancelledError:
        # Окно отмены после создания шапки: поставка могла уже создаться (guid) —
        # фиксируем audit ДО re-raise (shield), иначе анти-дубль ослепнет.
        status = _uncertain_if_guid()
        error = message = "Запрос отменён во время отправки — проверьте поставку в кабинете ФФ."
        await asyncio.shield(
            _record_outcome(
                db, project_id=project_id, receipt_id=receipt.id, warehouse_id=receipt.warehouse_id,
                status=status, guid=guid, reference=reference, payload=payload,
                filename=filename, excerpt=None, error=error, actor=actor,
                total_qty=total_pieces,
            )
        )
        raise
    except MigfullPortalError as e:
        message = error = str(e)
        status = _uncertain_if_guid()
    except (httpx.HTTPError, CircuitOpenError, ValueError) as e:
        message = error = f"Ошибка связи с порталом ФФ: {e}. Проверьте в кабинете перед повтором."
        status = _uncertain_if_guid()
    except Exception as e:  # noqa: BLE001 — необратимо: всегда фиксируем audit-исход, не ретраим
        message = error = f"Непредвиденная ошибка отправки: {e}. Проверьте в кабинете."
        status = _uncertain_if_guid()

    order = await _record_outcome(
        db, project_id=project_id, receipt_id=receipt.id, warehouse_id=receipt.warehouse_id,
        status=status, guid=guid, reference=reference, payload=payload,
        filename=filename, excerpt=excerpt, error=error, actor=actor,
        total_qty=total_pieces,
    )

    logger.info("migfull_portal_inbound.send", project_id=project_id, receipt_id=receipt_id, status=status, guid=guid)
    return MigfullSendResult(
        ok=(status == MigfullShipmentStatus.SENT),
        shipment_guid=guid,
        shipment_number=reference,
        message=message,
        order_id=order.id,
    )
