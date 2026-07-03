"""
Warehouse inbound — receipts (priemka).
"""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.utils.time import utcnow

from backend.models.warehouse import (
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    MovementType,
    StockMovement,
)
from backend.services.warehouse_crud import get_warehouse
from backend.services.warehouse_stock_engine import (
    _next_number,
    _resolve_barcodes_batch,
    _update_stock,
)

logger = logging.getLogger(__name__)


# ─── Inbound Receipts (Приёмка) ────────────────────────────────────────────


async def list_receipts(
    db: AsyncSession,
    project_id: int,
    warehouse_id: int,
    include_defect: bool = True,
) -> list:
    """List receipts for a warehouse. Includes defect receipts by default (rendered with 'Брак' badge)."""
    query = (
        select(InboundReceipt)
        .options(selectinload(InboundReceipt.items))
        .where(
            InboundReceipt.project_id == project_id,
            InboundReceipt.warehouse_id == warehouse_id,
            InboundReceipt.is_deleted == False,  # noqa: E712
        )
    )
    if not include_defect:
        query = query.where(InboundReceipt.is_defect.is_(False))
    query = query.order_by(InboundReceipt.id.desc()).limit(500)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_receipt(db: AsyncSession, project_id: int, receipt_id: int) -> InboundReceipt | None:
    """Get receipt with items."""
    result = await db.execute(
        select(InboundReceipt).where(
            InboundReceipt.id == receipt_id,
            InboundReceipt.project_id == project_id,
            InboundReceipt.is_deleted == False,  # noqa: E712
        )
    )
    receipt = result.scalar_one_or_none()
    if receipt:
        # Eagerly load items
        await db.refresh(receipt, ["items"])
    return receipt


async def create_receipt(db: AsyncSession, project_id: int, warehouse_id: int, payload: dict) -> InboundReceipt:
    """Create a new inbound receipt with items (barcode lookup)."""
    # Verify warehouse exists
    wh = await get_warehouse(db, project_id, warehouse_id)
    if not wh:
        raise ValueError("Warehouse not found")

    number = await _next_number(db, project_id, "IN", InboundReceipt)

    receipt = InboundReceipt(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=number,
        status=InboundStatus.EXPECTED,
        planned_date=payload.get("planned_date"),
        comment=payload.get("comment"),
        tags=payload.get("tags"),
    )
    db.add(receipt)
    await db.flush()  # get receipt.id

    # Add items — resolve barcodes in one batch query.
    # Collapse duplicate barcode lines into a single line (summing qty). The
    # receipt model assumes one line per barcode (see update_receipt's
    # {barcode: item} map); without this dedup a payload that repeats a barcode
    # creates duplicate lines that double the stock on accept.
    items_data = payload.get("items", [])
    merged: dict[str, dict] = {}
    for d in items_data:
        bc = d["barcode"]
        if bc in merged:
            merged[bc]["expected_qty"] += d.get("expected_qty", 0) or 0
            merged[bc]["actual_qty"] += d.get("actual_qty", 0) or 0
        else:
            merged[bc] = {
                "expected_qty": d.get("expected_qty", 0) or 0,
                "actual_qty": d.get("actual_qty", 0) or 0,
            }
    barcode_map = await _resolve_barcodes_batch(db, project_id, list(merged.keys()))
    for bc, qty in merged.items():
        nom = barcode_map[bc]
        item = InboundReceiptItem(
            project_id=project_id,
            receipt_id=receipt.id,
            nomenclature_id=nom.id,
            barcode=bc,
            expected_qty=qty["expected_qty"],
            actual_qty=qty["actual_qty"],
        )
        db.add(item)

    await db.commit()
    await db.refresh(receipt, ["items"])

    # Best-effort: уведомить ФФ-оператора (портал, напр. Хамза) о новой приёмке на
    # его складе. CancelledError (BaseException) пробрасывается, прочее — глушим.
    try:
        from backend.services import fulfillment_notify

        await fulfillment_notify.notify_new_ff_acceptance(
            db,
            project_id,
            warehouse_id,
            warehouse_name=wh.name,
            items_count=len(merged),
            total_qty=sum(int(v["expected_qty"]) for v in merged.values()),
        )
    except Exception:
        logger.warning("new-ff-acceptance notify failed", exc_info=True)
    return receipt


