"""
Warehouse outbound — shipments and stock transfers.
"""

import logging
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.cache import invalidate_cache
from backend.models.warehouse import (
    MovementType,
    OutboundShipment,
    OutboundShipmentItem,
    OutboundStatus,
    StockMovement,
    StockTransfer,
    StockTransferItem,
    TransferStatus,
    WarehouseStock,
    WarehouseType,
)
from backend.services.warehouse_crud import get_warehouse
from backend.services.warehouse_stock_engine import (
    _next_number,
    _resolve_barcodes_batch,
    _update_stock,
)
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


# ─── Outbound Shipments (Отгрузка) ────────────────────────────────────────


async def list_shipments(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    include_defect: bool = False,
) -> list:
    """List shipments for a warehouse. By default excludes defect writeoff shipments."""
    query = (
        select(OutboundShipment)
        .options(selectinload(OutboundShipment.items))
        .where(
            OutboundShipment.project_id == project_id,
            OutboundShipment.warehouse_id == warehouse_id,
            OutboundShipment.is_deleted == False,  # noqa: E712
        )
    )
    if not include_defect:
        query = query.where(OutboundShipment.is_defect.is_(False))
    query = query.order_by(OutboundShipment.id.desc()).limit(500)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_shipment(db: AsyncSession, project_id: int, shipment_id: int) -> OutboundShipment | None:
    """Get shipment with items."""
    result = await db.execute(
        select(OutboundShipment).where(
            OutboundShipment.id == shipment_id,
            OutboundShipment.project_id == project_id,
            OutboundShipment.is_deleted == False,  # noqa: E712
        )
    )
    shipment = result.scalar_one_or_none()
    if shipment:
        await db.refresh(shipment, ["items"])
    return shipment


async def create_shipment(db: AsyncSession, project_id: int, warehouse_id: int, payload: dict) -> OutboundShipment:
    """Create outbound shipment. Only from FULFILLMENT warehouses."""
    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")
    if wh.warehouse_type != WarehouseType.FULFILLMENT:
        raise ValueError("Shipments can only be created from FULFILLMENT warehouses")

    number = await _next_number(db, project_id, "OUT", OutboundShipment)

    shipment = OutboundShipment(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=number,
        status=OutboundStatus.DRAFT,
        destination=payload.get("destination"),
        comment=payload.get("comment"),
    )
    db.add(shipment)
    await db.flush()

    items_data = payload.get("items", [])
    barcode_map = await _resolve_barcodes_batch(db, project_id, [d["barcode"] for d in items_data])
    for item_data in items_data:
        nom = barcode_map[item_data["barcode"]]
        item = OutboundShipmentItem(
            project_id=project_id,
            shipment_id=shipment.id,
            nomenclature_id=nom.id,
            barcode=item_data["barcode"],
            quantity=item_data["quantity"],
        )
        db.add(item)

    await db.commit()
    await db.refresh(shipment, ["items"])
    return shipment


async def ship_shipment(db: AsyncSession, project_id: int, shipment_id: int) -> OutboundShipment:
    """
    Ship: DRAFT → SHIPPED.
    Checks stock >= qty, then stock -= qty for each item.
    """
    shipment = await get_shipment(db, project_id, shipment_id)
    if not shipment:
        raise ValueError("Shipment not found")

    if shipment.status != OutboundStatus.DRAFT:
        raise ValueError(f"Cannot ship in status {shipment.status}")

    if not shipment.items:
        raise ValueError("Cannot ship with no items")

    for item in shipment.items:
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=shipment.warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=-item.quantity,
            movement_type=MovementType.OUTBOUND,
            reference_type="SHIPMENT",
            reference_id=shipment.id,
        )

    shipment.status = OutboundStatus.SHIPPED
    shipment.shipped_date = date.today()

    await db.commit()
    await db.refresh(shipment, ["items"])
    return shipment


async def deliver_shipment(db: AsyncSession, project_id: int, shipment_id: int) -> OutboundShipment:
    """Mark shipment as delivered: SHIPPED → DELIVERED."""
    shipment = await get_shipment(db, project_id, shipment_id)
    if not shipment:
        raise ValueError("Shipment not found")

    if shipment.status != OutboundStatus.SHIPPED:
        raise ValueError(f"Cannot deliver in status {shipment.status}")

    shipment.status = OutboundStatus.DELIVERED

    await db.commit()
    await db.refresh(shipment, ["items"])
    return shipment


