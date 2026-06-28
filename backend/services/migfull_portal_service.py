# ruff: noqa: RUF002, RUF003
"""
Service: migfull-портал (plusvb.migfull.app) — создание заявки на отгрузку у ФФ «Натали».

Источник — ``AssemblyRequest`` (готовая сборка, склад «Натали»). Креды интеграции —
``IntegrationKey(service="migfull_portal")``: ``encrypted_key`` = Fernet-пароль,
``config={"login", "host"?}``, ``warehouse_id`` = склад (гейт: кнопку показываем
только для сборок с него). НЕ путать с read-only API (service="migfull").

build_draft   — локально (без портала): шапка-prefill + превью описи (короб/россыпь) +
                флаг «уже отправлена».
send_shipment — РЕАЛЬНОЕ создание заявки (шапка + загрузка описи). НЕОБРАТИМО (портал
                не даёт удалить/отменить). Анти-дубль гейт + audit ``MigfullShipmentOrder``
                + связь ``FulfillmentRequest(provider="migfull")`` со сборкой.
"""

import asyncio

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.integrations.migfull_portal_client import (
    BASE_URL,
    MigfullPortalClient,
    MigfullPortalError,
)
from backend.integrations.resilience import CircuitOpenError
from backend.models import (
    FulfillmentRequest,
    FulfillmentStock,
    IntegrationKey,
    MigfullShipmentOrder,
    MigfullShipmentStatus,
    Nomenclature,
    Warehouse,
)
from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.fulfillment import FfRequestKind
from backend.schemas.migfull_portal import (
    DELIVERY_TYPE_LABELS,
    MigfullDeliveryTypeOption,
    MigfullDraftResponse,
    MigfullOpisLine,
    MigfullPortalConfigResponse,
    MigfullSendRequest,
    MigfullSendResult,
    MigfullShipmentPrefill,
)
from backend.services.migfull_opis import OPIS_CONTENT_TYPE, build_opis_xlsx
from backend.utils.crypto import decrypt as _decrypt
from backend.utils.time import utcnow

logger = structlog.get_logger("dds.migfull_portal_service")

MIGFULL_PORTAL_SERVICE = "migfull_portal"  # IntegrationKey портальных кредов
MIGFULL_PROVIDER = "migfull"  # provider в FulfillmentRequest (общий с read-sync)
_WB_MARKETPLACE_ID = "2"  # Wildberries в migfull (все заявки склада «Натали»)


