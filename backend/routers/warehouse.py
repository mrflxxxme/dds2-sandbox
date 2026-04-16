"""
Router: /warehouse — warehouses CRUD, stock, receipts, shipments, transfers, adjustments.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.warehouse import (
    CostPriceUpdate,
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
    WarehouseCreate,
    WarehouseReorder,
    WarehouseSchema,
    WarehouseStockSchema,
    WarehouseUpdate,
)
from backend.services import warehouse_defect, warehouse_service
from backend.utils.rate_limit import rate_limit_write

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
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get current stock for a warehouse."""
    rows = await warehouse_service.get_warehouse_stock(db, project.id, warehouse_id)
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

    vehicles = await _get_expected(db, project.id, warehouse_id)
    return [
        {
            "order_no": v.order_no,
            "status": v.status,
            "invoice_no": v.invoice_no,
            "ship_date": v.ship_date.isoformat() if v.ship_date else None,
            "estimated_arrival_date": v.estimated_arrival_date.isoformat() if v.estimated_arrival_date else None,
            "actual_ship_date": v.actual_ship_date.isoformat() if v.actual_ship_date else None,
            "items_count": len(v.items),
            "total_qty": sum(i.qty for i in v.items),
            "container_type": v.container_type,
            "transport_type": v.transport_type,
        }
        for v in vehicles
    ]


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


@router.get("/transfers")
async def list_transfers(
    in_transit: bool = Query(False),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List transfers. ?in_transit=true for only active ones."""
    rows = await warehouse_service.list_transfers(db, project.id, in_transit)
    return [StockTransferSchema.model_validate(r) for r in rows]


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


@router.get("/defects/summary")
async def get_defect_summary(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Summary defective stock across all warehouses."""
    return await warehouse_defect.get_defect_summary(db, project.id)


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