async def cancel_shipment(db: AsyncSession, project_id: int, shipment_id: int) -> OutboundShipment:
    """
    Cancel shipped shipment: SHIPPED → CANCELLED.
    Returns stock for each item.
    """
    shipment = await get_shipment(db, project_id, shipment_id)
    if not shipment:
        raise ValueError("Shipment not found")

    if shipment.status != OutboundStatus.SHIPPED:
        raise ValueError(f"Can only cancel SHIPPED shipments, got {shipment.status}")

    # For defect writeoff shipments — return defect_quantity, not good stock.
    for item in shipment.items:
        if shipment.is_defect:
            delta = 0
            defect_delta = item.quantity
        else:
            delta = item.quantity
            defect_delta = 0
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=shipment.warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=delta,
            defect_delta=defect_delta,
            movement_type=MovementType.OUTBOUND_CANCEL,
            reference_type="SHIPMENT",
            reference_id=shipment.id,
            comment=f"Cancel shipment {shipment.number}",
        )

    shipment.status = OutboundStatus.CANCELLED

    await db.commit()
    await db.refresh(shipment, ["items"])
    return shipment


# ─── Stock Transfers (Перемещение) ─────────────────────────────────────────


async def list_transfers(
    db: AsyncSession,
    project_id: int,
    in_transit_only: bool = False,
    warehouse_id: int | None = None,
) -> list:
    """List transfers. Optionally filter only IN_TRANSIT and/or by warehouse (source OR destination)."""
    query = (
        select(StockTransfer)
        .options(selectinload(StockTransfer.items))
        .where(
            StockTransfer.project_id == project_id,
            StockTransfer.is_deleted == False,  # noqa: E712
        )
    )
    if in_transit_only:
        query = query.where(StockTransfer.status == TransferStatus.IN_TRANSIT)
    if warehouse_id is not None:
        query = query.where(
            or_(
                StockTransfer.from_warehouse_id == warehouse_id,
                StockTransfer.to_warehouse_id == warehouse_id,
            )
        )
    query = query.order_by(StockTransfer.id.desc()).limit(500)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_transfer(db: AsyncSession, project_id: int, transfer_id: int) -> StockTransfer | None:
    """Get transfer with items."""
    result = await db.execute(
        select(StockTransfer).where(
            StockTransfer.id == transfer_id,
            StockTransfer.project_id == project_id,
            StockTransfer.is_deleted == False,  # noqa: E712
        )
    )
    transfer = result.scalar_one_or_none()
    if transfer:
        await db.refresh(transfer, ["items"])
    return transfer


async def create_transfer(db: AsyncSession, project_id: int, payload: dict) -> StockTransfer:
    """Create stock transfer between two warehouses."""
    from_wh = await get_warehouse(db, project_id, payload["from_warehouse_id"])
    to_wh = await get_warehouse(db, project_id, payload["to_warehouse_id"])
    if not from_wh:
        raise ValueError("Source warehouse not found")
    if not to_wh:
        raise ValueError("Destination warehouse not found")
    if from_wh.id == to_wh.id:
        raise ValueError("Cannot transfer to the same warehouse")

    number = await _next_number(db, project_id, "TR", StockTransfer)

    transfer = StockTransfer(
        project_id=project_id,
        from_warehouse_id=from_wh.id,
        to_warehouse_id=to_wh.id,
        number=number,
        status=TransferStatus.DRAFT,
        comment=payload.get("comment"),
        is_defect=payload.get("is_defect", False),
        defect_reason=payload.get("defect_reason"),
    )
    db.add(transfer)
    await db.flush()

    items_data = payload.get("items", [])
    barcode_map = await _resolve_barcodes_batch(db, project_id, [d["barcode"] for d in items_data])
    for item_data in items_data:
        nom = barcode_map[item_data["barcode"]]
        item = StockTransferItem(
            project_id=project_id,
            transfer_id=transfer.id,
            nomenclature_id=nom.id,
            barcode=item_data["barcode"],
            quantity=item_data["quantity"],
        )
        db.add(item)

    await db.commit()
    await db.refresh(transfer, ["items"])
    return transfer