class MigfullPortalServiceError(Exception):
    """Доменная ошибка (роутер мапит в HTTPException)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ─── Креды / клиент ──────────────────────────────────────────────────────────


async def _get_key_or_none(db: AsyncSession, project_id: int) -> IntegrationKey | None:
    result = await db.execute(
        select(IntegrationKey).where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.service == MIGFULL_PORTAL_SERVICE,
            IntegrationKey.is_active.is_(True),
            IntegrationKey.is_deleted.is_(False),
        )
    )
    return result.scalar_one_or_none()


async def _get_key(db: AsyncSession, project_id: int) -> IntegrationKey:
    key = await _get_key_or_none(db, project_id)
    if key is None:
        raise MigfullPortalServiceError(
            "migfull-портал: интеграция не настроена (scripts/setup_migfull_portal_account.py)", status_code=400
        )
    return key


def _client_from_key(key: IntegrationKey) -> MigfullPortalClient:
    cfg = key.config or {}
    login = cfg.get("login") or key.label or ""
    if not login:
        raise MigfullPortalServiceError("migfull-портал: в ключе не задан login (config)", status_code=500)
    return MigfullPortalClient(
        login=login,
        password=_decrypt(key.encrypted_key),
        project_id=key.project_id,
        host=cfg.get("host") or BASE_URL,
    )


# ─── Config ──────────────────────────────────────────────────────────────────


async def get_config(db: AsyncSession, project_id: int) -> MigfullPortalConfigResponse:
    key = await _get_key_or_none(db, project_id)
    if key is None:
        return MigfullPortalConfigResponse(configured=False)
    wh_name: str | None = None
    if key.warehouse_id is not None:
        wh_name = await db.scalar(select(Warehouse.name).where(Warehouse.id == key.warehouse_id))
    return MigfullPortalConfigResponse(configured=True, warehouse_id=key.warehouse_id, warehouse_name=wh_name)


# ─── Загрузка сборки ─────────────────────────────────────────────────────────


async def _load_assembly(db: AsyncSession, project_id: int, assembly_id: int) -> AssemblyRequest | None:
    result = await db.execute(
        select(AssemblyRequest)
        .where(
            AssemblyRequest.id == assembly_id,
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
        )
        .options(selectinload(AssemblyRequest.items), selectinload(AssemblyRequest.wb_fbo_supply))
    )
    return result.scalar_one_or_none()


# ─── Опись: состав сборки → строки (короб/россыпь) ───────────────────────────


def classify_opis_lines(
    qty_by_bc: dict[str, int],
    *,
    box_for_piece: dict[str, tuple[str, int, str | None]],  # EAN13 россыпи → (ШК короба ITF14, шт/короб, имя)
    name_for_barcode: dict[str, str | None],  # ШК → имя товара
) -> tuple[list[MigfullOpisLine], list[str]]:
    """Чистое ядро описи (без БД): короб (есть сопоставление) → ITF14 + число коробов;
    иначе россыпь EAN13 + штуки. Кол-во не кратно коробу → fallback в россыпь + warning.
    """
    lines: list[MigfullOpisLine] = []
    warnings: list[str] = []
    for bc in sorted(qty_by_bc, key=lambda b: (name_for_barcode.get(b) or "", b)):
        pieces = qty_by_bc[bc]
        if pieces <= 0:
            continue
        box = box_for_piece.get(bc)
        if box is not None:
            box_barcode, upb, box_name = box
            upb = int(upb or 1)
            if upb > 1 and pieces % upb == 0:
                lines.append(
                    MigfullOpisLine(
                        barcode=box_barcode, name=box_name, quantity=pieces // upb,
                        is_box=True, units_per_box=upb, pieces=pieces,
                    )
                )
                continue
            if upb > 1:
                warnings.append(
                    f"{name_for_barcode.get(bc) or bc}: {pieces} шт не кратно коробу ({upb}) — отправлено россыпью"
                )
        lines.append(
            MigfullOpisLine(
                barcode=bc, name=name_for_barcode.get(bc), quantity=pieces,
                is_box=False, units_per_box=1, pieces=pieces,
            )
        )
    return lines, warnings


async def _compute_opis_lines(
    db: AsyncSession, project_id: int, warehouse_id: int, assembly: AssemblyRequest
) -> tuple[list[MigfullOpisLine], list[str]]:
    """Загрузка состава сборки + сопоставления короб→россыпь из БД → classify_opis_lines."""
    qty_by_bc: dict[str, int] = {}
    nom_by_bc: dict[str, int] = {}
    for it in assembly.items:
        bc = (it.barcode or "").strip()
        if not bc:
            continue
        qty_by_bc[bc] = qty_by_bc.get(bc, 0) + int(it.quantity or 0)
        if it.nomenclature_id:
            nom_by_bc.setdefault(bc, it.nomenclature_id)
    qty_by_bc = {bc: q for bc, q in qty_by_bc.items() if q > 0}
    if not qty_by_bc:
        return [], []

    # Зеркало остатков ФФ склада: имя товара + сопоставление короб→россыпь
    stock_rows = (
        await db.execute(
            select(FulfillmentStock).where(
                FulfillmentStock.project_id == project_id,
                FulfillmentStock.warehouse_id == warehouse_id,
            )
        )
    ).scalars().all()
    name_for_barcode: dict[str, str | None] = {}
    box_for_piece: dict[str, tuple[str, int, str | None]] = {}  # EAN13 → (ITF14, шт/короб, имя)
    for r in stock_rows:
        name_for_barcode.setdefault(r.barcode, r.name)
        if r.base_barcode and (r.units_per_box or 1) > 1:
            box_for_piece.setdefault(r.base_barcode, (r.barcode, int(r.units_per_box or 1), r.name))

    # Фолбэк имени из номенклатуры (для ШК без строки в зеркале ФФ)
    nom_ids = set(nom_by_bc.values())
    if nom_ids:
        nom_name: dict[int, str | None] = {}
        for nid, art in (
            await db.execute(
                select(Nomenclature.id, Nomenclature.article_seller).where(
                    Nomenclature.project_id == project_id, Nomenclature.id.in_(nom_ids)
                )
            )
        ).all():
            nom_name[nid] = art
        for bc, nid in nom_by_bc.items():
            if not name_for_barcode.get(bc):
                name_for_barcode[bc] = nom_name.get(nid)

    return classify_opis_lines(qty_by_bc, box_for_piece=box_for_piece, name_for_barcode=name_for_barcode)


# ─── Анти-дубль ──────────────────────────────────────────────────────────────


async def _already_sent(db: AsyncSession, project_id: int, assembly_id: int) -> tuple[bool, str | None, str | None]:
    """Уже создавали заявку для этой сборки? (audit SENT либо связанная FulfillmentRequest)."""
    order = (
        await db.execute(
            select(MigfullShipmentOrder)
            .where(
                MigfullShipmentOrder.project_id == project_id,
                MigfullShipmentOrder.assembly_request_id == assembly_id,
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
            select(FulfillmentRequest.external_id, FulfillmentRequest.number).where(
                FulfillmentRequest.project_id == project_id,
                FulfillmentRequest.provider == MIGFULL_PROVIDER,
                FulfillmentRequest.assembly_request_id == assembly_id,
            ).limit(1)
        )
    ).first()
    if existing is not None:
        return True, existing.external_id, existing.number
    return False, None, None


# ─── Примечание для оператора Натали ─────────────────────────────────────────


def _default_notes(ar: AssemblyRequest, supply: object | None) -> str:
    """Примечание заявки в портале Натали — оператор видит это поле и связывает
    заявку с нашей сборкой DDS. Пишем наш № сборки (ASM) и, если есть, № поставки WB.
    Это дефолт-prefill; пользователь может изменить текст в модалке перед отправкой.
    """
    parts = [f"DDS · {ar.number}"] if ar.number else ["DDS"]
    wb_supply = getattr(supply, "wb_supply_id", None) if supply is not None else None
    if wb_supply:
        parts.append(f"поставка ВБ {wb_supply}")
    return " · ".join(parts)


# ─── Draft (модалка) ─────────────────────────────────────────────────────────


async def build_draft(db: AsyncSession, project_id: int, assembly_id: int) -> MigfullDraftResponse:
    key = await _get_key(db, project_id)
    ar = await _load_assembly(db, project_id, assembly_id)
    if ar is None:
        raise MigfullPortalServiceError("Сборка не найдена", status_code=404)

    eligible = key.warehouse_id is not None and ar.warehouse_id == key.warehouse_id
    lines, warnings = await _compute_opis_lines(db, project_id, ar.warehouse_id, ar)
    already, guid, number = await _already_sent(db, project_id, assembly_id)
    supply = ar.wb_fbo_supply

    prefill = MigfullShipmentPrefill(
        number=(supply.wb_supply_id if supply else None),
        shipment_date=ar.delivery_date or ar.pickup_date,
        filter_delivery_type="direct",
        notes=_default_notes(ar, supply),  # № сборки (+поставка ВБ) — оператор Натали видит связь
        wb_warehouse_name=(supply.warehouse_name if supply else None) or ar.wb_warehouse_name_manual,
        assembly_number=ar.number,
    )
    return MigfullDraftResponse(
        eligible=eligible,
        already_sent=already,
        sent_guid=guid,
        sent_number=number,
        prefill=prefill,
        delivery_types=[MigfullDeliveryTypeOption(value=v, label=lbl) for v, lbl in DELIVERY_TYPE_LABELS.items()],
        opis_lines=lines,
        total_boxes=sum(line.quantity for line in lines if line.is_box),
        total_pieces=sum(line.pieces for line in lines),
        warnings=warnings,
    )


# ─── Send (реальное создание заявки) ─────────────────────────────────────────


def _opis_filename(ar: AssemblyRequest) -> str:
    safe = "".join(ch for ch in (ar.number or "opis") if ch.isalnum() or ch in "-_") or "opis"
    return f"opis_{safe}.xlsx"


async def _upsert_ff_link(
    db: AsyncSession, project_id: int, warehouse_id: int, assembly_id: int,
    guid: str, number: str | None, total_qty: int, dest: str | None,
) -> None:
    """Связать созданную заявку (provider=migfull, external_id=guid) со сборкой.

    Read-sync НЕ трогает assembly_request_id на upsert → связь переживает синки.
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
        existing.assembly_request_id = assembly_id
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
            kind=FfRequestKind.ASSEMBLY.value,
            status="new",
            assembly_request_id=assembly_id,
            total_qty=total_qty or None,
            dest_warehouse=dest,
            synced_at=utcnow(),
        )
    )


