# ruff: noqa: RUF002, RUF003
"""
Router: /warehouse — warehouses CRUD, stock, receipts, shipments, transfers, adjustments.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.box_multiplicity import (
    BoxMultiplicityBatchListResponse,
    BoxMultiplicityBatchRevertResponse,
    BoxMultiplicityBulkRequest,
    BoxMultiplicityBulkResponse,
    BoxMultiplicityChangesResponse,
    BoxMultiplicityPerWarehouseUpdate,
    BoxMultiplicityResponse,
    BoxMultiplicityRow,
    BoxMultiplicitySourcesResponse,
    BoxMultiplicityUpdate,
)
from backend.schemas.warehouse import (
    AcceptanceCheckRequest,
    AcceptanceCheckResponse,
    AcceptanceLimitsResponse,
    BarcodeEligibilityRequest,
    BarcodeEligibilityResponse,
    CostPriceUpdate,
    DefectBulkOperation,
    DefectBulkResponse,
    DefectMarkOperationSchema,
    DefectOperationCreate,
    DeliveryTimesUpdate,
    InboundReceiptCreate,
    InboundReceiptSchema,
    InboundReceiptUpdate,
    OutboundShipmentCreate,
    OutboundShipmentSchema,
    StockAdjustmentCreate,
    StockAdjustmentSchema,
    StockMovementSchema,
    StockTransferCreate,
    StockTransferSchema,
    SupplyAcceptanceSlotsResponse,
    TransferAssignVehicle,
    TransferAssignVehicleBulk,
    TransferLogisticsReport,
    WarehouseCounterpartyLink,
    WarehouseCreate,
    WarehouseExtraCounterpartyAdd,
    WarehouseReorder,
    WarehouseSchema,
    WarehouseStockSchema,
    WarehouseUpdate,
)
from backend.services import (
    barcode_eligibility_service,
    box_multiplicity_service,
    supply_acceptance_slots_service,
    transfer_logistics,
    warehouse_acceptance_service,
    warehouse_defect,
    warehouse_service,
)
from backend.utils.rate_limit import (
    rate_limit_acceptance,
    rate_limit_acceptance_force,
    rate_limit_write,
)

router = APIRouter(prefix="/warehouse", tags=["Warehouse"])


# ─── Warehouses CRUD ────────────────────────────────────────────────────────


@router.get("")
async def list_warehouses(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List all warehouses for the project."""
    rows = await warehouse_service.list_warehouses(db, project.id)
    return [WarehouseSchema.model_validate(r) for r in rows]


