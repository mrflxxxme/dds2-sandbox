"""
Router: /supply-chain — factory orders, vehicle status, supply chain overview.
Thin HTTP layer — all business logic is in services/supply_chain/.
"""

import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.config import settings
from backend.database import get_db
from backend.models import Project, User
from backend.project_context import get_current_project
from backend.schemas.supply_chain import (
    AddItemsToVehicleRequest,
    AddUnorderedItemsRequest,
    BulkPriceUpdateRequest,
    BulkRemoveVehicleItemsRequest,
    BulkUpdateItemSpecs,
    FactoryOrderArchiveUpdate,
    FactoryOrderCreate,
    FactoryOrderHistorySchema,
    FactoryOrderItemCreate,
    FactoryOrderItemSchema,
    FactoryOrderItemUpdate,
    FactoryOrderSchema,
    FactoryOrderStatusUpdate,
    FactoryOrderUpdate,
    MergeOrdersRequest,
    MergeOrdersResult,
    PostShipmentItemsRequest,
    SetMixGroupRequest,
    SetMixGroupResponse,
    SplitToVehiclesRequest,
    SupplierCreate,
    SupplierSchema,
    SupplierUpdate,
    SupplyProjectCreate,
    SupplyProjectSchema,
    SupplyProjectUpdate,
    VehicleCreate,
    VehicleDocumentSchema,
    VehicleItemUpdate,
    VehicleStatusHistorySchema,
    VehicleStatusUpdate,
    VehicleUpdate,
)
from backend.services.supply_chain import (
    factory_orders,
    supplier_catalog,
    supplier_service,
    supply_projects,
    vehicle_delivery,
)
from backend.services.supply_chain.factory_orders import FactoryQtyExceeded
from backend.services.supply_chain.vehicle_documents import (
    delete_document,
    download_document,
    list_documents,
    upload_document,
)
from backend.utils.rate_limit import rate_limit_read_heavy, rate_limit_write

ALLOWED_DOC_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv", ".doc", ".docx", ".jpg", ".jpeg", ".png"}

router = APIRouter(prefix="/supply-chain")


# ─── Factory Orders ──────────────────────────────────────────────────────────