async def update_receipt(db: AsyncSession, project_id: int, receipt_id: int, payload: dict) -> InboundReceipt | None:
    """Update receipt. DRAFT/EXPECTED: all fields. ACCEPTED: actual_qty + metadata only (with stock delta)."""
    receipt = await get_receipt(db, project_id, receipt_id)
    if not receipt:
        return None

    if receipt.status == InboundStatus.ACCEPTED:
        # ── ACCEPTED branch: only actual_qty changes + metadata ──
        for field in ("comment", "tags"):
            if field in payload:
                setattr(receipt, field, payload[field])

        if "items" in payload and payload["items"] is not None:
            existing_by_barcode = {item.barcode: item for item in receipt.items}
            for item_data in payload["items"]:
                barcode = item_data["barcode"]
                existing_item = existing_by_barcode.get(barcode)
                if not existing_item:
                    raise ValueError(f"Cannot add new items to ACCEPTED receipt. Barcode: {barcode}")

                new_actual = item_data.get("actual_qty", existing_item.actual_qty)
                old_actual = existing_item.actual_qty
                delta = new_actual - old_actual

                if delta != 0:
                    await _update_stock(
                        db,
                        project_id=project_id,
                        warehouse_id=receipt.warehouse_id,
                        nomenclature_id=existing_item.nomenclature_id,
                        barcode=barcode,
                        delta=delta,
                        movement_type=MovementType.INBOUND_EDIT,
                        reference_type="RECEIPT",
                        reference_id=receipt.id,
                        comment=f"Edit receipt {receipt.number}: {old_actual} → {new_actual}",
                    )
                    existing_item.actual_qty = new_actual

        await db.commit()
        await db.refresh(receipt, ["items"])
        return receipt

    elif receipt.status in (InboundStatus.DRAFT, InboundStatus.EXPECTED):
        # ── DRAFT/EXPECTED branch: full edit ──
        for field in ("planned_date", "comment", "tags"):
            if field in payload:
                setattr(receipt, field, payload[field])

        # Update status DRAFT → EXPECTED
        if (
            "status" in payload
            and payload["status"] == InboundStatus.EXPECTED
            and receipt.status == InboundStatus.DRAFT
        ):
            receipt.status = InboundStatus.EXPECTED

        # Replace items if provided
        if "items" in payload and payload["items"] is not None:
            for old_item in list(receipt.items):
                await db.delete(old_item)  # no-soft-delete-check: InboundReceiptItem has no SoftDeleteMixin
            await db.flush()

            barcode_map = await _resolve_barcodes_batch(db, project_id, [d["barcode"] for d in payload["items"]])
            for item_data in payload["items"]:
                nom = barcode_map[item_data["barcode"]]
                item = InboundReceiptItem(
                    project_id=project_id,
                    receipt_id=receipt.id,
                    nomenclature_id=nom.id,
                    barcode=item_data["barcode"],
                    expected_qty=item_data["expected_qty"],
                    actual_qty=item_data.get("actual_qty", 0),
                )
                db.add(item)

        await db.commit()
        await db.refresh(receipt, ["items"])
        return receipt

    else:
        raise ValueError(f"Cannot edit receipt in status {receipt.status}")