async def _get_transfer_locked(db: AsyncSession, project_id: int, transfer_id: int) -> StockTransfer | None:
    """Get transfer with row lock (FOR UPDATE) — для мутаций статуса, против гонки send/cancel."""
    result = await db.execute(
        select(StockTransfer)
        .where(
            StockTransfer.id == transfer_id,
            StockTransfer.project_id == project_id,
            StockTransfer.is_deleted == False,  # noqa: E712
        )
        .with_for_update()
    )
    transfer = result.scalar_one_or_none()
    if transfer:
        await db.refresh(transfer, ["items"])
    return transfer


async def cancel_transfer(db: AsyncSession, project_id: int, transfer_id: int) -> None:
    """Cancel (soft-delete) a transfer. Only DRAFT — sent transfers already moved stock."""
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Transfer not found")
    if transfer.status != TransferStatus.DRAFT:
        raise ValueError(f"Cannot cancel in status {transfer.status}")

    transfer.soft_delete()
    await db.commit()


async def send_transfer(db: AsyncSession, project_id: int, transfer_id: int) -> StockTransfer:
    """
    Send transfer: DRAFT → IN_TRANSIT.
    source.stock -= qty, target.in_transit += qty.
    """
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Transfer not found")

    if transfer.status != TransferStatus.DRAFT:
        raise ValueError(f"Cannot send in status {transfer.status}")

    if not transfer.items:
        raise ValueError("Cannot send transfer with no items")

    is_defect = transfer.is_defect

    for item in transfer.items:
        if is_defect:
            # Deduct from source defect stock
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=transfer.from_warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=0,
                defect_delta=-item.quantity,
                movement_type=MovementType.DEFECT_TRANSFER_OUT,
                reference_type="TRANSFER",
                reference_id=transfer.id,
            )
        else:
            # Deduct from source warehouse (normal)
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=transfer.from_warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=-item.quantity,
                movement_type=MovementType.TRANSFER_OUT,
                reference_type="TRANSFER",
                reference_id=transfer.id,
            )

        # Mark as in_transit on destination
        result = await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id == transfer.to_warehouse_id,
                WarehouseStock.nomenclature_id == item.nomenclature_id,
            )
        )
        target_stock = result.scalar_one_or_none()
        if target_stock is None:
            target_stock = WarehouseStock(
                project_id=project_id,
                warehouse_id=transfer.to_warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                quantity=0,
                in_transit=0,
                defect_quantity=0,
                defect_in_transit=0,
            )
            db.add(target_stock)
            await db.flush()

        if is_defect:
            target_stock.defect_in_transit += item.quantity
        else:
            target_stock.in_transit += item.quantity
        target_stock.updated_at = utcnow()

    transfer.status = TransferStatus.IN_TRANSIT

    await db.commit()
    await db.refresh(transfer, ["items"])
    return transfer


async def complete_transfer(db: AsyncSession, project_id: int, transfer_id: int) -> StockTransfer:
    """
    Complete transfer: IN_TRANSIT → COMPLETED.
    target.stock += qty, target.in_transit -= qty.
    """
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Transfer not found")

    if transfer.status != TransferStatus.IN_TRANSIT:
        raise ValueError(f"Cannot complete in status {transfer.status}")

    is_defect = transfer.is_defect

    for item in transfer.items:
        if is_defect:
            # Add to destination defect stock
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=transfer.to_warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=0,
                defect_delta=item.quantity,
                movement_type=MovementType.DEFECT_TRANSFER_IN,
                reference_type="TRANSFER",
                reference_id=transfer.id,
            )
        else:
            # Add to destination stock (normal)
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=transfer.to_warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=item.quantity,
                movement_type=MovementType.TRANSFER_IN,
                reference_type="TRANSFER",
                reference_id=transfer.id,
            )

        # Decrease in_transit
        result = await db.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id == transfer.to_warehouse_id,
                WarehouseStock.nomenclature_id == item.nomenclature_id,
            )
        )
        target_stock = result.scalar_one_or_none()
        if target_stock:
            if is_defect:
                target_stock.defect_in_transit = max(0, target_stock.defect_in_transit - item.quantity)
            else:
                target_stock.in_transit = max(0, target_stock.in_transit - item.quantity)
            target_stock.updated_at = utcnow()

    # Наследуем ручную кратность коробов на склад-получатель (товар переехал —
    # «шт/короб» те же; раньше кратность заносили заново руками). Best-effort:
    # сбой наследования не валит приёмку перемещения.
    inherited = 0
    if not is_defect:
        try:
            from backend.services import box_multiplicity_service

            inherited = await box_multiplicity_service.inherit_on_transfer(
                db,
                project_id,
                transfer.from_warehouse_id,
                transfer.to_warehouse_id,
                [i.barcode for i in transfer.items],
                transfer.number,
            )
        except Exception:
            logger.warning("transfer box-qty inherit failed for %s", transfer.number, exc_info=True)

    transfer.status = TransferStatus.COMPLETED

    await db.commit()
    if inherited:
        await invalidate_cache("reports:warehouse_need")
    await db.refresh(transfer, ["items"])
    return transfer


