"""
WB FBO Supplies schemas: request/response models.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

# ─── Supply Item ────────────────────────────────────────────────────────────


class WbFboSupplyItemSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    supply_id: int
    wb_order_id: str
    nm_id: int | None = None
    barcode: str
    article_seller: str | None = None
    product_name: str | None = None
    quantity: int
    accepted_qty: int = 0


# ─── Supply ─────────────────────────────────────────────────────────────────


class WbFboSupplySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    project_id: int
    wb_supply_id: str
    wb_status: str
    name: str | None = None
    created_at_wb: datetime
    planned_date: date | None = None
    actual_date: date | None = None
    warehouse_name: str | None = None
    cargo_type: str | None = None
    total_qty: int = 0
    accepted_qty: int = 0
    outbound_shipment_id: int | None = None
    assembly_request_id: int | None = None
    assembly_request_number: str | None = None
    assembly_request_status: str | None = None
    synced_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WbFboSupplyWithItemsSchema(WbFboSupplySchema):
    """Supply with expanded items list."""

    items: list[WbFboSupplyItemSchema] = []


# ─── List response with pagination ──────────────────────────────────────────


class WbFboSupplyListResponse(BaseModel):
    items: list[WbFboSupplySchema]
    total: int


# ─── Sync response ──────────────────────────────────────────────────────────


class FboSyncResultSchema(BaseModel):
    """Result of FBO supplies sync operation."""

    synced: int = 0
    created: int = 0
    updated: int = 0
    errors: int = 0
    message: str = ""


# ─── Return (недоприёмка) ──────────────────────────────────────────────────


class FboReturnItem(BaseModel):
    barcode: str
    quantity: int


class FboReturnRequest(BaseModel):
    """
    Handle unaccepted qty for a supply with partial acceptance.
    return_type: GOODS | DEFECT | UTILIZED.
    warehouse_id required for GOODS/DEFECT (source warehouse).
    """

    return_type: str
    warehouse_id: int | None = None
    items: list[FboReturnItem]
    comment: str | None = None


class FboReturnResponse(BaseModel):
    supply_id: int
    receipt_id: int | None = None
    receipt_number: str | None = None