async def accept_receipt(
    db: AsyncSession,
    project_id: int,
    receipt_id: int,
    actual_quantities: list[dict] | None = None,
) -> InboundReceipt:
    """
    Accept receipt: DRAFT/EXPECTED → ACCEPTED.
    Adds actual_qty to warehouse stock for each item.
    If actual_quantities provided, updates actual_qty before accepting.
    """
    receipt = await get_receipt(db, project_id, receipt_id)
    if not receipt:
        raise ValueError("Receipt not found")

    if receipt.status not in (InboundStatus.DRAFT, InboundStatus.EXPECTED):
        raise ValueError(f"Cannot accept receipt in status {receipt.status}")

    if not receipt.items:
        raise ValueError("Cannot accept receipt with no items")

    # Serialize concurrent accepts. The status check above reads an unlocked
    # snapshot, so two near-simultaneous requests (a double-click, or the API
    # client's transparent 401-retry) both see DRAFT/EXPECTED before either
    # commits and both apply stock → quantity doubled (prod incident: receipt
    # #123 went 162 → 324). Lock the receipt row and re-check the *committed*
    # status under the lock: the second caller blocks until the first commits,
    # then sees ACCEPTED and bails.
    locked = await db.execute(
        select(InboundReceipt.status)
        .where(
            InboundReceipt.id == receipt_id,
            InboundReceipt.project_id == project_id,
        )
        .with_for_update()
    )
    locked_status = locked.scalar_one_or_none()
    if locked_status not in (InboundStatus.DRAFT, InboundStatus.EXPECTED):
        raise ValueError(f"Cannot accept receipt in status {locked_status}")

    # Idempotency net: if stock was already booked for this receipt (e.g. a
    # retry that landed after a prior accept committed), do not book it again.
    # Defect receipts (WB returns from PVZ) book via DEFECT_RECEIVE, regular
    # receipts via INBOUND — guard on whichever type this receipt applies.
    # cancel_receipt adds offsetting *_CANCEL rows and moves the receipt to
    # CANCELLED, so it can never reach this point — only a genuine
    # re-application trips this guard.
    apply_movement_type = MovementType.DEFECT_RECEIVE if receipt.is_defect else MovementType.INBOUND
    already_applied = await db.execute(
        select(StockMovement.id)
        .where(
            StockMovement.project_id == project_id,
            StockMovement.reference_type == "RECEIPT",
            StockMovement.reference_id == receipt_id,
            StockMovement.movement_type == apply_movement_type,
        )
        .limit(1)
    )
    if already_applied.first() is not None:
        raise ValueError("Receipt stock already applied")

    # Apply provided actual quantities
    if actual_quantities:
        qty_map = {aq["item_id"]: aq["actual_qty"] for aq in actual_quantities}
        for item in receipt.items:
            if item.id in qty_map:
                item.actual_qty = qty_map[item.id]

    # Auto-fill actual_qty from expected_qty if not set
    for item in receipt.items:
        if item.actual_qty <= 0 and item.expected_qty > 0:
            item.actual_qty = item.expected_qty

    # Update stock for each item. Defect receipts (WB returns from PVZ) book
    # into defect_quantity via DEFECT_RECEIVE — mirror of cancel_receipt's
    # is_defect branch; regular receipts add good stock via INBOUND.
    for item in receipt.items:
        if item.actual_qty <= 0:
            continue
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=receipt.warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=0 if receipt.is_defect else item.actual_qty,
            defect_delta=item.actual_qty if receipt.is_defect else 0,
            movement_type=apply_movement_type,
            reference_type="RECEIPT",
            reference_id=receipt.id,
        )

    receipt.status = InboundStatus.ACCEPTED
    receipt.actual_date = date.today()

    # Auto-transition vehicle DISPATCHED → DELIVERED
    vehicle_delivered = False
    pre_dist_advanced = 0
    if receipt.cost_order_id:
        from backend.models.cost import CostOrder, CostOrderItem
        from backend.models.enums import VehicleStatus
        from backend.models.supply_chain import FactoryOrderItem

        result = await db.execute(select(CostOrder).where(CostOrder.id == receipt.cost_order_id))
        vehicle = result.scalar_one_or_none()
        if vehicle and vehicle.status == VehicleStatus.DISPATCHED:
            vehicle.status = VehicleStatus.DELIVERED
            vehicle_delivered = True

            # Mark linked FactoryOrderItems as delivered
            foi_ids_result = await db.execute(
                select(CostOrderItem.factory_order_item_id).where(
                    CostOrderItem.order_no == vehicle.order_no,
                    CostOrderItem.project_id == project_id,
                    CostOrderItem.is_deleted == False,  # noqa: E712
                    CostOrderItem.factory_order_item_id.isnot(None),
                )
            )
            foi_ids = [row[0] for row in foi_ids_result.all()]
            if foi_ids:
                foi_result = await db.execute(
                    select(FactoryOrderItem).where(
                        FactoryOrderItem.id.in_(foi_ids),
                        FactoryOrderItem.project_id == project_id,
                        FactoryOrderItem.is_deleted == False,  # noqa: E712
                    )
                )
                for foi in foi_result.scalars().all():
                    foi.is_delivered = True

        # Предраспределение машины в пути: заявки сборки, зарезервированные под товар
        # этой машины (source_vehicle_id) ДО приёмки, на разгрузке становятся обычными
        # (PRE_DISTRIBUTED → IN_PROGRESS) — резерв стал реальным стоком в ЭТОЙ же
        # транзакции. Хук по cost_order_id, НЕ по переходу DISPATCHED→DELIVERED:
        # машина могла стоять в CUSTOMS, а товар уже физически приехал (критика H1).
        from backend.services.assembly.pre_distribution import _advance_pre_distribution_assemblies

        pre_dist_advanced = await _advance_pre_distribution_assemblies(db, project_id, receipt.cost_order_id)

    await db.commit()

    # Invalidate supplier catalog cache — delivered_qty depends on vehicle status
    if vehicle_delivered:
        from backend.cache import invalidate_cache

        await invalidate_cache(f"supply_chain:supplier_catalog:project_id={project_id}")

    # Предраспределение переведено в сборку → отчёты сборки/потребности устарели.
    if pre_dist_advanced:
        from backend.cache import invalidate_cache

        await invalidate_cache("reports:assembly_flow")
        await invalidate_cache("reports:assembly_link_anomalies")
        await invalidate_cache("reports:warehouse_need")

    await db.refresh(receipt, ["items"])
    return receipt


