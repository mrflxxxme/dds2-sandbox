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
    FactoryOrderCreate,
    FactoryOrderHistorySchema,
    FactoryOrderItemCreate,
    FactoryOrderItemSchema,
    FactoryOrderItemUpdate,
    FactoryOrderSchema,
    FactoryOrderStatusUpdate,
    FactoryOrderUpdate,
    SplitToVehiclesRequest,
    VehicleCreate,
    VehicleDocumentSchema,
    VehicleStatusHistorySchema,
    VehicleStatusUpdate,
    VehicleUpdate,
)
from backend.services.supply_chain import factory_orders, vehicle_delivery
from backend.services.supply_chain.vehicle_documents import (
    delete_document,
    download_document,
    list_documents,
    upload_document,
)
from backend.utils.rate_limit import rate_limit_write

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
    return FactoryOrderSchema.model_validate(order)


@router.put("/factory-orders/{order_id}", dependencies=[Depends(rate_limit_write)])
async def update_factory_order(
    order_id: int,
    payload: FactoryOrderUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    order = await factory_orders.update_factory_order(db, project.id, order_id, payload)
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


@router.post("/vehicles/{order_no}/items", dependencies=[Depends(rate_limit_write)])
async def add_items_to_vehicle(
    order_no: str,
    payload: AddItemsToVehicleRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await vehicle_delivery.add_items_to_vehicle(db, project.id, order_no, payload)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/vehicles/{order_no}/items/{item_id}", dependencies=[Depends(rate_limit_write)])
async def remove_item_from_vehicle(
    order_no: str,
    item_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await vehicle_delivery.remove_item_from_vehicle(db, project.id, order_no, item_id)
    except ValueError as e:
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