async def receive_transfer_fact(
    db: AsyncSession,
    project_id: int,
    transfer_id: int,
    accepted_by_barcode: dict[str, int],
    *,
    defect_by_barcode: dict[str, int] | None = None,
    note: str | None = None,
    mark_ff_request_applied: int | None = None,
) -> dict:
    """Порционный приём перемещения ПО ФАКТУ (авто-приём по связанной ФФ-приёмке).

    ФФ принимает наш TR своими приёмками (возможно, порциями — кейс
    PVB-0000121 → TR-21) и отчитывается фактами. Здесь: TRANSFER_IN на факт
    (годное + брак отдельно), транзит уменьшается на принятое, сверх плана
    не приходуем (излишек — предмет сверки зеркала, не транзита). Transfer
    закрывается ТОЛЬКО когда суммарный факт покрыл весь план; недобор остаётся
    видимым транзитом до следующих порций или ручного закрытия. Идемпотентно:
    «уже принятое» выводится из движений TRANSFER_IN/DEFECT_TRANSFER_IN этого
    transfer на складе-назначении, повторный вызов доберёт максимум остаток.

    mark_ff_request_applied — id ФФ-заявки: если что-то принято (или план
    покрыт), её transfer_fact_applied_at ставится В ТОЙ ЖЕ транзакции, что и
    движения. Иначе между «приход закоммичен» и «маркер закоммичен» было бы
    окно, в котором падение процесса заставило бы следующий синк применить
    тот же факт повторно (частичный факт добрал бы фантомный остаток).
    """
    defect_by_barcode = defect_by_barcode or {}
    transfer = await _get_transfer_locked(db, project_id, transfer_id)
    if not transfer:
        raise ValueError("Transfer not found")
    if transfer.status != TransferStatus.IN_TRANSIT:
        raise ValueError(f"Cannot receive facts in status {transfer.status}")

    is_defect = transfer.is_defect

    # Уже принято по этому transfer на складе-назначении (порционные приёмы).
    received_res = await db.execute(
        select(
            StockMovement.nomenclature_id,
            func.coalesce(func.sum(StockMovement.quantity + StockMovement.defect_delta), 0),
        )
        .where(
            StockMovement.project_id == project_id,
            StockMovement.warehouse_id == transfer.to_warehouse_id,
            StockMovement.reference_type == "TRANSFER",
            StockMovement.reference_id == transfer.id,
            StockMovement.movement_type.in_(
                [MovementType.TRANSFER_IN.value, MovementType.DEFECT_TRANSFER_IN.value]
            ),
        )
        .group_by(StockMovement.nomenclature_id)
    )
    received_map: dict[int, int] = {row[0]: int(row[1] or 0) for row in received_res.all()}

    # План по номенклатуре: дубли строк одного ШК схлопываем ДО цикла — иначе
    # каждая строка получила бы полный факт и «приём по факту» превратился бы
    # в «приём по плану» (create_transfer дубли не запрещает).
    plan_map: dict[int, tuple[str, int]] = {}
    for item in transfer.items:
        bc, qty = plan_map.get(item.nomenclature_id, (item.barcode, 0))
        plan_map[item.nomenclature_id] = (bc, qty + item.quantity)
    plan_barcodes = {bc for bc, _ in plan_map.values()}
    # ШК факта, которых нет в составе перемещения, — сигнал кривой связки или
    # чужого товара в приёмке; вызывающий логирует, при пустом приёме не ставит маркер.
    unmatched = sorted((set(accepted_by_barcode) | set(defect_by_barcode)) - plan_barcodes)

    received_good = 0
    received_defect = 0
    remaining_total = 0
    for nomenclature_id, (barcode, plan_qty) in plan_map.items():
        already = received_map.get(nomenclature_id, 0)
        remaining = max(plan_qty - already, 0)
        fact_good = max(int(accepted_by_barcode.get(barcode, 0)), 0)
        fact_defect = max(int(defect_by_barcode.get(barcode, 0)), 0)
        take_good = min(fact_good, remaining)
        take_defect = min(fact_defect, remaining - take_good)

        # Перемещение брака: весь факт приходуется браком же.
        if is_defect:
            take_defect, take_good = take_good + take_defect, 0

        if take_good > 0:
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=transfer.to_warehouse_id,
                nomenclature_id=nomenclature_id,
                barcode=barcode,
                delta=take_good,
                movement_type=MovementType.TRANSFER_IN,
                reference_type="TRANSFER",
                reference_id=transfer.id,
                comment=note,
            )
        if take_defect > 0:
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=transfer.to_warehouse_id,
                nomenclature_id=nomenclature_id,
                barcode=barcode,
                delta=0,
                defect_delta=take_defect,
                movement_type=MovementType.DEFECT_TRANSFER_IN,
                reference_type="TRANSFER",
                reference_id=transfer.id,
                comment=note,
            )

        taken = take_good + take_defect
        if taken > 0:
            result = await db.execute(
                select(WarehouseStock).where(
                    WarehouseStock.project_id == project_id,
                    WarehouseStock.warehouse_id == transfer.to_warehouse_id,
                    WarehouseStock.nomenclature_id == nomenclature_id,
                )
            )
            target_stock = result.scalar_one_or_none()
            if target_stock:
                if is_defect:
                    target_stock.defect_in_transit = max(0, target_stock.defect_in_transit - taken)
                else:
                    target_stock.in_transit = max(0, target_stock.in_transit - taken)
                target_stock.updated_at = utcnow()

        received_good += take_good
        received_defect += take_defect
        remaining_total += remaining - taken

    completed = remaining_total == 0
    if completed:
        transfer.status = TransferStatus.COMPLETED
    if note:
        stamp = f"{note}: принято {received_good} годн."
        if received_defect:
            stamp += f" + {received_defect} брак"
        if remaining_total:
            stamp += f", в пути осталось {remaining_total}"
        transfer.comment = f"{transfer.comment}\n{stamp}" if transfer.comment else stamp

    # Полное покрытие = фактическое завершение перемещения: наследуем ручную
    # кратность коробов на склад-получатель, как ручной complete_transfer —
    # авто-приём для migfull-складов ЗАМЕНЯЕТ ручное закрытие, без наследования
    # SKU без кратности выпадал бы из отгрузки в предбронь (грабля TR-20).
    inherited = 0
    if completed and not is_defect:
        try:
            from backend.services import box_multiplicity_service

            inherited = await box_multiplicity_service.inherit_on_transfer(
                db,
                project_id,
                transfer.from_warehouse_id,
                transfer.to_warehouse_id,
                [bc for bc, _ in plan_map.values()],
                transfer.number,
            )
        except Exception:
            logger.warning("transfer box-qty inherit failed for %s", transfer.number, exc_info=True)

    marker_set = False
    if mark_ff_request_applied is not None and (received_good + received_defect > 0 or completed):
        from backend.models.fulfillment import FulfillmentRequest

        req_row = await db.get(FulfillmentRequest, mark_ff_request_applied)
        if req_row is not None and req_row.project_id == project_id:
            req_row.transfer_fact_applied_at = utcnow()
            marker_set = True

    await db.commit()
    # Сток изменился — сбрасываем отчётные кэши (iron rule 7): расхождение
    # остатков и потребность обязаны увидеть приход сразу, а не через TTL.
    if received_good or received_defect:
        await invalidate_cache("reports:balance")
        await invalidate_cache("reports:assembly_link_anomalies")
        await invalidate_cache("reports:warehouse_need")
    elif inherited:
        await invalidate_cache("reports:warehouse_need")
    await db.refresh(transfer, ["items"])
    return {
        "received_good": received_good,
        "received_defect": received_defect,
        "remaining_total": remaining_total,
        "completed": completed,
        "unmatched": unmatched,
        "applied_marker": marker_set,
    }