async def cancel_receipt(db: AsyncSession, project_id: int, receipt_id: int) -> InboundReceipt:
    """
    Cancel accepted receipt: ACCEPTED → CANCELLED.
    Rolls back actual_qty from warehouse stock.
    """
    receipt = await get_receipt(db, project_id, receipt_id)
    if not receipt:
        raise ValueError("Receipt not found")

    if receipt.status != InboundStatus.ACCEPTED:
        raise ValueError(f"Can only cancel ACCEPTED receipts, got {receipt.status}")

    # Reverse stock for each item. For defect receipts — rollback defect_quantity.
    for item in receipt.items:
        if item.actual_qty <= 0:
            continue
        if receipt.is_defect:
            delta = 0
            defect_delta = -item.actual_qty
        else:
            delta = -item.actual_qty
            defect_delta = 0
        await _update_stock(
            db,
            project_id=project_id,
            warehouse_id=receipt.warehouse_id,
            nomenclature_id=item.nomenclature_id,
            barcode=item.barcode,
            delta=delta,
            defect_delta=defect_delta,
            movement_type=MovementType.INBOUND_CANCEL,
            reference_type="RECEIPT",
            reference_id=receipt.id,
            comment=f"Cancel receipt {receipt.number}",
        )

    receipt.status = InboundStatus.CANCELLED

    await db.commit()
    await db.refresh(receipt, ["items"])
    return receipt


# ─── FF-портал: «взять в работу» + приёмка с расхождениями/браком ─────────────


async def claim_receipt(db: AsyncSession, project_id: int, receipt_id: int, user_id: int) -> InboundReceipt:
    """FF: «взять в работу». Помечает кто/когда начал приёмку (статус не меняется)."""
    receipt = await get_receipt(db, project_id, receipt_id)
    if not receipt:
        raise ValueError("Receipt not found")
    if receipt.status != InboundStatus.EXPECTED:
        raise ValueError(f"Взять в работу можно только ожидаемую приёмку, статус: {receipt.status}")
    receipt.assigned_to_user_id = user_id
    receipt.work_started_at = utcnow()
    await db.commit()
    await db.refresh(receipt, ["items"])
    return receipt