async def _record_outcome(
    db: AsyncSession, *, project_id: int, assembly_id: int, warehouse_id: int,
    status: str, guid: str | None, reference: str | None, payload: dict,
    filename: str, excerpt: str | None, error: str | None, actor: str | None,
    total_qty: int, dest: str | None,
) -> MigfullShipmentOrder:
    """Зафиксировать исход. Audit-строку коммитим ПЕРВОЙ (необратимый факт отправки не
    должен зависеть от FF-link). Затем — best-effort связь со сборкой, терпящая гонку с
    read-sync по тому же guid (audit уже сохранён → анти-дубль цел в любом случае).
    """
    order = MigfullShipmentOrder(
        project_id=project_id, assembly_request_id=assembly_id, status=status,
        shipment_guid=guid, shipment_number=reference, payload=payload,
        opis_filename=filename, response_excerpt=excerpt, error=error, created_by=actor,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)

    if guid and status in (MigfullShipmentStatus.SENT, MigfullShipmentStatus.UNCERTAIN):
        try:
            await _upsert_ff_link(db, project_id, warehouse_id, assembly_id, guid, reference, total_qty, dest)
            await db.commit()
        except IntegrityError:
            # Гонка: read-sync создал FulfillmentRequest с тем же guid между нашим SELECT и INSERT.
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
                existing.assembly_request_id = assembly_id
                if reference:
                    existing.number = reference
                await db.commit()
            else:
                logger.warning("migfull_portal.ff_link_race", project_id=project_id, guid=guid)
    return order