@router.get("/factory-orders")
async def list_factory_orders(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    orders = await factory_orders.get_factory_orders(db, project.id)
    return [FactoryOrderSchema.model_validate(o) for o in orders]


@router.get("/factory-orders/{order_id}")
async def get_factory_order(
    order_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    order = await factory_orders.get_factory_order(db, project.id, order_id)
    if not order:
        raise HTTPException(404, "Factory order not found")
    return FactoryOrderSchema.model_validate(order)


@router.post("/factory-orders", status_code=201, dependencies=[Depends(rate_limit_write)])
async def create_factory_order(
    payload: FactoryOrderCreate,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        display_name = user.first_name or user.username or user.email
        order = await factory_orders.create_factory_order(db, project.id, payload, user_name=display_name)
    except IntegrityError:
        raise HTTPException(409, f"Заказ с номером «{payload.order_number}» уже существует") from None  # noqa: RUF001
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return FactoryOrderSchema.model_validate(order)


@router.post("/factory-orders/merge", dependencies=[Depends(rate_limit_write)])
async def merge_factory_orders(
    payload: MergeOrdersRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Объединить несколько заказов в один (target_id), слив дубли barcode."""
    try:
        display_name = user.first_name or user.username or user.email
        result = await factory_orders.merge_factory_orders(
            db, project.id, payload.target_id, payload.source_ids, user_name=display_name
        )
        return MergeOrdersResult.model_validate(result)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/factory-orders/{order_id}", dependencies=[Depends(rate_limit_write)])
async def update_factory_order(
    order_id: int,
    payload: FactoryOrderUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        order = await factory_orders.update_factory_order(db, project.id, order_id, payload)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not order:
        raise HTTPException(404, "Factory order not found")
    return FactoryOrderSchema.model_validate(order)


@router.put("/factory-orders/{order_id}/archive", dependencies=[Depends(rate_limit_write)])
async def archive_factory_order(
    order_id: int,
    payload: FactoryOrderArchiveUpdate,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Закрыть (скрыть из списка) или открыть заказ — ручной флаг is_archived."""
    display_name = user.first_name or user.username or user.email
    order = await factory_orders.set_order_archived(
        db, project.id, order_id, payload.is_archived, user_name=display_name
    )
    if not order:
        raise HTTPException(404, "Factory order not found")
    return FactoryOrderSchema.model_validate(order)


@router.delete("/factory-orders/{order_id}", dependencies=[Depends(rate_limit_write)])
async def delete_factory_order(
    order_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    deleted = await factory_orders.delete_factory_order(db, project.id, order_id)
    if not deleted:
        raise HTTPException(404, "Factory order not found")
    return {"ok": True}


@router.post("/factory-orders/{order_id}/items", dependencies=[Depends(rate_limit_write)])
async def add_items_to_order(
    order_id: int,
    items: list[FactoryOrderItemCreate],
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        display_name = user.first_name or user.username or user.email
        created = await factory_orders.add_items(db, project.id, order_id, items, user_name=display_name)
        return {"ok": True, "added": len(created)}
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.put("/factory-orders/{order_id}/items/bulk-specs", dependencies=[Depends(rate_limit_write)])
async def bulk_update_item_specs(
    order_id: int,
    payload: list[BulkUpdateItemSpecs],
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        updates = [u.model_dump(exclude_unset=True) for u in payload]
        return await factory_orders.bulk_update_items_specs(db, project.id, order_id, updates)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/factory-orders/items/bulk-price", dependencies=[Depends(rate_limit_write)])
async def bulk_update_item_prices(
    payload: BulkPriceUpdateRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    updates = [u.model_dump() for u in payload.items]
    return await factory_orders.bulk_update_item_prices(db, project.id, updates, user_name=user.email)


@router.put("/factory-orders/{order_id}/items/{item_id}", dependencies=[Depends(rate_limit_write)])
async def update_factory_order_item(
    order_id: int,
    item_id: int,
    payload: FactoryOrderItemUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        item = await factory_orders.update_item(db, project.id, order_id, item_id, payload)
        if not item:
            raise HTTPException(404, "Item not found")
        return FactoryOrderItemSchema.model_validate(item)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/factory-orders/{order_id}/items/{item_id}", dependencies=[Depends(rate_limit_write)])
async def delete_factory_order_item(
    order_id: int,
    item_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        await factory_orders.delete_item(db, project.id, order_id, item_id)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/factory-orders/{order_id}/split-to-vehicles", dependencies=[Depends(rate_limit_write)])
async def split_to_vehicles(
    order_id: int,
    payload: SplitToVehiclesRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        display_name = user.first_name or user.username or user.email
        result = await factory_orders.split_to_vehicles(db, project.id, order_id, payload, user_name=display_name)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ─── Mix Groups ──────────────────────────────────────────────────────────────


@router.post("/factory-orders/{order_id}/mix-group", dependencies=[Depends(rate_limit_write)])
async def set_mix_group(
    order_id: int,
    payload: SetMixGroupRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        mix_id, item_ids = await factory_orders.set_mix_group(db, project.id, order_id, payload.items, payload.box_size)
        return SetMixGroupResponse(mix_group_id=mix_id, item_ids=item_ids, box_size=payload.box_size)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/factory-orders/{order_id}/mix-group/{mix_group_id}", dependencies=[Depends(rate_limit_write)])
async def remove_mix_group(
    order_id: int,
    mix_group_id: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        count = await factory_orders.remove_mix_group(db, project.id, order_id, mix_group_id)
        return {"ok": True, "removed_items": count}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ─── Factory Order History & Status ─────────────────────────────────────────


@router.get("/factory-orders/{order_id}/history")
async def get_factory_order_history(
    order_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    history = await factory_orders.get_factory_order_history(db, project.id, order_id)
    return [FactoryOrderHistorySchema.model_validate(h) for h in history]


@router.put("/factory-orders/{order_id}/status", dependencies=[Depends(rate_limit_write)])
async def update_factory_order_status(
    order_id: int,
    payload: FactoryOrderStatusUpdate,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        display_name = user.first_name or user.username or user.email
        order = await factory_orders.update_factory_order_status(
            db,
            project.id,
            order_id,
            payload.status,
            user_name=display_name,
        )
        if not order:
            raise HTTPException(404, "Factory order not found")
        return FactoryOrderSchema.model_validate(order)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ─── Vehicles (CostOrder) ────────────────────────────────────────────────────


@router.get("/vehicles")
async def list_vehicles(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await vehicle_delivery.list_vehicles(db, project.id)


@router.get("/vehicles/available-items")
async def get_available_items(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get factory order items with remaining qty > 0, grouped by order."""
    return await vehicle_delivery.get_available_items(db, project.id)


@router.get("/vehicles/find")
async def find_vehicle_by_order_no(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Find a vehicle by order_no passed as query param (supports slashes in order_no)."""
    vehicle = await vehicle_delivery.get_vehicle(db, project.id, order_no)
    if not vehicle:
        raise HTTPException(404, "Vehicle not found")
    return vehicle


@router.delete("/vehicles/find", dependencies=[Depends(rate_limit_write)])
async def delete_vehicle_by_order_no(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Delete a vehicle by order_no passed as query param (supports slashes in order_no)."""
    try:
        return await vehicle_delivery.delete_vehicle(db, project.id, order_no)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/vehicles/{order_no}")
async def get_vehicle(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await vehicle_delivery.get_vehicle(db, project.id, order_no)
    if not vehicle:
        raise HTTPException(404, "Vehicle not found")
    return vehicle


@router.post("/vehicles", status_code=201, dependencies=[Depends(rate_limit_write)])
async def create_vehicle(
    payload: VehicleCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await vehicle_delivery.create_vehicle(db, project.id, payload)
    except IntegrityError:
        raise HTTPException(409, f"Машина «{payload.order_no}» уже существует") from None


@router.put("/vehicles/{order_no}", dependencies=[Depends(rate_limit_write)])
async def update_vehicle(
    order_no: str,
    payload: VehicleUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        vehicle = await vehicle_delivery.update_vehicle(db, project.id, order_no, payload)
        if not vehicle:
            raise HTTPException(404, "Vehicle not found")
        return vehicle
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/vehicles/{order_no}", dependencies=[Depends(rate_limit_write)])
async def delete_vehicle(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await vehicle_delivery.delete_vehicle(db, project.id, order_no)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/vehicles/{order_no}/status", dependencies=[Depends(rate_limit_write)])
async def update_vehicle_status(
    order_no: str,
    payload: VehicleStatusUpdate,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        display_name = user.first_name or user.username or user.email
        result = await vehicle_delivery.update_vehicle_status(db, project.id, order_no, payload, user_name=display_name)
        return result
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/vehicles/recalc-all", dependencies=[Depends(rate_limit_write)])
async def recalculate_all_vehicles(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Recalculate costs for all vehicles in the project."""
    return await vehicle_delivery.recalculate_all_vehicles(db, project.id)


@router.post("/vehicles/{order_no}/recalc", dependencies=[Depends(rate_limit_write)])
async def recalculate_vehicle(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await vehicle_delivery.recalculate_vehicle(db, project.id, order_no)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        import logging

        logging.getLogger("dds").exception("recalculate_vehicle %s failed", order_no)
        raise HTTPException(500, f"Ошибка пересчёта: {e}") from e


@router.post("/vehicles/{order_no}/items", dependencies=[Depends(rate_limit_write)])
async def add_items_to_vehicle(
    order_no: str,
    payload: AddItemsToVehicleRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    display_name = user.first_name or user.username or user.email
    try:
        return await vehicle_delivery.add_items_to_vehicle(db, project.id, order_no, payload, user_name=display_name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/vehicles/{order_no}/unordered-items", dependencies=[Depends(rate_limit_write)])
async def add_unordered_items_to_vehicle(
    order_no: str,
    payload: AddUnorderedItemsRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Корзина «✗»: завести позиции в заказ (новый/существующий) и положить в машину."""
    display_name = user.first_name or user.username or user.email
    try:
        return await vehicle_delivery.add_unordered_items_to_vehicle(
            db, project.id, order_no, payload, user_name=display_name
        )
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Конфликт номера авто-заказа, повторите") from None
    except FactoryQtyExceeded as e:
        raise HTTPException(422, detail=e.detail) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/vehicles/{order_no}/post-shipment-items", dependencies=[Depends(rate_limit_write)])
async def add_post_shipment_items(
    order_no: str,
    payload: PostShipmentItemsRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """sc18: добавить позиции в машину после отгрузки (status >= SHIPPED).

    DISPATCHED — добавляет в существующий InboundReceipt.
    DELIVERED — создаёт отдельный InboundReceipt (DRAFT) для ручной приёмки.
    """
    display_name = user.first_name or user.username or user.email
    try:
        return await vehicle_delivery.add_items_post_shipment(
            db,
            project.id,
            order_no,
            payload,
            user_name=display_name,
        )
    except FactoryQtyExceeded as e:
        raise HTTPException(422, detail=e.detail) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/vehicles/{order_no}/price-resync/preview")
async def preview_vehicle_price_resync(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Preview items whose price differs from the linked factory order."""
    try:
        return await vehicle_delivery.preview_price_resync(db, project.id, order_no)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/vehicles/{order_no}/price-resync", dependencies=[Depends(rate_limit_write)])
async def apply_vehicle_price_resync(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Apply price resync: update CostOrderItem.price_cny from factory orders + recalc."""
    try:
        return await vehicle_delivery.apply_price_resync(db, project.id, order_no)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.delete("/vehicles/{order_no}/items/{item_id}", dependencies=[Depends(rate_limit_write)])
async def remove_item_from_vehicle(
    order_no: str,
    item_id: int,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    display_name = user.first_name or user.username or user.email
    try:
        return await vehicle_delivery.remove_item_from_vehicle(
            db, project.id, order_no, item_id, user_name=display_name
        )
    except ValueError as e:
        # sc19: статус не разрешает удаление (например DELIVERED) → 400
        if "запрещено" in str(e).lower():
            raise HTTPException(400, str(e)) from e
        raise HTTPException(404, str(e)) from e


@router.patch("/vehicles/{order_no}/items/{item_id}", dependencies=[Depends(rate_limit_write)])
async def update_vehicle_item(
    order_no: str,
    item_id: int,
    payload: VehicleItemUpdate,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        fields = payload.model_dump(exclude_unset=True)
        display_name = user.first_name or user.username or user.email
        return await vehicle_delivery.update_vehicle_item(
            db,
            project.id,
            order_no,
            item_id,
            new_qty=payload.qty,
            box_size_override=payload.box_size_override,
            pcs_per_box_override=payload.pcs_per_box_override,
            box_detail_override=payload.box_detail_override,
            set_box_size_override="box_size_override" in fields,
            set_pcs_per_box_override="pcs_per_box_override" in fields,
            set_box_detail_override="box_detail_override" in fields,
            mode=payload.mode,
            user_name=display_name,
        )
    except FactoryQtyExceeded as e:
        raise HTTPException(422, detail=e.detail) from e
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.delete("/vehicles/{order_no}/items", dependencies=[Depends(rate_limit_write)])
async def clear_all_vehicle_items(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await vehicle_delivery.clear_all_vehicle_items(db, project.id, order_no)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/vehicles/{order_no}/items/bulk-delete", dependencies=[Depends(rate_limit_write)])
async def bulk_remove_vehicle_items(
    order_no: str,
    payload: BulkRemoveVehicleItemsRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Удалить N позиций машины одним вызовом (вместо N DELETE-запросов).

    DELETE-with-body не везде поддерживается прокси, поэтому POST. rate_limit_write
    срабатывает один раз на всю партию.
    """
    display_name = user.first_name or user.username or user.email
    try:
        return await vehicle_delivery.bulk_remove_items_from_vehicle(
            db, project.id, order_no, payload.item_ids, user_name=display_name
        )
    except ValueError as e:
        if "запрещено" in str(e).lower():
            raise HTTPException(400, str(e)) from e
        raise HTTPException(404, str(e)) from e


# ─── Vehicle Documents ──────────────────────────────────────────────────────

ALLOWED_DOC_TYPES = {"invoice", "order", "customs_declaration", "contract", "other"}


@router.post("/vehicles/{order_no}/documents", status_code=201, dependencies=[Depends(rate_limit_write)])
async def upload_vehicle_document(
    order_no: str,
    file: UploadFile = File(...),
    doc_type: str = Form(...),
    note: str | None = Form(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    # Validate extension
    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(
            400, f"Недопустимый тип файла: {ext}. Разрешены: {', '.join(sorted(ALLOWED_DOC_EXTENSIONS))}"
        )

    # Read and validate size
    data = await file.read()
    max_size = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(data) > max_size:
        raise HTTPException(413, f"Файл слишком большой. Максимум: {settings.MAX_UPLOAD_SIZE_MB} МБ")

    # Validate file magic bytes
    from backend.utils.file_validation import validate_file_content

    validate_file_content(data, filename)

    try:
        doc = await upload_document(db, project.id, order_no, data, filename, doc_type, note)
        return VehicleDocumentSchema.model_validate(doc)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/vehicles/{order_no}/documents")
async def list_vehicle_documents(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    docs = await list_documents(db, project.id, order_no)
    return [VehicleDocumentSchema.model_validate(d) for d in docs]


@router.get("/vehicles/{order_no}/documents/{doc_id}/download")
async def download_vehicle_document(
    order_no: str,
    doc_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        data, filename, content_type = await download_document(db, project.id, doc_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    # Sanitize filename for Content-Disposition header (prevent header injection)
    from urllib.parse import quote

    safe_name = filename.replace('"', "").replace("\n", "").replace("\r", "")
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe_name)}"},
    )


@router.delete("/vehicles/{order_no}/documents/{doc_id}", dependencies=[Depends(rate_limit_write)])
async def delete_vehicle_document(
    order_no: str,
    doc_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    deleted = await delete_document(db, project.id, doc_id)
    if not deleted:
        raise HTTPException(404, "Document not found")
    return {"ok": True}


# ─── Vehicle Status History ─────────────────────────────────────────────────


@router.get("/vehicles/{order_no}/history")
async def get_vehicle_status_history(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    history = await vehicle_delivery.get_vehicle_history(db, project.id, order_no)
    return [VehicleStatusHistorySchema.model_validate(h) for h in history]


# ─── Overview ────────────────────────────────────────────────────────────────


@router.get("/overview")
async def supply_chain_overview(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await vehicle_delivery.get_supply_chain_overview(db, project.id)


# ─── Suppliers ───────────────────────────────────────────────────────────────


@router.get("/suppliers")
async def list_suppliers(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    suppliers = await supplier_service.list_suppliers(db, project.id)
    return [SupplierSchema.model_validate(s) for s in suppliers]


@router.get("/suppliers/{supplier_id}/catalog", dependencies=[Depends(rate_limit_read_heavy)])
async def get_supplier_catalog_endpoint(
    supplier_id: int,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    return await supplier_catalog.get_supplier_catalog(db, project.id, supplier_id)


@router.get("/suppliers/{supplier_id}/shipment-matrix", dependencies=[Depends(rate_limit_read_heavy)])
async def get_shipment_matrix_endpoint(
    supplier_id: int,
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    return await supplier_catalog.get_shipment_matrix(db, project.id, supplier_id)


@router.get("/suppliers/{supplier_id}")
async def get_supplier(
    supplier_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    supplier = await supplier_service.get_supplier(db, project.id, supplier_id)
    if not supplier:
        raise HTTPException(404, "Supplier not found")
    return SupplierSchema.model_validate(supplier)


@router.post("/suppliers", status_code=201, dependencies=[Depends(rate_limit_write)])
async def create_supplier(
    payload: SupplierCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        supplier = await supplier_service.create_supplier(db, project.id, payload)
    except IntegrityError:
        raise HTTPException(409, f"Supplier with name '{payload.name}' already exists") from None
    return SupplierSchema.model_validate(supplier)


@router.put("/suppliers/{supplier_id}", dependencies=[Depends(rate_limit_write)])
async def update_supplier(
    supplier_id: int,
    payload: SupplierUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        supplier = await supplier_service.update_supplier(db, project.id, supplier_id, payload)
    except IntegrityError:
        raise HTTPException(409, "Supplier with this name already exists") from None
    if not supplier:
        raise HTTPException(404, "Supplier not found")
    return SupplierSchema.model_validate(supplier)


@router.delete("/suppliers/{supplier_id}", dependencies=[Depends(rate_limit_write)])
async def delete_supplier(
    supplier_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    deleted = await supplier_service.delete_supplier(db, project.id, supplier_id)
    if not deleted:
        raise HTTPException(404, "Supplier not found")
    return {"ok": True}


# ─── Supply Projects (grouping of factory orders) ────────────────────────────


@router.get("/projects")
async def list_supply_projects(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    projects = await supply_projects.list_supply_projects(db, project.id)
    return [SupplyProjectSchema.model_validate(p) for p in projects]


@router.post("/projects", status_code=201, dependencies=[Depends(rate_limit_write)])
async def create_supply_project(
    payload: SupplyProjectCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    sp = await supply_projects.create_supply_project(db, project.id, payload)
    return SupplyProjectSchema.model_validate(sp)


@router.put("/projects/{sp_id}", dependencies=[Depends(rate_limit_write)])
async def update_supply_project(
    sp_id: int,
    payload: SupplyProjectUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    sp = await supply_projects.update_supply_project(db, project.id, sp_id, payload)
    if not sp:
        raise HTTPException(404, "Supply project not found")
    return SupplyProjectSchema.model_validate(sp)


@router.delete("/projects/{sp_id}", dependencies=[Depends(rate_limit_write)])
async def delete_supply_project(
    sp_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    deleted = await supply_projects.delete_supply_project(db, project.id, sp_id)
    if not deleted:
        raise HTTPException(404, "Supply project not found")
    return {"ok": True}