async def accept_receipt_ff(
    db: AsyncSession,
    project_id: int,
    receipt_id: int,
    items: list[dict],
) -> InboundReceipt:
    """FF: приёмка с факт. кол-вом и браком по позициям. EXPECTED → ACCEPTED.

    items: [{item_id, actual_qty, defect_qty, defect_reason?}].
      actual_qty → годный в WarehouseStock.quantity (INBOUND);
      defect_qty → брак в WarehouseStock.defect_quantity (DEFECT_RECEIVE);
      недовоз = expected − (actual + defect) — фиксируется расхождением (для сверки).
    Та же запись, что у нас → «автоматически принимается» бесплатно.
    """
    from backend.cache import invalidate_cache

    receipt = await get_receipt(db, project_id, receipt_id)
    if not receipt:
        raise ValueError("Receipt not found")
    if receipt.status not in (InboundStatus.DRAFT, InboundStatus.EXPECTED):
        raise ValueError(f"Нельзя принять приёмку в статусе {receipt.status}")
    if not receipt.items:
        raise ValueError("В приёмке нет позиций")

    # Row-lock + ре-чтение статуса (как в accept_receipt): сериализует двойной клик /
    # ретрай 401 — второй видит ACCEPTED под локом и падает, без двойного прихода.
    locked_status = (
        await db.execute(
            select(InboundReceipt.status)
            .where(InboundReceipt.id == receipt_id, InboundReceipt.project_id == project_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked_status not in (InboundStatus.DRAFT, InboundStatus.EXPECTED):
        raise ValueError(f"Нельзя принять приёмку в статусе {locked_status}")

    # Идемпотентность: если приход уже проведён для этой приёмки — не дублируем.
    # Сужаем до приходных типов (как accept_receipt): прочие движения по этой
    # приёмке (напр. INBOUND_CANCEL) не должны ложно блокировать приём.
    already = await db.execute(
        select(StockMovement.id)
        .where(
            StockMovement.project_id == project_id,
            StockMovement.reference_type == "RECEIPT",
            StockMovement.reference_id == receipt_id,
            StockMovement.movement_type.in_([MovementType.INBOUND, MovementType.DEFECT_RECEIVE]),
        )
        .limit(1)
    )
    if already.first() is not None:
        raise ValueError("Приход по этой приёмке уже проведён")

    by_id = {it.id: it for it in receipt.items}
    for payload in items:
        raw_id = payload.get("item_id")
        if raw_id is None:
            continue
        item = by_id.get(int(raw_id))
        if item is None:
            continue
        actual = max(0, int(payload.get("actual_qty") or 0))
        defect = max(0, int(payload.get("defect_qty") or 0))
        item.actual_qty = actual
        item.defect_qty = defect
        reason = payload.get("defect_reason")
        item.defect_reason = (reason or "").strip() or None

    # Проводим сток по факту: годный → quantity, брак → defect_quantity.
    for item in receipt.items:
        if item.actual_qty > 0:
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=receipt.warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=item.actual_qty,
                movement_type=MovementType.INBOUND,
                reference_type="RECEIPT",
                reference_id=receipt.id,
            )
        if item.defect_qty > 0:
            await _update_stock(
                db,
                project_id=project_id,
                warehouse_id=receipt.warehouse_id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                delta=0,
                defect_delta=item.defect_qty,
                movement_type=MovementType.DEFECT_RECEIVE,
                reference_type="RECEIPT",
                reference_id=receipt.id,
                comment="Брак при приёмке (ФФ)",
            )

    receipt.status = InboundStatus.ACCEPTED
    receipt.actual_date = date.today()

    await db.commit()
    await db.refresh(receipt, ["items"])

    await invalidate_cache("reports:balance")
    await invalidate_cache("reports:assembly_link_anomalies")
    return receipt
