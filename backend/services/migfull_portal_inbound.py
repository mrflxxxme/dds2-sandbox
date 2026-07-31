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
    MigfullInboundItem,
    MigfullInboundPrefill,
    MigfullInboundSendRequest,
    MigfullOpisLine,
    MigfullPackingLine,
    MigfullSendResult,
    PackSource,
)
from backend.services.box_multiplicity_service import (
    resolve_box_qty_map,
    resolve_source_machine_box_qty,
)
from backend.services.migfull_opis import OPIS_CONTENT_TYPE, build_opis_xlsx
from backend.services.migfull_portal_service import (
    MIGFULL_PROVIDER,
    MigfullPortalServiceError,
    _client_from_key,
    _get_key,
    _with_portal_session,
    load_opis_context,
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


# ─── Кратность (короб/россыпь) ───────────────────────────────────────────────


def _gtin14_check_digit(body13: str) -> str:
    """Контрольная цифра GTIN-14 по первым 13 цифрам (веса 3/1 слева направо)."""
    total = sum((3 if i % 2 == 0 else 1) * int(c) for i, c in enumerate(body13))
    return str((10 - total % 10) % 10)


def ean13_to_itf14(barcode: str, indicator: str = "1") -> str | None:
    """ШК россыпи (EAN13) → ШК короба (ITF14 / GTIN-14, индикатор упаковки «1»).

    Обратная операция к ``_itf14_to_ean13`` (fulfillment_service): тело EAN13 без
    контрольной цифры + индикатор + контрольная GTIN-14. Проверено на живых коробах
    Натали — их ITF14 выводится из EAN13 товара именно так. Не EAN13 → None.
    """
    b = (barcode or "").strip()
    if len(b) != 13 or not b.isdigit():
        return None
    body = indicator + b[:12]
    return body + _gtin14_check_digit(body)


async def _resolve_pack_prefill(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    vehicle: CostOrder | None,
    barcodes: list[str],
    box_for_piece: dict[str, tuple[str, int, str | None]],
) -> dict[str, tuple[int | None, PackSource]]:
    """Prefill кратности per ШК: карта Натали → НАША кратность → россыпь.

    Цепочка «ours»: кратность со строк самой машины-источника (работает и для
    едущей, ещё не принятой машины — кейс новинок V-…) → box-multiplicity
    (machine по ACCEPTED-приёмкам → ручная per-ФФ → SKU-дефолт). Кратность 1
    трактуем как «нет» (короб из 1 шт = россыпь).
    """
    out: dict[str, tuple[int | None, PackSource]] = {}
    remaining: list[str] = []
    for bc in barcodes:
        natali = box_for_piece.get(bc)
        if natali is not None:
            out[bc] = (natali[1], "natali")
        else:
            remaining.append(bc)
    if not remaining:
        return out

    vehicle_ppb: dict[str, int] = {}
    if vehicle is not None and vehicle.order_no:
        vm = await resolve_source_machine_box_qty(db, project_id, {vehicle.id: vehicle.order_no})
        vehicle_ppb = {bc: ppb for (_vid, bc), ppb in vm.items()}
    ours_map = await resolve_box_qty_map(db, project_id, warehouse_id, remaining)
    for bc in remaining:
        upb = vehicle_ppb.get(bc) or ours_map.get(bc)
        if upb is not None and upb >= 2:
            out[bc] = (int(upb), "ours")
        else:
            out[bc] = (None, "none")
    return out


def build_opis_lines_from_packing(
    packing: list[MigfullPackingLine],
    *,
    box_for_piece: dict[str, tuple[str, int, str | None]],
    name_for_barcode: dict[str, str | None],
) -> tuple[list[MigfullOpisLine], list[str]]:
    """Per-line packing → строки описи (чистая функция, без БД).

    ``units_per_box`` >= 2 → целые короба (ШК короба: карта Натали, иначе
    выведенный GTIN-14) + некратный остаток россыпью (с warning). None/1 —
    вся строка россыпью. ``boxes`` — явный сплит: ровно N коробов, остаток
    россыпью БЕЗ warning (осознанный выбор в модалке); 0 — вся строка
    россыпью. Опись строится ПО packing — сервис не пересчитывает.
    """
    lines: list[MigfullOpisLine] = []
    warnings: list[str] = []
    for p in sorted(packing, key=lambda x: (name_for_barcode.get(x.barcode) or "", x.barcode)):
        pieces = int(p.qty)
        if pieces <= 0:
            continue
        name = name_for_barcode.get(p.barcode)
        upb = int(p.units_per_box or 1)
        if upb > 1 and p.boxes != 0:
            natali = box_for_piece.get(p.barcode)
            box_barcode = natali[0] if natali is not None else ean13_to_itf14(p.barcode)
            if box_barcode is None:
                warnings.append(f"{name or p.barcode}: не удалось вывести ШК короба — вся строка россыпью")
            else:
                if natali is not None and natali[1] != upb:
                    warnings.append(
                        f"{name or p.barcode}: в карточке Натали короб {natali[1]} шт, отправляем по {upb} шт"
                    )
                box_name = (
                    natali[2]
                    if natali is not None and natali[1] == upb
                    else (f"{name} — короб {upb} шт." if name else None)
                )
                if p.boxes is not None:
                    # Явный сплит из модалки: ровно N коробов, остаток россыпью без warning.
                    boxes = int(p.boxes)
                    rest = pieces - boxes * upb
                else:
                    boxes, rest = divmod(pieces, upb)
                if boxes > 0:
                    lines.append(
                        MigfullOpisLine(
                            barcode=box_barcode, name=box_name, quantity=boxes,
                            is_box=True, units_per_box=upb, pieces=boxes * upb,
                        )
                    )
                if rest > 0:
                    if p.boxes is None:
                        warnings.append(
                            f"{name or p.barcode}: {pieces} шт не кратно коробу ({upb}) — {rest} шт россыпью"
                        )
                    lines.append(
                        MigfullOpisLine(
                            barcode=p.barcode, name=name, quantity=rest,
                            is_box=False, units_per_box=1, pieces=rest,
                        )
                    )
                continue
        lines.append(
            MigfullOpisLine(
                barcode=p.barcode, name=name, quantity=pieces,
                is_box=False, units_per_box=1, pieces=pieces,
            )
        )
    return lines, warnings


def _validated_packing(
    packing: list[MigfullPackingLine], qty_by_bc: dict[str, int]
) -> list[MigfullPackingLine]:
    """Валидация packing из модалки (нарушение → 400, не 422).

    qty > 0, units_per_box >= 1, boxes >= 0 и boxes×units_per_box <= qty (явный
    сплит требует кратности >= 2), ШК без дублей и строго совпадают с составом
    приёмки (ни лишних, ни пропущенных — опись обязана покрыть весь состав).
    """
    seen: set[str] = set()
    for p in packing:
        bc = (p.barcode or "").strip()
        if bc not in qty_by_bc:
            raise MigfullPortalServiceError(f"Packing: ШК {bc or '—'} нет в составе приёмки", status_code=400)
        if bc in seen:
            raise MigfullPortalServiceError(f"Packing: ШК {bc} передан дважды", status_code=400)
        seen.add(bc)
        if p.qty <= 0:
            raise MigfullPortalServiceError(f"Packing: количество по ШК {bc} должно быть > 0", status_code=400)
        if p.units_per_box is not None and p.units_per_box < 1:
            raise MigfullPortalServiceError(f"Packing: шт в коробе по ШК {bc} должно быть >= 1", status_code=400)
        if p.boxes is not None:
            if p.boxes < 0:
                raise MigfullPortalServiceError(f"Packing: число коробов по ШК {bc} должно быть >= 0", status_code=400)
            upb = p.units_per_box or 1
            if p.boxes > 0 and upb < 2:
                raise MigfullPortalServiceError(
                    f"Packing: по ШК {bc} задано число коробов без «шт в коробе» (>= 2)", status_code=400
                )
            if p.boxes * upb > p.qty:
                raise MigfullPortalServiceError(
                    f"Packing: по ШК {bc} короба {p.boxes} × {upb} шт превышают количество {p.qty}",
                    status_code=400,
                )
    missing = sorted(set(qty_by_bc) - seen)
    if missing:
        raise MigfullPortalServiceError(
            f"Packing не покрывает весь состав приёмки (нет ШК: {', '.join(missing[:5])}"
            f"{'…' if len(missing) > 5 else ''})",
            status_code=400,
        )
    return list(packing)


def _default_packing(
    qty_by_bc: dict[str, int], prefill: dict[str, tuple[int | None, PackSource]]
) -> list[MigfullPackingLine]:
    """Packing по умолчанию (без ручных правок): prefill-кратность, иначе россыпь."""
    return [
        MigfullPackingLine(barcode=bc, qty=q, units_per_box=(prefill.get(bc) or (None, "none"))[0])
        for bc, q in qty_by_bc.items()
    ]


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
    box_for_piece, name_for_barcode = await load_opis_context(db, project_id, receipt.warehouse_id, nom_by_bc)
    pack_prefill = await _resolve_pack_prefill(
        db, project_id, receipt.warehouse_id, vehicle, list(qty_by_bc), box_for_piece
    )
    items = [
        MigfullInboundItem(
            barcode=bc,
            name=name_for_barcode.get(bc),
            qty=qty,
            units_per_box=pack_prefill[bc][0],
            pack_source=pack_prefill[bc][1],
            box_barcode=(box_for_piece[bc][0] if bc in box_for_piece else ean13_to_itf14(bc)),
        )
        for bc, qty in sorted(qty_by_bc.items(), key=lambda kv: (name_for_barcode.get(kv[0]) or "", kv[0]))
        if qty > 0
    ]
    lines, warnings = build_opis_lines_from_packing(
        _default_packing(qty_by_bc, pack_prefill), box_for_piece=box_for_piece, name_for_barcode=name_for_barcode
    )
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
        items=items,
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
    vehicle = await _load_vehicle(db, project_id, receipt)
    box_for_piece, name_for_barcode = await load_opis_context(db, project_id, receipt.warehouse_id, nom_by_bc)
    if req.packing is not None:
        # Опись строится ПО ручному packing из модалки — не пересчитываем сами.
        packing = _validated_packing(req.packing, {bc: q for bc, q in qty_by_bc.items() if q > 0})
    else:
        # Legacy/дефолт: цепочка кратности natali → ours → россыпь.
        pack_prefill = await _resolve_pack_prefill(
            db, project_id, receipt.warehouse_id, vehicle, list(qty_by_bc), box_for_piece
        )
        packing = _default_packing(qty_by_bc, pack_prefill)
    lines, warnings = build_opis_lines_from_packing(
        packing, box_for_piece=box_for_piece, name_for_barcode=name_for_barcode
    )
    if not lines:
        raise MigfullPortalServiceError("В приёмке нет позиций для описи", status_code=400)

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
               "opis_lines": [line.model_dump() for line in lines], "warnings": warnings,
               "packing": [p.model_dump() for p in packing] if req.packing is not None else None}

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