async def send_shipment(
    db: AsyncSession, project_id: int, assembly_id: int, req: MigfullSendRequest, actor: str | None = None
) -> MigfullSendResult:
    key = await _get_key(db, project_id)
    ar = await _load_assembly(db, project_id, assembly_id)
    if ar is None:
        raise MigfullPortalServiceError("Сборка не найдена", status_code=404)
    if key.warehouse_id is None or ar.warehouse_id != key.warehouse_id:
        raise MigfullPortalServiceError("Эта сборка не со склада ФФ «Натали» — отправка недоступна", status_code=400)
    if ar.status == AssemblyStatus.CANCELLED.value:
        raise MigfullPortalServiceError("Нельзя отправить отменённую сборку", status_code=400)

    # Анти-дубль: создание НЕОБРАТИМО (нет delete/cancel у клиента).
    if not req.force_resend:
        already, _, num = await _already_sent(db, project_id, assembly_id)
        if already:
            raise MigfullPortalServiceError(
                f"Заявка для этой сборки уже создана в ФФ ({num or '—'}). Подтвердите повторную отправку.",
                status_code=409,
            )

    lines, warnings = await _compute_opis_lines(db, project_id, ar.warehouse_id, ar)
    if not lines:
        raise MigfullPortalServiceError("В сборке нет позиций для описи", status_code=400)

    supply = ar.wb_fbo_supply
    dest = (supply.warehouse_name if supply else None) or ar.wb_warehouse_name_manual
    shipment_date = req.shipment_date or ar.delivery_date or ar.pickup_date
    header: dict[str, object] = {
        "marketplace_id": _WB_MARKETPLACE_ID,
        "shipment_type": "fbo",
        "number": req.number or (supply.wb_supply_id if supply else None),
        "shipment_date": shipment_date.isoformat() if shipment_date else None,
        "notes": req.notes or _default_notes(ar, supply),
        "filter_delivery_type": req.filter_delivery_type,
    }
    xlsx = build_opis_xlsx(
        lines,
        incoming_number=(supply.wb_supply_id if supply else ar.number),
        incoming_date=shipment_date.isoformat() if shipment_date else None,
    )
    filename = _opis_filename(ar)
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
            await client.authenticate()
            created = await client.create_shipment(header)
            guid = created.guid
            upload = await client.upload_opis(guid, filename, xlsx, OPIS_CONTENT_TYPE)
        reference = upload.reference
        status = MigfullShipmentStatus.SENT if upload.ok else MigfullShipmentStatus.UNCERTAIN
        message = (
            f"Заявка создана ({reference or guid}), опись загружена"
            if upload.ok
            else f"Заявка создана ({reference or guid}), но загрузка описи не подтверждена — проверьте в кабинете"
        )
        excerpt = upload.excerpt or None
    except asyncio.CancelledError:
        # Окно отмены (дисконнект/таймаут/shutdown) после создания шапки: заявка могла
        # уже создаться (guid) — фиксируем audit ДО re-raise (shield от отмены коммита),
        # иначе анти-дубль ослепнет и пользователь создаст вторую необратимую заявку.
        status = _uncertain_if_guid()
        error = message = "Запрос отменён во время отправки — проверьте заявку в кабинете ФФ."
        await asyncio.shield(
            _record_outcome(
                db, project_id=project_id, assembly_id=ar.id, warehouse_id=ar.warehouse_id,
                status=status, guid=guid, reference=reference, payload=payload,
                filename=filename, excerpt=None, error=error, actor=actor,
                total_qty=total_pieces, dest=dest,
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
        db, project_id=project_id, assembly_id=ar.id, warehouse_id=ar.warehouse_id,
        status=status, guid=guid, reference=reference, payload=payload,
        filename=filename, excerpt=excerpt, error=error, actor=actor,
        total_qty=total_pieces, dest=dest,
    )

    logger.info("migfull_portal.send", project_id=project_id, assembly_id=assembly_id, status=status, guid=guid)
    return MigfullSendResult(
        ok=(status == MigfullShipmentStatus.SENT),
        shipment_guid=guid,
        shipment_number=reference,
        message=message,
        order_id=order.id,
    )
