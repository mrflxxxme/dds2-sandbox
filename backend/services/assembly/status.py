# ruff: noqa: RUF002, RUF003
"""
Assembly Request service — status transitions and audit.

Part of the assembly service package. See backend/DOMAIN_ASSEMBLY.md for spec.
One-way dependency: status -> crud (never crud -> status, except _log_status_change).
"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.models.assembly import (
    ASSEMBLY_TRANSITIONS,
    AssemblyRequest,
    AssemblyStatus,
    AssemblyStatusHistory,
)
from backend.models.counterparty import Counterparty
from backend.models.warehouse import (
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    MovementType,
    OutboundShipment,
    OutboundShipmentItem,
    OutboundStatus,
    Warehouse,
    WarehouseType,
)
from backend.models.wb_fbo import WbFboSupply
from backend.schemas.assembly import AssignVehicle
from backend.services.warehouse_service import _next_number, _update_stock
from backend.utils.time import utcnow


async def _resolve_carrier(db: AsyncSession, project_id: int, inn: str | None, name: str | None) -> int | None:
    """Upsert Counterparty by (project_id, inn), bump OTHER→CARRIER. Returns cp.id or None."""
    if not inn or not inn.strip():
        return None
    clean_inn = inn.strip()
    cp_name = (name or "").strip() or clean_inn
    res = await db.execute(
        select(Counterparty).where(
            Counterparty.project_id == project_id,
            Counterparty.inn == clean_inn,
            Counterparty.is_deleted == False,  # noqa: E712
        )
    )
    cp = res.scalar_one_or_none()
    if cp is None:
        cp = Counterparty(
            project_id=project_id,
            inn=clean_inn,
            name=cp_name,
            primary_type="CARRIER",
            created_by_import=False,
        )
        db.add(cp)
        await db.flush()
        return cp.id
    # Update name only if longer; bump type only if OTHER
    if cp_name and len(cp_name) > len(cp.name or ""):
        cp.name = cp_name
    if cp.primary_type == "OTHER":
        cp.primary_type = "CARRIER"
    return cp.id


# --- Helpers (used by crud.py via from .status import _log_status_change) ---


def _check_transition(current: AssemblyStatus, target: AssemblyStatus) -> None:
    """Validate status transition. Raises ValueError if not allowed."""
    allowed = ASSEMBLY_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ValueError(f"Cannot transition from {current} to {target}. Allowed: {allowed or 'none'}")


async def _log_status_change(
    db: AsyncSession,
    project_id: int,
    request_id: int,
    old_status: str | None,
    new_status: str,
    changed_by: str = "user",
    comment: str | None = None,
) -> None:
    """Record a status transition in assembly_status_history."""
    history = AssemblyStatusHistory(
        project_id=project_id,
        assembly_request_id=request_id,
        old_status=old_status,
        new_status=new_status,
        changed_at=utcnow(),
        changed_by=changed_by,
        comment=comment,
    )
    db.add(history)


# --- Status transitions ----------------------------------------------------


async def start_assembly(db: AsyncSession, project_id: int, request_id: int) -> AssemblyRequest:
    """PENDING -> IN_PROGRESS (legacy), or READY -> IN_PROGRESS (reopen).

    When reopening from READY, clear actual_ready_date (it will be set again on mark_ready).
    Для legacy PENDING-заявок — проверяем доступные остатки (с учётом резерва других заявок).
    """
    from .crud import _format_deficit_error, _validate_available_for_assembly, get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    # Idempotent: новые заявки создаются сразу в IN_PROGRESS, повторный клик «Начать сборку» — no-op.
    if AssemblyStatus(req.status) == AssemblyStatus.IN_PROGRESS:
        return req

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.IN_PROGRESS)

    # Validate stock when starting from PENDING (legacy path: новые заявки уже создаются в IN_PROGRESS).
    # READY -> IN_PROGRESS skip — позиции уже резервировались, повторно не проверяем.
    if AssemblyStatus(req.status) == AssemblyStatus.PENDING:
        deficits = await _validate_available_for_assembly(
            db, project_id, req.warehouse_id, req.items, exclude_request_id=req.id
        )
        if deficits:
            raise ValueError(_format_deficit_error(deficits))

    old = req.status
    req.status = AssemblyStatus.IN_PROGRESS
    if AssemblyStatus(old) == AssemblyStatus.READY:
        req.actual_ready_date = None
    comment = "Возврат в сборку" if AssemblyStatus(old) == AssemblyStatus.READY else None
    await _log_status_change(db, project_id, req.id, old, AssemblyStatus.IN_PROGRESS, comment=comment)
    await db.commit()
    await db.refresh(req)
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")
    return req


async def mark_ready(db: AsyncSession, project_id: int, request_id: int) -> AssemblyRequest:
    """IN_PROGRESS -> READY. Set actual_ready_date = today.

    Requires pallets_count > 0 and pallet_weight_kg > 0.
    """
    from .crud import get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    if not req.wb_fbo_supply_id:
        raise ValueError("Нельзя перевести заявку в статус ГОТОВО без привязанной поставки WB")
    if not req.pallets_count or req.pallets_count <= 0:
        raise ValueError("Укажите количество палет перед завершением сборки")
    if not req.pallet_weight_kg or req.pallet_weight_kg <= 0:
        raise ValueError("Укажите вес палеты перед завершением сборки")

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.READY)
    old = req.status
    req.status = AssemblyStatus.READY
    req.actual_ready_date = date.today()
    await _log_status_change(db, project_id, req.id, old, AssemblyStatus.READY)
    await db.commit()
    await db.refresh(req)
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")
    return req


async def assign_vehicle(db: AsyncSession, project_id: int, request_id: int, payload: AssignVehicle) -> AssemblyRequest:
    """READY -> VEHICLE_ASSIGNED. Set vehicle_info, vehicle_assigned_at."""
    from .crud import get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.VEHICLE_ASSIGNED)
    old = req.status
    req.status = AssemblyStatus.VEHICLE_ASSIGNED
    req.vehicle_info = payload.vehicle_info
    req.vehicle_brand = payload.vehicle_brand
    req.driver_phone = payload.driver_phone
    req.pickup_date = payload.pickup_date
    req.pickup_time_slot = payload.pickup_time_slot
    req.pickup_cost = payload.pickup_cost
    req.delivery_date = payload.delivery_date
    req.vehicle_assigned_at = utcnow()
    cp_id = await _resolve_carrier(db, project_id, payload.carrier_inn, payload.carrier_name)
    if cp_id is not None:
        req.counterparty_id = cp_id
    await _log_status_change(db, project_id, req.id, old, AssemblyStatus.VEHICLE_ASSIGNED, comment=payload.vehicle_info)
    await db.commit()
    await db.refresh(req)
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")
    return req


async def unassign_vehicle(db: AsyncSession, project_id: int, request_id: int) -> AssemblyRequest:
    """VEHICLE_ASSIGNED -> READY. Clear vehicle info, return to ready for shipping."""
    from .crud import get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.READY)
    old = req.status
    req.status = AssemblyStatus.READY
    req.vehicle_info = None
    req.vehicle_brand = None
    req.driver_phone = None
    req.pickup_date = None
    req.pickup_time_slot = None
    req.pickup_cost = None
    req.delivery_date = None
    req.vehicle_assigned_at = None
    await _log_status_change(db, project_id, req.id, old, AssemblyStatus.READY, comment="Отмена назначения машины")
    await db.commit()
    await db.refresh(req)
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")
    return req


async def ship_request(db: AsyncSession, project_id: int, request_id: int) -> AssemblyRequest:
    """
    VEHICLE_ASSIGNED -> SHIPPED.
    """
    from .crud import _validate_stock_for_ship, get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    # Row-lock + ре-чтение статуса под локом: сериализует параллельные попытки
    # отгрузить ОДНУ заявку (WB-ACCEPTED авто-шип ‖ FF авто-шип ‖ ручной/bulk —
    # это разные scheduler-джобы/эндпоинты в одном event-loop). Без лока оба
    # читают VEHICLE_ASSIGNED и оба отгружают → дубль OutboundShipment + движений
    # стока. Проигравший блокируется до commit победителя, видит SHIPPED и
    # бракуется _check_transition. Симметрично row-lock в return_to_warehouse.
    locked_status = (
        await db.execute(
            select(AssemblyRequest.status)
            .where(AssemblyRequest.id == req.id, AssemblyRequest.project_id == project_id)
            .with_for_update()
        )
    ).scalar_one()

    _check_transition(AssemblyStatus(locked_status), AssemblyStatus.SHIPPED)

    # 1. Validate stock
    deficits = await _validate_stock_for_ship(db, project_id, req.warehouse_id, req.items)
    if deficits:
        lines = [f"  {d['name']} (ШК {d['barcode']}): нужно {d['need']}, на складе {d['have']}" for d in deficits]
        raise ValueError(f"Недостаточно остатков на складе ({len(deficits)} поз.):\n" + "\n".join(lines))

    # 2. Deduct stock
    for item in req.items:
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=req.warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=-item.quantity,
            movement_type=MovementType.OUTBOUND,
            reference_type="ASSEMBLY",
            reference_id=req.id,
        )

    # 3. Load FBO supply for linking
    fbo_result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == req.wb_fbo_supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    fbo_supply = fbo_result.scalar_one_or_none()

    # 4. Create OutboundShipment — попытка отгрузки со СНИМКОМ логистики.
    #    attempt_no = max по прошлым отгрузкам этой заявки + 1 (под тем же row-lock,
    #    что и статус) — переотгрузка после возврата даёт попытку №2, №3, …
    max_attempt = (
        await db.execute(
            select(func.coalesce(func.max(OutboundShipment.attempt_no), 0)).where(
                OutboundShipment.assembly_request_id == req.id,
                OutboundShipment.project_id == project_id,
                OutboundShipment.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one()
    ship_number = await _next_number(db, project_id, "OUT", OutboundShipment)
    shipment = OutboundShipment(
        project_id=project_id,
        warehouse_id=req.warehouse_id,
        number=ship_number,
        status=OutboundStatus.SHIPPED,
        destination=fbo_supply.warehouse_name if fbo_supply else None,
        wb_supply_id=fbo_supply.wb_supply_id if fbo_supply else None,
        shipped_date=date.today(),
        # Цепочка попыток: долговечная связь + снимок логистики на момент отгрузки.
        assembly_request_id=req.id,
        wb_fbo_supply_id=req.wb_fbo_supply_id,
        attempt_no=int(max_attempt) + 1,
        pickup_cost=req.pickup_cost,
        vehicle_info=req.vehicle_info,
        vehicle_brand=req.vehicle_brand,
        driver_phone=req.driver_phone,
        counterparty_id=req.counterparty_id,
        pickup_date=req.pickup_date,
        pickup_time_slot=req.pickup_time_slot,
        delivery_date=req.delivery_date,
        pallets_count=req.pallets_count,
        pallet_weight_kg=req.pallet_weight_kg,
    )
    db.add(shipment)
    await db.flush()

    # 5. Create shipment items
    for item in req.items:
        ship_item = OutboundShipmentItem(
            project_id=project_id,
            shipment_id=shipment.id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            quantity=item.quantity,
        )
        db.add(ship_item)

    # 6. Link FBO supply
    if fbo_supply:
        fbo_supply.outbound_shipment_id = shipment.id

    # 7. Update assembly request
    old = req.status
    req.outbound_shipment_id = shipment.id
    req.shipped_at = utcnow()
    req.status = AssemblyStatus.SHIPPED
    await _log_status_change(db, project_id, req.id, old, AssemblyStatus.SHIPPED)

    await db.commit()
    await db.refresh(req)

    await invalidate_cache("reports:balance")
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")

    return req


async def cancel_request(db: AsyncSession, project_id: int, request_id: int) -> AssemblyRequest:
    """
    Any status -> CANCELLED.
    If current status == SHIPPED -> rollback first.
    """
    from .crud import get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    current = AssemblyStatus(req.status)
    _check_transition(current, AssemblyStatus.CANCELLED)

    if current == AssemblyStatus.SHIPPED:
        # Rollback stock
        for item in req.items:
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=req.warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=+item.quantity,
                movement_type=MovementType.OUTBOUND,
                reference_type="ASSEMBLY_CANCEL",
                reference_id=req.id,
            )

        # Unlink FBO supply
        fbo_result = await db.execute(
            select(WbFboSupply).where(
                WbFboSupply.id == req.wb_fbo_supply_id,
                WbFboSupply.project_id == project_id,
            )
        )
        fbo_supply = fbo_result.scalar_one_or_none()
        # Совместная поставка: чистим указатель отгрузки поставки только если он наш
        # (для 1:1 это всегда так — no-op; не затираем отгрузку сестринской сборки).
        if fbo_supply and fbo_supply.outbound_shipment_id == req.outbound_shipment_id:
            fbo_supply.outbound_shipment_id = None

        # Soft-delete OutboundShipment
        if req.outbound_shipment_id:
            ship_result = await db.execute(
                select(OutboundShipment).where(
                    OutboundShipment.id == req.outbound_shipment_id,
                    OutboundShipment.project_id == project_id,
                    OutboundShipment.is_deleted == False,  # noqa: E712
                )
            )
            shipment = ship_result.scalar_one_or_none()
            if shipment:
                shipment.soft_delete()

        # Clear shipment link
        req.outbound_shipment_id = None
        req.shipped_at = None

    req.status = AssemblyStatus.CANCELLED
    await _log_status_change(db, project_id, req.id, current, AssemblyStatus.CANCELLED)
    await db.commit()
    await db.refresh(req)

    await invalidate_cache("reports:balance")
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")

    return req


async def return_to_warehouse(
    db: AsyncSession,
    project_id: int,
    request_id: int,
    changed_by: str = "user",
    comment: str | None = None,
    return_warehouse_id: int | None = None,
) -> AssemblyRequest:
    """
    SHIPPED/DELIVERED -> RETURNED: WB не принял поставку, товар вернулся на склад.

    Возврат НЕтерминальный: из RETURNED заявку либо переотгружают (`reopen_for_reship`,
    новый водитель + новая FBW-поставка), либо закрывают (`close_request`).

      - заявка НЕ отменяется → попытка остаётся в аналитике логистики (перевозка оплачена);
      - OutboundShipment этой попытки НЕ soft-удаляется (история отгрузки сохранена);
      - товар возвращается ГОДНЫМ стоком через InboundReceipt(ACCEPTED) на склад возврата
        (`return_warehouse_id` или склад-источник, если не задан — можно вернуть на ДРУГОЙ);
      - приёмка-возврат привязывается к заявке и НОМЕРУ ПОПЫТКИ (assembly_attempt_no);
      - зеркало последней попытки на заявке очищается (outbound_shipment_id / shipped_at /
        wb_fbo_supply_id → None), чтобы заявку можно было пере-связать с новой FBW-поставкой
        (partial-unique индекс по wb_fbo_supply_id перестаёт её держать).

    Связанная FBO-поставка помечается return_processed_at / return_type=GOODS (если ещё
    не обработана) — чтобы она не висела в «недоприёмке» и не было двойного возврата.
    """
    from .crud import get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    # Row-lock + ре-чтение статуса (симметрично ship_request): сериализует
    # «Вернуть» ‖ параллельные переходы по этой заявке.
    locked_status = (
        await db.execute(
            select(AssemblyRequest.status)
            .where(AssemblyRequest.id == req.id, AssemblyRequest.project_id == project_id)
            .with_for_update()
        )
    ).scalar_one()
    current = AssemblyStatus(locked_status)
    _check_transition(current, AssemblyStatus.RETURNED)

    if not req.items:
        raise ValueError("Нет позиций для возврата")

    # Склад возврата: по умолчанию — склад-источник, иначе валидируем выбранный.
    return_wh_id = req.warehouse_id
    if return_warehouse_id is not None and return_warehouse_id != req.warehouse_id:
        new_wh = (
            await db.execute(
                select(Warehouse).where(
                    Warehouse.id == return_warehouse_id,
                    Warehouse.project_id == project_id,
                    Warehouse.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if not new_wh:
            raise ValueError("Склад возврата не найден")
        if new_wh.warehouse_type != WarehouseType.FULFILLMENT:
            raise ValueError("Вернуть можно только на склад фулфилмента")
        return_wh_id = return_warehouse_id

    # Номер текущей попытки — с её отгрузки (для привязки приёмки-возврата).
    current_attempt_no = 1
    if req.outbound_shipment_id:
        cur_ship = (
            await db.execute(
                select(OutboundShipment.attempt_no).where(
                    OutboundShipment.id == req.outbound_shipment_id,
                    OutboundShipment.project_id == project_id,
                )
            )
        ).scalar_one_or_none()
        if cur_ship:
            current_attempt_no = int(cur_ship)

    # 0. Блокируем связанную FBO-поставку и гардим двойной возврат. Защищает от
    #    кросс-флоу (возврат уже оформлен через «недоприёмку») и гонки кликов.
    fbo_supply = None
    if req.wb_fbo_supply_id:
        fbo_supply = (
            await db.execute(
                select(WbFboSupply)
                .where(WbFboSupply.id == req.wb_fbo_supply_id, WbFboSupply.project_id == project_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if fbo_supply and fbo_supply.return_processed_at is not None:
            raise ValueError("Возврат по связанной поставке уже обработан")

    # 1. Приёмка-возврат на складе возврата, привязанная к попытке.
    #    Если склад возврата ведёт оператор ФФ-портала (Хамза) — приёмку создаём
    #    EXPECTED: сток вернётся, когда оператор её ПРИМЕТ в портале (с факт. кол-вом
    #    и браком). Иначе — прежнее поведение: сразу ACCEPTED + возврат годного стока.
    from backend.ff_context import warehouse_has_ff_operator

    pending_accept = await warehouse_has_ff_operator(db, project_id, return_wh_id)

    receipt_number = await _next_number(db, project_id, "IN", InboundReceipt)
    auto_comment = f"Возврат по заявке {req.number} (попытка {current_attempt_no}, WB не принял поставку)"
    if pending_accept:
        auto_comment += " — ожидает приёмки оператором ФФ"
    receipt = InboundReceipt(
        project_id=project_id,
        warehouse_id=return_wh_id,
        number=receipt_number,
        status=InboundStatus.EXPECTED if pending_accept else InboundStatus.ACCEPTED,
        planned_date=date.today(),
        actual_date=None if pending_accept else date.today(),
        comment=f"{auto_comment}. {comment}" if comment else auto_comment,
        assembly_request_id=req.id,
        assembly_attempt_no=current_attempt_no,
    )
    db.add(receipt)
    await db.flush()

    # 2. Позиции приёмки. Возврат остатков (+qty, годный) проводим СРАЗУ только если
    #    склад не портальный; для портального — при приёмке оператором (accept_receipt_ff).
    for item in req.items:
        db.add(
            InboundReceiptItem(
                project_id=project_id,
                receipt_id=receipt.id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                expected_qty=item.quantity,
                actual_qty=0 if pending_accept else item.quantity,
            )
        )
        if not pending_accept:
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=return_wh_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=+item.quantity,
                movement_type=MovementType.INBOUND,
                reference_type="RECEIPT",
                reference_id=receipt.id,
                comment=f"Возврат по заявке {req.number}",
            )

    # 3. Помечаем связанную FBO-поставку обработанной возвратом (флаг был None —
    #    проверено под row-lock на шаге 0) — уходит из «недоприёмки».
    if fbo_supply is not None:
        fbo_supply.return_processed_at = utcnow()
        fbo_supply.return_type = "GOODS"
        fbo_supply.return_qty = sum(it.quantity for it in req.items)
        # Указатель отгрузки чистим только если он наш (совместная поставка: не
        # затираем отгрузку сестринской сборки; для 1:1 — no-op).
        if fbo_supply.outbound_shipment_id == req.outbound_shipment_id:
            fbo_supply.outbound_shipment_id = None

    # 4. Статус → RETURNED. Снимок попытки уже на OutboundShipment — очищаем зеркало
    #    заявки, чтобы её можно было пере-связать с новой FBW-поставкой при переотгрузке.
    req.status = AssemblyStatus.RETURNED
    req.outbound_shipment_id = None
    req.shipped_at = None
    req.wb_fbo_supply_id = None
    await _log_status_change(
        db,
        project_id,
        req.id,
        current,
        AssemblyStatus.RETURNED,
        changed_by=changed_by,
        comment=comment or "Возврат на склад (WB не принял)",
    )
    await db.commit()
    await db.refresh(req)

    await invalidate_cache("reports:balance")
    await invalidate_cache("reports:logistics_analytics_v2")
    await invalidate_cache("reports:logistics_forecast")
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")

    return req


async def reopen_for_reship(
    db: AsyncSession,
    project_id: int,
    request_id: int,
    changed_by: str = "user",
) -> AssemblyRequest:
    """RETURNED -> READY: переотгрузка. Заявка снова «готова» — пользователь
    привязывает НОВУЮ FBW-поставку и назначает нового водителя, затем отгружает
    (следующая попытка).

    Сток обычно уже вернулся при возврате — кроме возврата на ПОРТАЛЬНЫЙ ФФ-склад:
    там приёмка-возврат создаётся EXPECTED и сток вернётся только когда оператор её
    примет (accept_receipt_ff). Пока приёмка не принята — переотгрузка запрещена,
    иначе ship списал бы недоступный сток (нехватка или двойное списание)."""
    from .crud import get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    pending_return = (
        await db.execute(
            select(InboundReceipt.id)
            .where(
                InboundReceipt.assembly_request_id == req.id,
                InboundReceipt.project_id == project_id,
                InboundReceipt.status == InboundStatus.EXPECTED,
                InboundReceipt.is_deleted == False,  # noqa: E712
            )
            .limit(1)
        )
    ).first()
    if pending_return is not None:
        raise ValueError("Возврат ещё не принят оператором ФФ — переотгрузка недоступна")

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.READY)
    old = req.status
    req.status = AssemblyStatus.READY
    req.actual_ready_date = date.today()
    await _log_status_change(
        db, project_id, req.id, old, AssemblyStatus.READY, changed_by=changed_by, comment="Переотгрузка"
    )
    await db.commit()
    await db.refresh(req)
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")
    return req


async def close_request(
    db: AsyncSession,
    project_id: int,
    request_id: int,
    changed_by: str = "user",
    comment: str | None = None,
) -> AssemblyRequest:
    """RETURNED/DELIVERED -> CLOSED: терминальное закрытие заявки. Сток уже вернулся
    при возврате (RETURNED) — здесь только меняем статус; история попыток сохранена."""
    from .crud import get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    _check_transition(AssemblyStatus(req.status), AssemblyStatus.CLOSED)
    old = req.status
    req.status = AssemblyStatus.CLOSED
    await _log_status_change(
        db,
        project_id,
        req.id,
        old,
        AssemblyStatus.CLOSED,
        changed_by=changed_by,
        comment=comment or "Заявка закрыта",
    )
    await db.commit()
    await db.refresh(req)
    await invalidate_cache("reports:logistics_analytics_v2")
    await invalidate_cache("reports:logistics_forecast")
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")
    return req


async def delete_request(db: AsyncSession, project_id: int, request_id: int) -> None:
    """
    Permanently remove (soft-delete) an assembly request.

    Allowed only for PENDING or CANCELLED requests — anything that has consumed
    or moved stock must remain visible for audit. To delete a SHIPPED/IN_PROGRESS
    request, cancel it first (which rolls back stock), then delete.
    """
    from .crud import get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    current = AssemblyStatus(req.status)
    if current not in {AssemblyStatus.PENDING, AssemblyStatus.CANCELLED}:
        raise ValueError(f"Заявку в статусе «{current.value}» нельзя удалить. Сначала отмените её.")

    req.soft_delete()
    await db.commit()

    await invalidate_cache("reports:balance")
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")


# --- Bulk operations --------------------------------------------------------


async def assign_vehicle_bulk(
    db: AsyncSession,
    project_id: int,
    ids: list[int],
    payload: AssignVehicle,
) -> list[AssemblyRequest]:
    """
    Bulk assign vehicle to multiple requests.
    """
    results = []
    for req_id in ids:
        req = await assign_vehicle(db, project_id, req_id, payload)
        results.append(req)
    return results


async def ship_bulk(
    db: AsyncSession,
    project_id: int,
    ids: list[int],
) -> list[AssemblyRequest]:
    """
    Bulk ship multiple requests.
    """
    results = []
    for req_id in ids:
        req = await ship_request(db, project_id, req_id)
        results.append(req)
    return results


# --- Deliver ----------------------------------------------------------------


async def deliver_request(
    db: AsyncSession,
    project_id: int,
    request_id: int,
    changed_by: str = "user",
    comment: str | None = None,
) -> AssemblyRequest:
    """SHIPPED -> DELIVERED."""
    from .crud import get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")
    _check_transition(AssemblyStatus(req.status), AssemblyStatus.DELIVERED)
    old = req.status
    req.status = AssemblyStatus.DELIVERED
    await _log_status_change(
        db, project_id, req.id, old, AssemblyStatus.DELIVERED, changed_by=changed_by, comment=comment
    )
    await db.commit()
    await db.refresh(req)
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")
    return req


# --- History ----------------------------------------------------------------


async def get_assembly_history(
    db: AsyncSession,
    project_id: int,
    request_id: int,
) -> list[AssemblyStatusHistory]:
    """Get status change history for an assembly request."""
    from .crud import get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")
    result = await db.execute(
        select(AssemblyStatusHistory)
        .where(
            AssemblyStatusHistory.assembly_request_id == request_id,
            AssemblyStatusHistory.project_id == project_id,
        )
        .order_by(AssemblyStatusHistory.changed_at.asc())
    )
    return list(result.scalars().all())