@router.post("", dependencies=[Depends(rate_limit_write)])
async def create_warehouse(
    payload: WarehouseCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Create a new warehouse."""
    wh = await warehouse_service.create_warehouse(
        db,
        project.id,
        payload.model_dump(exclude_unset=True),
    )
    return WarehouseSchema.model_validate(wh)


@router.put("/reorder", dependencies=[Depends(rate_limit_write)])
async def reorder_warehouses(
    payload: WarehouseReorder,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Update sort_order for multiple warehouses."""
    await warehouse_service.reorder_warehouses(db, project.id, payload.items)
    return {"ok": True}


@router.put("/{warehouse_id}", dependencies=[Depends(rate_limit_write)])
async def update_warehouse(
    warehouse_id: int,
    payload: WarehouseUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Update a warehouse."""
    wh = await warehouse_service.update_warehouse(
        db,
        project.id,
        warehouse_id,
        payload.model_dump(exclude_unset=True),
    )
    if not wh:
        raise HTTPException(404, "Warehouse not found")
    return WarehouseSchema.model_validate(wh)


@router.patch("/{warehouse_id}/counterparty", dependencies=[Depends(rate_limit_write)])
async def set_warehouse_counterparty(
    warehouse_id: int,
    payload: WarehouseCounterpartyLink,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Link warehouse to a Counterparty by INN. Auto-creates CP as FULFILLMENT if new/OTHER."""
    wh = await warehouse_service.link_counterparty(
        db,
        project.id,
        warehouse_id,
        inn=payload.inn,
        name=payload.name,
    )
    if not wh:
        raise HTTPException(404, "Warehouse not found")
    return WarehouseSchema.model_validate(wh)


@router.post("/{warehouse_id}/counterparties", dependencies=[Depends(rate_limit_write)])
async def add_warehouse_extra_counterparty(
    warehouse_id: int,
    payload: WarehouseExtraCounterpartyAdd,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Привязать ДОПОЛНИТЕЛЬНОГО контрагента к складу (поверх основного). Авто-FULFILLMENT."""
    wh = await warehouse_service.add_extra_counterparty(
        db,
        project.id,
        warehouse_id,
        inn=payload.inn,
        name=payload.name,
    )
    if not wh:
        raise HTTPException(404, "Warehouse not found")
    return WarehouseSchema.model_validate(wh)


@router.delete("/{warehouse_id}/counterparties/{counterparty_id}", dependencies=[Depends(rate_limit_write)])
async def remove_warehouse_extra_counterparty(
    warehouse_id: int,
    counterparty_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Отвязать доп. контрагента от склада (связь удаляется, сам контрагент остаётся)."""
    wh = await warehouse_service.remove_extra_counterparty(
        db,
        project.id,
        warehouse_id,
        counterparty_id,
    )
    if not wh:
        raise HTTPException(404, "Warehouse not found")
    return WarehouseSchema.model_validate(wh)


@router.delete("/{warehouse_id}", dependencies=[Depends(rate_limit_write)])
async def delete_warehouse(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a warehouse."""
    deleted = await warehouse_service.delete_warehouse(db, project.id, warehouse_id)
    if not deleted:
        raise HTTPException(404, "Warehouse not found")
    return {"ok": True}


# ─── WB Acceptance Check (Проверка приёмки) ────────────────────────────────


@router.post(
    "/acceptance-check",
    response_model=AcceptanceCheckResponse,
    dependencies=[Depends(rate_limit_acceptance)],
)
async def check_wb_acceptance(
    body: AcceptanceCheckRequest,
    request: Request,
    force: bool = Query(False, description="Skip Redis cache and call WB API live"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Live WB acceptance/options check + per-SKU redistribute.

    For each {nm_id, barcode, distribution} we ask WB which warehouses accept
    the SKU (canBox/canMonopallet/canSupersafe), pick a single package_type
    per SKU, and move qty away from closed warehouses to the largest open
    warehouse in the same федеральный округ. Availability кэшируется пер-баркод
    на 10 минут (WB rate limit 6 req/min) — живой вызов только по недостающим
    баркодам; distribution пересчитывается на каждый запрос. `force=true` —
    обойти кэш по всем баркодам батча (UI «🔄 Обновить»). Отдельный rate-limit
    бакет (не write): фоновые проверки не выедают лимит автосейва черновиков;
    force дополнительно гейтится под квоту WB (6/мин) — он бьёт в WB живьём.
    """
    if force:
        await rate_limit_acceptance_force(request)
    items = [it.model_dump() for it in body.items]
    try:
        return await warehouse_acceptance_service.check_acceptance_and_redistribute(
            db,
            project.id,
            items,
            force=force,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/acceptance-limits", response_model=AcceptanceLimitsResponse)
async def get_wb_acceptance_limits(
    warehouse: str | None = Query(None, description="Фильтр по имени склада (substring, case-insensitive)"),
    force: bool = Query(False, description="Skip Redis cache and call WB API live"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сводный календарь лимитов приёмки WB на ближайшие 14 дней.

    Для каждого (склад × тип упаковки) отдаёт по дню: коэффициент WB
    (-1 закрыто, 0..1 бесплатно, ≥2 платно), флаги is_free / is_closed и
    тарифы хранения/логистики. Источник — кэш коэффициентов (TTL 1ч, WB
    rate limit 6 req/min); `force=true` обходит кэш (UI «🔄 Обновить»).
    """
    try:
        return await warehouse_acceptance_service.get_acceptance_limits(
            db,
            project.id,
            warehouse=warehouse,
            force=force,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/acceptance-slots", response_model=SupplyAcceptanceSlotsResponse)
async def get_supply_acceptance_slots(
    force: bool = Query(False, description="Skip Redis cache and call WB API live"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Слоты сдачи приёмки WB для активных заявок (Лист логиста → поставки).

    Для каждой активной заявки (IN_PROGRESS / READY / VEHICLE_ASSIGNED) с её
    складом WB отдаёт календарь коэффициентов приёмки этого склада (free/paid/
    closed по дням) — какие ещё дни доступны для сдачи, кроме плановой даты.
    Источник тот же, что у `/warehouse/acceptance-limits` (кэш 1ч); `force=true`
    обходит кэш (UI «🔄»). `matched=false` в строке — склад заявки не нашёлся в
    календаре приёмки.
    """
    try:
        return await supply_acceptance_slots_service.get_supply_acceptance_slots(
            db,
            project.id,
            force=force,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/barcode-eligibility",
    response_model=BarcodeEligibilityResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def get_barcode_eligibility(
    body: BarcodeEligibilityRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Per-barcode: eligible WB-склады (по приёмке) + свободный ФФ-остаток.

    Для каждого баркода возвращает (1) WB-склады, куда его можно сдать
    (canBox/canMonopallet/canSupersafe из acceptance/options; `no_limit`=⌛
    предзаявка если публичный лимит закрыт), и (2) свободный остаток по
    ФФ-складам-источникам (`available = quantity − резерв активных сборок`).
    Неизвестные баркоды (без Nomenclature) возвращаются в `unknown`, запрос не
    падает. Eligibility переиспользует кэш WB-приёмки (5 мин, rate limit
    6 req/min).
    """
    try:
        return await barcode_eligibility_service.get_barcode_eligibility(
            db,
            project.id,
            body.barcodes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ─── Delivery Times (Время доставки до WB) ────────────────────────────────


@router.get("/{warehouse_id}/delivery-times")
async def get_delivery_times(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get delivery time settings for a warehouse to WB warehouses."""
    result = await warehouse_service.get_delivery_times(db, project.id, warehouse_id)
    if result is None:
        raise HTTPException(404, "Warehouse not found")
    return result


@router.put("/{warehouse_id}/delivery-times", dependencies=[Depends(rate_limit_write)])
async def update_delivery_times(
    warehouse_id: int,
    body: DeliveryTimesUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Update delivery time settings for a warehouse."""
    result = await warehouse_service.update_delivery_times(
        db,
        project.id,
        warehouse_id,
        assembly_days=body.assembly_days,
        wb_acceptance_days=body.wb_acceptance_days,
        items=[item.model_dump() for item in body.items],
    )
    if result is None:
        raise HTTPException(404, "Warehouse not found")
    return result


# ─── Warehouse Stock ──────────────────────────────────────────────────────


@router.get("/{warehouse_id}/stock")
async def get_warehouse_stock(
    warehouse_id: int,
    exclude_assembly_id: int | None = Query(
        None, description="Не вычитать из available резерв этой сборки (экран её редактирования)"
    ),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get current stock for a warehouse."""
    rows = await warehouse_service.get_warehouse_stock(
        db, project.id, warehouse_id, exclude_assembly_id=exclude_assembly_id
    )
    return [WarehouseStockSchema(**r) for r in rows]


@router.get("/{warehouse_id}/movements")
async def get_stock_movements(
    warehouse_id: int,
    limit: int = Query(200, le=1000),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get movement history for a warehouse."""
    rows = await warehouse_service.get_stock_movements(db, project.id, warehouse_id, limit)
    return [StockMovementSchema.model_validate(r) for r in rows]


@router.get("/{warehouse_id}/expected-vehicles")
async def get_expected_vehicles(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get vehicles in transit (SHIPPED/CUSTOMS) targeting this warehouse."""
    from backend.services.warehouse_crud import get_expected_vehicles as _get_expected
    from backend.services.warehouse_crud import get_vehicle_receipt_map

    vehicles = await _get_expected(db, project.id, warehouse_id)
    receipt_map = await get_vehicle_receipt_map(db, project.id, [v.id for v in vehicles])
    # Номера заявок ФФ, уже связанных с приёмками этих машин (PVB-… Натали):
    # карточка показывает связку, чтобы «Связать»/«Создать поставку» читались
    # в контексте, а не выглядели непройденным шагом.
    ff_by_receipt: dict[int, list[str]] = {}
    receipt_ids = [rid for rid in receipt_map.values() if rid]
    if receipt_ids:
        from backend.models.fulfillment import FulfillmentRequest

        ff_rows = await db.execute(
            select(FulfillmentRequest.inbound_receipt_id, FulfillmentRequest.number)
            .where(
                FulfillmentRequest.project_id == project.id,
                FulfillmentRequest.inbound_receipt_id.in_(receipt_ids),
                FulfillmentRequest.archived == False,  # noqa: E712
            )
            .order_by(FulfillmentRequest.number)
            .limit(200)
        )
        for rid, number in ff_rows.all():
            if rid is not None and number:
                ff_by_receipt.setdefault(rid, []).append(number)
    result = []
    for v in vehicles:
        # selectinload(CostOrder.items) грузит и soft-deleted строки (перезаливки
        # Excel мягко удаляют старые, а не физически) — считать только активные,
        # иначе «поз/шт» задваиваются (V-0027: 118 актив + 153 мёртвых = 271 поз /
        # 10 442 шт вместо 5 221). Деталь машины давно считает active-only.
        active = [i for i in v.items if not i.is_deleted]
        result.append(
            {
                "id": v.id,
                "order_no": v.order_no,
                "status": v.status,
                "invoice_no": v.invoice_no,
                "ship_date": v.ship_date.isoformat() if v.ship_date else None,
                "estimated_arrival_date": v.estimated_arrival_date.isoformat() if v.estimated_arrival_date else None,
                "actual_ship_date": v.actual_ship_date.isoformat() if v.actual_ship_date else None,
                "items_count": len(active),
                "total_qty": sum(i.qty for i in active),
                "container_type": v.container_type,
                "transport_type": v.transport_type,
                # id нашей приёмки этой машины — для связки заявок ФФ из карточки
                "receipt_id": receipt_map.get(v.id),
                # уже связанные заявки ФФ (номера PVB-…) — карточка показывает связку
                "linked_ff_numbers": ff_by_receipt.get(receipt_map.get(v.id) or 0, []),
            }
        )
    return result


# ─── Inbound Receipts (Приёмка) ────────────────────────────────────────────


@router.get("/{warehouse_id}/receipts")
async def list_receipts(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List receipts for a warehouse."""
    rows = await warehouse_service.list_receipts(db, project.id, warehouse_id)
    return [InboundReceiptSchema.model_validate(r) for r in rows]


@router.post("/{warehouse_id}/receipts", dependencies=[Depends(rate_limit_write)])
async def create_receipt(
    warehouse_id: int,
    payload: InboundReceiptCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Create a new inbound receipt."""
    try:
        receipt = await warehouse_service.create_receipt(
            db,
            project.id,
            warehouse_id,
            payload.model_dump(),
        )
        return InboundReceiptSchema.model_validate(receipt)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/receipts/{receipt_id}")
async def get_receipt(
    receipt_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get receipt details with items."""
    receipt = await warehouse_service.get_receipt(db, project.id, receipt_id)
    if not receipt:
        raise HTTPException(404, "Receipt not found")
    return InboundReceiptSchema.model_validate(receipt)


@router.put("/receipts/{receipt_id}", dependencies=[Depends(rate_limit_write)])
async def update_receipt(
    receipt_id: int,
    payload: InboundReceiptUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Update receipt (DRAFT/EXPECTED only)."""
    try:
        receipt = await warehouse_service.update_receipt(
            db,
            project.id,
            receipt_id,
            payload.model_dump(exclude_unset=True),
        )
        if not receipt:
            raise HTTPException(404, "Receipt not found")
        return InboundReceiptSchema.model_validate(receipt)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/receipts/{receipt_id}/accept", dependencies=[Depends(rate_limit_write)])
async def accept_receipt(
    receipt_id: int,
    actual_quantities: list[dict] | None = None,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Accept receipt → write stock. Optionally pass actual_quantities: [{item_id, actual_qty}]."""
    try:
        receipt = await warehouse_service.accept_receipt(
            db,
            project.id,
            receipt_id,
            actual_quantities=actual_quantities,
        )
        return InboundReceiptSchema.model_validate(receipt)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/receipts/{receipt_id}/cancel", dependencies=[Depends(rate_limit_write)])
async def cancel_receipt(
    receipt_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Cancel accepted receipt → rollback stock."""
    try:
        receipt = await warehouse_service.cancel_receipt(db, project.id, receipt_id)
        return InboundReceiptSchema.model_validate(receipt)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# ─── Outbound Shipments (Отгрузка) ────────────────────────────────────────


@router.get("/{warehouse_id}/shipments")
async def list_shipments(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List shipments for a warehouse."""
    rows = await warehouse_service.list_shipments(db, project.id, warehouse_id)
    return [OutboundShipmentSchema.model_validate(r) for r in rows]


@router.post("/{warehouse_id}/shipments", dependencies=[Depends(rate_limit_write)])
async def create_shipment(
    warehouse_id: int,
    payload: OutboundShipmentCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Create outbound shipment (FULFILLMENT only)."""
    try:
        shipment = await warehouse_service.create_shipment(
            db,
            project.id,
            warehouse_id,
            payload.model_dump(),
        )
        return OutboundShipmentSchema.model_validate(shipment)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/shipments/{shipment_id}")
async def get_shipment(
    shipment_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get shipment details with items."""
    shipment = await warehouse_service.get_shipment(db, project.id, shipment_id)
    if not shipment:
        raise HTTPException(404, "Shipment not found")
    return OutboundShipmentSchema.model_validate(shipment)


@router.post("/shipments/{shipment_id}/ship", dependencies=[Depends(rate_limit_write)])
async def ship_shipment(
    shipment_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Ship → deduct stock."""
    try:
        shipment = await warehouse_service.ship_shipment(db, project.id, shipment_id)
        return OutboundShipmentSchema.model_validate(shipment)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/shipments/{shipment_id}/deliver", dependencies=[Depends(rate_limit_write)])
async def deliver_shipment(
    shipment_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Mark as delivered (final status)."""
    try:
        shipment = await warehouse_service.deliver_shipment(db, project.id, shipment_id)
        return OutboundShipmentSchema.model_validate(shipment)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/shipments/{shipment_id}/cancel", dependencies=[Depends(rate_limit_write)])
async def cancel_shipment(
    shipment_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Cancel shipped shipment → return stock."""
    try:
        shipment = await warehouse_service.cancel_shipment(db, project.id, shipment_id)
        return OutboundShipmentSchema.model_validate(shipment)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# ─── Stock Transfers (Перемещение) ────────────────────────────────────────


def _transfer_error(e: ValueError) -> HTTPException:
    """ValueError сервиса перемещений → HTTP-код.

    404 — документа нет (тот же код, что у `GET /transfers/{id}` на тот же id:
    иначе один и тот же несуществующий переезд отвечал бы то 404, то 400).
    400 — доменный отказ (не тот статус, у склада нет контрагента и т.п.).
    """
    if "не найдено" in str(e).lower():
        return HTTPException(404, str(e))
    return HTTPException(400, str(e))


@router.get("/transfers")
async def list_transfers(
    in_transit: bool = Query(False),
    warehouse_id: int | None = Query(None),
    # Whitelist статусов — симметрично `group_by` в отчёте: мусорное значение
    # честно отбивается 422, а не возвращает пустой список, который читается
    # как «переездов нет».
    status: str | None = Query(None, pattern="^(DRAFT|IN_TRANSIT|COMPLETED)$"),
    has_vehicle: bool | None = Query(None),
    converted_from_assembly_id: int | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List transfers. ?in_transit=true — только активные, ?warehouse_id= — источник ИЛИ получатель.

    Срез Листа логиста: `?status=DRAFT&has_vehicle=false` — переезды, ждущие машину.
    `?converted_from_assembly_id=` — переезды из конкретной заявки: карточка
    заявки показывает «уже переделана в TR-хх», не ловя 400 повторной попыткой.

    🔴 Состав (`items`) в списке ВСЕГДА пустой — вместо него `units_total` и
    `sku_count`. За составом — `GET /warehouse/transfers/{id}`.
    """
    rows = await warehouse_service.list_transfers(
        db,
        project.id,
        in_transit,
        warehouse_id,
        status=status,
        has_vehicle=has_vehicle,
        converted_from_assembly_id=converted_from_assembly_id,
    )
    return [StockTransferSchema.model_validate(r) for r in rows]


@router.post(
    "/transfers/assign-vehicle-bulk",
    response_model=list[StockTransferSchema],
    dependencies=[Depends(rate_limit_write)],
)
async def assign_vehicle_transfer_bulk(
    payload: TransferAssignVehicleBulk,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Одна машина на несколько переездов (Лист логиста).

    Первый отказ роняет весь вызов — частичное назначение логист бы не заметил.

    🔴 Объявлен ВЫШЕ `/transfers/{transfer_id}`: иначе путь съест
    «assign-vehicle-bulk» как int-параметр и вернёт 422.
    """
    try:
        rows = await warehouse_service.assign_vehicle_transfer_bulk(
            db, project.id, payload.ids, payload.payload
        )
        return [StockTransferSchema.model_validate(r) for r in rows]
    except ValueError as e:
        raise _transfer_error(e) from None


@router.get("/transfers/logistics-report", response_model=TransferLogisticsReport)
async def transfer_logistics_report(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    group_by: str = Query("month", pattern="^(day|week|month)$"),
    from_warehouse_id: int | None = Query(None),
    to_warehouse_id: int | None = Query(None),
    counterparty_id: int | None = Query(None),
    summary_only: bool = Query(False),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Логистика ПЕРЕЕЗДОВ: стоимость перевозки между нашими складами.

    Отдельный от логистики сборок отчёт — маршрут «наш склад → наш склад»
    несопоставим с маршрутами на маркетплейс. Период — по дате забора.

    🔴 Объявлен ВЫШЕ `/transfers/{transfer_id}`: иначе FastAPI сматчит
    «logistics-report» на int-параметр пути и вернёт 422 вместо отчёта.
    """
    return await transfer_logistics.get_transfer_logistics_report(
        db,
        project.id,
        date_from=date_from,
        date_to=date_to,
        group_by=group_by,
        from_warehouse_id=from_warehouse_id,
        to_warehouse_id=to_warehouse_id,
        counterparty_id=counterparty_id,
        summary_only=summary_only,
    )


@router.get("/transfers/{transfer_id}", response_model=StockTransferSchema)
async def get_transfer(
    transfer_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Одно перемещение с составом (карточка переезда: машина, логистика, связки)."""
    transfer = await warehouse_service.get_transfer(db, project.id, transfer_id)
    if not transfer:
        raise HTTPException(404, "Перемещение не найдено")
    return StockTransferSchema.model_validate(transfer)


@router.post(
    "/transfers/{transfer_id}/assign-vehicle",
    response_model=StockTransferSchema,
    dependencies=[Depends(rate_limit_write)],
)
async def assign_vehicle_transfer(
    transfer_id: int,
    payload: TransferAssignVehicle,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Назначить машину на переезд. Статус НЕ меняется — признак назначения `vehicle_assigned_at`."""
    try:
        transfer = await warehouse_service.assign_vehicle_transfer(
            db, project.id, transfer_id, payload
        )
        return StockTransferSchema.model_validate(transfer)
    except ValueError as e:
        raise _transfer_error(e) from None


@router.post(
    "/transfers/{transfer_id}/unassign-vehicle",
    response_model=StockTransferSchema,
    dependencies=[Depends(rate_limit_write)],
)
async def unassign_vehicle_transfer(
    transfer_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Снять машину с переезда (только DRAFT). Транспортную единицу груза не трогает."""
    try:
        transfer = await warehouse_service.unassign_vehicle_transfer(db, project.id, transfer_id)
        return StockTransferSchema.model_validate(transfer)
    except ValueError as e:
        raise _transfer_error(e) from None


@router.post("/transfers", dependencies=[Depends(rate_limit_write)])
async def create_transfer(
    payload: StockTransferCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Create stock transfer between warehouses."""
    try:
        transfer = await warehouse_service.create_transfer(
            db,
            project.id,
            payload.model_dump(),
        )
        return StockTransferSchema.model_validate(transfer)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/transfers/{transfer_id}/send", dependencies=[Depends(rate_limit_write)])
async def send_transfer(
    transfer_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Send transfer → deduct source, mark in_transit."""
    try:
        transfer = await warehouse_service.send_transfer(db, project.id, transfer_id)
        return StockTransferSchema.model_validate(transfer)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/transfers/{transfer_id}/complete", dependencies=[Depends(rate_limit_write)])
async def complete_transfer(
    transfer_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Complete transfer → add to destination."""
    try:
        transfer = await warehouse_service.complete_transfer(db, project.id, transfer_id)
        return StockTransferSchema.model_validate(transfer)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.delete("/transfers/{transfer_id}", dependencies=[Depends(rate_limit_write)])
async def cancel_transfer(
    transfer_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Cancel DRAFT transfer (soft-delete). Sent transfers cannot be cancelled."""
    try:
        await warehouse_service.cancel_transfer(db, project.id, transfer_id)
        return {"message": "Перемещение удалено"}
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# ─── Stock Adjustments (Корректировка) ────────────────────────────────────


@router.post("/{warehouse_id}/adjustment", dependencies=[Depends(rate_limit_write)])
async def create_adjustment(
    warehouse_id: int,
    payload: StockAdjustmentCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Create stock adjustment (±)."""
    try:
        adj = await warehouse_service.create_adjustment(
            db,
            project.id,
            warehouse_id,
            payload.model_dump(),
        )
        return StockAdjustmentSchema.model_validate(adj)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


# ─── Defective Goods (Брак) ──────────────────────────────────────────────


@router.get("/{warehouse_id}/defects")
async def get_defect_stock(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get defective stock for a warehouse."""
    return await warehouse_defect.get_defect_stock(db, project.id, warehouse_id)


@router.post("/{warehouse_id}/defects/mark", dependencies=[Depends(rate_limit_write)])
async def mark_defect(
    warehouse_id: int,
    payload: DefectOperationCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Mark existing good stock as defective."""
    try:
        return await warehouse_defect.mark_defect(db, project.id, warehouse_id, payload.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{warehouse_id}/defects/receive", dependencies=[Depends(rate_limit_write)])
async def receive_defect(
    warehouse_id: int,
    payload: DefectOperationCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Receive defective goods from outside (WB returns, etc.)."""
    try:
        return await warehouse_defect.receive_defect(db, project.id, warehouse_id, payload.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{warehouse_id}/defects/writeoff", dependencies=[Depends(rate_limit_write)])
async def writeoff_defect(
    warehouse_id: int,
    payload: DefectOperationCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Write off (destroy) defective goods."""
    try:
        return await warehouse_defect.writeoff_defect(db, project.id, warehouse_id, payload.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{warehouse_id}/defects/recover", dependencies=[Depends(rate_limit_write)])
async def recover_defect(
    warehouse_id: int,
    payload: DefectOperationCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Restore defective goods to good stock (repaired)."""
    try:
        return await warehouse_defect.recover_defect(db, project.id, warehouse_id, payload.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post(
    "/{warehouse_id}/defects/mark-bulk",
    response_model=DefectBulkResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def mark_defect_bulk(
    warehouse_id: int,
    payload: DefectBulkOperation,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Bulk: mark existing good stock as defective (one request, N items)."""
    try:
        return await warehouse_defect.mark_defect_bulk(db, project.id, warehouse_id, payload.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post(
    "/{warehouse_id}/defects/receive-bulk",
    response_model=DefectBulkResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def receive_defect_bulk(
    warehouse_id: int,
    payload: DefectBulkOperation,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Bulk: receive defective goods from outside (WB returns, etc.)."""
    try:
        return await warehouse_defect.receive_defect_bulk(db, project.id, warehouse_id, payload.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post(
    "/{warehouse_id}/defects/writeoff-bulk",
    response_model=DefectBulkResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def writeoff_defect_bulk(
    warehouse_id: int,
    payload: DefectBulkOperation,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Bulk: write off (destroy) defective goods."""
    try:
        return await warehouse_defect.writeoff_defect_bulk(db, project.id, warehouse_id, payload.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post(
    "/{warehouse_id}/defects/recover-bulk",
    response_model=DefectBulkResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def recover_defect_bulk(
    warehouse_id: int,
    payload: DefectBulkOperation,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Bulk: restore defective goods to good stock (repaired)."""
    try:
        return await warehouse_defect.recover_defect_bulk(db, project.id, warehouse_id, payload.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.delete(
    "/{warehouse_id}/defects/movements/{movement_id}",
    dependencies=[Depends(rate_limit_write)],
)
async def delete_defect_movement(
    warehouse_id: int,
    movement_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete a defect stock movement and revert its stock effect."""
    try:
        return await warehouse_defect.delete_defect_movement(db, project.id, warehouse_id, movement_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/defects/summary")
async def get_defect_summary(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Summary defective stock across all warehouses."""
    return await warehouse_defect.get_defect_summary(db, project.id)


@router.get("/{warehouse_id}/defect-receipts")
async def list_defect_receipts(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List defect receipts (InboundReceipt with is_defect=true) for a warehouse."""
    rows = await warehouse_defect.get_defect_receipts(db, project.id, warehouse_id)
    return [InboundReceiptSchema.model_validate(r) for r in rows]


@router.get("/{warehouse_id}/defect-shipments")
async def list_defect_shipments(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List defect writeoff shipments (OutboundShipment with is_defect=true) for a warehouse."""
    rows = await warehouse_defect.get_defect_shipments(db, project.id, warehouse_id)
    return [OutboundShipmentSchema.model_validate(r) for r in rows]


@router.get("/{warehouse_id}/defect-mark-operations")
async def list_defect_mark_operations(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List mark-as-defect operations (DM-N) for a warehouse."""
    rows = await warehouse_defect.list_mark_operations(db, project.id, warehouse_id)
    return [DefectMarkOperationSchema.model_validate(r) for r in rows]


@router.get("/defect-mark-operations/{operation_id}")
async def get_defect_mark_operation(
    operation_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get a single mark-as-defect operation by id."""
    op = await warehouse_defect.get_mark_operation(db, project.id, operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail="Operation not found")
    return DefectMarkOperationSchema.model_validate(op)


@router.post(
    "/defect-mark-operations/{operation_id}/cancel",
    dependencies=[Depends(rate_limit_write)],
)
async def cancel_defect_mark_operation(
    operation_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a DM-N operation: revert each item (defect → back to good)."""
    try:
        return await warehouse_defect.cancel_mark_operation(db, project.id, operation_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ─── Summary & Cost Price ────────────────────────────────────────────────


@router.get("/stock/summary")
async def get_stock_summary(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Summary stock across all warehouses."""
    return await warehouse_service.get_stock_summary(db, project.id)


@router.get("/stock/unified")
async def get_unified_stock(
    group_by: str = Query("sku", pattern="^(sku|brand|subject|imt|tag|abc)$"),
    brand: str | None = Query(None, max_length=200),
    include_forecast: bool = Query(False),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Unified stock: own warehouses + WB + in-transit. Supports grouping.

    include_forecast=False (default) — realization matches БДР (fact-only).
    include_forecast=True — items that are en route (factory/vehicle) but not
    yet physically on shelf get an estimated revenue from the category average.
    """
    return await warehouse_service.get_unified_stock_summary(
        db,
        project.id,
        group_by=group_by,
        brand=brand or None,
        include_forecast=include_forecast,
    )


@router.put("/stock/{stock_id}/cost-price", dependencies=[Depends(rate_limit_write)])
async def update_cost_price(
    stock_id: int,
    payload: CostPriceUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Set manual cost_price on a stock row."""
    stock = await warehouse_service.update_cost_price(
        db,
        project.id,
        stock_id,
        payload.cost_price,
    )
    if not stock:
        raise HTTPException(404, "Stock record not found")
    return WarehouseStockSchema.model_validate(stock)


# ─── Box-multiplicity (кратность коробки) ───────────────────────────────────


@router.get("/box-multiplicity", response_model=BoxMultiplicityResponse)
async def get_box_multiplicity(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Per-SKU box quantity table: manual override + last vehicle ppb + factory fallback."""
    items = await box_multiplicity_service.get_box_multiplicity_table(db, project.id)
    return {"items": items, "total": len(items)}


@router.patch(
    "/box-multiplicity/{nm_id}",
    response_model=BoxMultiplicityRow,
    dependencies=[Depends(rate_limit_write)],
)
async def patch_box_multiplicity(
    nm_id: int,
    payload: BoxMultiplicityUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Partial update: any subset of {box_qty_override, use_box_multiplicity}.

    Send only fields you want to change; omit others. `box_qty_override=null`
    inside the body explicitly clears the manual ppb.
    """
    fields = payload.model_dump(exclude_unset=True)
    kwargs: dict = {}
    if "box_qty_override" in fields:
        kwargs["box_qty_override"] = fields["box_qty_override"]
    if "use_box_multiplicity" in fields:
        kwargs["use_box_multiplicity"] = fields["use_box_multiplicity"]

    ok = await box_multiplicity_service.update_box_multiplicity(
        db,
        project.id,
        nm_id,
        **kwargs,
    )
    if not ok:
        raise HTTPException(404, "SKU not found")
    items = await box_multiplicity_service.get_box_multiplicity_table(
        db,
        project.id,
        nm_id_filter=nm_id,
    )
    if not items:
        raise HTTPException(404, "SKU not found after update")
    return items[0]


@router.post(
    "/box-multiplicity/bulk",
    response_model=BoxMultiplicityBulkResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def bulk_box_multiplicity(
    payload: BoxMultiplicityBulkRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Bulk paste-update by barcode. Each item is partial: только переданные
    поля применяются (Pydantic `exclude_unset`-семантика)."""
    items_dicts = [it.model_dump(exclude_unset=True) for it in payload.items]
    # Кэшируем project.id ДО commit-а в сервисе: после commit ORM-атрибуты
    # помечаются expired и ленивая подгрузка ломается в async-контексте
    # (MissingGreenlet).
    project_id = project.id

    result = await box_multiplicity_service.bulk_update_by_barcode(
        db,
        project_id,
        items_dicts,
    )

    updated_rows: list[dict] = []
    if result["updated_nm_ids"]:
        all_rows = await box_multiplicity_service.get_box_multiplicity_table(
            db,
            project_id,
        )
        upd_set = result["updated_nm_ids"]
        updated_rows = [r for r in all_rows if r["nm_id"] in upd_set]

    return {
        "updated": updated_rows,
        "not_found": result["not_found"],
        "matched_count": len(result["matched_barcodes"]),
        "locked": result["locked"],
        "batch_id": result.get("batch_id"),
    }


@router.patch(
    "/box-multiplicity/per-warehouse/{barcode}/{warehouse_id}",
    response_model=BoxMultiplicityRow,
    dependencies=[Depends(rate_limit_write)],
)
async def patch_per_warehouse_box_multiplicity(
    barcode: str,
    warehouse_id: int,
    payload: BoxMultiplicityPerWarehouseUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Per-ФФ ручная кратность/размер, partial update. Создаёт строку если её нет.

    Если на этом ФФ есть машинная приёмка И запрос меняет `box_qty`/`box_size`
    → 409. Менять только `use_box_multiplicity` на машинном ФФ — разрешено.
    """
    fields = payload.model_dump(exclude_unset=True)
    kwargs: dict = {}
    if "box_qty" in fields:
        kwargs["box_qty"] = fields["box_qty"]
    if "box_size" in fields:
        kwargs["box_size"] = fields["box_size"]
    if "use_box_multiplicity" in fields:
        kwargs["use_box_multiplicity"] = fields["use_box_multiplicity"]

    # 409-guard: машинно-заблокированную ячейку нельзя менять по box_qty/box_size.
    if ("box_qty" in kwargs or "box_size" in kwargs) and await box_multiplicity_service.has_machine_box_qty(
        db, project.id, barcode, warehouse_id
    ):
        raise HTTPException(
            409,
            "Кратность на этом ФФ задаётся машиной-поставкой — box_qty/box_size не редактируется",
        )

    ok = await box_multiplicity_service.update_per_warehouse(
        db,
        project.id,
        barcode,
        warehouse_id,
        **kwargs,
    )
    if not ok:
        raise HTTPException(404, "barcode or warehouse not found in project")

    # Вернём целую строку SKU (по barcode → nm_id) с обновлённым per_warehouse.
    items = await box_multiplicity_service.get_box_multiplicity_table(db, project.id)
    row = next((r for r in items if r["barcode"] == barcode), None)
    if not row:
        raise HTTPException(404, "SKU not found after update")
    return row


@router.get(
    "/box-multiplicity/sources/{barcode}",
    response_model=BoxMultiplicitySourcesResponse,
)
async def get_box_multiplicity_sources(
    barcode: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Drill-down источников снабжения SKU: машины + заказы на фабрику (read-only история)."""
    items = await box_multiplicity_service.get_sku_box_sources(db, project.id, barcode)
    return {"items": items}


@router.get(
    "/box-multiplicity/changes/{barcode}",
    response_model=BoxMultiplicityChangesResponse,
)
async def get_box_multiplicity_changes(
    barcode: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """История изменений кратности/размера коробки одного SKU — свежие сверху."""
    items = await box_multiplicity_service.get_change_log(db, project.id, barcode)
    return {"items": items}


@router.post(
    "/box-multiplicity/changes/{change_id}/revert",
    response_model=BoxMultiplicityRow,
    dependencies=[Depends(rate_limit_write)],
)
async def revert_box_multiplicity_change(
    change_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Откатить одно изменение из журнала — применить `old_value` обратно.

    404 если запись журнала не найдена; 409 если откат `box_qty`/`box_size`
    заблокирован машиной-поставкой на этом ФФ.
    """
    result = await box_multiplicity_service.revert_change(db, project.id, change_id)
    if result["barcode"] is None:
        raise HTTPException(404, "Change log entry not found")
    if result["machine_locked"]:
        raise HTTPException(
            409,
            "Кратность на этом ФФ задаётся машиной-поставкой — откат не применён",
        )

    items = await box_multiplicity_service.get_box_multiplicity_table(db, project.id)
    row = next((r for r in items if r["barcode"] == result["barcode"]), None)
    if not row:
        raise HTTPException(404, "SKU not found after revert")
    return row


@router.get(
    "/box-multiplicity/changes/batches",
    response_model=BoxMultiplicityBatchListResponse,
)
async def list_box_multiplicity_batches(
    limit: int = 50,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Список последних bulk-операций проекта (для глобального вида истории)."""
    items = await box_multiplicity_service.list_recent_batches(db, project.id, limit)
    return {"items": items}


@router.post(
    "/box-multiplicity/changes/batch/{batch_id}/revert",
    response_model=BoxMultiplicityBatchRevertResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def revert_box_multiplicity_batch(
    batch_id: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Откатить всю bulk-операцию — все записи журнала с этим batch_id.

    404 если batch не найден в проекте. Машинно-заблокированные ФФ для
    box_qty/box_size пропускаются и возвращаются в `locked_barcodes`.
    """
    result = await box_multiplicity_service.revert_batch(db, project.id, batch_id)
    if not result["found"]:
        raise HTTPException(404, "Batch not found")
    return {
        "reverted": result["reverted"],
        "locked_barcodes": result["locked_barcodes"],
        "affected_barcodes": result["affected_barcodes"],
    }
